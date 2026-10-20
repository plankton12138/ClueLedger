/**
 * 改动与历史抽屉（0.4 V04-4）。
 *
 * 显示三件事：仓库是否已启用版本记录、当前未记录的改动、提交历史与某次提交的差异。
 * 撤销只对最新一次应用提交生效，后端会校验 HEAD 与工作区是否干净，界面不代替它判断。
 */

import { useWorkspaceStore } from "../../state/workspace";

const CHANGE_LABEL: Record<string, string> = {
  "??": "未跟踪",
  M: "修改",
  A: "新增",
  D: "删除",
  R: "重命名",
  C: "复制",
};

function changeLabel(code: string): string {
  const key = code.trim();
  return CHANGE_LABEL[key] ?? CHANGE_LABEL[key[0] ?? ""] ?? code;
}

export default function ChangesDrawer() {
  const open = useWorkspaceStore((state) => state.drawerOpen);
  const close = useWorkspaceStore((state) => state.closeDrawer);
  const git = useWorkspaceStore((state) => state.git);
  const diff = useWorkspaceStore((state) => state.diff);
  const log = useWorkspaceStore((state) => state.log);
  const selectedCommit = useWorkspaceStore((state) => state.selectedCommit);
  const commitDiff = useWorkspaceStore((state) => state.commitDiff);
  const initGit = useWorkspaceStore((state) => state.initGit);
  const recordChanges = useWorkspaceStore((state) => state.recordChanges);
  const revert = useWorkspaceStore((state) => state.revert);
  const showCommit = useWorkspaceStore((state) => state.showCommit);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30">
      <div className="flex h-full w-full max-w-xl flex-col border-l border-[#cfc7ae] bg-[#fbf8ef] shadow-2xl">
        <div className="flex flex-none items-center gap-2 border-b border-[#e2dbc6] px-4 py-3">
          <h2 className="font-serif text-base font-bold text-sheetInk">改动与历史</h2>
          <div className="flex-1" />
          <button className="btn btn-gray px-2 py-1 text-[11px]" onClick={close}>
            关闭
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-auto px-4 py-4 text-xs text-[#4b4737]">
          {!git || !git.initialized ? (
            <section>
              <h3 className="font-bold">还没有版本记录</h3>
              <p className="mt-1 leading-6 text-[#6b6555]">
                启用后会把当前内容记成第一个提交，之后的修改各记一次，可以查看差异与撤销。
              </p>
              <button
                className="btn btn-gold mt-2 px-3 py-1 text-[11px]"
                onClick={() => void initGit()}
              >
                启用版本记录
              </button>
            </section>
          ) : (
            <>
              <section>
                <h3 className="font-bold">
                  未记录的改动
                  {git.clean && <span className="ml-2 font-normal text-[#6b6555]">（干净）</span>}
                </h3>
                {git.changes.length > 0 && (
                  <ul className="mt-1 space-y-1">
                    {git.changes.map((change) => (
                      <li key={change.path} className="flex items-center gap-2">
                        <span className="w-14 shrink-0 text-[10px] text-[#8a8371]">
                          {changeLabel(change.code)}
                        </span>
                        <span className="min-w-0 flex-1 truncate">{change.path}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {git.conflicts.length > 0 && (
                  <p className="mt-2 text-[#a33]">
                    有未解决的合并冲突：{git.conflicts.join("、")}；先在同一仓库的 Git 里解决。
                  </p>
                )}
                {!git.clean && (
                  <button
                    className="btn btn-gray mt-2 px-3 py-1 text-[11px]"
                    onClick={() => void recordChanges()}
                  >
                    把这些改动记为一次提交
                  </button>
                )}
              </section>

              {diff && (
                <section>
                  <h3 className="font-bold">工作区差异</h3>
                  <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded border border-[#e2dbc6] bg-white/70 p-2 text-[11px] leading-5">
                    {diff}
                  </pre>
                  <p className="mt-1 text-[10px] text-[#8a8371]">
                    新章节还没被跟踪，所以不出现在这里；提交后可在历史里看到完整差异。
                  </p>
                </section>
              )}

              <section>
                <h3 className="font-bold">历史</h3>
                <ul className="mt-1 divide-y divide-[#e2dbc6]">
                  {log.map((commit) => (
                    <li key={commit.commit} className="flex items-center gap-2 py-2">
                      <button
                        className="min-w-0 flex-1 truncate text-left hover:underline"
                        onClick={() => void showCommit(commit)}
                      >
                        {commit.subject}
                      </button>
                      <code className="shrink-0 text-[10px] text-[#8a8371]">
                        {commit.commit.slice(0, 7)}
                      </code>
                      <span className="shrink-0 text-[10px] text-[#8a8371]">
                        {commit.at.slice(0, 16).replace("T", " ")}
                      </span>
                      {log[0]?.commit === commit.commit && (
                        <button
                          className="btn btn-gray shrink-0 px-2 py-0.5 text-[10px]"
                          onClick={() => void revert(commit.commit)}
                        >
                          撤销
                        </button>
                      )}
                    </li>
                  ))}
                  {log.length === 0 && <li className="py-2 text-[#8a8371]">还没有提交。</li>}
                </ul>
              </section>

              {selectedCommit && (
                <section>
                  <h3 className="font-bold">提交差异 · {selectedCommit.subject}</h3>
                  <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded border border-[#e2dbc6] bg-white/70 p-2 text-[11px] leading-5">
                    {commitDiff || "（这次提交没有可显示的差异）"}
                  </pre>
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
