/**
 * 章版本历史（V03-2.5）：只读列表 + 恢复到某个版本。
 *
 * 恢复是一次新发布，不是回滚历史；当前版本不可「恢复」，旧版本恢复后需要
 * 重新载入列表（新版本出现在首位）。副本保护由服务端拦截，这里只显示错误。
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchChapterHistory,
  restoreChapterVersion,
  type ChapterVersion,
} from "../../api/history";
import { message } from "../../state/v2";
import { Empty, ErrorText, Pill } from "../Workbench";

export default function ChapterHistory({
  projectId,
  chapterId,
  onRestored,
}: {
  projectId: string;
  chapterId: string;
  onRestored(): void;
}) {
  const [versions, setVersions] = useState<ChapterVersion[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setVersions(await fetchChapterHistory(projectId, chapterId));
    } catch (e) {
      setError(message(e));
      setVersions([]);
    }
  }, [projectId, chapterId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function restore(version: ChapterVersion) {
    const current = versions?.find((item) => item.is_current);
    if (!current) return;
    setBusy(version.commit_id);
    setError(null);
    try {
      await restoreChapterVersion(
        projectId,
        chapterId,
        version.commit_id,
        current.chapter_base,
      );
      onRestored();
      await load();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  }

  if (versions === null) {
    return <Empty>正在读取版本历史…</Empty>;
  }
  if (versions.length === 0) {
    return <Empty>这一章还没有可回看的历史版本。</Empty>;
  }
  return (
    <div className="h-full overflow-y-auto p-4">
      <p className="mb-3 text-[11px] leading-5 text-sub">
        恢复会生成一个新版本，旧版本仍然保留；正在手工修改的章需要先完成或放弃本轮。
      </p>
      <ol className="space-y-2" aria-label="版本历史">
        {versions.map((version) => (
          <li
            key={version.commit_id}
            className="flex flex-wrap items-start gap-2 border-b border-line pb-2 text-xs"
          >
            <div className="min-w-0 flex-1">
              <p className="flex items-center gap-2">
                <span className="truncate text-fgStrong">{version.summary}</span>
                {version.is_current && <Pill tone="green">当前</Pill>}
              </p>
              <p className="mt-1 text-[11px] text-sub">
                {version.created_at.slice(0, 16).replace("T", " ")} ·{" "}
                {version.char_count} 字
              </p>
              <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-sub">
                {version.preview}
              </p>
            </div>
            <button
              className="btn btn-gray px-3 py-1 text-[11px]"
              disabled={version.is_current || busy !== null}
              onClick={() => void restore(version)}
            >
              {busy === version.commit_id ? "恢复中…" : "恢复此版本"}
            </button>
          </li>
        ))}
      </ol>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}
