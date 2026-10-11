/**
 * 输入区（P3 T8）：发送按钮三态——空闲 = prompt（发送）、运行中 = steer（插话）
 * 与 follow_up（排队追问），另有中止按钮（仅运行中可用）。命令失败的 error
 * 文案就地显示——协议错误帧原样透传，不做人话翻译。
 */

import { useState, type KeyboardEvent } from "react";
import type { CommandType } from "../api/types";
import { Button } from "@/components/ui/button";
import { useChatStore } from "../state/store";

interface ComposerProps {
  sendCommand(type: CommandType, message: string): Promise<void>;
  abort(): Promise<void>;
}

export default function Composer({ sendCommand, abort }: ComposerProps) {
  const running = useChatStore((s) => s.running);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (type: CommandType) => {
    const message = text.trim();
    if (!message || busy) return;
    setBusy(true);
    setError(null);
    try {
      await sendCommand(type, message);
      setText("");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const onAbort = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await abort();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit(running ? "steer" : "prompt");
    }
  };

  return (
    <div className="min-w-0 border-t border-line bg-ink-soft/40 px-6 py-3">
      {error && (
        <p className="mb-2 break-all border border-overdue/50 bg-overdue/10 px-3 py-1.5 text-xs leading-relaxed text-paper">
          {error}
        </p>
      )}
      <div className="flex items-end gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          placeholder={
            running ? "主编正在写作——可插话（steer）或排队追问（follow_up）" : "给主编下达指令…"
          }
          className="min-w-0 flex-1 resize-none border border-line bg-ink px-3 py-2 text-sm text-paper placeholder:text-paper/30 focus:border-gold focus:outline-none"
        />
        <div className="flex flex-col items-end gap-2">
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={!text.trim() || busy}
              onClick={() => void submit(running ? "steer" : "prompt")}
              className="border-gold bg-gold/15 text-gold hover:bg-gold/25 hover:text-gold"
            >
              {running ? "插话" : "发送"}
            </Button>
            {running && (
              <Button
                variant="outline"
                disabled={!text.trim() || busy}
                onClick={() => void submit("follow_up")}
                className="text-gold-soft hover:border-gold/50 hover:bg-transparent hover:text-gold-soft"
              >
                追问
              </Button>
            )}
          </div>
          {running && (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void onAbort()}
              className="h-auto border-overdue/60 px-4 py-1.5 text-xs text-overdue hover:bg-overdue/10 hover:text-overdue"
            >
              中止写作
            </Button>
          )}
        </div>
      </div>
      <p className="mt-2 text-xs text-paper/30">
        Enter 发送 · Shift+Enter 换行{running ? " · 写作中：Enter 发送插话，追问会排到本轮结束后" : ""}
      </p>
    </div>
  );
}
