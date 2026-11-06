/**
 * 账号页面（v1.4 P1）：登录、注册、验证邮箱、忘记与重置密码。
 *
 * 验证与重置链接只负责打开本页；用户点击按钮后才提交凭证，避免邮件预览
 * 自动消耗一次性 token。页面不依赖路由库，hash 变化由 App 与本组件共同观察。
 */

import { useEffect, useState, type FormEvent } from "react";
import { AuthApiError, resetPassword, verifyEmail } from "../../api/auth";
import { useSessionStore } from "../../state/session";
import { Button } from "../ui/Button";

type View =
  | "login"
  | "register"
  | "register-sent"
  | "forgot"
  | "forgot-sent"
  | "verify"
  | "verified"
  | "reset"
  | "reset-done";

type AuthRoute = { kind: "verify" | "reset"; token: string };

export function authRouteFromHash(hash: string): AuthRoute | null {
  const raw = hash.replace(/^#/, "");
  if (!raw.startsWith("/")) return null;
  const [path, query = ""] = raw.split("?");
  const token = new URLSearchParams(query).get("token") ?? "";
  if (path === "/verify-email") return { kind: "verify", token };
  if (path === "/reset-password") return { kind: "reset", token };
  return null;
}

function clearAuthHash(): void {
  if (!authRouteFromHash(window.location.hash)) return;
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function isEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

export default function AuthScreen() {
  const login = useSessionStore((state) => state.login);
  const register = useSessionStore((state) => state.register);
  const forgotPassword = useSessionStore((state) => state.forgotPassword);
  const busy = useSessionStore((state) => state.busy);
  const error = useSessionStore((state) => state.error);
  const clearError = useSessionStore((state) => state.clearError);
  const [route, setRoute] = useState<AuthRoute | null>(() => authRouteFromHash(window.location.hash));
  const [view, setView] = useState<View>(route?.kind ?? "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [localError, setLocalError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    function onHashChange() {
      const next = authRouteFromHash(window.location.hash);
      setRoute(next);
      if (next) setView(next.kind);
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function go(next: View) {
    setLocalError("");
    clearError();
    setView(next);
  }

  async function submitLogin(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    if (!isEmail(email)) {
      setLocalError("请填写正确的邮箱地址。");
      return;
    }
    setSubmitting(true);
    await login(email.trim(), password);
    setSubmitting(false);
  }

  async function submitRegister(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    if (!isEmail(email)) {
      setLocalError("请填写正确的邮箱地址。");
      return;
    }
    if (password.length < 8) {
      setLocalError("密码至少需要 8 个字符。");
      return;
    }
    setSubmitting(true);
    const ok = await register(email.trim(), password);
    setSubmitting(false);
    if (ok) go("register-sent");
  }

  async function submitForgot(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    if (!isEmail(email)) {
      setLocalError("请填写正确的邮箱地址。");
      return;
    }
    setSubmitting(true);
    const ok = await forgotPassword(email.trim());
    setSubmitting(false);
    if (ok) go("forgot-sent");
  }

  async function submitVerify() {
    if (!route?.token) {
      setLocalError("链接缺少验证凭证。请重新发送验证邮件。");
      return;
    }
    setLocalError("");
    setSubmitting(true);
    try {
      await verifyEmail(route.token);
      setView("verified");
      clearAuthHash();
    } catch (reason) {
      setLocalError(reason instanceof AuthApiError ? reason.message : "验证失败，请重新发送验证邮件。");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitReset(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    if (password.length < 8) {
      setLocalError("密码至少需要 8 个字符。");
      return;
    }
    if (password !== confirm) {
      setLocalError("两次输入的密码不一致。");
      return;
    }
    if (!route?.token) {
      setLocalError("链接缺少重置凭证。请重新发送找回邮件。");
      return;
    }
    setSubmitting(true);
    try {
      await resetPassword(route.token, password);
      setPassword("");
      setConfirm("");
      setView("reset-done");
      clearAuthHash();
    } catch (reason) {
      setLocalError(reason instanceof AuthApiError ? reason.message : "重置失败，请重新发送找回邮件。");
    } finally {
      setSubmitting(false);
    }
  }

  const shownError = localError || error;
  const working = busy || submitting;

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="auth-title">
        <header className="auth-brand">
          <span className="auth-mark" aria-hidden="true">线</span>
          <div>
            <p className="auth-kicker">ClueLedger</p>
            <p className="auth-name">线簿</p>
          </div>
        </header>

        {view === "login" && (
          <>
            <h1 id="auth-title" className="auth-title">登录</h1>
            <p className="auth-sub">用注册邮箱继续你的作品。</p>
            <form className="auth-form" onSubmit={(event) => void submitLogin(event)}>
              <label className="auth-field">邮箱
                <input className="ui-input" type="email" autoComplete="email" value={email}
                  onChange={(event) => setEmail(event.target.value)} required />
              </label>
              <label className="auth-field">密码
                <input className="ui-input" type="password" autoComplete="current-password" value={password}
                  onChange={(event) => setPassword(event.target.value)} required />
              </label>
              {shownError && <p className="auth-error" role="alert">{shownError}</p>}
              <Button type="submit" variant="primary" busy={working} disabled={working}>登录</Button>
            </form>
            <div className="auth-links">
              <button type="button" onClick={() => go("register")}>注册账号</button>
              <button type="button" onClick={() => go("forgot")}>忘记密码</button>
            </div>
          </>
        )}

        {view === "register" && (
          <>
            <h1 id="auth-title" className="auth-title">注册</h1>
            <p className="auth-sub">先验证邮箱，再开始写作。第一版一个邮箱对应一个账号。</p>
            <form className="auth-form" onSubmit={(event) => void submitRegister(event)}>
              <label className="auth-field">邮箱
                <input className="ui-input" type="email" autoComplete="email" value={email}
                  onChange={(event) => setEmail(event.target.value)} required />
              </label>
              <label className="auth-field">密码
                <input className="ui-input" type="password" autoComplete="new-password" minLength={8}
                  value={password} onChange={(event) => setPassword(event.target.value)} required />
              </label>
              <p className="ui-field-hint">至少 8 个字符；不要使用其他网站已经用过的密码。</p>
              {shownError && <p className="auth-error" role="alert">{shownError}</p>}
              <Button type="submit" variant="primary" busy={working} disabled={working}>发送验证邮件</Button>
            </form>
            <div className="auth-links">
              <button type="button" onClick={() => go("login")}>已有账号，去登录</button>
            </div>
          </>
        )}

        {view === "register-sent" && (
          <>
            <h1 id="auth-title" className="auth-title">验证邮件已发送</h1>
            <p className="auth-sub">打开邮件里的链接完成验证，然后回来登录。链接 24 小时内有效。</p>
            <Button variant="primary" onClick={() => go("login")}>去登录</Button>
          </>
        )}

        {view === "forgot" && (
          <>
            <h1 id="auth-title" className="auth-title">找回密码</h1>
            <p className="auth-sub">填写注册邮箱。如果账号存在，会收到一封重置邮件。</p>
            <form className="auth-form" onSubmit={(event) => void submitForgot(event)}>
              <label className="auth-field">邮箱
                <input className="ui-input" type="email" autoComplete="email" value={email}
                  onChange={(event) => setEmail(event.target.value)} required />
              </label>
              {shownError && <p className="auth-error" role="alert">{shownError}</p>}
              <Button type="submit" variant="primary" busy={working} disabled={working}>发送重置邮件</Button>
            </form>
            <div className="auth-links">
              <button type="button" onClick={() => go("login")}>返回登录</button>
            </div>
          </>
        )}

        {view === "forgot-sent" && (
          <>
            <h1 id="auth-title" className="auth-title">重置邮件已发送</h1>
            <p className="auth-sub">如果该邮箱已注册，重置链接会在几分钟内送达。链接 1 小时内有效。</p>
            <Button variant="primary" onClick={() => go("login")}>返回登录</Button>
          </>
        )}

        {view === "verify" && (
          <>
            <h1 id="auth-title" className="auth-title">验证邮箱</h1>
            <p className="auth-sub">确认是你本人打开这个链接后，再完成验证。</p>
            {shownError && <p className="auth-error" role="alert">{shownError}</p>}
            <Button variant="primary" busy={working} disabled={working} onClick={() => void submitVerify()}>
              完成验证
            </Button>
            <div className="auth-links">
              <button type="button" onClick={() => go("login")}>返回登录</button>
            </div>
          </>
        )}

        {view === "verified" && (
          <>
            <h1 id="auth-title" className="auth-title">邮箱已验证</h1>
            <p className="auth-sub">现在可以用这个邮箱登录线簿。</p>
            <Button variant="primary" onClick={() => go("login")}>去登录</Button>
          </>
        )}

        {view === "reset" && (
          <>
            <h1 id="auth-title" className="auth-title">设置新密码</h1>
            <p className="auth-sub">设置成功后，其他设备上的登录会失效。</p>
            <form className="auth-form" onSubmit={(event) => void submitReset(event)}>
              <label className="auth-field">新密码
                <input className="ui-input" type="password" autoComplete="new-password" minLength={8}
                  value={password} onChange={(event) => setPassword(event.target.value)} required />
              </label>
              <label className="auth-field">再次输入
                <input className="ui-input" type="password" autoComplete="new-password"
                  value={confirm} onChange={(event) => setConfirm(event.target.value)} required />
              </label>
              {shownError && <p className="auth-error" role="alert">{shownError}</p>}
              <Button type="submit" variant="primary" busy={working} disabled={working}>保存新密码</Button>
            </form>
          </>
        )}

        {view === "reset-done" && (
          <>
            <h1 id="auth-title" className="auth-title">密码已重置</h1>
            <p className="auth-sub">旧会话已退出。用新密码重新登录即可继续写作。</p>
            <Button variant="primary" onClick={() => go("login")}>去登录</Button>
          </>
        )}
      </section>
    </main>
  );
}
