/**
 * 作品位置（0.4 V04-8）：用系统文件夹对话框选位置，不再手抄路径。
 *
 * 打开已有作品选「作品文件夹」本身；新建作品分两步：选「存放位置」（默认文档目录），
 * 再起一个作品名，最终目录是「位置 / 作品名」。路径由对话框给出，界面只显示结果，
 * 因此不需要、也不接受手输路径。
 *
 * 对话框由本机服务弹出（`POST /api/file-workspaces/pick-directory`）：浏览器拿不到
 * 本机绝对路径，只有跑在同一台机器上的服务能打开系统窗口。
 */

import { useEffect, useState } from "react";

import {
  fetchDefaultLocation,
  pickDirectory,
  WorkspaceApiError,
} from "../../api/workspace";
import { useWorkspaceStore } from "../../state/workspace";

/** 新作品的占位名：与「新的对话」同一类占位，作者可以随手改掉。 */
const DEFAULT_NAME = "未命名作品";

function joinPath(root: string, name: string): string {
  const separator = root.includes("\\") ? "\\" : "/";
  return root.endsWith(separator) ? `${root}${name}` : `${root}${separator}${name}`;
}

function describePickerError(error: unknown): string {
  if (error instanceof WorkspaceApiError) return `${error.why}。${error.fix}`;
  return error instanceof Error ? error.message : "打不开文件夹选择窗口";
}

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function DirectoryDialog({ open, onClose }: Props) {
  const bindings = useWorkspaceStore((state) => state.bindings);
  const openWorkspace = useWorkspaceStore((state) => state.open);
  const loading = useWorkspaceStore((state) => state.loading);
  const [mode, setMode] = useState<"open" | "create">("open");
  const [pickedPath, setPickedPath] = useState<string | null>(null);
  const [location, setLocation] = useState<string | null>(null);
  const [name, setName] = useState(DEFAULT_NAME);
  const [picking, setPicking] = useState(false);
  const [pickerError, setPickerError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setPickedPath(null);
    setPickerError(null);
    if (useWorkspaceStore.getState().bindings.length === 0) setMode("create");
    fetchDefaultLocation()
      .then((defaults) => {
        if (alive) setLocation(defaults.root);
      })
      .catch(() => {
        if (alive) setLocation(null);
      });
    return () => {
      alive = false;
    };
  }, [open]);

  if (!open) return null;

  const target =
    mode === "create" ? (location && name.trim() ? joinPath(location, name.trim()) : null) : pickedPath;
  const canSubmit = Boolean(target) && !loading && !picking;

  const choose = async () => {
    setPicking(true);
    setPickerError(null);
    try {
      const initial = mode === "create" ? (location ?? "") : (pickedPath ?? "");
      const picked = await pickDirectory(initial);
      if (picked.path) {
        if (mode === "create") setLocation(picked.path);
        else setPickedPath(picked.path);
      }
    } catch (error) {
      setPickerError(describePickerError(error));
    } finally {
      setPicking(false);
    }
  };

  const submit = async () => {
    if (!target) return;
    await openWorkspace(target, mode === "create");
    onClose();
  };

  const tabClass = (active: boolean) =>
    `flex-1 rounded px-3 py-1.5 text-xs font-bold ${
      active ? "bg-[#efe6cd] text-sheetInk shadow-inner" : "text-[#6b6555] hover:bg-[#f3ecd8]"
    }`;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label="作品位置"
    >
      <div className="w-full max-w-lg rounded-md border border-[#cfc7ae] bg-[#fbf8ef] p-5 shadow-xl">
        <h2 className="font-serif text-lg font-bold text-sheetInk">作品位置</h2>
        <p className="mt-1 text-xs text-[#6b6555]">
          作品保存在本机文件夹里。位置用系统窗口选择，不用抄路径。
        </p>

        <div role="tablist" aria-label="选择方式" className="mt-4 flex gap-1 rounded border border-[#e2dbc6] bg-white/60 p-1">
          <button role="tab" aria-selected={mode === "open"} className={tabClass(mode === "open")} onClick={() => setMode("open")}>
            打开已有
          </button>
          <button
            role="tab"
            aria-selected={mode === "create"}
            className={tabClass(mode === "create")}
            onClick={() => setMode("create")}
          >
            新建作品
          </button>
        </div>

        {mode === "open" ? (
          <div className="mt-4">
            <div className="text-xs font-bold text-[#4b4737]">作品文件夹</div>
            <div className="mt-1 min-h-[38px] break-all rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm">
              {pickedPath ?? <span className="text-[#8a8371]">还没有选择文件夹</span>}
            </div>
            <button className="btn btn-gray mt-2 px-3 py-1 text-xs" disabled={picking} onClick={() => void choose()}>
              {picking ? "等待选择…" : "选择作品文件夹…"}
            </button>
          </div>
        ) : (
          <div className="mt-4">
            <div className="text-xs font-bold text-[#4b4737]">存放位置</div>
            <div className="mt-1 min-h-[38px] break-all rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm">
              {location ?? <span className="text-[#8a8371]">读取默认位置…</span>}
            </div>
            <button className="btn btn-gray mt-2 px-3 py-1 text-xs" disabled={picking} onClick={() => void choose()}>
              {picking ? "等待选择…" : "更改位置…"}
            </button>

            <label className="mt-3 block text-xs font-bold text-[#4b4737]" htmlFor="workspace-name">
              作品名
            </label>
            <input
              id="workspace-name"
              aria-label="作品名"
              className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm"
              value={name}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit();
              }}
            />
            {target && <p className="mt-1 text-[11px] text-[#8a8371]">将创建：{target}</p>}
          </div>
        )}

        {pickerError && <p className="mt-3 text-[11px] text-[#a33]">{pickerError}</p>}

        {bindings.length > 0 && (
          <div className="mt-4">
            <div className="text-xs font-bold text-[#4b4737]">最近打开</div>
            <ul className="mt-1 max-h-40 overflow-auto rounded border border-[#e2dbc6] bg-white/60">
              {bindings.map((binding) => (
                <li key={binding.id}>
                  <button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:bg-[#f3ecd8]"
                    onClick={() => {
                      setMode("open");
                      setPickedPath(binding.path);
                    }}
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
          <button className="btn btn-gold px-3 py-1 text-xs" disabled={!canSubmit} onClick={() => void submit()}>
            {mode === "create" ? "新建并打开" : "打开"}
          </button>
        </div>
      </div>
    </div>
  );
}
