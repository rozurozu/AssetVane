"""判断ログ横断想起の FTS5 索引（judgment_fts）と同期トリガ（ADR-078・D-1）。

設計の真実: docs/decisions.md ADR-078・tasks/hermes-transfer-2026-07-02.md（★2 D-1）。

夜AI・チャットの過去判断（帯域 n=1 問題）を、埋め込み/LLM 不要の全文検索で横断想起できる
ようにする。永続済みの判断ログ 3 ソース（advisor_journal / proposals / notable_picks）だけを
trigram トークナイザ（CJK 部分一致）で索引し、生チャットは索引しない（ADR-029 の会話揮発を守る）。

構造上の要点:
- FTS5 仮想表・トリガは SQLAlchemy の metadata（schema.py）に載らない。そこで DDL の**単一
  真実源**をこのモジュールに置き、alembic migration（本番）と create_schema()（テスト経路）の
  両方から `ensure_judgment_fts` を呼ぶ（同じ DDL を二重に書かない＝ADR-078 の Q3=A）。
- 統合スタンドアロン 1 表（origin 判別列つき）。external-content は使わない（トリガが単純・
  結果ランキングが 1 本＝ADR-078 の Q2=A）。body にテキストのコピーを持つ（1 日数行なので容量は
  無視でき、索引済み列は既に永続済みなので ADR-029 と矛盾しない）。
- 既存行の初回投入（backfill）は `rebuild_judgment_fts`（全消し＋3 表から再 INSERT・reindex
  兼用）で行い、**migration だけ**が呼ぶ（本番は既存 journal/proposals/notable がある。新規 DB・
  テストは空なので no-op）。

計算境界（ADR-014/016）とは無関係の索引 DDL なので、生 SQL（text）で組み立てる。値は列名
定数のみで外部入力は混ぜない（検索クエリは repo 側で bind＝別モジュール）。
"""

from __future__ import annotations

from sqlalchemy import Connection, text

# 仮想表名（downgrade/検証で参照）。
FTS_TABLE = "judgment_fts"

# 同期トリガ名（各基底表 × INSERT/UPDATE/DELETE の 9 本・downgrade で DROP する）。
TRIGGER_NAMES: tuple[str, ...] = (
    "judgment_fts_journal_ai",
    "judgment_fts_journal_ad",
    "judgment_fts_journal_au",
    "judgment_fts_proposal_ai",
    "judgment_fts_proposal_ad",
    "judgment_fts_proposal_au",
    "judgment_fts_notable_ai",
    "judgment_fts_notable_ad",
    "judgment_fts_notable_au",
)

