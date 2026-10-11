/**
 * 对话面板头部（P9 T6，D4）：会话选择器入口（P10 T14，D13 收编原「新会话」）。
 * 会话的列出/新建/切换/重命名/删除下沉到 SessionSelector（Popover + 确认框）；
 * 写作中按钮禁用（服务端另有 409 硬守卫）。
 * P10 T13（D12）追加「压缩上下文」按钮：同一确认模式，走同一两档压缩原语。
 */

import { useState } from "react";
import { compactSession } from "../api/rest";
import { useChatStore } from "../state/store";
import { formatContextUsage } from "./contextUsage";
import SessionSelector from "./SessionSelector";
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

export default function ChatHeader({
  slug,
  running,
  onSessionChanged,
  onCompacted,
}: {
  slug: string;
  running: boolean;
  /** 切换/新建/删除活跃会话后回调（App 触发 WS 重连，快照重放得到新会话）。 */
  onSessionChanged: () => void;
  /** 压缩成功后回调（同样重连，快照重放得到压缩后的消息序列）。 */
  onCompacted: () => void;
}) {
  const [compactOpen, setCompactOpen] = useState(false);
  const [compactBusy, setCompactBusy] = useState(false);
  const [compactError, setCompactError] = useState<string | null>(null);
  const [compactNotice, setCompactNotice] = useState<string | null>(null);
  // 上下文占用（P10 T12，D11）：get_state 初值 + 每轮 token_usage 帧刷新
  const tokenUsage = useChatStore((s) => s.tokenUsage);

  const handleCompact = async () => {
    setCompactBusy(true);
    setCompactError(null);
    setCompactNotice(null);
    try {
      const result = await compactSession(slug);
      if (result.compacted) {
        setCompactOpen(false);
        onCompacted();
      } else {
        // 合法空结果：不是错误，提示「无需压缩」并留在对话框内。
        setCompactNotice("当前上下文无需压缩。怎样算对：继续写作即可，超阈值时会自动压缩。");
      }
    } catch (exc) {
      setCompactError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setCompactBusy(false);
    }
  };

  return (
    <div className="flex flex-none items-center justify-between gap-2 border-b border-line bg-ink-soft/60 px-3 py-1.5">
      <span className="flex-none truncate text-xs tracking-widest text-gold-soft">主编对话</span>
      <SessionSelector key={slug} slug={slug} running={running} onSessionChanged={onSessionChanged} />
      {tokenUsage && (
        <span
          className="min-w-0 flex-1 truncate text-right text-[10px] tabular-nums text-paper/40"
          title={formatContextUsage(tokenUsage)}
        >
          {formatContextUsage(tokenUsage)}
        </span>
      )}
      <AlertDialog
        open={compactOpen}
        onOpenChange={(next) => {
          if (compactBusy) return;
          setCompactOpen(next);
          if (!next) {
            setCompactError(null);
            setCompactNotice(null);
          }
        }}
      >
        <AlertDialogTrigger asChild>
          <Button
            variant="outline"
            disabled={running}
            title={running ? "写作中不可压缩上下文" : "剥离旧章正文参数并生成前情摘要"}
            className="h-auto flex-none px-2 py-0.5 text-[11px] text-paper/70 hover:border-gold/60 hover:bg-transparent hover:text-gold"
          >
            压缩上下文
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogTitle>压缩上下文？</AlertDialogTitle>
          <AlertDialogDescription>
            压缩会剥离较旧章节的正文参数并生成前情摘要，以回收上下文窗口。
            <span className="text-gold-soft">正文与故事账本不受影响</span>。
            {running && <span className="text-overdue">写作中不可压缩上下文。</span>}
          </AlertDialogDescription>
          {compactError && (
            <p className="mt-1.5 break-all text-xs text-overdue">{compactError}</p>
          )}
          {compactNotice && <p className="mt-1.5 text-xs text-paper/70">{compactNotice}</p>}
          <AlertDialogFooter>
            <AlertDialogAction
              disabled={compactBusy || running}
              onClick={() => void handleCompact()}
              className="border-gold/60 text-gold hover:bg-gold/10 hover:text-gold"
            >
              {compactBusy ? "压缩中…" : "确认压缩"}
            </AlertDialogAction>
            <AlertDialogCancel disabled={compactBusy}>取消</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}