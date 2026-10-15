/**
 * 迁移面板（T17.4）：一个 V1 工作区的迁移流程 UI。
 *
 * 流程即 plan §4 的产品化：dry-run 报告（逐文件归类 + 阻断项）→ 用户确认 →
 * apply 落盘。阻断项逐条列出（错误消息是喂给人的 API——why 原样透出）；报告
 * 未通过时确认按钮禁用。迁移成功展示 V2 项目 ID，由父组件刷新状态。
 */

import { useState } from "react";
import {
  applyMigration,
  dryRunMigration,
  summarizeReport,
  type MigrationApplyResult,
  type MigrationReportWire,
} from "../api/v2migration";
import { Button } from "@/components/ui/button";

export default function MigrationPanel({
  slug,
  onMigrated,
}: {
  slug: string;
  onMigrated: (projectId: string) => void;
}) {
  const [report, setReport] = useState<MigrationReportWire | null>(null);
  const [result, setResult] = useState<MigrationApplyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runDryRun(): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError(null);
    setReport(null);
    setResult(null);
    try {
      setReport(await dryRunMigration(slug));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function confirmApply(): Promise<void> {
    if (busy || report === null || !report.ok) return;
    setBusy(true);
    setError(null);
    try {
      const applied = await applyMigration(slug, report.run_id);
      setResult(applied);
      onMigrated(applied.project_id);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  const summary = report !== null ? summarizeReport(report) : null;

  return (
    <div className="border border-line bg-ink p-4 text-xs leading-relaxed">
      <div className="flex items-center justify-between gap-2">
        <span className="text-gold-soft">迁移到 V2（旧工作区保持只读）</span>
        <Button
          variant="outline"
          disabled={busy}
          onClick={() => void runDryRun()}
          className="h-auto border-gold/60 px-2 py-0.5 text-xs text-gold hover:bg-gold/15 hover:text-gold"
        >
          {busy && report === null ? "预检中…" : "① 预检（dry-run）"}
        </Button>
      </div>

      {summary !== null && report !== null && (
        <div className="mt-3 space-y-1.5">
          <p className="text-paper/70">
            报告{report.ok ? "通过" : "未通过"}：源 {report.counts.files} 个文件、
            正文 {report.counts.chapters} 章 / {report.counts.chars} 字、
            故事对象 {report.counts.story_entities} 项；
            可迁移归类 {summary.mapped} 项、只读归档 {summary.ignored} 项。
          </p>
          {summary.blockers.length > 0 ? (
            <ul className="list-inside list-disc text-overdue">
              {summary.blockers.map((blocker) => (
                <li key={blocker} className="break-all">
                  {blocker}
                </li>
              ))}
            </ul>
          ) : (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void confirmApply()}
              className="h-auto border-gold px-2 py-0.5 text-xs text-gold hover:bg-gold/15 hover:text-gold"
            >
              {busy ? "迁移中…" : "② 确认迁移（生成 V2 项目）"}
            </Button>
          )}
        </div>
      )}

      {result !== null && (
        <p className="mt-3 text-gold">
          已迁移：V2 项目 <span className="font-mono">{result.project_id}</span>
          （{result.validation_ok ? "校验通过" : "校验未通过，请勿使用"}）。
          旧工作区文件原样保留，写作请回到创作台。
        </p>
      )}
      {error !== null && <p className="mt-3 break-all text-overdue">{error}</p>}
    </div>
  );
}
