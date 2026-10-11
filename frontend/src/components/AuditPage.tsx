/**
 * 审计中心页（P7 T9，计划 D7）：单章确定性审计报告的阅读与执行面。
 *
 * - 左栏章节阅读：ChapterPaper 复用 + 章节下拉（默认最新章——审计最常
 *   针对刚写完的章）；右栏报告：三 RingStat + 检查清单；
 * - severity 配色：error=红 / warn=金 / pass=绿（清单空 = 全部通过）；
 * - 「定位问题章节」：清单项带 anchor_chapter → navigate({kind:"chapter"})，
 *   App 现有 nav effect 自动切回 studio（P6 T11 机制零改动）；
 * - 「生成修订建议」：把非 pass 项组装成一条 steer 消息（运行中）/ prompt
 *   （空闲）经现有 WS 命令发送——零新后端机制，对话流里可追溯。模板组装
 *   在 buildRevisionRequest 纯函数（vitest 钉住）；
 * - 口径诚实标注：三指标是结构代理非语义判断（与 T8 响应口径一致）；
 * - 无章节/空账本：合法空结果态（提示「先让 agent 登记账本」），不报错。
 */

import { useCallback, useEffect, useState } from "react";
import {
  fetchAudit,
  fetchChapter,
  fetchChapters,
  type AuditCheckWire,
  type AuditReportWire,
} from "../api/rest";
import { chapterNumber, countChars, parseChapterHeading, splitParagraphs } from "../state/chapter";
import { useChatStore } from "../state/store";
import { Button } from "@/components/ui/button";
import ChapterPaper from "./ChapterPaper";
import RingStat from "./RingStat";

/** 修订建议模板（D7）：非 pass 项逐条编号，运行中走 steer / 空闲走 prompt。 */
export function buildRevisionRequest(chapterId: string, checks: AuditCheckWire[]): string {
  const lines = checks.map(
    (c, i) =>
      `${i + 1}. [${c.severity === "error" ? "错误" : "警告"}] ${c.rule}：${c.message}` +
      (c.anchor_chapter !== null ? `（问题章节：${c.anchor_chapter}）` : ""),
  );
  return `以下是第 ${chapterNumber(chapterId)} 章审计发现的确定性问题，请逐条核对并修订：\n${lines.join("\n")}`;
}

/** 检查规则 → 中文短标签（清单徽注用）。 */
const RULE_LABELS: Record<string, string> = {
  timeline_conflict: "时间线冲突",
  foreshadow_overdue: "伏笔逾期",
  clue_planting: "公平呈现",
  method_state: "手法状态",
};

