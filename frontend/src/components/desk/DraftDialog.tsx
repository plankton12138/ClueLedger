/**
 * 试写对话框（V03-3.3/3.4/3.5）：账本与作者要求 → 只读预览 → 继续调整 → 采用。
 *
 * 三条产品约束在这里落地：
 * - 候选采用前不写正文；预览展示的完整正文就是会被提交的那一份；
 * - 「继续调整」产生新修订，旧修订随即不可采用（按钮跟着预览哈希走）；
 * - 采用成功后主区定位到正式正文，并留下可回看的操作结果与后续状态。
 */

import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useMemo, useState } from "react";

import {
  adoptDraft,
  createDraft,
  discardDraft,
  fetchLedger,
  reviseDraft,
  type ChapterCandidate,
  type LedgerEntry,
} from "../../api/drafting";
import { message } from "../../state/v2";
import { ErrorText, Pill } from "../Workbench";

export default function DraftDialog({
  projectId,
  open,
  chapters,
  onOpenChange,
  onAdopted,
}: {
  projectId: string;
  open: boolean;
  chapters: { chapter_id: string; title: string }[];
  onOpenChange(open: boolean): void;
  onAdopted(chapterId: string, operationId: string): void;
}) {
  const [target, setTarget] = useState("new");
  const [instruction, setInstruction] = useState("");
  const [chapterTitle, setChapterTitle] = useState("");
  const [viewpoint, setViewpoint] = useState("");
  const [lengthTarget, setLengthTarget] = useState("");
  const [boundary, setBoundary] = useState("");
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [refs, setRefs] = useState<string[]>([]);
  const [candidate, setCandidate] = useState<ChapterCandidate | null>(null);
  const [adjustment, setAdjustment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void fetchLedger(projectId)
      .then((entries) => {
        if (!cancelled) setLedger(entries);
      })
      .catch(() => {
        if (!cancelled) setLedger([]);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId]);

  function reset() {
    setTarget("new");
    setInstruction("");
    setChapterTitle("");
    setViewpoint("");
    setLengthTarget("");
    setBoundary("");
    setRefs([]);
    setCandidate(null);
    setAdjustment("");
    setError(null);
    setBusy(false);
  }

  function close(next: boolean) {
    if (!next) reset();
    onOpenChange(next);
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const created = await createDraft(projectId, {
        target,
        chapter_title: chapterTitle,
        instruction,
        viewpoint,
        length_target: lengthTarget,
        narrative_boundary: boundary,
        refs,
        idempotency_key: crypto.randomUUID(),
      });
      setCandidate(created);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function adjust() {
    if (!candidate) return;
    if (!adjustment.trim()) {
      setError("写下这次要怎么改，例如「对白更克制，结尾停在门外」。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const revised = await reviseDraft(projectId, candidate.candidate_id, {
        instruction: adjustment,
        expectedRevision: candidate.current_revision,
      });
      setCandidate(revised);
      setAdjustment("");
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function adopt() {
    if (!candidate) return;
    setBusy(true);
    setError(null);
    try {
      const result = await adoptDraft(
        projectId,
        candidate.candidate_id,
        candidate.preview_hash,
      );
      onAdopted(result.chapter_id, result.operation_id);
      close(false);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function discard() {
    if (!candidate) return;
    setBusy(true);
    try {
      await discardDraft(projectId, candidate.candidate_id);
    } catch {
      // 丢弃失败不影响作者继续预览；错误在关闭后可重试。
    } finally {
      setBusy(false);
      close(false);
    }
  }

  const grouped = useMemo(() => {
    const map = new Map<string, LedgerEntry[]>();
    for (const entry of ledger) {
      const list = map.get(entry.kind_label) ?? [];
      list.push(entry);
      map.set(entry.kind_label, list);
    }
    return [...map.entries()];
  }, [ledger]);

  return (
    <Dialog.Root open={open} onOpenChange={close}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
        <Dialog.Content className="panel fixed left-1/2 top-1/2 z-50 flex max-h-[88vh] w-[min(820px,94vw)] -translate-x-1/2 -translate-y-1/2 flex-col p-6">
          <Dialog.Title className="font-serif text-xl text-fgStrong">
            {candidate ? "试写预览" : "试写一章"}
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs leading-6 text-sub">
            {candidate
              ? "预览是只读的：这一版不会自动写入正文，采用后才会成为正式稿，并可以继续手工修改。"
              : "从设定与你的要求出发写一章。不需要先有大纲或节拍；空作品也可以直接试写首章。"}
          </Dialog.Description>

          <div className="mt-4 min-h-0 flex-1 overflow-y-auto pr-1">
            {candidate ? (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2 text-xs text-sub">
                  <Pill tone="amber">修订 {candidate.current_revision}</Pill>
                  <span>{candidate.title}</span>
                  <span>· {candidate.text.length} 字</span>
                  {candidate.model && <span>· {candidate.model}</span>}
                  <span className="ml-auto">采用会提交这一份完整正文</span>
                </div>
                <article
                  className="paper-sheet max-h-[46vh] overflow-y-auto px-6 py-6 font-serif text-[15px] leading-8"
                  aria-label="候选正文预览"
                  data-readonly="true"
                >
                  {candidate.text.split("\n").map((paragraph, index) =>
                    paragraph.trim() ? (
                      <p key={index} className="mb-4 last:mb-0">
                        {paragraph}
                      </p>
                    ) : null,
                  )}
                </article>

                <div className="space-y-2">
                  <label className="block text-xs text-sub" htmlFor="draft-adjustment">
                    继续调整（生成新修订，旧修订随即失效）
                  </label>
                  <textarea
                    id="draft-adjustment"
                    className="w-full resize-none rounded border border-line bg-transparent px-3 py-2 text-xs leading-6"
                    rows={2}
                    value={adjustment}
                    placeholder="例如：开头的天气描写再短一些，结尾停在门外。"
                    onChange={(e) => setAdjustment(e.target.value)}
                  />
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className="block text-xs">
                    <span className="text-sub">目标</span>
                    <select
                      className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
                      value={target}
                      onChange={(e) => setTarget(e.target.value)}
                    >
                      <option value="new">新章（追加到末尾）</option>
                      {chapters.map((chapter) => (
                        <option key={chapter.chapter_id} value={chapter.chapter_id}>
                          改写：{chapter.title}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-xs">
                    <span className="text-sub">章标题（可空）</span>
                    <input
                      className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
                      value={chapterTitle}
                      onChange={(e) => setChapterTitle(e.target.value)}
                      placeholder="例如：雪山旅馆"
                    />
                  </label>
                </div>

                <label className="block text-xs">
                  <span className="text-sub">这一章要发生什么（必填）</span>
                  <textarea
                    className="mt-1 w-full resize-none rounded border border-line bg-transparent px-3 py-2 leading-6"
                    rows={4}
                    value={instruction}
                    onChange={(e) => setInstruction(e.target.value)}
                    placeholder="例如：林昭在雪夜住进一家只有姐妹两人打理的旅馆，她不确定谁在说谎。"
                  />
                </label>

                <div className="grid gap-3 sm:grid-cols-3">
                  <label className="block text-xs">
                    <span className="text-sub">视角</span>
                    <input
                      className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
                      value={viewpoint}
                      onChange={(e) => setViewpoint(e.target.value)}
                      placeholder="林昭第三人称限知"
                    />
                  </label>
                  <label className="block text-xs">
                    <span className="text-sub">篇幅</span>
                    <input
                      className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
                      value={lengthTarget}
                      onChange={(e) => setLengthTarget(e.target.value)}
                      placeholder="2000-3000 字"
                    />
                  </label>
                  <label className="block text-xs">
                    <span className="text-sub">叙事边界</span>
                    <input
                      className="mt-1 w-full rounded border border-line bg-transparent px-2 py-1.5"
                      value={boundary}
                      onChange={(e) => setBoundary(e.target.value)}
                      placeholder="不揭示凶手"
                    />
                  </label>
                </div>

                {grouped.length > 0 && (
                  <div className="notice p-3 text-xs">
                    <p className="text-fgStrong">引用设定（可选）</p>
                    <div className="mt-2 max-h-40 space-y-2 overflow-y-auto">
                      {grouped.map(([label, entries]) => (
                        <div key={label}>
                          <p className="text-[11px] text-sub">{label}</p>
                          {entries.map((entry) => (
                            <label
                              key={entry.id}
                              className="flex items-start gap-2 py-0.5 leading-6"
                            >
                              <input
                                type="checkbox"
                                className="mt-1.5"
                                checked={refs.includes(entry.id)}
                                onChange={(e) =>
                                  setRefs((current) =>
                                    e.target.checked
                                      ? [...current, entry.id]
                                      : current.filter((id) => id !== entry.id),
                                  )
                                }
                              />
                              <span className="min-w-0">
                                <span className="text-fgStrong">{entry.title}</span>
                                <span className="text-sub"> · {entry.summary}</span>
                              </span>
                            </label>
                          ))}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
            <ErrorText>{error}</ErrorText>
          </div>

          <div className="mt-4 flex flex-wrap justify-end gap-2">
            {candidate ? (
              <>
                <button
                  className="btn btn-gray px-4 py-2 text-xs"
                  disabled={busy}
                  onClick={() => void discard()}
                >
                  放弃候选
                </button>
                <button
                  className="btn btn-gray px-4 py-2 text-xs"
                  disabled={busy || !adjustment.trim()}
                  onClick={() => void adjust()}
                >
                  {busy ? "处理中…" : "继续调整"}
                </button>
                <button
                  className="btn px-4 py-2 text-xs"
                  disabled={busy}
                  onClick={() => void adopt()}
                >
                  {busy ? "提交中…" : "采用这一版"}
                </button>
              </>
            ) : (
              <>
                <Dialog.Close className="btn btn-gray px-4 py-2 text-xs">
                  取消
                </Dialog.Close>
                <button
                  className="btn px-4 py-2 text-xs"
                  disabled={busy || !instruction.trim()}
                  onClick={() => void start()}
                >
                  {busy ? "正在写…" : "开始试写"}
                </button>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
