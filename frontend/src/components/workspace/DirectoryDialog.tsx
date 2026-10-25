import { Button } from "../ui/Button";
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
} from "../../api/workspace";
import { AppDialog } from "../ui/AppDialog";
import { Tabs } from "../ui/Tabs";
import { errorMessage } from "../../api/errors";
import { useWorkspaceStore } from "../../state/workspace";

/** 新作品的占位名：与「新的对话」同一类占位，作者可以随手改掉。 */
const DEFAULT_NAME = "未命名作品";

function joinPath(root: string, name: string): string {
  const separator = root.includes("\\") ? "\\" : "/";
  return root.endsWith(separator) ? `${root}${name}` : `${root}${separator}${name}`;
}

function describePickerError(error: unknown): string {
  return errorMessage(error, "作品位置");
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
    if (!target || !canSubmit) return;
    const success = await openWorkspace(target, mode === "create");
    if (success) onClose();
    else setPickerError(useWorkspaceStore.getState().error);
  };

  return (
    <AppDialog open={open} onOpenChange={value => { if (!value) onClose(); }} title="作品位置" returnFocus={() => document.querySelector<HTMLElement>('[data-dialog-return="workspace"]')}>
        <Tabs label="选择方式" value={mode} onValueChange={value => setMode(value as "open" | "create")} items={[{ value: "open", label: "打开已有", content: null }, { value: "create", label: "新建作品", content: null }]} />

        {mode === "open" ? (
          <div className="mt-4">
            <div className="text-sm font-bold text-[#4b4737]">作品文件夹</div>
            <div className="mt-1 min-h-[38px] break-all rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm">
              {pickedPath ?? <span className="text-ui-muted">还没有选择文件夹</span>}
            </div>
            <Button className="mt-2 px-3 py-1 text-sm" disabled={picking} onClick={() => void choose()}>
              {picking ? "等待选择…" : "选择作品文件夹…"}
            </Button>
          </div>
        ) : (
          <div className="mt-4">
            <div className="text-sm font-bold text-[#4b4737]">存放位置</div>
            <div className="mt-1 min-h-[38px] break-all rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm">
              {location ?? <span className="text-ui-muted">读取默认位置…</span>}
            </div>
            <Button className="mt-2 px-3 py-1 text-sm" disabled={picking} onClick={() => void choose()}>
              {picking ? "等待选择…" : "更改位置…"}
            </Button>

            <label className="mt-3 block text-sm font-bold text-[#4b4737]" htmlFor="workspace-name">
              作品名
            </label>
            <input
              id="workspace-name"
              aria-label="作品名"
              className="mt-1 w-full rounded border border-[#cfc7ae] bg-white px-3 py-2 text-sm"
              value={name}
              onChange={(event) => setName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.nativeEvent.isComposing) void submit();
              }}
            />
            {target && <p className="mt-1 text-sm text-ui-muted">将创建：{target}</p>}
          </div>
        )}

        {pickerError && <p role="alert" className="mt-3 text-sm text-[#a33]">{pickerError}</p>}

        {bindings.length > 0 && (
          <div className="mt-4">
            <div className="text-sm font-bold text-[#4b4737]">最近打开</div>
            <ul className="mt-1 max-h-40 overflow-auto rounded border border-[#e2dbc6] bg-white/60">
              {bindings.map((binding) => (
                <li key={binding.id}>
                  <Button
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-[#f3ecd8]"
                    onClick={() => {
                      setMode("open");
                      setPickedPath(binding.path);
                    }}
                  >
                    <span className="min-w-0 flex-1 truncate">{binding.name}</span>
                    <span className="truncate text-[12px] text-ui-muted">{binding.path}</span>
                    {!binding.exists && <span className="text-[12px] text-[#a33]">目录已不在</span>}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <Button className="px-3 py-1 text-sm" onClick={onClose}>
            取消
          </Button>
          <Button variant="primary" className="px-3 py-1 text-sm" disabled={!canSubmit} onClick={() => void submit()}>
            {mode === "create" ? "新建并打开" : "打开"}
          </Button>
        </div>
    </AppDialog>
  );
}
