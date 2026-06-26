"""夜間バッチのジョブ群（spec §3.3）。

`NIGHTLY_JOBS` が**実行順の単一の真実**。後続 Phase はここに append する。
順序の意図:
  マスタ → 日足取得 → 指数取得 → 財務取得 → バリュエーション計算 → シグナル計算
  （当日の事実が揃ってから算出）→ 資産スナップショット（今日の株価が確定してから評価額を焼く）。
Phase 2 で fetch_index / fetch_financials を calc_signals の前に挿入（phase2-spec.md §3）。
Phase 2 で snapshot_assets を末尾に追加（phase2-spec.md §3.3・app レーン担当）。
ADR-031 で calc_valuation を fetch_financials の後・calc_signals の前に挿入（screen の土台）。
"""

from __future__ import annotations

from app.batch.jobs import (
    calc_lead_lag,
    calc_signals,
    calc_us_valuation,
    calc_valuation,
    embed_cards,
    embed_news,
    embed_themes,
    fetch_edinet_descriptions,
    fetch_financials,
    fetch_fund_navs,
    fetch_fx_rates,
    fetch_general_news,
    fetch_index,
    fetch_quotes,
    fetch_sector_news,
    fetch_us_fundamentals,
    fetch_us_quotes,
    investigate_dossier,
    notify_cost_warn,
    notify_digest,
    run_advisor,
    score_ai_alpha,
    snapshot_assets,
    sync_master,
    sync_us_universe,
    tag_jp_themes,
    tag_news_polarity,
    tag_us_themes,
)

