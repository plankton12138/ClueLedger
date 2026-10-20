/**
 * 目录选择（0.4 V04-4）：按服务器本地路径打开或新建作品。
 *
 * 打开已有目录只检查、不改写；新建会建立两块内容、三份设定与 Git 基线。
 * 不做浏览器文件上传式的「目录授权」——应用运行在本机，路径就是事实。
 */

import { useState } from "react";

import { useWorkspaceStore } from "../../state/workspace";

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function DirectoryDialog({ open, onClose }: Props) {
  const bindings = useWorkspaceStore((state) => state.bindings);
  const openWorkspace = useWorkspaceStore((state) => state.open);
  const loading = useWorkspaceStore((state) => state.loading);
  const [path, setPath] = useState("");
  const [create, setCreate] = useState(false);

  if (!open) return null;

  const submit = async () => {
    if (!path.trim()) return;
    await openWorkspace(path.trim(), create);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="打开作品目录"
    >
      <div className="w-full max-w-lg rounded-md border border-[#cfc7ae] bg-[#fbf8ef] p-5 shadow-xl">
        <h2 className="font-serif text-lg font-bold text-sheetInk">打开作品目录</h2>
        <p className="mt-1 text-xs text-[#6b6555]">
          填一个本机目录的完整路径。新建会建立 正文/、设定集/ 与版本记录；打开只读取，不改写文件。
        </p>

        <label className="mt-4 block text-xs font-bold text-[#4b4737]" htmlFor="workspace-path">
          目录路径
        </label>
        <input
          id="workspace-path"
          className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm"
          placeholder="C:\\Users\\你\\我的小说"
          value={path}
          onChange={(event) => setPath(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void submit();
          }}
        />

        <label className="mt-3 flex items-center gap-2 text-xs text-[#4b4737]">
          <input
            type="checkbox"
            checked={create}
            onChange={(event) => setCreate(event.target.checked)}
          />
          新建作品（目录不存在时创建；已存在时补齐缺失的设定文件）
        </label>

        {bindings.length > 0 && (
          <div className="mt-4">
            <div className="text-xs font-bold text-[#4b4737]">最近打开</div>
            <ul className="mt-1 max-h-40 overflow-auto rounded border border-[#e2dbc6] bg-white/60">
              {bindings.map((binding) => (
                <li key={binding.id}>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-[#f3ecd8]"
                    onClick={() => setPath(binding.path)}
                  >
                    <span className="min-w-0 flex-1 truncate">{binding.name}</span>
                    <span className="truncate text-[10px] text-[#8a8371]">{binding.path}</span>
                    {!binding.exists && <span className="text-[10px] text-[#a33]">目录已不在</span>}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button className="btn btn-gray px-3 py-1 text-xs" onClick={onClose}>
            取消
          </button>
          <button
            className="btn btn-gold px-3 py-1 text-xs"
            disabled={loading || !path.trim()}
            onClick={() => void submit()}
          >
            {create ? "新建并打开" : "打开"}
          </button>
        </div>
      </div>
    </div>
  );
}
