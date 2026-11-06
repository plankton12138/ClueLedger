/**
 * 账号页面（v1.4）：邮箱 + 6 位验证码，不使用邮件链接。
 *
 * 注册后停留在验证码页面；找回密码同样先收验证码，再设置新密码。
 * 页面不依赖路由库，也不从 URL 读取凭证。
 */

import { useState, type FormEvent } from "react";
import { AuthApiError, resetPassword, verifyEmail } from "../../api/auth";
import { useSessionStore } from "../../state/session";
import { Button } from "../ui/Button";

type View =
  | "login"
  | "register"
  | "verify"
  | "verified"
  | "forgot"
  | "reset"
  | "reset-done";

function isEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

function isCode(value: string): boolean {
  return /^\d{6}$/.test(value.trim());
}

export default function AuthScreen() {
  const login = useSessionStore((state) => state.login);
  const register = useSessionStore((state) => state.register);
  const resendVerification = useSessionStore((state) => state.resendVerification);
  const forgotPassword = useSessionStore((state) => state.forgotPassword);
  const busy = useSessionStore((state) => state.busy);
  const error = useSessionStore((state) => state.error);
  const clearError = useSessionStore((state) => state.clearError);
  const [view, setView] = useState<View>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [localError, setLocalError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function go(next: View) {
    setLocalError("");
    setNotice("");
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
    if (ok) {
      setCode("");
      setNotice(`验证码已发送到 ${email.trim()}，10 分钟内有效。`);
      setView("verify");
    }
  }

  async function resendCode() {
    setLocalError("");
    setNotice("");
    const ok = await resendVerification(email.trim());
    if (ok) setNotice("新的验证码已发送，请查收。");
  }

  async function submitVerify(event?: FormEvent) {
    event?.preventDefault();
    setLocalError("");
    if (!isCode(code)) {
      setLocalError("请输入邮件中的 6 位数字验证码。");
      return;
    }
    setSubmitting(true);
    try {
      await verifyEmail(email.trim(), code.trim());
      setNotice("");
      setView("verified");
    } catch (reason) {
      setLocalError(reason instanceof AuthApiError ? reason.message : "验证失败，请重新获取验证码。");
    } finally {
      setSubmitting(false);
    }
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
    if (ok) {
      setCode("");
      setPassword("");
      setConfirm("");
      setNotice("如果该邮箱已注册，验证码会在几分钟内送达。");
      setView("reset");
    }
  }

  async function submitReset(event: FormEvent) {
    event.preventDefault();
    setLocalError("");
    if (!isCode(code)) {
      setLocalError("请输入邮件中的 6 位数字验证码。");
      return;
    }
    if (password.length < 8) {
      setLocalError("密码至少需要 8 个字符。");
      return;
    }
    if (password !== confirm) {
      setLocalError("两次输入的密码不一致。");
      return;
    }
    setSubmitting(true);
    try {
      await resetPassword(email.trim(), code.trim(), password);
      setPassword("");
      setConfirm("");
      setCode("");
      setView("reset-done");
    } catch (reason) {
      setLocalError(reason instanceof AuthApiError ? reason.message : "重置失败，请重新获取验证码。");
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
            <p className="auth-sub">验证邮箱后会发送 6 位验证码，不需要点击链接。</p>
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
              <Button type="submit" variant="primary" busy={working} disabled={working}>发送验证码</Button>
            </form>
            <div className="auth-links">
              <button type="button" onClick={() => go("login")}>已有账号，去登录</button>
            </div>
          </>
        )}

        {view === "verify" && (
          <>
            <h1 id="auth-title" className="auth-title">输入验证码</h1>
            <p className="auth-sub">验证码已发送到 <strong>{email}</strong>，10 分钟内有效。</p>
            <form className="auth-form" onSubmit={(event) => void submitVerify(event)}>
              <label className="auth-field">6 位验证码
                <input className="ui-input" inputMode="numeric" autoComplete="one-time-code"
                  maxLength={6} pattern="\d{6}" value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  required />
              </label>
              {notice && <p className="ui-field-hint">{notice}</p>}
              {shownError && <p className="auth-error" role="alert">{shownError}</p>}
              <Button type="submit" variant="primary" busy={working} disabled={working}>完成验证</Button>
            </form>
            <div className="auth-links">
              <button type="button" disabled={working} onClick={() => void resendCode()}>重新发送验证码</button>
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

        {view === "forgot" && (
          <>
            <h1 id="auth-title" className="auth-title">找回密码</h1>
            <p className="auth-sub">填写注册邮箱，我们会发送 6 位重置验证码。</p>
            <form className="auth-form" onSubmit={(event) => void submitForgot(event)}>
              <label className="auth-field">邮箱
                <input className="ui-input" type="email" autoComplete="email" value={email}
                  onChange={(event) => setEmail(event.target.value)} required />
              </label>
              {shownError && <p className="auth-error" role="alert">{shownError}</p>}
              <Button type="submit" variant="primary" busy={working} disabled={working}>发送重置验证码</Button>
            </form>
            <div className="auth-links">
              <button type="button" onClick={() => go("login")}>返回登录</button>
            </div>
          </>
        )}

        {view === "reset" && (
          <>
            <h1 id="auth-title" className="auth-title">重置密码</h1>
            <p className="auth-sub">验证码已发送到 <strong>{email}</strong>，10 分钟内有效。</p>
            <form className="auth-form" onSubmit={(event) => void submitReset(event)}>
              <label className="auth-field">6 位验证码
                <input className="ui-input" inputMode="numeric" autoComplete="one-time-code"
                  maxLength={6} pattern="\d{6}" value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  required />
              </label>
              <label className="auth-field">新密码
                <input className="ui-input" type="password" autoComplete="new-password" minLength={8}
                  value={password} onChange={(event) => setPassword(event.target.value)} required />
              </label>
              <label className="auth-field">再次输入
                <input className="ui-input" type="password" autoComplete="new-password"
                  value={confirm} onChange={(event) => setConfirm(event.target.value)} required />
              </label>
              {notice && <p className="ui-field-hint">{notice}</p>}
              {shownError && <p className="auth-error" role="alert">{shownError}</p>}
              <Button type="submit" variant="primary" busy={working} disabled={working}>保存新密码</Button>
            </form>
            <div className="auth-links">
              <button type="button" onClick={() => go("forgot")}>重新获取验证码</button>
              <button type="button" onClick={() => go("login")}>返回登录</button>
            </div>
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
