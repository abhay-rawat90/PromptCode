import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, isTransient, SESSION_EXPIRED_EVENT } from "./api";
import type { AuthConfig, User } from "./types";

interface AuthState {
  user: User | null;
  config: AuthConfig | null;
  loading: boolean;
  login: (login: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string) => Promise<void>;
  googleLogin: (credential: string) => Promise<User>;
  logout: () => Promise<void>;
  setUser: (user: User | null) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    // The session lives in an httpOnly cookie, so ask the server who we are. If the server
    // is unreachable, keep asking rather than wrongly showing the user as logged out.
    const loadMe = () =>
      api<User>("/auth/me")
        .then((u) => !cancelled && (setUser(u), setLoading(false)))
        .catch((e) => {
          if (cancelled) return;
          if (isTransient(e)) return void setTimeout(loadMe, 3000);
          setUser(null);
          setLoading(false);
        });
    const loadConfig = () =>
      api<AuthConfig>("/auth/config")
        .then((c) => !cancelled && setConfig(c))
        .catch(() => !cancelled && setTimeout(loadConfig, 5000));
    loadMe();
    loadConfig();

    const onExpired = () => setUser(null);
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => {
      cancelled = true;
      window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
    };
  }, []);

  const login = useCallback(async (login: string, password: string) => {
    setUser(await api<User>("/auth/login", { method: "POST", body: { login, password } }));
  }, []);

  const register = useCallback(async (username: string, email: string, password: string) => {
    setUser(await api<User>("/auth/register", { method: "POST", body: { username, email, password } }));
  }, []);

  const googleLogin = useCallback(async (credential: string) => {
    const u = await api<User>("/auth/google", { method: "POST", body: { credential } });
    setUser(u);
    return u;
  }, []);

  const logout = useCallback(async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, config, loading, login, register, googleLogin, logout, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
