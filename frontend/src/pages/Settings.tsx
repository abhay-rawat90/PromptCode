import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { allPasswordRulesPass, PasswordChecklist, PasswordInput } from "../components/PasswordInput";
import { Avatar, ConfirmDialog, Spinner } from "../components/ui";
import { useToast } from "../toast";
import type { User } from "../types";
import { usernameError } from "../validation";

function UsernameCard() {
  const { user, setUser } = useAuth();
  const toast = useToast();
  const [username, setUsername] = useState(user!.username);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const changed = username.trim() !== user!.username;
  const localError = changed ? usernameError(username.trim()) : null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (localError) return setError(localError);
    setBusy(true);
    setError(null);
    try {
      setUser(await api<User>("/auth/me/username", { method: "PATCH", body: { username: username.trim() } }));
      toast("Username updated", "success");
    } catch (err) {
      setError((err as ApiError).fields?.username ?? (err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card settings-card" onSubmit={onSubmit}>
      <h2>Profile</h2>
      <div className="profile-row">
        <Avatar name={user!.username} url={user!.avatar_url} size={56} />
        <div>
          <div><strong>{user!.username}</strong></div>
          <div className="muted small">{user!.email}</div>
          <div className="muted small">Signed in with {[user!.has_password && "password", user!.has_google && "Google"].filter(Boolean).join(" and ")}</div>
        </div>
      </div>
      <div className="field">
        <label htmlFor="username">Username</label>
        <input id="username" value={username} maxLength={20} onChange={(e) => setUsername(e.target.value)} aria-invalid={Boolean(error || localError)} />
        {error || localError ? <p className="field-error">{error || localError}</p> : <p className="field-hint">This is shown on the leaderboard and your problems.</p>}
      </div>
      <div className="card-actions">
        <button className="btn primary" disabled={!changed || busy || Boolean(localError)}>{busy ? <Spinner small /> : "Save username"}</button>
      </div>
    </form>
  );
}

function EmailCard() {
  const { user } = useAuth();
  const toast = useToast();
  const [busy, setBusy] = useState(false);

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
    <div className="card settings-card">
      <h2>Email</h2>
      <div className="email-row">
        <span>{user!.email}</span>
        {user!.email_verified ? <span className="pill-ok">✓ Verified</span> : <span className="pill-warn">Not verified</span>}
      </div>
      {!user!.email_verified && (
        <>
          <p className="muted small">Verify your email to publish problems and to reset your password if you forget it.</p>
          <div className="card-actions">
            <button className="btn primary" onClick={resend} disabled={busy}>{busy ? <Spinner small /> : "Send verification email"}</button>
          </div>
        </>
      )}
    </div>
  );
}

function PasswordCard() {
  const { user, setUser } = useAuth();
  const toast = useToast();
  const hasPassword = user!.has_password;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const valid = allPasswordRulesPass(next, user!.username, user!.email) && next === confirm && (!hasPassword || current);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (next !== confirm) return setError("The new passwords don't match.");
    setBusy(true);
    setError(null);
    try {
      setUser(await api<User>("/auth/me/password", { method: "POST", body: { current_password: current || null, new_password: next } }));
      setCurrent("");
      setNext("");
      setConfirm("");
      toast(hasPassword ? "Password changed. Other devices were signed out." : "Password set. You can now log in with it too.", "success");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card settings-card" onSubmit={onSubmit}>
      <h2>{hasPassword ? "Change password" : "Set a password"}</h2>
      {!hasPassword && <p className="muted">Your account uses Google sign-in. Add a password to also log in with your email or username.</p>}
      {hasPassword && (
        <div className="field">
          <label htmlFor="current">Current password</label>
          <PasswordInput id="current" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
        </div>
      )}
      <div className="field">
        <label htmlFor="new">New password</label>
        <PasswordInput id="new" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" maxLength={128} />
        {next && <PasswordChecklist password={next} username={user!.username} email={user!.email} />}
      </div>
      <div className="field">
        <label htmlFor="confirm">Confirm new password</label>
        <PasswordInput id="confirm" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" aria-invalid={Boolean(confirm && confirm !== next)} />
        {confirm && confirm !== next && <p className="field-error">Passwords don't match.</p>}
      </div>
      {error && <div className="alert error">{error}</div>}
      <div className="card-actions">
        <button className="btn primary" disabled={!valid || busy}>{busy ? <Spinner small /> : hasPassword ? "Change password" : "Set password"}</button>
      </div>
    </form>
  );
}

function SessionsCard() {
  const { setUser } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  async function logoutAll() {
    setBusy(true);
    try {
      await api("/auth/logout-all", { method: "POST" });
      setUser(null);
      toast("Signed out on every device.", "success");
      navigate("/login");
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
      setOpen(false);
    }
  }

  return (
    <div className="card settings-card">
      <h2>Sessions</h2>
      <p className="muted">Lost a device or used a shared computer? Sign out everywhere, including here.</p>
      <div className="card-actions">
        <button className="btn danger" onClick={() => setOpen(true)}>Sign out of all devices</button>
      </div>
      <ConfirmDialog open={open} title="Sign out everywhere?" confirmLabel="Sign out all" danger busy={busy} onConfirm={logoutAll} onCancel={() => setOpen(false)}>
        Every device - including this one - will need to log in again.
      </ConfirmDialog>
    </div>
  );
}

export default function Settings() {
  return (
    <div className="page narrow-wide">
      <h1>Settings</h1>
      <div className="stack">
        <UsernameCard />
        <EmailCard />
        <PasswordCard />
        <SessionsCard />
      </div>
    </div>
  );
}