# 仮想表の DDL。body=検索対象（trigram）。origin/ref_id/code/entry_date は UNINDEXED
# （MATCH と併記して WHERE で絞る／表示・整列に使う。join せず 1 発で返せる）。
_CREATE_FTS = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    body,
    origin UNINDEXED,
    ref_id UNINDEXED,
    code UNINDEXED,
    entry_date UNINDEXED,
    tokenize = 'trigram'
);
"""

# 各ソースの「body に詰めるテキスト」「code 抽出」「entry_date」。トリガ（new.*）と backfill
# （直接列参照）の両方で使う式のカタログ（DDL の重複を最小化）。proposals.body は JSON だが
# 種別により code を持たないことがあるので json_valid で守ってから json_extract する（不正 JSON で
# proposals の INSERT ごと落とさない＝頑健性）。
_SOURCES = (
    {
        "origin": "journal",
        "table": "advisor_journal",
        "body": "trim(coalesce({p}observations, '') || ' ' || coalesce({p}proposal, ''))",
        "code": "NULL",
        "date": "{p}date",
    },
    {
        "origin": "proposal",
        "table": "proposals",
        "body": "coalesce({p}rationale, '')",
        "code": "CASE WHEN json_valid({p}body) THEN json_extract({p}body, '$.code') ELSE NULL END",
        "date": "{p}created_date",
    },
    {
        "origin": "notable",
        "table": "notable_picks",
        "body": "coalesce({p}reason, '')",
        "code": "{p}code",
        "date": "{p}date",
    },
)


def _insert_values(src: dict[str, str], prefix: str) -> str:
    """judgment_fts への INSERT の VALUES 本体（prefix で new./無印を切替）。"""
    body = src["body"].format(p=prefix)
    code = src["code"].format(p=prefix)
    date = src["date"].format(p=prefix)
    ref = f"{prefix}id"
    return f"({body}, '{src['origin']}', {ref}, {code}, {date})"


def _trigger_ddls() -> list[str]:
    """9 本の同期トリガ DDL（AFTER INSERT/UPDATE/DELETE）を組み立てる。"""
    ddls: list[str] = []
    for src in _SOURCES:
        origin = src["origin"]
        table = src["table"]
        values = _insert_values(src, "new.")
        insert = f"INSERT INTO {FTS_TABLE}(body, origin, ref_id, code, entry_date) VALUES {values};"
        delete = f"DELETE FROM {FTS_TABLE} WHERE origin='{origin}' AND ref_id=old.id;"
        ddls.append(
            f"CREATE TRIGGER IF NOT EXISTS judgment_fts_{origin}_ai "
            f"AFTER INSERT ON {table} BEGIN {insert} END;"
        )
        ddls.append(
            f"CREATE TRIGGER IF NOT EXISTS judgment_fts_{origin}_ad "
            f"AFTER DELETE ON {table} BEGIN {delete} END;"
        )
        # UPDATE は ref_id 不変なので旧行を消してから入れ直す（本文/code/date を作り直す）。
        ddls.append(
            f"CREATE TRIGGER IF NOT EXISTS judgment_fts_{origin}_au "
            f"AFTER UPDATE ON {table} BEGIN {delete} {insert} END;"
        )
    return ddls


def _backfill_inserts() -> list[str]:
    """既存行を判断ログから judgment_fts へ流し込む INSERT ... SELECT（rebuild 用）。"""
    inserts: list[str] = []
    for src in _SOURCES:
        body = src["body"].format(p="")
        code = src["code"].format(p="")
        date = src["date"].format(p="")
        inserts.append(
            f"INSERT INTO {FTS_TABLE}(body, origin, ref_id, code, entry_date) "
            f"SELECT {body}, '{src['origin']}', id, {code}, {date} FROM {src['table']};"
        )
    return inserts


def ensure_judgment_fts(conn: Connection) -> None:
    """judgment_fts 仮想表＋同期トリガを冪等に作る（構造のみ・ADR-078）。

    migration と create_schema() の両方から呼ぶ。IF NOT EXISTS なので二重実行しても無害。
    backfill はしない（新規 DB・テストは空・既存行の投入は rebuild が担う）。
    """
    conn.execute(text(_CREATE_FTS))
    for ddl in _trigger_ddls():
        conn.execute(text(ddl))


def rebuild_judgment_fts(conn: Connection) -> None:
    """judgment_fts を作り直す（全消し＋3 表から再 INSERT・reindex/backfill 兼用・冪等）。

    migration の upgrade が既存 journal/proposals/notable_picks を投入するために呼ぶ。トリガで
    随時同期されるので通常運用では不要だが、索引を作り直したいとき（DDL 変更後の再構築）にも使う。
    """
    ensure_judgment_fts(conn)
    conn.execute(text(f"DELETE FROM {FTS_TABLE};"))
    for ins in _backfill_inserts():
        conn.execute(text(ins))


def drop_judgment_fts(conn: Connection) -> None:
    """同期トリガと仮想表を落とす（migration の downgrade 用）。"""
    for name in TRIGGER_NAMES:
        conn.execute(text(f"DROP TRIGGER IF EXISTS {name};"))
    conn.execute(text(f"DROP TABLE IF EXISTS {FTS_TABLE};"))
