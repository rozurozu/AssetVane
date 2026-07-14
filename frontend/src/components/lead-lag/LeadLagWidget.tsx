"use client";

// 業種リードラグ widget（Phase 7・GET /lead-lag）。翌日強含み業種の Top N を眺める。
// 内部 GET の明示例外（review-2026-06-12 §3）: frontend-component-pattern は「GET はページが持つ・feature は
// 内部 GET しない」を規約とするが、本 widget は Dashboard 専用の単一データで他ページからは使わず、
// loading/error/empty を StatusBlock で自前表示する自己完結 feature のため、内部 fetch（useApi）を例外として
// 保つ（FundNavSparkline と同じ理由付け）。GeneralNewsWidget は props 渡しだが loading/error を握り潰す形で、
// props 化すると本 widget の三状態表示が後退するため非対称のまま据え置く。三分岐は StatusBlock に畳む。
// 整形は lib/format.ts。数値（score/ic/hit_rate）は backend が事前計算した「事実」を読むだけ（ADR-014）。
// meta.is_delayed=true（plan=free か model_as_of が約 3 ヶ月古い）のとき低信頼バナーを出す。バナーの
// 理由はこの 2 つで別物なので、プラン由来の遅延（/health の jquants＝ADR-061）とモデルの陳腐化を
// 書き分ける（旧実装は「{plan} プランの 12 週間遅延」固定で、Light 契約でモデルが古いときに嘘になった）。
// プラン状態の取得も上記の内部 GET 例外に含める（自己完結 widget）。

import { Card } from "@/components/ui/Card";
import { DataTable, Td } from "@/components/ui/DataTable";
import { StatusBlock } from "@/components/ui/StatusBlock";
import { getLeadLag } from "@/lib/api";
import { fmtRatio, pct } from "@/lib/format";
import { hasPlanDelay, jquantsSourceNote, useJquantsStatus } from "@/lib/jquants";
import { useApi } from "@/lib/use-api";

/** 表示する Top N（翌日強含み業種・score 降順）。 */
const TOP_N = 6;

export function LeadLagWidget() {
  const { data, error, loading } = useApi((signal) => getLeadLag(signal), []);
  const jquants = useJquantsStatus();

  const ranking = data?.ranking ?? [];
  const meta = data?.meta;
  const top = ranking.slice(0, TOP_N);

  // モデル品質の小さな注記（ヘッダ右 meta）。IC / hit_rate は 0..1 想定で % 整形。
  const qualityMeta =
    meta != null
      ? `IC ${pct(meta.ic)} ・ Hit ${pct(meta.hit_rate)}${
          meta.model_as_of ? ` ・ ${meta.model_as_of}` : ""
        }`
      : undefined;

  return (
    <Card title="業種リードラグ（翌日強含み）" meta={qualityMeta}>
      {/* 低信頼バナー（is_delayed=true のときだけ・目立つ位置＝表の前）。 */}
      {meta?.is_delayed && (
        <div className="mb-3 rounded-md border border-warning border-l-2 border-l-warning bg-canvas px-3 py-2 text-[12px] text-warning leading-[1.5]">
          {hasPlanDelay(jquants) ? (
            <>
              {jquantsSourceNote(jquants)}のため、モデル/検証が約 3
              ヶ月古く、翌日予測は実用外なのだ。Light プラン以上を推奨するのだ。
            </>
          ) : (
            <>
              モデル/検証が古い（{meta.model_as_of ?? "算出日不明"}
              ）ため、翌日予測の信頼度が落ちているのだ。夜間バッチの実行を確認するのだ。
            </>
          )}
        </div>
      )}

      <StatusBlock
        loading={loading}
        error={error}
        empty={top.length === 0}
        className="py-4 text-center"
        errorHint="backend が起動しているか確認するのだ。"
        emptyText="まだリードラグの算出がないのだ。夜間バッチで生成されるのだ。"
      >
        <DataTable
          columns={[
            { label: "業種" },
            { label: "スコア", right: true },
            { label: "シグナル", right: true },
          ]}
        >
          {top.map((row) => (
            <tr key={row.code} className="hover:[&>td]:bg-surface-2">
              <Td>
                <span className="text-ink">{row.label}</span>{" "}
                <span className="num text-[11px] text-ink-subtle">{row.code}</span>
              </Td>
              <Td right>
                <span className="inline-flex items-center justify-end gap-2">
                  <span className="h-1 w-12 overflow-hidden rounded-full bg-hairline">
                    <i
                      className="block h-full bg-accent"
                      style={{ width: `${Math.max(0, Math.min(1, row.score)) * 100}%` }}
                    />
                  </span>
                  <span className="num">{row.score.toFixed(2)}</span>
                </span>
              </Td>
              <Td right>
                {/* 縮退時は signal=null（backend の正規応答）。null は中立色で "—" 表示。 */}
                <span
                  className={`num ${
                    row.signal == null
                      ? "text-ink-subtle"
                      : row.signal >= 0
                        ? "text-up"
                        : "text-down"
                  }`}
                >
                  {row.signal != null && row.signal >= 0 ? "+" : ""}
                  {fmtRatio(row.signal, 2)}
                </span>
              </Td>
            </tr>
          ))}
        </DataTable>
      </StatusBlock>
    </Card>
  );
}
