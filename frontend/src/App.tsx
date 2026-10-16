import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import { Avatar, EmptyState, Spinner } from "./components/ui";
import { StreakChip } from "./components/Daily";
import { ForgotPassword, ResetPassword, VerifyBanner, VerifyEmail } from "./pages/AccountFlows";
import Admin from "./pages/Admin";
import AuthPage from "./pages/AuthPage";
import DailyArchive from "./pages/DailyArchive";
import Leaderboard from "./pages/Leaderboard";
import ProblemForm from "./pages/ProblemForm";
import ProblemList from "./pages/ProblemList";
import ProblemPage from "./pages/ProblemPage";
import Profile from "./pages/Profile";
import Settings from "./pages/Settings";
import Submissions from "./pages/Submissions";
import { useTheme } from "./theme";
import { useToast } from "./toast";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="page center-page"><Spinner /></div>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="page center-page"><Spinner /></div>;
  if (!user?.is_admin) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function GuestOnly({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="page center-page"><Spinner /></div>;
  return user ? <Navigate to="/" replace /> : <>{children}</>;
}

function UserMenu() {
  const { user, logout } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  if (!user) return null;
  return (
    <div className="menu" ref={ref}>
      <button className="menu-trigger" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <Avatar name={user.username} url={user.avatar_url} size={30} />
      </button>
      {open && (
        <div className="menu-panel" role="menu" onClick={() => setOpen(false)}>
          <div className="menu-head">
            <strong>{user.username}</strong>
            <span className="muted small">{user.email}</span>
          </div>
          <Link role="menuitem" to={`/u/${user.username}`}>Profile</Link>
          <Link role="menuitem" to="/submissions">My submissions</Link>
          <Link role="menuitem" to="/settings">Settings</Link>
          {user.is_admin && <Link role="menuitem" to="/admin">Admin</Link>}
          <button
            role="menuitem"
            onClick={async () => {
              await logout();
              toast("You've been logged out.");
              navigate("/");
            }}
          >
            Log out
          </button>
        </div>
      )}
    </div>
  );
}

function Nav() {
  const { user, loading } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  useEffect(() => setMobileOpen(false), [location.pathname]);

  return (
    <header className="nav">
      <Link to="/" className="brand">
        <span className="brand-mark">{"{ }"}</span> PromptCode
      </Link>
      <button className="hamburger" onClick={() => setMobileOpen((o) => !o)} aria-label="Menu" aria-expanded={mobileOpen}>
        <span /><span /><span />
      </button>
      <nav className={mobileOpen ? "open" : ""}>
        <NavLink to="/" end>Problems</NavLink>
        <NavLink to="/daily">Daily</NavLink>
        <NavLink to="/leaderboard">Leaderboard</NavLink>
        {user && <NavLink to="/problems/new">Create</NavLink>}
        {!user && !loading && <NavLink to="/login" className="mobile-only">Log in</NavLink>}
      </nav>
      <div className="nav-right">
        <button className="icon-btn" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} title="Toggle theme">
          {theme === "dark" ? "☀" : "☾"}
        </button>
        {loading ? null : user ? (
          <>
            <Link to="/daily" className="nav-streak" aria-label={`Daily streak: ${user.current_streak} days`}>
              <StreakChip streak={user.current_streak} />
            </Link>
            <UserMenu />
          </>
        ) : (
          <>
            <Link to="/login" className="btn ghost small">Log in</Link>
            <Link to="/register" className="btn primary small">Sign up</Link>
          </>
        )}
      </div>
    </header>
  );
}

export default function App() {
  const location = useLocation();
  const isAuthPage = location.pathname === "/login" || location.pathname === "/register";

  return (
    <>
      <a href="#main" className="skip-link">Skip to content</a>
      {!isAuthPage && <Nav />}
      {!isAuthPage && <VerifyBanner />}
      <div id="main">
        <Routes>
          <Route path="/" element={<ProblemList />} />
          <Route path="/login" element={<GuestOnly><AuthPage mode="login" /></GuestOnly>} />
          <Route path="/register" element={<GuestOnly><AuthPage mode="register" /></GuestOnly>} />
          <Route path="/leaderboard" element={<Leaderboard />} />
          <Route path="/daily" element={<DailyArchive />} />
          <Route path="/forgot-password" element={<GuestOnly><ForgotPassword /></GuestOnly>} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/admin" element={<RequireAdmin><Admin /></RequireAdmin>} />
          <Route path="/u/:username" element={<Profile />} />
          <Route path="/problems/new" element={<RequireAuth><ProblemForm /></RequireAuth>} />
          <Route path="/problems/:slug" element={<ProblemPage />} />
          <Route path="/problems/:slug/edit" element={<RequireAuth><ProblemForm /></RequireAuth>} />
          <Route path="/submissions" element={<RequireAuth><Submissions /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><Settings /></RequireAuth>} />
          <Route
            path="*"
            element={
              <div className="page">
                <EmptyState title="Page not found">
                  That page doesn't exist. <Link to="/">Back to problems</Link>
                </EmptyState>
              </div>
            }
          />
        </Routes>
      </div>
    </>
  );
}
