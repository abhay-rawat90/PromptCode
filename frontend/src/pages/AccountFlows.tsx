import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { allPasswordRulesPass, PasswordChecklist, PasswordInput } from "../components/PasswordInput";
import { Spinner } from "../components/ui";
import { useToast } from "../toast";
import type { User } from "../types";
import { emailError } from "../validation";

/** Read ?token= once, then strip it from the address bar so it isn't left in history. */
function useOneTimeToken(): string | null {
  const [params] = useSearchParams();
  const token = useRef(params.get("token"));
  useEffect(() => {
    if (token.current) window.history.replaceState(null, "", window.location.pathname);
  }, []);
  return token.current;
}

function AuthShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="page narrow-flow">
      <div className="card flow-card">
        <Link to="/" className="brand flow-brand"><span className="brand-mark">{"{ }"}</span> PromptCode</Link>
        <h1>{title}</h1>
        {children}
      </div>
    </div>
  );
}

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const local = emailError(email.trim());
    if (local) return setError(local);
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ detail: string }>("/auth/forgot-password", { method: "POST", body: { email: email.trim() } });
      setSent(r.detail);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Reset your password">
      {sent ? (
        <>
          <div className="alert info">{sent}</div>
          <p className="muted small">The link expires in 1 hour. Didn't get it? Check spam, or try again in a few minutes.</p>
          <Link to="/login" className="btn ghost block">Back to log in</Link>
        </>
      ) : (
        <form className="form" onSubmit={onSubmit} noValidate>
          <p className="muted">Enter the email on your account and we'll send you a link to choose a new password.</p>
          <div className="field">
            <label htmlFor="email">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" autoFocus aria-invalid={Boolean(error)} />
            {error && <p className="field-error">{error}</p>}
          </div>
          <button className="btn primary block" disabled={busy}>{busy ? <Spinner small /> : "Send reset link"}</button>
          <p className="muted small center"><Link to="/login">Back to log in</Link></p>
        </form>
      )}
    </AuthShell>
  );
}

export function ResetPassword() {
  const token = useOneTimeToken();
  const { setUser } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const valid = allPasswordRulesPass(password) && password === confirm;

  if (!token) {
    return (
      <AuthShell title="Link missing">
        <p className="muted">This page needs the link from your reset email.</p>
        <Link to="/forgot-password" className="btn primary block">Request a new link</Link>
      </AuthShell>
    );
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) return setError("The passwords don't match.");
    setBusy(true);
    setError(null);
    try {
      setUser(await api<User>("/auth/reset-password", { method: "POST", body: { token, new_password: password } }));
      toast("Password updated - you're logged in. Other devices were signed out.", "success");
      navigate("/", { replace: true });
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell title="Choose a new password">
      <form className="form" onSubmit={onSubmit} noValidate>
        <div className="field">
          <label htmlFor="new">New password</label>
          <PasswordInput id="new" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" autoFocus maxLength={128} />
          {password && <PasswordChecklist password={password} />}
        </div>
        <div className="field">
          <label htmlFor="confirm">Confirm new password</label>
          <PasswordInput id="confirm" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" aria-invalid={Boolean(confirm && confirm !== password)} />
          {confirm && confirm !== password && <p className="field-error">Passwords don't match.</p>}
        </div>
        {error && (
          <div className="alert error">
            {error} {error.includes("expired") && <Link to="/forgot-password">Request a new link</Link>}
          </div>
        )}
        <button className="btn primary block" disabled={!valid || busy}>{busy ? <Spinner small /> : "Update password"}</button>
      </form>
    </AuthShell>
  );
}

export function VerifyEmail() {
  const token = useOneTimeToken();
  const { user, setUser } = useAuth();
  const [state, setState] = useState<{ status: "working" | "ok" | "error"; message?: string }>({ status: "working" });
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return; // StrictMode runs effects twice; the token is single-use
    ran.current = true;
    if (!token) return setState({ status: "error", message: "This page needs the link from your verification email." });
    api<{ detail: string; email: string }>("/auth/verify-email", { method: "POST", body: { token } })
      .then((r) => setState({ status: "ok", message: r.email }))
      .catch((e) => setState({ status: "error", message: (e as Error).message }));
  }, [token]);

  useEffect(() => {
    if (state.status === "ok" && user && !user.email_verified) setUser({ ...user, email_verified: true });
  }, [state.status, user, setUser]);

  return (
    <AuthShell title={state.status === "ok" ? "Email confirmed" : state.status === "error" ? "Couldn't verify" : "Verifying…"}>
      {state.status === "working" && <p className="muted"><Spinner small /> Checking your link…</p>}
      {state.status === "ok" && (
        <>
          <p className="muted"><strong>{state.message}</strong> is verified. You can now publish problems and reset your password by email.</p>
          <Link to="/" className="btn primary block">Go to problems</Link>
        </>
      )}
      {state.status === "error" && (
        <>
          <div className="alert error">{state.message}</div>
          <p className="muted small">{user ? "You can send a fresh link from Settings." : "Log in to send a fresh link from Settings."}</p>
          <Link to={user ? "/settings" : "/login"} className="btn ghost block">{user ? "Open Settings" : "Log in"}</Link>
        </>
      )}
    </AuthShell>
  );
}

export function VerifyBanner() {
  const { user } = useAuth();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [hidden, setHidden] = useState(false);
  if (!user || user.email_verified || hidden) return null;

  async function resend() {
    setBusy(true);
    try {
      toast((await api<{ detail: string }>("/auth/resend-verification", { method: "POST" })).detail, "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="verify-banner" role="status">
      <span>📧 Please confirm <strong>{user.email}</strong> - check your inbox for our link. You'll need it to publish problems or reset your password.</span>
      <span className="verify-actions">
        <button className="btn ghost small" onClick={resend} disabled={busy}>{busy ? <Spinner small /> : "Resend email"}</button>
        <button className="icon-btn small-icon" onClick={() => setHidden(true)} aria-label="Dismiss">×</button>
      </span>
    </div>
  );
}