export default function AuditPage({ onSteer }: { onSteer: (message: string) => Promise<void> }) {
  const slug = useChatStore((s) => s.slug);
  const running = useChatStore((s) => s.running);
  const navigate = useChatStore((s) => s.navigate);
  const [chapters, setChapters] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [chapter, setChapter] = useState<{ title: string | null; paragraphs: string[]; chars: number } | null>(null);
  const [report, setReport] = useState<AuditReportWire | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  // 章节清单：进入页 / 换工作区重拉；默认选最新章。
  useEffect(() => {
    if (slug === null) return;
    fetchChapters(slug)
      .then((list) => {
        setChapters(list);
        setSelected((cur) => cur ?? (list.length > 0 ? (list[list.length - 1] as string) : null));
      })
      .catch((exc: unknown) => setLoadError(exc instanceof Error ? exc.message : String(exc)));
  }, [slug]);

  // 选章变化：拉正文 + 报告（D7：两接口独立，任一失败就地展示三要素）。
  useEffect(() => {
    if (slug === null || selected === null) return;
    let cancelled = false;
    setLoadError(null);
    setActionMessage(null);
    fetchChapter(slug, selected)
      .then((body) => {
        if (cancelled) return;
        const parsed = parseChapterHeading(body.content);
        setChapter({
          title: parsed.title ?? selected,
          paragraphs: splitParagraphs(parsed.body),
          chars: countChars(body.content),
        });
      })
      .catch((exc: unknown) => {
        if (!cancelled) setLoadError(exc instanceof Error ? exc.message : String(exc));
      });
    fetchAudit(slug, selected)
      .then((body) => {
        if (!cancelled) setReport(body);
      })
      .catch((exc: unknown) => {
        if (!cancelled) setLoadError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      cancelled = true;
    };
  }, [selected, slug]);

  const step = useCallback(
    (delta: number) => {
      if (selected === null) return;
      const idx = chapters.indexOf(selected);
      const next = chapters[idx + delta];
      if (next !== undefined) setSelected(next);
    },
    [chapters, selected],
  );

  const sendRevision = useCallback(async () => {
    if (report === null || selected === null) return;
    setSending(true);
    setActionMessage(null);
    try {
      await onSteer(buildRevisionRequest(selected, report.checks));
      setActionMessage(
        running
          ? "修订建议已插话（steer），主编会在写作流中处理——可在对话流追溯。"
          : "修订建议已作为指令（prompt）发送，可在对话流查看处理进度。",
      );
    } catch (exc) {
      setActionMessage(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSending(false);
    }
  }, [onSteer, report, running, selected]);

  const selectedIdx = selected !== null ? chapters.indexOf(selected) : -1;
  const total = chapters.length;
  const checks = report?.checks ?? [];

  return (
    <main className="flex min-h-0 flex-1 flex-col border-t border-line">
      <div className="flex flex-none items-center gap-3 border-b border-line bg-ink-soft/50 px-4 py-2">
        <h2 className="font-serif text-sm font-bold text-gold">审计中心</h2>
        <span className="text-xs text-paper/45">单章确定性审计（结构代理指标，非语义判断）</span>
        {chapters.length > 0 && (
          <select
            aria-label="选择审计章节"
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value)}
            className="ml-auto border border-line bg-transparent px-1.5 py-0.5 text-xs text-paper focus:border-gold/60 focus:outline-none"
          >
            {chapters.map((id) => (
              <option key={id} value={id} className="bg-ink">
                {chapterNumber(id)}
              </option>
            ))}
          </select>
        )}
      </div>
      {chapters.length === 0 ? (
        <div className="flex flex-1 items-center justify-center px-4 text-center">
          <p className="max-w-sm text-xs leading-relaxed text-paper/40">
            {loadError !== null ? (
              <span className="text-overdue">{loadError}</span>
            ) : (
              "暂无章节——先在创作台让 agent 登记账本并写作章节，审计中心据实生成报告。"
            )}
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row lg:overflow-hidden">
          {/* 左栏：章节阅读（ChapterPaper 复用，D7）。 */}
          <section className="flex min-h-0 min-w-0 flex-1 flex-col px-6 py-4">
            <ChapterPaper
              title={chapter?.title ?? selected ?? "—"}
              chars={chapter?.chars ?? 0}
              chapterNo={chapterNumber(selected ?? "")}
              total={total}
              onPrev={selectedIdx > 0 ? () => step(-1) : undefined}
              onNext={selectedIdx >= 0 && selectedIdx < total - 1 ? () => step(1) : undefined}
              paragraphs={chapter?.paragraphs ?? []}
            />
          </section>
          {/* 右栏：报告（三 RingStat + 检查清单）。 */}
          <aside className="w-full flex-none space-y-3 border-t border-line px-4 py-3 text-xs leading-relaxed lg:w-96 lg:border-l lg:border-t-0">
            {loadError !== null && (
              <p className="border border-overdue/50 bg-overdue/10 px-2 py-1.5 text-paper">{loadError}</p>
            )}
            <div className="flex items-center justify-around gap-2">
              <RingStat
                rate={report?.foreshadow_recovery.rate ?? 0}
                size={56}
                label="伏笔回收率"
                color="text-recovered"
                center={`${Math.round((report?.foreshadow_recovery.rate ?? 0) * 100)}%`}
              />
              <RingStat
                rate={report?.clue_fairness.rate ?? 0}
                size={56}
                label="线索公平性"
                color="text-planned"
                center={`${Math.round((report?.clue_fairness.rate ?? 0) * 100)}%`}
              />
              <RingStat
                rate={report?.character_consistency?.rate ?? 0}
                size={56}
                label="人物一致性"
                color="text-planted"
                center={report?.character_consistency === null ? "—" : `${Math.round((report?.character_consistency?.rate ?? 0) * 100)}%`}
              />
            </div>
            <div className="flex justify-around text-[10px] text-paper/50">
              <span>回收 {report?.foreshadow_recovery.count}/{report?.foreshadow_recovery.total}</span>
              <span>呈现 {report?.clue_fairness.count}/{report?.clue_fairness.total}</span>
              <span>
                冲突 {report?.character_consistency?.conflicts ?? 0}/{report?.character_consistency?.entries ?? 0}
              </span>
            </div>
            <div className="flex min-h-0 flex-1 flex-col border-t border-line pt-3">
              <div className="mb-2 flex items-center gap-2">
                <h3 className="font-serif text-sm font-bold text-gold">检查清单</h3>
                {checks.length === 0 && report !== null && (
                  <span className="text-planted">✓ 全部通过</span>
                )}
                <Button
                  variant="outline"
                  disabled={sending || report === null || checks.length === 0}
                  onClick={() => void sendRevision()}
                  className="ml-auto h-auto px-2 py-0.5 text-[10px] text-gold-soft hover:border-gold/60 hover:bg-transparent hover:text-gold"
                  title={running ? "运行中：以插话（steer）发送" : "空闲：以指令（prompt）发送"}
                >
                  {sending ? "发送中…" : "生成修订建议 →"}
                </Button>
              </div>
              {actionMessage !== null && (
                <p className="mb-2 whitespace-pre-wrap border border-line bg-ink-soft/60 px-2 py-1.5 text-paper/80">
                  {actionMessage}
                </p>
              )}
              {report !== null && checks.length > 0 && (
                <ul className="min-h-0 flex-1 space-y-2 overflow-auto">
                  {checks.map((c, i) => (
                    <li
                      key={i}
                      className={`border px-2 py-1.5 ${
                        c.severity === "error"
                          ? "border-overdue/50 bg-overdue/5"
                          : "border-gold/40 bg-gold/5"
                      }`}
                    >
                      <div className="mb-1 flex min-w-0 items-center gap-2">
                        <span
                          className={`border px-1 py-px text-[10px] ${
                            c.severity === "error"
                              ? "border-overdue/60 text-overdue"
                              : "border-gold/60 text-gold"
                          }`}
                        >
                          {c.severity === "error" ? "错误" : "警告"}
                        </span>
                        <span className="min-w-0 truncate text-gold-soft" title={c.rule}>
                          {RULE_LABELS[c.rule] ?? c.rule}
                          {c.anchor_name !== null ? ` · ${c.anchor_name}` : ""}
                        </span>
                        {c.anchor_chapter !== null && (
                          <Button
                            variant="outline"
                            onClick={() => navigate({ kind: "chapter", chapterId: c.anchor_chapter as string })}
                            className="ml-auto h-auto shrink-0 px-1.5 py-px text-[10px] text-paper/60 hover:border-gold/60 hover:bg-transparent hover:text-gold"
                            title={`定位到 ${c.anchor_chapter}（回到创作台正文）`}
                          >
                            定位
                          </Button>
                        )}
                      </div>
                      <p className="whitespace-pre-wrap text-paper/75">{c.message}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </aside>
        </div>
      )}
    </main>
  );
}
