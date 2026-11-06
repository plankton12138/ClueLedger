/**
 * 应用外壳（v1.4）。
 *
 * local 模式直接进入工作区；server 模式先确认会话，未登录只渲染账号页面。
 * 邮箱验证使用页面内验证码，不再从 URL 读取凭证。
 */

import { useEffect } from "react";
import AuthScreen from "./components/auth/AuthScreen";
import ErrorBoundary from "./components/ErrorBoundary";
import WorkspaceShell from "./components/workspace/WorkspaceShell";
import { useSessionStore } from "./state/session";

export default function App() {
  const status = useSessionStore((state) => state.status);
  const mode = useSessionStore((state) => state.mode);
  const init = useSessionStore((state) => state.init);

  useEffect(() => {
    void init();
  }, [init]);

  if (status === "loading") {
    return (
      <main className="auth-shell">
        <section className="auth-card auth-loading" aria-live="polite">
          <span className="auth-mark" aria-hidden="true">线</span>
          <p className="auth-sub">正在连接线簿…</p>
        </section>
      </main>
    );
  }

  if (mode === "server" && status === "anonymous") {
    return (
      <ErrorBoundary label="账号">
        <AuthScreen />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary label="工作区">
      <WorkspaceShell />
    </ErrorBoundary>
  );
}
