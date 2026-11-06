/**
 * 应用外壳（v1.4 P1）。
 *
 * local 模式直接进入工作区；server 模式先确认会话，未登录只渲染账号页面。
 * 验证与重置链接即使已登录也优先显示账号页，完成后清掉 hash 回到工作区。
 */

import { useEffect, useState } from "react";
import AuthScreen, { authRouteFromHash } from "./components/auth/AuthScreen";
import ErrorBoundary from "./components/ErrorBoundary";
import WorkspaceShell from "./components/workspace/WorkspaceShell";
import { useSessionStore } from "./state/session";

export default function App() {
  const status = useSessionStore((state) => state.status);
  const mode = useSessionStore((state) => state.mode);
  const init = useSessionStore((state) => state.init);
  const [hash, setHash] = useState(() => window.location.hash);

  useEffect(() => {
    void init();
  }, [init]);

  useEffect(() => {
    function onHashChange() {
      setHash(window.location.hash);
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  if (authRouteFromHash(hash)) {
    return (
      <ErrorBoundary label="账号">
        <AuthScreen />
      </ErrorBoundary>
    );
  }

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
