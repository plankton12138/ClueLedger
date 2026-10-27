import { Button } from "../ui/Button";
/**
 * 订阅登录面板（V09-5）。
 *
 * 只呈现三件事：登录入口、授权进度、账号标识。服务地址与协议由订阅来源决定，
 * 界面上没有可填的地址或令牌；登录成功也不等于连接可用，还要选模型、保存、再测试。
 *
 * 会话是临时状态：离开表单或取消即清理，令牌不会经过这一层。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { errorMessage } from "../../api/errors";
import {
  cancelAuthSession,
  ModelApiError,
  readAuthSession,
  startAuthSession,
  type AuthSession,
} from "../../api/models";

interface Props {
  providerId: string;
  providerName: string;
  /** 已保存连接上的账号；为空表示这个连接还没有登录过。 */
  savedAccount?: string;
  /** 登录结果回传给表单：只有成功时才带会话 ID。 */
  onState(state: { sessionId: string; account: string } | null): void;
}

const POLL_MS = 1500;

export default function SubscriptionLogin({
  providerId,
  providerName,
  savedAccount,
  onState,
}: Props) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const stopped = useRef(false);

  const clearTimer = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const settle = useCallback(
    (next: AuthSession | null) => {
      setSession(next);
      const done = next?.status === "succeeded";
      onState(done && next ? { sessionId: next.id, account: next.account } : null);
    },
    [onState],
  );

  const poll = useCallback(
    async (sessionId: string) => {
      try {
        const next = await readAuthSession(sessionId);
        if (stopped.current) return;
        settle(next);
        if (next.status === "pending") {
          timer.current = window.setTimeout(() => void poll(sessionId), POLL_MS);
        }
      } catch (caught) {
        if (stopped.current) return;
        // 会话被清理（取消、超时或进程重启）时后端返回 404：当作过期，不当网络错误。
        if (caught instanceof ModelApiError && caught.code === "MODEL_AUTH_SESSION_NOT_FOUND") {
          settle(null);
          setError("这次登录已失效，请重新登录。");
          return;
        }
        setError(errorMessage(caught, "订阅登录"));
      }
    },
    [settle],
  );

  const begin = useCallback(async () => {
    clearTimer();
    setBusy(true);
    setError(null);
    settle(null);
    try {
      const started = await startAuthSession(providerId);
      if (stopped.current) {
        await cancelAuthSession(started.id).catch(() => undefined);
        return;
      }
      setSession(started);
      // 由点击触发，浏览器不会拦截；授权页在系统浏览器里完成。
      window.open(started.auth_url, "_blank", "noopener,noreferrer");
      timer.current = window.setTimeout(() => void poll(started.id), POLL_MS);
    } catch (caught) {
      setError(errorMessage(caught, "订阅登录"));
    } finally {
      setBusy(false);
    }
  }, [clearTimer, poll, providerId, settle]);

  const cancel = useCallback(async () => {
    clearTimer();
    const current = session;
    settle(null);
    if (current) {
      await cancelAuthSession(current.id).catch(() => undefined);
    }
  }, [clearTimer, session, settle]);

  useEffect(() => {
    stopped.current = false;
    return () => {
      stopped.current = true;
      clearTimer();
    };
  }, [clearTimer]);

  const pending = session?.status === "pending";
  const succeeded = session?.status === "succeeded";

  return (
    <div className="rounded border border-[#e2dbc6] bg-white/60 p-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-bold text-[#4b4737]">订阅账号</span>
        <div className="flex-1" />
        {!succeeded && (
          <Button
            variant={savedAccount ? undefined : "primary"}
            className="px-2 py-0.5 text-sm"
            disabled={busy || pending}
            onClick={() => void begin()}
          >
            {busy ? "打开授权页…" : pending ? "等待授权…" : savedAccount ? "重新登录" : "登录"}
          </Button>
        )}
        {pending && (
          <Button className="px-2 py-0.5 text-sm" onClick={() => void cancel()}>
            取消
          </Button>
        )}
        {succeeded && (
          <Button className="px-2 py-0.5 text-sm" onClick={() => void begin()}>
            重新登录
          </Button>
        )}
      </div>

      <p className="mt-1 text-sm leading-4 text-ui-muted">
        {succeeded
          ? `已登录 ${session?.account || "该账号"}，保存后仍要测试一次工具调用。`
          : pending
            ? "在浏览器里完成授权，完成后这一页会自动更新。"
            : savedAccount
              ? `当前账号 ${savedAccount}；要换账号请重新登录。`
              : `用 ${providerName} 订阅账号登录；按订阅额度计费，不消耗 API Key。`}
      </p>

      {pending && session && (
        <a
          className="mt-1 inline-block text-sm text-[#3a6b8c] underline"
          href={session.auth_url}
          target="_blank"
          rel="noreferrer"
        >
          重新打开授权页
        </a>
      )}

      {session?.status === "failed" && (
        <p className="mt-1 text-sm leading-4 text-[#8a3b3b]">
          {session.message || "授权没有完成，请重试。"}
        </p>
      )}

      {error && <p className="mt-1 text-sm leading-4 text-[#8a3b3b]">{error}</p>}
    </div>
  );
}
