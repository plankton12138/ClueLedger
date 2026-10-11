/**
 * 文章管理页（P7 T14）：把工作区管理从顶栏下拉（P7 T1 WorkspaceMenu，本任务撤除）
 * 收进独立页面——列表 / 切换 / 导出 / 删除 / 新建。数据全走 store 的 workspaces 切片，
 * 增删后本地同步（新建/删除的接口成功即代表盘上事实变更，列表按响应结果收敛）。
 * 删除守门（输入 slug 确认）在本页内联完成；服务端 422/409 三要素文案原样透出。
 */

import { useState } from "react";
import { createWorkspace, deleteWorkspace, fetchWorkspaces } from "../api/rest";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useChatStore } from "../state/store";

interface Props {
  /** 切换到某篇文章（App 的 switchTo：setSlug + ?w= 记址）。 */
  onSwitch: (slug: string) => void;
  /** 「进入」后跳创作台。 */
  onEnter: () => void;
}

/** 新建文章表单（原 App.tsx 的 CreateWorkspaceForm 迁入本页，P7 T14）。 */
function CreateWorkspaceForm({ onCreated }: { onCreated: (slug: string) => void }) {
  const [slug, setSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    const trimmed = slug.trim();
    if (trimmed === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      await createWorkspace(trimmed);
      onCreated(trimmed);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-80">
      <label htmlFor="workspace-slug" className="mb-2 block text-xs text-gold-soft">
        故事标识（slug，将成为目录名：字母、数字、中文或连字符）
      </label>
      <input
        id="workspace-slug"
        value={slug}
        onChange={(e) => setSlug(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void submit();
        }}
        placeholder="orient-express"
        className="w-full border border-line bg-transparent px-2 py-1.5 text-sm text-paper placeholder:text-paper/30 focus:border-gold/60 focus:outline-none"
      />
      {error && <p className="mt-2 text-xs leading-relaxed text-overdue">{error}</p>}
      <Button
        variant="outline"
        onClick={() => void submit()}
        disabled={busy || slug.trim() === ""}
        className="mt-3 h-auto w-full border-gold/60 px-3 py-1.5 text-sm text-gold hover:bg-gold/15 hover:text-gold"
      >
        {busy ? "创建中…" : "打开新文章"}
      </Button>
    </div>
  );
}

export default function ArticlesPage({ onSwitch, onEnter }: Props) {
  const slug = useChatStore((s) => s.slug);
  const workspaces = useChatStore((s) => s.workspaces);
  const [showCreate, setShowCreate] = useState(false);
  // 模态删除确认：confirmSlug = 待确认的文章；输入其 slug 才亮「确认删除」。
  const [confirmSlug, setConfirmSlug] = useState<string | null>(null);
  const [confirmInput, setConfirmInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleCreate(created: string): Promise<void> {
    setShowCreate(false);
    try {
      useChatStore.getState().setWorkspaces(await fetchWorkspaces());
    } catch {
      // 列表刷新失败不阻塞切换——下次启动/新建会补齐
    }
    onSwitch(created);
    onEnter();
  }

  async function handleDelete(target: string): Promise<void> {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await deleteWorkspace(target);
      setConfirmSlug(null);
      setConfirmInput("");
      const remaining = workspaces.filter((w) => w !== target);
      useChatStore.getState().setWorkspaces(remaining);
      if (slug === target && remaining[0] !== undefined) {
        onSwitch(remaining[0]); // 删的是当前文章：切到剩余第一篇；空列表留给本页空态
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex-1 overflow-y-auto border-t border-line p-6">
      <div className="mx-auto flex max-w-xl flex-col gap-6">
        <div className="border border-line bg-ink-soft/35 p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-serif text-lg font-bold text-gold">文章</h2>
            <Button
              variant="outline"
              onClick={() => setShowCreate((v) => !v)}
              className="h-auto flex-none border-gold/60 px-3 py-1 text-xs text-gold hover:bg-gold/15 hover:text-gold"
            >
              ＋ 新建文章
            </Button>
          </div>

          {showCreate && (
            <div className="mt-4 border border-line bg-ink p-4">
              <CreateWorkspaceForm onCreated={(created) => void handleCreate(created)} />
            </div>
          )}

          {workspaces.length === 0 ? (
            <p className="mt-4 text-xs leading-relaxed text-paper/50">
              还没有任何文章。新建第一篇，开始你的悬疑连载。
            </p>
          ) : (
            <ul className="mt-4 divide-y divide-line/60">
              {workspaces.map((w) => (
                <li key={w} className="py-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <span
                      aria-hidden="true"
                      className={`flex-none ${w === slug ? "text-gold" : "text-paper/40"}`}
                    >
                      {w === slug ? "◆" : "◇"}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-paper" title={w}>
                      {w}
                      {w === slug && <span className="ml-2 text-[11px] text-gold-soft">当前</span>}
                    </span>
                    {w !== slug && (
                      <Button
                        variant="outline"
                        onClick={() => {
                          onSwitch(w);
                          onEnter();
                        }}
                        className="h-auto flex-none px-2 py-0.5 text-xs text-paper/75 hover:bg-transparent hover:text-gold"
                      >
                        进入
                      </Button>
                    )}
                    <Button asChild variant="outline" className="h-auto flex-none px-2 py-0.5 text-xs text-paper/75 hover:bg-transparent hover:text-gold">
                      <a
                        href={`/api/${encodeURIComponent(w)}/export`}
                        title="导出全量备份（zip：账本/正文/设置/会话/事件）"
                      >
                        ⇩ 导出
                      </a>
                    </Button>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="outline"
                        onClick={() => {
                          setConfirmSlug(w);
                          setConfirmInput("");
                          setError(null);
                        }}
                        className="h-auto flex-none px-2 py-0.5 text-xs text-paper/75 hover:border-overdue/60 hover:bg-transparent hover:text-overdue"
                      >
                        删除
                      </Button>
                    </AlertDialogTrigger>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <AlertDialog
            open={confirmSlug !== null}
            onOpenChange={(next) => {
              // busy 中拦截关闭请求（Radix 在 Action 点击后必发 close）：
              // 失败时错误要留在弹窗内；成功路径由 handleDelete 自行收口。
              if (!next && busy) return;
              if (!next) setConfirmSlug(null);
            }}
          >
            <AlertDialogContent>
              <AlertDialogTitle>删除文章「{confirmSlug ?? ""}」？</AlertDialogTitle>
              <AlertDialogDescription>
                删除会整目录移除该文章（账本、正文、会话），不可恢复。输入{" "}
                <span className="text-overdue">{confirmSlug ?? ""}</span> 以确认：
              </AlertDialogDescription>
              <input
                value={confirmInput}
                onChange={(e) => setConfirmInput(e.target.value)}
                placeholder={confirmSlug ?? ""}
                className="mt-2 w-full border border-line bg-transparent px-2 py-1 text-sm text-paper placeholder:text-paper/30 focus:border-overdue/60 focus:outline-none"
              />
              {error && <p className="mt-1.5 break-all text-xs text-overdue">{error}</p>}
              <AlertDialogFooter>
                <AlertDialogAction
                  disabled={confirmInput !== confirmSlug || busy}
                  onClick={() => confirmSlug !== null && void handleDelete(confirmSlug)}
                  className="border-overdue/60 text-overdue hover:bg-overdue/10 hover:text-overdue"
                >
                  {busy ? "删除中…" : "确认删除"}
                </AlertDialogAction>
                <AlertDialogCancel disabled={busy} onClick={() => setConfirmSlug(null)}>
                  取消
                </AlertDialogCancel>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          {error && (
            <p className="mt-3 text-xs leading-relaxed text-overdue">{error}</p>
          )}
        </div>
      </div>
    </main>
  );
}
