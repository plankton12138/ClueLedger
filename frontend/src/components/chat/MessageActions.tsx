/**
 * 复制与展开（V05-2C）。
 *
 * 复制的是该条公开回复的 Markdown 原文：不夹带思考、工具日志、耗时或按钮文字。
 * 生成中复制的是当前已收到的文字；没有公开文字时不给按钮，避免复制出一段空白。
 *
 * 剪贴板不可用或被拒绝时不显示虚假成功：把原文交回给作者手动选择。
 */

import { useEffect, useState } from "react";

import type { ChatMessage } from "../../state/chatMessages";
import { publicText } from "../../state/chatMessages";

export type CopyResult = "copied" | "failed";

/** 写入剪贴板；失败（没有 API、被拒绝、非安全上下文）一律返回 failed。 */
export async function copyPlainText(text: string): Promise<CopyResult> {
  if (!text.trim()) return "failed";
  try {
    if (!navigator.clipboard) return "failed";
    await navigator.clipboard.writeText(text);
    return "copied";
  } catch {
    return "failed";
  }
}

interface Props {
  message: ChatMessage;
  onExpand: () => void;
  /** 复制失败：交给上层打开可选原文，不显示虚假成功。 */
  onCopyFailed: () => void;
}

export default function MessageActions({ message, onExpand, onCopyFailed }: Props) {
  const [state, setState] = useState<"idle" | "copied">("idle");
  const text = publicText(message);

  useEffect(() => {
    if (state !== "copied") return;
    const timer = setTimeout(() => setState("idle"), 1600);
    return () => clearTimeout(timer);
  }, [state]);

  const copy = async () => {
    const result = await copyPlainText(text);
    if (result === "copied") {
      setState("copied");
      return;
    }
    onCopyFailed();
  };

  if (!text.trim()) return null;

  return (
    <div className="chat-actions">
      <button type="button" className="chat-action" onClick={() => void copy()}>
        {state === "copied" ? "已复制" : "复制"}
      </button>
      <button type="button" className="chat-action" onClick={onExpand}>
        展开阅读
      </button>
    </div>
  );
}
