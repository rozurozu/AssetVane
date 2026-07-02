"""Optimizer データ・財務・バリュエ・スクリーニング・資産スナップショット（Phase 2・ADR-031）。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Float, and_, func, select, type_coerce, update

from app.db.engine import get_engine
from app.db.repo._common import _upsert
from app.db.schema import (
    asset_snapshots,
    financials,
    holdings,
    index_quotes,
    screening_filters,
    stocks,
    valuation_snapshots,
)

# ===== Phase 2: Portfolio Optimizer（phase2-spec.md §2・ADR-002） =====


def upsert_index_quotes(rows: list[dict[str, Any]]) -> int:
    """index_quotes を冪等 UPSERT する（phase2-spec.md §3.1・ADR-002）。

    衝突キー: (symbol, date)。IndexAdapter が内部列名に正規化済みの行を受け取る。
    """
    return _upsert(index_quotes, rows, index_elements=["symbol", "date"])


def upsert_financials(rows: list[dict[str, Any]]) -> int:
    """financials を冪等 UPSERT する（phase2-spec.md §3.2・ADR-002・0005_financials）。

    衝突キー: (code, disclosed_date, fiscal_period)。JQuantsAdapter が正規化済みの行を受け取る。
    """
    return _upsert(financials, rows, index_elements=["code", "disclosed_date", "fiscal_period"])


def get_financials(conn: Connection, code: str, limit: int = 8) -> list[dict[str, Any]]:
    """指定銘柄の financials を disclosed_date 降順で返す（get_financials Tool 用・spec §4.4）。

    各行 dict は disclosed_date/fiscal_period/net_sales/operating_profit/profit/eps/bps を含む。
    `limit` は直近 N 件（既定 8 = 約 2 年分の四半期）。素の dict 返し（変換はルータ/handler）。
    """
    stmt = (
        select(
            financials.c.disclosed_date,
            financials.c.fiscal_period,
            financials.c.net_sales,
            financials.c.operating_profit,
            financials.c.profit,
            financials.c.eps,
            financials.c.bps,
        )
        .where(financials.c.code == code)
        .order_by(financials.c.disclosed_date.desc())
        .limit(limit)
    )
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# --- バリュエーション・スクリーニング（ADR-031・0007_screening） ---


def _latest_financials_subquery(only_with_bps: bool):
    """銘柄ごと disclosed_date 降順で 1 番目の行を取る window サブクエリを組む（ADR-031）。

    only_with_bps=True は BPS が入る通期(FY)行のみを対象にし、PER/PBR 用の実績 EPS/BPS を拾う
    （四半期は EPS が累計・BPS が空のため＝実機確認 2026-06）。
    """
    rn = (
        func.row_number()
        .over(
            partition_by=financials.c.code,
            order_by=financials.c.disclosed_date.desc(),
        )
        .label("rn")
    )
    base = select(
        financials.c.code,
        financials.c.disclosed_date,
        financials.c.net_sales,
        financials.c.operating_profit,
        financials.c.profit,
        financials.c.eps,
        financials.c.bps,
        financials.c.dividend_per_share,
        financials.c.shares_outstanding,
        financials.c.treasury_shares,
        rn,
    )
    if only_with_bps:
        base = base.where(financials.c.bps.isnot(None))
    return base.subquery()


def get_latest_financials_by_code(conn: Connection) -> dict[str, dict[str, Any]]:
    """銘柄ごと最新開示行（配当・株数用）を {code: {...}} で返す（ADR-031）。"""
    sub = _latest_financials_subquery(only_with_bps=False)
    rows = conn.execute(select(sub).where(sub.c.rn == 1)).mappings().all()
    return {r["code"]: dict(r) for r in rows}


def get_latest_annual_financials_by_code(conn: Connection) -> dict[str, dict[str, Any]]:
    """銘柄ごと最新の通期(FY)行（実績 EPS/BPS・利益率・成長率の当期）を {code: {...}} で返す。

    ADR-031（PER/PBR の実績 EPS/BPS）＋ ADR-048（営業利益率/純利益率/YoY の当期＝最新FY）。
    """
    sub = _latest_financials_subquery(only_with_bps=True)
    rows = conn.execute(select(sub).where(sub.c.rn == 1)).mappings().all()
    return {r["code"]: dict(r) for r in rows}


def get_prior_annual_financials_by_code(conn: Connection) -> dict[str, dict[str, Any]]:
    """銘柄ごと前期の通期(FY)行（YoY 成長率の前年同期）を {code: {...}} で返す（ADR-048）。

    最新FYの 1 つ前（rn==2）。同一 fiscal_period タイプ（FY）の直前行を前年同期として使う。
    前期 FY が無い銘柄（新規上場等）は dict に現れない（成長率は None になる）。
    """
    sub = _latest_financials_subquery(only_with_bps=True)
    rows = conn.execute(select(sub).where(sub.c.rn == 2)).mappings().all()
    return {r["code"]: dict(r) for r in rows}


def get_recent_financials_by_code(
    conn: Connection, limit: int = 8
) -> dict[str, list[dict[str, Any]]]:
    """銘柄ごと直近 N 開示行（実績＋会社予想）を {code: [新しい順 dict]} で返す（ADR-063 #4）。

    会社予想の beat/miss・上方/下方修正（quant.forecast_guidance）の素。各四半期開示に当期FY予想が
    standing で載り FY実績行では空になる実機の形を、複数開示行をまとめて service へ渡すために引く。
    既定 limit=8（約 2 年分の四半期＝当期＋前期FY の予想/実績が収まる）。
    """
    rn = (
        func.row_number()
        .over(partition_by=financials.c.code, order_by=financials.c.disclosed_date.desc())
        .label("rn")
    )
    sub = select(
        financials.c.code,
        financials.c.disclosed_date,
        financials.c.fiscal_period,
        financials.c.operating_profit,
        financials.c.profit,
        financials.c.forecast_operating_profit,
        financials.c.forecast_profit,
        rn,
    ).subquery()
    stmt = select(sub).where(sub.c.rn <= limit).order_by(sub.c.code, sub.c.disclosed_date.desc())
    out: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(stmt).mappings().all():
        row = dict(r)
        out.setdefault(row["code"], []).append(row)
    return out


def upsert_valuation_snapshots(rows: list[dict[str, Any]]) -> int:
    """valuation_snapshots を冪等 UPSERT（code 1 行・最新のみ保持・ADR-002/031）。

    partial=True＝**渡された列だけ**更新する。売掛/在庫の質列（DSO/DIO・受取債権/在庫 YoY）は
    calc_valuation ではなく後段 calc_receivables_inventory が cadence で UPDATE 充填するため
    （ADR-064 #2）、全列 EXCLUDED 更新にすると毎晩ここで NULL に潰れ、7 晩に 1 晩しか #2 が
    生きなくなる。build_valuation_snapshots が作る主要列だけを更新し #2 列は温存する（#1）。
    """
    return _upsert(valuation_snapshots, rows, index_elements=["code"], partial=True)


# screen_stocks が受け付ける数値レンジのキー（{field}_min / {field}_max で絞る）。
# ADR-048 で ROE・利益率・YoY 成長率を追加（バリュエーション + ファンダの横断スクリーン）。
_SCREEN_RANGE_FIELDS = (
    "per",
    "pbr",
    "market_cap",
    "dividend_yield",
    "roe",
    "operating_margin",
    "net_margin",
    "revenue_growth_yoy",
    "op_growth_yoy",
    "profit_growth_yoy",
    "eps_growth_yoy",
    "net_cash",  # 清原式ネットキャッシュ絶対額（ADR-079）
    "net_cash_ratio",  # net_cash / market_cap（read-time 導出列・清原式は net_cash_ratio_min≥1）
)
# sort_by に許す列名（外側サブクエリの列）。安全な allowlist。
_SCREEN_SORT_COLS = {
    "per",
    "pbr",
    "market_cap",
    "dividend_yield",
    "roe",
    "operating_margin",
    "net_margin",
    "revenue_growth_yoy",
    "op_growth_yoy",
    "profit_growth_yoy",
    "eps_growth_yoy",
    "net_cash",
    "net_cash_ratio",
    "per_sector_pctile",
    "market_cap_rank",
    "code",
}


def _valuation_inner_subquery():
    """valuation_snapshots × stocks ＋ window ランク列の内側サブクエリ（ADR-031/048）。

    業種内パーセンタイル（per_sector_pctile・per 昇順＝安いほど低い）と時価総額順位
    （market_cap_rank・降順 1 位が最大）を ~4000 行に対して都度算出する。screen_stocks（一覧）
    と get_valuation_snapshot（単票）が同じランクを共有するための単一の真実。
    """
    v = valuation_snapshots
    s = stocks
    # percent_rank は SQLAlchemy が Numeric(asdecimal=True) と解釈し Decimal を返す。handler→
    # LLM/MCP 境界で JSON 化されて 500 になるため Float 化して素の float で返す（ADR-014・
    # [[backend-repo-pattern]] / [[advisor-tool-pattern]]）。
    per_sector_pctile = type_coerce(
        func.percent_rank().over(partition_by=s.c.sector33_code, order_by=v.c.per),
        Float(),
    ).label("per_sector_pctile")
    market_cap_rank = (
        func.row_number().over(order_by=v.c.market_cap.desc()).label("market_cap_rank")
    )
    # 清原式ネットキャッシュ比率は read-time 導出（ADR-079）。net_cash は BS 由来で四半期ごとにしか
    # 動かないが market_cap は日次で動くので、比率は物理列に焼かず最新 market_cap と都度割る
    # （per_sector_pctile/market_cap_rank と同じ read-time 方式・鮮度は market_cap 側に従う）。
    # SQLite は market_cap が 0/NULL のとき除算結果を NULL にする＝quant のガード（None）と一致。
    # JSON 境界での Decimal 化を避けるため Float 化する（ADR-014・per_sector_pctile と同型）。
    net_cash_ratio = type_coerce(v.c.net_cash / v.c.market_cap, Float()).label("net_cash_ratio")
    return (
        select(
            v.c.code,
            s.c.company_name,
            s.c.sector33_code,
            s.c.market_code,
            s.c.is_etf,
            v.c.as_of_date,
            v.c.close,
            v.c.eps,
            v.c.bps,
            v.c.dividend_per_share,
            v.c.per,
            v.c.pbr,
            v.c.market_cap,
            v.c.dividend_yield,
            v.c.roe,
            v.c.operating_margin,
            v.c.net_margin,
            v.c.revenue_growth_yoy,
            v.c.op_growth_yoy,
            v.c.profit_growth_yoy,
            v.c.eps_growth_yoy,
            v.c.op_forecast_achievement,
            v.c.profit_forecast_achievement,
            v.c.op_forecast_revision,
            v.c.profit_forecast_revision,
            v.c.receivables_turnover_days,
            v.c.inventory_turnover_days,
            v.c.receivables_growth_yoy,
            v.c.inventory_growth_yoy,
            v.c.net_cash,
            net_cash_ratio,
            per_sector_pctile,
            market_cap_rank,
        )
        .select_from(v.join(s, v.c.code == s.c.code))
        .subquery()
    )


def get_valuation_snapshot(conn: Connection, code: str) -> dict[str, Any] | None:
    """指定銘柄のバリュエーション事実（PER/PBR/ROE/利益率/成長率＋業種内ランク）を返す（ADR-048）。

    screen_stocks と同じ window ランクを共有し、code で 1 行に絞る。未焼成・未上場なら None。
    数値は夜間 calc_valuation が焼いた事実で、verdict（割安/割高の判定）は持たない（ADR-014）。
    """
    inner = _valuation_inner_subquery()
    row = conn.execute(select(inner).where(inner.c.code == code)).mappings().first()
    return dict(row) if row else None


def get_market_caps_by_code(conn: Connection) -> dict[str, float]:
    """全銘柄の最新時価総額を {code: market_cap} で返す（stealth_accum のフロア用・ADR-074）。

    valuation_snapshots は code 1 行（最新のみ・ADR-031）。market_cap が NULL の行は除く。
    calc_signals がループ前に 1 回引き、銘柄ごとの N クエリを避ける（下ごしらえ＝ADR-016）。
    """
    v = valuation_snapshots
    stmt = select(v.c.code, v.c.market_cap).where(v.c.market_cap.is_not(None))
    return {r["code"]: float(r["market_cap"]) for r in conn.execute(stmt).mappings().all()}


def screen_stocks(conn: Connection, criteria: dict[str, Any]) -> list[dict[str, Any]]:
    """valuation_snapshots × stocks を絞り込み・整列して返す（読み取り時計算・ADR-026/031/048）。

    業種内パーセンタイル（per_sector_pctile）と時価総額順位（market_cap_rank）は ~4000 行への
    window 関数で都度算出する。criteria は薄い辞書（router/Tool が Pydantic から作る）:
      q（コード/銘柄名の部分一致）、
      {field}_min/{field}_max（per/pbr/market_cap/dividend_yield/roe/operating_margin/net_margin/
      *_growth_yoy の絶対レンジ）、sector33_code・market_code（完全一致）、exclude_etf(bool)、
      per_sector_pctile_max（業種内で安い割合・0..1）、market_cap_rank_max（時価総額 上位 N）、
      sort_by・sort_dir('asc'|'desc')・limit・offset。
    戻り値は素 dict（company_name/sector33_code/market_code/is_etf を stocks から JOIN 補完）。
    """
    inner = _valuation_inner_subquery()

    conds = []
    # コード/銘柄名の部分一致（list_stocks と同じ LIKE OR・db/repo/stocks.py）
    if criteria.get("q"):
        like = f"%{criteria['q']}%"
        conds.append(inner.c.code.like(like) | inner.c.company_name.like(like))
    # 絶対レンジ（min/max）
    for field in _SCREEN_RANGE_FIELDS:
        col = inner.c[field]
        lo = criteria.get(f"{field}_min")
        hi = criteria.get(f"{field}_max")
        if lo is not None:
            conds.append(col >= lo)
        if hi is not None:
            conds.append(col <= hi)
    # 完全一致・ETF 除外
    if criteria.get("sector33_code"):
        conds.append(inner.c.sector33_code == criteria["sector33_code"])
    if criteria.get("market_code"):
        conds.append(inner.c.market_code == criteria["market_code"])
    if criteria.get("exclude_etf"):
        conds.append(inner.c.is_etf == 0)
    # ランク系（業種内で安い割合・時価総額 上位 N）
    if criteria.get("per_sector_pctile_max") is not None:
        conds.append(inner.c.per_sector_pctile <= criteria["per_sector_pctile_max"])
    if criteria.get("market_cap_rank_max") is not None:
        conds.append(inner.c.market_cap_rank <= criteria["market_cap_rank_max"])

    stmt = select(inner)
    if conds:
        stmt = stmt.where(and_(*conds))

    # 整列（allowlist・既定は時価総額降順）
    sort_by = criteria.get("sort_by") or "market_cap"
    if sort_by not in _SCREEN_SORT_COLS:
        sort_by = "market_cap"
    sort_col = inner.c[sort_by]
    stmt = stmt.order_by(sort_col.asc() if criteria.get("sort_dir") == "asc" else sort_col.desc())

    limit = int(criteria.get("limit") or 200)
    limit = max(1, min(limit, 1000))  # 暴走防止の上限
    stmt = stmt.limit(limit)
    if criteria.get("offset"):
        stmt = stmt.offset(int(criteria["offset"]))

    return [dict(r) for r in conn.execute(stmt).mappings().all()]


# --- 保存スクリーニング条件（screening_filters・CRUD・ADR-001/031） ---


def list_screening_filters(conn: Connection) -> list[dict[str, Any]]:
    """保存フィルタを更新日時降順で返す（criteria_json は生の文字列・パースは router）。"""
    stmt = select(screening_filters).order_by(screening_filters.c.updated_at.desc())
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_screening_filter(conn: Connection, filter_id: int) -> dict[str, Any] | None:
    row = (
        conn.execute(select(screening_filters).where(screening_filters.c.id == filter_id))
        .mappings()
        .first()
    )
    return dict(row) if row else None


def insert_screening_filter(name: str, criteria_json: str) -> int:
    """保存フィルタを 1 件作成し id を返す（W1・単発・自前 begin）。"""
    now = datetime.now(UTC).isoformat()
    with get_engine().begin() as conn:
        result = conn.execute(
            screening_filters.insert().values(
                name=name, criteria_json=criteria_json, created_at=now, updated_at=now
            )
        )
    # inserted_primary_key は型上 Optional（pyright reportOptionalSubscript）。
    # SQLite は INSERT 後に主キーを返すので None ガードしてから添字する。
    pk = result.inserted_primary_key
    if pk is None:
        raise RuntimeError("screening_filters の INSERT で主キーを取得できませんでした。")
    return int(pk[0])


def update_screening_filter(filter_id: int, name: str, criteria_json: str) -> int:
    """保存フィルタを更新（W1）。更新行数を返す（0 なら未存在）。"""
    now = datetime.now(UTC).isoformat()
    with get_engine().begin() as conn:
        result = conn.execute(
            screening_filters.update()
            .where(screening_filters.c.id == filter_id)
            .values(name=name, criteria_json=criteria_json, updated_at=now)
        )
    return int(result.rowcount)


def delete_screening_filter(filter_id: int) -> int:
    """保存フィルタを削除（W1）。削除行数を返す。"""
    with get_engine().begin() as conn:
        result = conn.execute(screening_filters.delete().where(screening_filters.c.id == filter_id))
    return int(result.rowcount)


def upsert_asset_snapshots(rows: list[dict[str, Any]]) -> int:
    """asset_snapshots を冪等 UPSERT する（phase2-spec.md §3.3・ADR-002）。

    衝突キー: date（1 日 1 行）。snapshot_assets ジョブが計算済み行を渡す。
    """
    return _upsert(asset_snapshots, rows, index_elements=["date"])


def get_index_quotes(
    conn: Connection,
    symbol: str,
    from_: str | None = None,
    to: str | None = None,
) -> list[dict[str, Any]]:
    """指定シンボルの index_quotes を date 昇順で返す（backtest のベンチ用・spec §4.4）。"""
    stmt = select(index_quotes).where(index_quotes.c.symbol == symbol)
    if from_:
        stmt = stmt.where(index_quotes.c.date >= from_)
    if to:
        stmt = stmt.where(index_quotes.c.date <= to)
    stmt = stmt.order_by(index_quotes.c.date)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def get_asset_snapshots(
    conn: Connection,
    limit: int = 365,
) -> list[dict[str, Any]]:
    """asset_snapshots を date 昇順で返す（資産推移トレンド用・spec §5 P2-7）。

    `limit` は最新 N 日分（既定 365 日）。スパークライン用途なので古い日付側を切り捨てる。
    """
    # date 降順で limit 行取ってから date 昇順に並べ直す（最新 N 日を昇順表示）。
    subq = select(asset_snapshots).order_by(asset_snapshots.c.date.desc()).limit(limit).subquery()
    stmt = select(subq).order_by(subq.c.date)
    return [dict(r) for r in conn.execute(stmt).mappings().all()]


def list_holding_codes(conn: Connection, portfolio_id: int) -> list[str]:
    """holdings の code 一覧を返す（fetch_financials の対象銘柄特定用・spec §3.2）。"""
    rows = (
        conn.execute(
            select(holdings.c.code)
            .where(holdings.c.portfolio_id == portfolio_id)
            .order_by(holdings.c.code)
        )
        .scalars()
        .all()
    )
    return list(rows)


def list_all_holding_codes(conn: Connection) -> list[str]:
    """全ポートフォリオの保有 code を重複なく返す（#2 売掛/在庫の質の対象解決・ADR-064）。

    portfolio_id を跨いで distinct。watchlist と合わせて edinetdb.jp 取得対象を絞る（
    レート予算節約）。
    """
    rows = (
        conn.execute(select(holdings.c.code).distinct().order_by(holdings.c.code)).scalars().all()
    )
    return list(rows)


# 財務の質の更新対象列（ADR-064 #2＋ADR-079）。calc_valuation が焼いた行を後段ジョブが cadence で
# UPDATE する。net_cash（清原式・ADR-079）も同じ edinetdb.jp/yfinance 経路で焼くため相乗りさせる。
# net_cash_ratio は物理列でなく read-time 導出なのでここには入れない（ADR-079）。
_RECV_INV_COLUMNS = (
    "receivables_turnover_days",
    "inventory_turnover_days",
    "receivables_growth_yoy",
    "inventory_growth_yoy",
    "net_cash",
)


def update_valuation_receivables_inventory(
    conn: Connection, code: str, quality: dict[str, Any]
) -> int:
    """valuation_snapshots の既存 1 行に売掛/在庫の質列だけ UPDATE する（W2・ADR-064 #2）。

    calc_valuation が as_of_date/価格込みで焼いた行が前提（
    NIGHTLY 順で calc_valuation の後に回す）。
    存在しない code（価格なし＝行なし）は UPDATE 0 件で安全に no-op。quality は _RECV_INV_COLUMNS の
    キーのみ採用（fin_disclosed_date/updated_at は calc_valuation の値を尊重して触らない）。返り値は
    更新行数（0 or 1）。呼び出し側（夜間ジョブ）が begin() 境界を所有する。
    """
    values = {k: quality.get(k) for k in _RECV_INV_COLUMNS}
    res = conn.execute(
        update(valuation_snapshots).where(valuation_snapshots.c.code == code).values(**values)
    )
    return res.rowcount or 0
