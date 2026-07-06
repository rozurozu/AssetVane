"""tool_runs → advisor_journal/proposals の橋渡し共通サービス（ADR-018/029/013）。

設計の真実: docs/phase-specs/phase3-spec.md §5・§6.5・ADR-029。

軸1（夜の分析AI＝nightly）と軸2（昼の相談チャット＝router の /chat）の両方が、Tool ループの
tool_runs から `submit_journal` の引数を拾い、advisor_journal を 1 件記録し、必要なら方針変更
proposal を起票する。この「橋渡し」を 1 か所に集約して真実の所在地を一本化する（同じ事実は
1 か所で計算して複数の出力先に配る＝backend-service-quant-pattern）。

接続規約（W2・backend-repo-pattern）: 本サービスは `conn` を受け取り insert を実行するだけで、
自分では commit しない。呼び出し側（nightly ジョブ／router）が `with get_engine().begin()` で
トランザクション境界を所有し、journal＋proposal を 1 トランザクションに atomic に束ねる。

依存方向: 本モジュールは router を import しない（循環回避・片方向依存）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Connection

from app.advisor.tools.schemas import (
    AdjustCardWeightArgs,
    NotablePickArg,
    ProposeCardArgs,
    ProposeProfileNoteArgs,
    ProposeTradeArgs,
    WatchlistCandidateArg,
    coerce_policy_change,
)
from app.db import repo
from app.services.policy import normalize_policy_row

logger = logging.getLogger(__name__)


def _extract_submit_journal(
    tool_runs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """tool_runs から submit_journal の args を取り出す（最後の呼び出しを採用・spec §5）。

    複数回呼ばれた場合は最後の args を採用する（最終意思を尊重）。submit_journal が一度も
    呼ばれていなければ None を返す（呼び出し側は reply フォールバックに倒す＝ADR-018）。
    """
    submitted: dict[str, Any] | None = None
    for run in tool_runs:
        if run.get("name") == "submit_journal":
            args = run.get("args")
            if isinstance(args, dict):
                submitted = args
    return submitted


def persist_journal_from_tool_runs(
    conn: Connection,
    *,
    tool_runs: list[dict[str, Any]],
    reply: str | None,
    source: str,
    date: str,
    situation_briefing: str | None,
    policy: dict[str, Any] | None,
    llm_model: str | None,
) -> int | None:
    """tool_runs から submit_journal を拾い journal 1件記録・proposal 起票（ADR-018/029/013）。

    戻り値: journal_id（記録）/ None（observations 空でスキップ＝ADR-018）。

    手順（nightly の既存橋渡しロジックを移植・一本化）:
    1. tool_runs から submit_journal の最終 args を拾う。
    2. observations/proposal/proposed_policy_change を組む（submit が無くても reply 非空なら
       observations=reply にフォールバック＝既存挙動を保つ・ADR-018）。
    3. observations が空（縮退）なら journal を書かず None を返す（不変条件・ADR-018）。
    4. coerce_policy_change で単一 {field,to} に正規化（多列 patch・非 dict は None に倒し
       適用不能な提案を起票しない＝ADR-013/018）。
    5. insert_journal（date/source/situation_briefing/policy_snapshot を JSON 文字列で）。
       policy_snapshot は dumps 前に normalize_policy_row で JSON 列を型へ直す＝
       「snapshot は単エンコード（JSON 文字列の入れ子を作らない）」の不変条件を
       本書き込み境界が所有する（nightly の repo 生行・chat の正規化済みの両方を受ける）。
    6. proposed_change があれば insert_proposal（kind=policy_change・pending・journal_id 紐付け）。

    接続規約（W2）: commit はしない。呼び出し側が `with get_engine().begin()` で境界を所有する。
    """
    submitted = _extract_submit_journal(tool_runs)
    if submitted is not None:
        observations = str(submitted.get("observations") or reply or "")
        proposal = submitted.get("proposal")
        raw_change = submitted.get("proposed_policy_change")
    else:
        # submit_journal 不呼び出しでも reply 非空なら正常（フォールバックを否定しない・ADR-018）。
        observations = reply or ""
        proposal = None
        raw_change = None

    # 縮退（例外なし・observations 空＝実質何も書くことがない）は journal を残さない（ADR-018）。
    if not observations.strip():
        return None

    # 変更案を単一 {field,to} に正規化（多列 patch 等は None＝適用不能な提案を起票しない）。
    # 正規化済み dict は apply_policy_change がそのまま食える形（ADR-013/018・U-10 裁定①）。
    proposed_change = coerce_policy_change(raw_change)
    if raw_change is not None and proposed_change is None:
        logger.warning(
            "journaling: proposed_policy_change が単一 {field,to} 形でない。"
            "提案は起票せず journal のみ記録する（ADR-013/018）。"
        )

    proposed_change_json = (
        json.dumps(proposed_change, ensure_ascii=False) if proposed_change else None
    )

    journal_id = repo.insert_journal(
        conn,
        date=date,
        source=source,
        situation_briefing=situation_briefing,
        observations=observations,
        proposal=proposal if isinstance(proposal, str) else None,
        proposed_policy_change=proposed_change_json,
        policy_snapshot=(
            json.dumps(normalize_policy_row(policy), ensure_ascii=False)
            if policy is not None
            else None
        ),
        llm_model=llm_model,
    )

    # 方針変更案があれば承認制の提案として起票する（kind=policy_change・pending・§6.5）。
    # proposed_change は正規化済み（None なら起票せず＝適用不能な提案を回避）。
    if proposed_change:
        reason = proposed_change.get("reason")
        repo.insert_proposal(
            conn,
            created_date=date,
            kind="policy_change",
            body=proposed_change_json,
            rationale=str(reason) if reason else None,
            status="pending",
            journal_id=journal_id,
        )

    return journal_id


def resolve_trade_target(conn: Connection, code: str) -> dict[str, str] | None:
    """売買提案の銘柄 code を解決する（ADR-052）。JP→US の順で引き当てる。

    `stocks`（JP 5 桁）→ `us_stocks`（US ティッカー）の順で探し、見つかれば
    `{"company_name", "market"}` を返す。どちらにも無ければ None（未知＝幻覚/誤記の疑い）。
    company_name が NULL の行でも market は確定できるので company_name="" で返す。
    """
    jp = repo.get_stock(conn, code)
    if jp is not None:
        return {"company_name": str(jp.get("company_name") or ""), "market": "JP"}
    us = repo.get_us_stock(conn, code)
    if us is not None:
        return {"company_name": str(us.get("company_name") or ""), "market": "US"}
    return None


def _extract_trade_proposals(tool_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """tool_runs から propose_trade の args を全件抽出する（ADR-052・複数可）。

    submit_journal が「最後の 1 回」を採るのと違い、売買提案は 1 ターンに複数あり得るので
    全件拾う（買い A・売り B が同じ晩に出ても両方起票する）。args が dict でないものは捨てる。
    """
    out: list[dict[str, Any]] = []
    for run in tool_runs:
        if run.get("name") == "propose_trade":
            args = run.get("args")
            if isinstance(args, dict):
                out.append(args)
    return out


# 確信度の canonical 集合と別名（日本語 高/中/低・大小文字・mid 揺れを吸収＝ADR-084）。
_CONVICTION_ALIASES: dict[str, str] = {
    "high": "high",
    "medium": "medium",
    "mid": "medium",
    "low": "low",
    "高": "high",
    "中": "medium",
    "低": "low",
}


def _normalize_conviction(raw: object) -> str | None:
    """確信度の自由入力を canonical 'high'/'medium'/'low' に正規化する（ADR-084）。

    CORE 要素⑤は AI に「高/中/低」で述べさせるが、Tool 引数の揺れ（日本語・大文字・mid 等）を
    吸収して canonical に寄せ、未知/空は None に倒す（メタ不備で提案を drop しない＝ADR-018）。
    """
    if not isinstance(raw, str):
        return None
    return _CONVICTION_ALIASES.get(raw.strip().lower())


def persist_trade_proposals_from_tool_runs(
    conn: Connection,
    *,
    tool_runs: list[dict[str, Any]],
    date: str,
    journal_id: int | None = None,
) -> list[int]:
    """tool_runs から propose_trade を拾い buy/sell 提案を承認制で起票する（ADR-052）。

    戻り値: 起票した proposals の id 一覧（drop/dedup された分は含まない）。

    手順:
    1. propose_trade の args を全件抽出（複数可）。
    2. action/code/reason を検証（ProposeTradeArgs）。不正な args はスキップ。
    3. resolve_trade_target で JP→US 解決。未知コードは起票せず warning（ADR-018＝queue に
       幻覚/誤記を入れない）。body に company_name/market を焼く。
    4. pending dedup（同一 (kind, code) の pending があればスキップ）。
    5. insert_proposal（kind=action・body=JSON・rationale=reason・depends_on=None）。

    接続規約（W2）: commit はしない。呼び出し側（nightly ジョブ／router）が begin() 境界を所有し、
    journal＋proposal を 1 トランザクションに atomic に束ねる。
    """
    inserted: list[int] = []
    for raw in _extract_trade_proposals(tool_runs):
        try:
            args = ProposeTradeArgs.model_validate(raw)
        except ValidationError:
            logger.warning("propose_trade: 引数が不正（%s）。起票せずスキップ", raw)
            continue

        target = resolve_trade_target(conn, args.code)
        if target is None:
            # 未知コード（幻覚/誤記の疑い）は queue に入れない（ADR-018）。
            logger.warning(
                "propose_trade: 銘柄 %s が stocks/us_stocks に無い。起票せずスキップ", args.code
            )
            continue

        if repo.pending_trade_proposal_exists(conn, args.action, args.code):
            logger.info(
                "propose_trade: %s %s は既に pending。重複起票をスキップ", args.action, args.code
            )
            continue

        # body は kind 依存 JSON（buy/sell）。code/company_name/market に加え、判断属性（ADR-084）を
        # 揃ったものだけ載せる（無い提案は従来どおり最小の 3 キー＝後方互換）。conviction は正規化。
        payload: dict[str, Any] = {
            "code": args.code,
            "company_name": target["company_name"],
            "market": target["market"],
        }
        conviction = _normalize_conviction(args.conviction)
        if conviction is not None:
            payload["conviction"] = conviction
        if args.invalidation and args.invalidation.strip():
            payload["invalidation"] = args.invalidation.strip()
        if args.catalyst and args.catalyst.strip():
            payload["catalyst"] = args.catalyst.strip()
        body = json.dumps(payload, ensure_ascii=False)
        proposal_id = repo.insert_proposal(
            conn,
            created_date=date,
            kind=args.action,
            body=body,
            rationale=args.reason,
            status="pending",
            journal_id=journal_id,
            depends_on=None,
        )
        inserted.append(proposal_id)

    return inserted


def persist_profile_notes_from_tool_runs(
    conn: Connection,
    *,
    tool_runs: list[dict[str, Any]],
    date: str,
) -> list[int]:
    """tool_runs から propose_profile_note を拾い傾向メモを承認制で起票する（ADR-082・W2）。

    戻り値: 起票した proposals（kind='profile_note'）の id 一覧（dedup された分は含まない）。

    手順（persist_trade_proposals_from_tool_runs と同型）:
    1. propose_profile_note の args を全件抽出（1 晩に複数可）。
    2. text/evidence を検証（ProposeProfileNoteArgs）。不正・空 text はスキップ。
    3. pending dedup（同一 text の pending があればスキップ＝毎晩の氾濫を抑える）。
    4. insert_proposal（kind='profile_note'・body=JSON{text,evidence}・rationale=evidence）。

    承認すると resolve_proposal→apply_profile_note が投資家プロファイル本文へ追記する（人間承認で
    のみ active 文書が育つ＝ADR-009）。接続規約（W2）: commit しない＝呼び出し側 job が begin 所有。
    """
    inserted: list[int] = []
    for raw in _extract_args(tool_runs, "propose_profile_note"):
        try:
            args = ProposeProfileNoteArgs.model_validate(raw)
        except ValidationError:
            logger.warning("propose_profile_note: 引数が不正（%s）。起票せずスキップ", raw)
            continue
        text = args.text.strip()
        if not text:
            continue
        if repo.pending_profile_note_exists(conn, text):
            logger.info("propose_profile_note: 同一 text が既に pending。重複起票をスキップ")
            continue
        body = json.dumps({"text": text, "evidence": args.evidence}, ensure_ascii=False)
        proposal_id = repo.insert_proposal(
            conn,
            created_date=date,
            kind="profile_note",
            body=body,
            rationale=args.evidence,
            status="pending",
        )
        inserted.append(proposal_id)

    return inserted


def _extract_notable_picks(tool_runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """tool_runs から submit_notable_stocks の args を取り出す（最後の呼び出しを採用・ADR-067）。

    submit_journal と同じく「最後の 1 回」を最終意思として採る。一度も呼ばれていなければ None。
    """
    submitted: dict[str, Any] | None = None
    for run in tool_runs:
        if run.get("name") == "submit_notable_stocks":
            args = run.get("args")
            if isinstance(args, dict):
                submitted = args
    return submitted


def persist_notable_picks_from_tool_runs(
    conn: Connection,
    *,
    tool_runs: list[dict[str, Any]],
    date: str,
    source: str = "nightly",
) -> list[str]:
    """tool_runs から submit_notable_stocks を拾い notable_picks を永続する（ADR-067・W2）。

    戻り値: 永続した銘柄 code の一覧（未知/重複で drop した分は含まない）。

    手順（persist_trade_proposals_from_tool_runs と同型）:
    1. submit_notable_stocks の最終 args を拾う（無ければ何もしない＝[]）。
    2. picks を **1 件ずつ** NotablePickArg で検証（不備の pick だけ warning でスキップ・#11）。
       全件一括 model_validate だと 1 件の不備で有効分まで全落ちし、ADR-067 が解消した
       「注目＝AI 提案なし（空 digest）」を再誘発する。coerce_policy_change / propose_trade と
       同じ per-item グレースフル方針（ADR-018「ループを落とさない」）に揃える。
    3. 各 pick の JP コードを stocks で解決。未知は drop（幻覚/誤記を digest に載せない）。
    4. 同一 code は 1 度だけ（dedup）。upsert_notable_pick で冪等 UPSERT（再実行で重複しない）。

    接続規約（W2）: commit はしない。呼び出し側（nightly ジョブ）が begin() 境界を所有し、
    journal/proposal と 1 トランザクションに束ねる。source は 'nightly'（digest が読む）。
    """
    raw = _extract_notable_picks(tool_runs)
    if raw is None:
        return []
    raw_picks = raw.get("picks") if isinstance(raw, dict) else None
    if not isinstance(raw_picks, list):
        logger.warning("submit_notable_stocks: picks が配列でない（%s）。永続せずスキップ", raw)
        return []

    inserted: list[str] = []
    seen: set[str] = set()
    for raw_pick in raw_picks:
        try:
            pick = NotablePickArg.model_validate(raw_pick)
        except ValidationError:
            # 1 件の不備（reason 欠落・非 dict 等）はこの pick だけ落とし、有効分は残す（#11）。
            logger.warning("notable pick: 1 件が不正（%s）。この pick だけスキップ", raw_pick)
            continue
        code = pick.code
        if code in seen:
            continue
        if repo.get_stock(conn, code) is None:
            # 未知コード（幻覚/誤記の疑い）は digest に載せない（ADR-014/018）。
            logger.warning("notable pick: 銘柄 %s が stocks に無い。永続せずスキップ", code)
            continue
        seen.add(code)
        repo.upsert_notable_pick(conn, date=date, code=code, reason=pick.reason, source=source)
        inserted.append(code)
    return inserted


def build_watchlist_candidates_from_tool_runs(
    conn: Connection, *, tool_runs: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """tool_runs から propose_watchlist を拾い UI 用ウォッチ候補を組む（ADR-080・読み取り専用）。

    戻り値: [{code, company_name, reason}] の配列（未知/重複/不正で落ちた分は含まない）。

    persist_* 族と違い **何も永続しない**＝候補を UI に載せる surfacing は昼 router だけに配線し、
    夜 nightly が propose_watchlist を呼んでもこの関数を通らず no-op になる（ADR-080）。手順は
    persist_notable_picks と同型:
    1. propose_watchlist の args から candidates を全件集める（1 ターンに複数呼び出し可）。
    2. 各候補を WatchlistCandidateArg で検証（不備の 1 件だけ skip・per-item グレースフル）。
    3. code を stocks で解決（JP のみ）。未知は drop（幻覚/US を候補に載せない・ADR-018）。
    4. 同一 code は初出のみ（dedup）。reason はそのまま持ち回る（追加時 note に焼く元）。

    接続規約: 読み取り専用（get_stock のみ）。呼び出し側（router）が connect() 境界を所有する。
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for run in tool_runs:
        if run.get("name") != "propose_watchlist":
            continue
        args = run.get("args")
        raw_candidates = args.get("candidates") if isinstance(args, dict) else None
        if not isinstance(raw_candidates, list):
            continue
        for raw in raw_candidates:
            try:
                cand = WatchlistCandidateArg.model_validate(raw)
            except ValidationError:
                # 1 件の不備（code 欠落・非 dict 等）はこの候補だけ落とし、有効分は残す（ADR-018）。
                logger.warning(
                    "propose_watchlist: 候補 1 件が不正（%s）。この候補だけスキップ", raw
                )
                continue
            code = cand.code.strip()
            if not code or code in seen:
                continue
            seen.add(code)
            stock = repo.get_stock(conn, code)
            if stock is None:
                # 未知コード（幻覚/US）は候補に載せない（ADR-014/018）。watchlist は JP 専用。
                logger.warning("propose_watchlist: 銘柄 %s が stocks に無い。候補から drop", code)
                continue
            out.append(
                {
                    "code": code,
                    "company_name": str(stock.get("company_name") or ""),
                    "reason": cand.reason,
                }
            )
    return out


