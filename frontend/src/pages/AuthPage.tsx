import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import GoogleButton from "../components/GoogleButton";
import { allPasswordRulesPass, PasswordChecklist, PasswordInput } from "../components/PasswordInput";
import { Spinner } from "../components/ui";
import { useTheme } from "../theme";
import { useToast } from "../toast";
import { emailError, usernameError } from "../validation";

type Availability = { state: "idle" | "checking" | "ok" | "bad"; message?: string };

function useUsernameAvailability(username: string, enabled: boolean): Availability {
  const [result, setResult] = useState<Availability>({ state: "idle" });

  useEffect(() => {
    if (!enabled || !username) return setResult({ state: "idle" });
    const localError = usernameError(username);
    if (localError) return setResult({ state: "bad", message: localError });

    setResult({ state: "checking" });
    const timer = setTimeout(() => {
      api<{ available: boolean; reason: string | null }>(`/auth/username-available?username=${encodeURIComponent(username)}`)
        .then((r) => setResult(r.available ? { state: "ok", message: "Available" } : { state: "bad", message: r.reason ?? "Unavailable" }))
        .catch(() => setResult({ state: "idle" }));
    }, 400);
    return () => clearTimeout(timer);
  }, [username, enabled]);

  return result;
}

export default function AuthPage({ mode }: { mode: "login" | "register" }) {
  const isLogin = mode === "login";
  const { login, register, googleLogin, config } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const [theme] = useTheme();
  const redirectTo = (location.state as { from?: string } | null)?.from ?? "/";

  const [loginId, setLoginId] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const availability = useUsernameAvailability(username.trim(), !isLogin);

  useEffect(() => {
    setFormError(null);
    setFieldErrors({});
    setTouched({});
  }, [mode]);

  const touch = (field: string) => setTouched((t) => ({ ...t, [field]: true }));

  const clientErrors: Record<string, string | null> = isLogin
    ? {}
    : {
        username: username ? (availability.state === "bad" ? availability.message ?? null : null) : "Choose a username.",
        email: email ? emailError(email) : "Enter your email.",
        password: allPasswordRulesPass(password, username, email) ? null : "Meet all the password requirements.",
      };
  const showError = (field: string) => fieldErrors[field] || (touched[field] ? clientErrors[field] : null);
  const canSubmit = isLogin
    ? loginId.trim() && password
    : !Object.values(clientErrors).some(Boolean) && availability.state !== "checking";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setTouched({ username: true, email: true, password: true });
    if (!canSubmit) return;
    setFormError(null);
    setFieldErrors({});
    setBusy(true);
    try {
      if (isLogin) await login(loginId.trim(), password);
      else await register(username.trim(), email.trim(), password);
      toast(isLogin ? "Welcome back!" : "Account created - welcome to PromptCode!", "success");
      navigate(redirectTo, { replace: true });
    } catch (err) {
      const apiErr = err as ApiError;
      if (Object.keys(apiErr.fields ?? {}).length) setFieldErrors(apiErr.fields);
      else setFormError(apiErr.message);
    } finally {
      setBusy(false);
    }
  }

  async function onGoogle(credential: string) {
    setFormError(null);
    setBusy(true);
    try {
      const u = await googleLogin(credential);
      if (u.notice) toast(u.notice, "info");
      else toast("Signed in with Google", "success");
      navigate(redirectTo, { replace: true });
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-layout">
      <aside className="auth-hero">
        <div className="auth-hero-inner">
          <span className="brand-mark big">{"{ }"}</span>
          <h1>Get better at prompting, one test case at a time.</h1>
          <ul className="auth-points">
            <li>Real tasks with automated tests, graded by a model</li>
            <li>Hidden test cases keep your prompts honest</li>
            <li>Publish your own problems for others to solve</li>
          </ul>
        </div>
      </aside>

      <main className="auth-main">
        <form className="auth-card" onSubmit={onSubmit} noValidate>
          <h2>{isLogin ? "Welcome back" : "Create your account"}</h2>
          <p className="muted">
            {isLogin ? "New here? " : "Already have an account? "}
            <Link to={isLogin ? "/register" : "/login"} state={location.state}>
              {isLogin ? "Create an account" : "Log in"}
            </Link>
          </p>

          {config?.google_client_id && (
            <>
              <GoogleButton
                clientId={config.google_client_id}
                onCredential={onGoogle}
                text={isLogin ? "signin_with" : "signup_with"}
                theme={theme}
              />
              <div className="divider"><span>or</span></div>
            </>
          )}

          {isLogin ? (
            <div className="field">
              <label htmlFor="login">Username or email</label>
              <input id="login" value={loginId} onChange={(e) => setLoginId(e.target.value)} autoComplete="username" autoFocus required />
            </div>
          ) : (
            <>
              <div className="field">
                <label htmlFor="username">Username</label>
                <div className="input-with-status">
                  <input
                    id="username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    onBlur={() => touch("username")}
                    autoComplete="username"
                    maxLength={20}
                    autoFocus
                    aria-invalid={Boolean(showError("username"))}
                  />
                  <span className="input-status">
                    {availability.state === "checking" && <Spinner small />}
                    {availability.state === "ok" && <span className="ok-text">✓</span>}
                  </span>
                </div>
                {showError("username") ? (
                  <p className="field-error">{showError("username")}</p>
                ) : availability.state === "ok" ? (
                  <p className="field-ok">{username.trim()} is available</p>
                ) : (
                  <p className="field-hint">3–20 characters, starts with a letter. Letters, numbers and _ only.</p>
                )}
              </div>
              <div className="field">
                <label htmlFor="email">Email</label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  onBlur={() => touch("email")}
                  autoComplete="email"
                  aria-invalid={Boolean(showError("email"))}
                />
                {showError("email") && <p className="field-error">{showError("email")}</p>}
              </div>
            </>
          )}

          <div className="field">
            <div className="label-row">
              <label htmlFor="password">Password</label>
              {isLogin && <Link to="/forgot-password" className="small">Forgot password?</Link>}
            </div>
            <PasswordInput
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onBlur={() => touch("password")}
              autoComplete={isLogin ? "current-password" : "new-password"}
              maxLength={128}
              aria-invalid={Boolean(!isLogin && showError("password"))}
            />
            {!isLogin && (password || touched.password) && <PasswordChecklist password={password} username={username} email={email} />}
            {!isLogin && fieldErrors.password && <p className="field-error">{fieldErrors.password}</p>}
          </div>

          {formError && <div className="alert error" role="alert">{formError}</div>}

          <button className="btn primary block" disabled={busy || (!isLogin && !canSubmit && Object.keys(touched).length > 0)}>
            {busy ? <Spinner small /> : isLogin ? "Log in" : "Create account"}
          </button>

          {!isLogin && <p className="fine-print">By creating an account you agree to use PromptCode respectfully - no abusive content in prompts or problems.</p>}
        </form>
      </main>
    </div>
  );
}