NIGHTLY_JOBS = [
    sync_master.run,
    fetch_quotes.run,
    fetch_index.run,
    fetch_financials.run,
    calc_valuation.run,  # ADR-031: 財務取得後に全銘柄のバリュエーションを焼く（screen の土台）
    calc_signals.run,
    # Phase 7: 日米業種リードラグを焼く。calc_signals の後・run_advisor の前に置き、夜の分析AI が
    # 当日の lead_lag を読めるようにする（SIG-FIN-036-13・US/JP 業種 ETF が揃ってから算出）。
    calc_lead_lag.run,
    # Phase 5: 学習済みモデルで ai_alpha を焼く。calc_signals の後・run_advisor の前に置き、
    # 夜の分析AI が当日の ai_alpha を読めるようにする（phase5-spec.md §4.4）。
    score_ai_alpha.run,
    # ADR-054: 投信 NAV（基準価額）を取得。snapshot_assets が当日 NAV から fund_value を焼くため、
    # その前に置き NAV を揃える（fetch_index と同様の取得→評価の順序）。
    fetch_fund_navs.run,
    # ADR-057: FX レート（USDJPY）を取得。snapshot_assets が当夜の FX で us_stock_value を
    # JPY 換算するため、その前に置き FX を揃える（fetch_fund_navs が NAV を揃えるのと同じ意図）。
    # ※ プランの「米株ブロック先頭」配置ではなく snapshot_assets の直前に置くことで、当夜の
    #   FX レートが同夜の snapshot に確実に反映される（米株ブロックは snapshot_assets の後に
    #   あるため、米株ブロック先頭に置くと当夜 FX が snapshot に間に合わない）。
    fetch_fx_rates.run,
    snapshot_assets.run,  # Phase 2: 今日の株価確定後に評価額を焼く（phase2-spec.md §3.3）
    # Phase 7(B-1) 米株ブロック・日本株フローと独立（ADR-031/039）。米市場のユニバース/OHLCV/
    # fundamentals/valuation を 1 ブロックで回す（提示専用・JPY コア無改変）。順序は日本株フロー
    # （マスタ→価格→財務→valuation）をミラー: ユニバース同期 → OHLCV 取得 → 財務ローテ巡回 →
    # その後に valuation を焼く（業種/財務/価格が揃ってから・ADR-031）。各ジョブ部分失敗は握って
    # 後続継続（ADR-018）。
    # ※ fetch_fx_rates と違い米株 quotes は snapshot_assets の「後」（前に移さない）。理由:
    #   02:00 JST は米国ザラ場中（22:30〜05:00 JST・C-2）で当夜 quotes は部分足になり得る。
    #   よって snapshot は米株を「前夜の確定 close」で評価し、当夜の部分足を確定値にしない。
    #   C-1 の overlap 再取得が翌晩に確定足で UPSERT 上書き＝評価額は翌晩に自己修復する。
    #   FX は単一レートで部分足問題が無く snapshot 直前に置ける（上の fetch_fx_rates）。
    sync_us_universe.run,
    fetch_us_quotes.run,
    fetch_us_fundamentals.run,
    calc_us_valuation.run,
    # ADR-050 段階A: US テーマの grounded タグ付け。fetch_us_fundamentals が company_descriptions
    # を更新した直後・run_advisor の前に置き、夜の分析AI が当夜のタグを Tool で読めるようにする。
    tag_us_themes.run,
    # ADR-034: 夜の分析AI の市況文脈材料として run_advisor の直前で一般ニュースを取得・保存。
    fetch_general_news.run,
    # ADR-044: 統合コーパスのセクター層を埋める。一般ニュースの直後・run_advisor の前に置き、
    # 夜の分析AI が当日の (ii) セクター文脈も読めるようにする（fetch_general_news と同型）。
    fetch_sector_news.run,
    run_advisor.run,  # Phase 3: 事実が揃ってから夜の分析AI を回す（phase3-spec.md §5）
    investigate_dossier.run,  # Phase 4: watchlist を古い順に巡回しドシエ調査（phase4-spec.md §6）
    # ADR-056 段階C: EDINET 有報「事業の内容」を JP 全ユニバースへ取り込む（提出日クロール差分）。
    # investigate_dossier（dossier を書く）の直後・tag_jp_themes（説明を食う）の直前に置き、JP の
    # description 書き手を dossier→edinet の優先順に並べる（既存 dossier は事前 skip で残す）。
    # 当夜埋めた company_descriptions(JP,'edinet') を直後の tag_jp_themes が source 不問で拾う。
    fetch_edinet_descriptions.run,
    # ADR-050 段階B/C: JP テーマの grounded タグ付け。investigate_dossier（dossier）＋
    # fetch_edinet_descriptions（edinet）が company_descriptions(JP) を更新した直後に置き、当夜
    # 書かれた説明変化を同じ夜にタグ付けまで反映する（US と非対称＝JP の信号源は後段で生まれる）。
    # embed_themes より前に置き、当夜の新テーマ語彙が後続の埋め込み reconcile に乗る順序を保つ。
    tag_jp_themes.run,
    # ADR-045: 全ニュース書込後に embedding が null/モデル不一致の行を埋める（意味検索の素地）。
    # investigate_dossier の後・通知系の前に置き、当夜貯めた要約まで含めて意味検索に乗せる。
    embed_news.run,
    # ADR-049/051: stock 層ニュースに定性 polarity を付ける（embed_news 同型）。investigate_dossier
    # （stock 層 news を書く）の後・notify_digest の前に置き、当夜取り込んだ stock 層ニュースに
    # polarity がついてから digest の②保有銘柄悪材料アラートが polarity='negative' を拾えるように
    # する（embedding とは独立なので embed_news の隣でまとめて回す）。
    tag_news_polarity.run,
    # ADR-050: tag_us_themes/tag_jp_themes が当夜増やしたテーマ語彙を埋め込み near_duplicate_of を
    # 判定する（embed_news の直後＝embedding 系をまとめて回す・語彙 reconcile の第二段）。
    embed_themes.run,
    # ADR-062: 知識カードの when_to_apply を埋め込む（embedding 系をまとめて回す）。
    # UI 追加時の即時埋め込み（best-effort）の取りこぼしを夜間で拾う（フェーズ2 retrieval の素地）。
    embed_cards.run,
    # ADR-028: warn 超過時、その月最初の夜に 1 通だけ警告（通知系を digest と並べる）。
    notify_cost_warn.run,
    notify_digest.run,  # Phase 6: ⑦⑧＋夜AI 提案を 1 通の Discord digest に束ねる（phase6-spec §3）
]