def _extract_args(tool_runs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """tool_runs から指定 Tool の args（dict）を全件抽出する（ADR-062 追補・複数可）。"""
    return [
        run["args"]
        for run in tool_runs
        if run.get("name") == name and isinstance(run.get("args"), dict)
    ]


def _card_title_or_fallback(title: str | None, body: str) -> str:
    """title 空なら本文先頭で代替する（ADR-062 追補・router の _fallback_title と同型）。"""
    stripped_title = (title or "").strip()
    if stripped_title:
        return stripped_title
    stripped_body = (body or "").strip()
    first = stripped_body.splitlines()[0] if stripped_body else ""
    return first[:40] or "（無題）"


def persist_card_ops_from_tool_runs(
    conn: Connection,
    *,
    tool_runs: list[dict[str, Any]],
    date: str,
    source_override: str | None = None,
) -> dict[str, list[int]]:
    """tool_runs から propose_card / adjust_card_weight を承認制で起票する（ADR-062 追補・W2）。

    - propose_card → 知識カードを draft で起票（人間が /cards で active 化＝ADR-009）。埋め込みは
      夜間 embed_cards が拾う（tx 内で await しない＝C-6）。
    - adjust_card_weight → weight 変更を proposals(kind='card_weight') へ承認制で起票。
      /proposals で承認すると resolve_proposal が body の card_id/weight を反映する。
    commit はしない＝呼び出し側（router/nightly）が begin を所有し journal/proposal と束ねる（W2）。

    source_override を渡すと propose_card の source を **決定論で強制上書き**する（LLM の source
    引数を信用しない・ADR-081）。reviewer 経路が 'reviewer' を渡し /cards で由来を識別できる。
    既定 None は従来どおり tool 引数の source を使う（chat/nightly 不変）。
    戻り値: {"cards": [draft id...], "weight_proposals": [proposal id...]}。
    """
    card_ids: list[int] = []
    for raw in _extract_args(tool_runs, "propose_card"):
        try:
            a = ProposeCardArgs.model_validate(raw)
        except ValidationError:
            logger.warning("propose_card: 引数が不正（%s）。起票せずスキップ", raw)
            continue
        if not a.body.strip():
            continue
        # code 付きは銘柄ノート（ADR-062 追補・⑥）。tool 文脈由来だが幻覚もあり得るので JP→US で実在
        # 検証し、未知 code は起票せず drop（propose_trade と同型・ADR-018＝queue に幻覚を
        # 入れない）。
        # code 付きは level='stock' に確定（推測させない・always_inject は既定 0＝汎用注入に
        # 混ぜない）。
        market: str | None = None
        code: str | None = None
        level = a.level if a.level in ("stock", "sector", "market", "general") else None
        if a.code and a.code.strip():
            target = resolve_trade_target(conn, a.code.strip())
            if target is None:
                logger.warning(
                    "propose_card: 銘柄 %s が stocks/us_stocks に無い。起票せずスキップ", a.code
                )
                continue
            code = a.code.strip()
            market = target["market"]
            level = "stock"
        cid = repo.insert_knowledge_card_tx(
            conn,
            title=_card_title_or_fallback(a.title, a.body),
            body=a.body,
            when_to_apply=a.when_to_apply,
            status="draft",
            level=level,
            market=market,
            code=code,
            source=source_override if source_override is not None else a.source,
        )
        card_ids.append(cid)

    weight_pids: list[int] = []
    for raw in _extract_args(tool_runs, "adjust_card_weight"):
        try:
            a = AdjustCardWeightArgs.model_validate(raw)
        except ValidationError:
            logger.warning("adjust_card_weight: 引数が不正（%s）。起票せずスキップ", raw)
            continue
        if a.weight <= 0:
            continue
        if repo.get_knowledge_card(conn, a.card_id) is None:
            logger.warning("adjust_card_weight: カード %s が無い。起票せずスキップ", a.card_id)
            continue
        pid = repo.insert_proposal(
            conn,
            created_date=date,
            kind="card_weight",
            body=json.dumps({"card_id": a.card_id, "weight": a.weight}, ensure_ascii=False),
            rationale=a.reason,
            status="pending",
        )
        weight_pids.append(pid)

    return {"cards": card_ids, "weight_proposals": weight_pids}
