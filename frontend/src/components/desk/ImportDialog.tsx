/**
 * 导入正文对话框（V03-2.3 / M06）：文件 → 预览 → 重复处理 → 整批确认。
 *
 * 交互原则：预览展示的是**会被写入的实际文本**，确认前不产生正文；同名章默认
 * 新增，内容重复必须由作者选择（新增副本 / 替换这些章 / 取消），不静默覆盖。
 */

import * as Dialog from "@radix-ui/react-dialog";
import { useState } from "react";

import {
  confirmImportPreview,
  createImportPreview,
  replacementTargets,
  updateImportPreview,
  type ImportPreview,
} from "../../api/imports";
import { message } from "../../state/v2";
import { ErrorText } from "../Workbench";

type Decision = "append" | "replace";

export default function ImportDialog({
  projectId,
  open,
  onOpenChange,
  onImported,
}: {
  projectId: string;
  open: boolean;
  onOpenChange(open: boolean): void;
  onImported(chapterIds: string[], operationId?: string): void;
}) {
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [decision, setDecision] = useState<Decision>("append");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  function close(next: boolean) {
    if (!next) {
      setPreview(null);
      setError(null);
      setDone(null);
      setDecision("append");
      setBusy(false);
    }
    onOpenChange(next);
  }

  async function chooseFile(file: File) {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const parsed = await createImportPreview(projectId, file.name, bytes);
      setPreview(parsed);
      setDecision("append");
    } catch (e) {
      setPreview(null);
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      let current = preview;
      let duplicateDecision: Decision | undefined;
      if (preview.duplicates.length > 0) {
        duplicateDecision = decision;
        if (decision === "replace") {
          current = await updateImportPreview(projectId, preview.preview_id, {
            expectedRevision: preview.revision,
            mode: "replace",
            replacements: replacementTargets(preview),
          });
        }
      }
      const result = await confirmImportPreview(projectId, current.preview_id, {
        previewHash: current.preview_hash,
        duplicateDecision,
      });
      if (result.status === "cancelled") {
        close(false);
        return;
      }
      setDone(
        result.status === "no_change"
          ? "没有需要写入的变化。"
          : `已导入 ${result.chapter_ids.length} 章。`,
      );
      onImported(result.chapter_ids, result.operation_id);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }

  const duplicates = preview?.duplicates ?? [];
  const totalChars =
    preview?.chapters.reduce((sum, chapter) => sum + chapter.text.length, 0) ?? 0;

  return (
    <Dialog.Root open={open} onOpenChange={close}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-overlay" />
        <Dialog.Content className="panel fixed left-1/2 top-1/2 z-50 flex max-h-[86vh] w-[min(680px,92vw)] -translate-x-1/2 -translate-y-1/2 flex-col p-6">
          <Dialog.Title className="font-serif text-xl text-fgStrong">
            导入正文
          </Dialog.Title>
          <Dialog.Description className="mt-1 text-xs leading-6 text-sub">
            支持 .txt / .md（UTF-8）。预览确认后才会写入；同名章默认新增，内容相同的章需要你决定。
          </Dialog.Description>

          <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
            {preview === null ? (
              <label className="notice block cursor-pointer p-6 text-center text-xs leading-6">
                <input
                  type="file"
                  accept=".txt,.md,.markdown"
                  className="hidden"
                  disabled={busy}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void chooseFile(file);
                  }}
                />
                {busy ? "正在解析…" : "点击选择文件（单文件上限 10 MiB，最多 1000 章）"}
              </label>
            ) : (
              <>
                <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-sub">
                  <span className="text-fgStrong">{preview.filename}</span>
                  <span>· {preview.chapters.length} 章</span>
                  <span>· {totalChars} 字</span>
                  <button
                    className="ml-auto text-blue underline"
                    onClick={() => {
                      setPreview(null);
                      setDone(null);
                      setError(null);
                    }}
                  >
                    重新选择
                  </button>
                </div>

                <ol className="space-y-1" aria-label="预览章节">
                  {preview.chapters.map((chapter) => (
                    <li
                      key={chapter.index}
                      className="flex items-center justify-between gap-3 border-b border-line px-1 py-2 text-xs"
                    >
                      <span className="min-w-0 flex-1 truncate">
                        <span className="mr-2 text-sub">{chapter.order_key}</span>
                        {chapter.title}
                      </span>
                      <span className="shrink-0 text-[11px] text-sub">
                        {chapter.text.length} 字
                      </span>
                    </li>
                  ))}
                </ol>

                {duplicates.length > 0 && (
                  <div className="notice mt-4 p-3 text-xs leading-6" role="status">
                    <p className="text-fgStrong">
                      这 {duplicates.length} 章与现有正文内容相同
                    </p>
                    <ul className="mt-1 space-y-0.5 text-sub">
                      {duplicates.map((duplicate) => (
                        <li key={duplicate.chapter_id}>
                          「{duplicate.title}」与「{duplicate.existing_title}」内容一致
                        </li>
                      ))}
                    </ul>
                    <div className="mt-2 space-y-1">
                      <label className="flex items-center gap-2">
                        <input
                          type="radio"
                          name="duplicate-decision"
                          checked={decision === "append"}
                          onChange={() => setDecision("append")}
                        />
                        新增副本（保留现有正文）
                      </label>
                      <label className="flex items-center gap-2">
                        <input
                          type="radio"
                          name="duplicate-decision"
                          checked={decision === "replace"}
                          onChange={() => setDecision("replace")}
                        />
                        用本次正文替换这些章
                      </label>
                    </div>
                  </div>
                )}
              </>
            )}
            <ErrorText>{error}</ErrorText>
            {done && (
              <p className="notice mt-3 p-3 text-xs text-fgStrong" role="status">
                {done}
              </p>
            )}
          </div>

          <div className="mt-4 flex justify-end gap-2">
            <Dialog.Close className="btn btn-gray px-4 py-2 text-xs">
              {done ? "完成" : "取消"}
            </Dialog.Close>
            <button
              className="btn px-4 py-2 text-xs"
              disabled={busy || preview === null || done !== null}
              onClick={() => void confirm()}
            >
              {busy ? "处理中…" : duplicates.length > 0 ? "按选择导入" : "确认导入"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
