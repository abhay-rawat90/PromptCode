import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./auth";
import { Avatar, EmptyState, Spinner } from "./components/ui";
import { StreakChip } from "./components/Daily";
import { ForgotPassword, ResetPassword, Unsubscribe, VerifyBanner, VerifyEmail } from "./pages/AccountFlows";
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

/** Which top-level section a path belongs to, so the right nav item stays lit on sub-pages. */
function sectionOf(pathname: string): "problems" | "daily" | "leaderboard" | "create" | "me" | null {
  if (pathname === "/problems/new") return "create";
  if (pathname === "/" || pathname.startsWith("/problems")) return "problems";
  if (pathname.startsWith("/daily")) return "daily";
  if (pathname.startsWith("/leaderboard")) return "leaderboard";
  if (/^\/(u\/|settings|submissions|admin)/.test(pathname)) return "me";
  return null;
}

const ICONS: Record<string, string> = {
  problems: "M4 6h16M4 12h16M4 18h10",
  daily: "M7 3v3m10-3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z",
  leaderboard: "M8 21V11m4 10V4m4 17v-7M4 21h16",
  me: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9a7 7 0 0 1 14 0",
};

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={ICONS[name]} />
    </svg>
  );
}

function UserMenu() {
  const { user, logout, config } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  if (!user) return null;
  const canCreate = user.is_admin || config?.allow_user_problems;
  return (
    <div className="menu" ref={ref}>
      <button className="menu-trigger" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} aria-label="Account menu">
        <Avatar name={user.username} url={user.avatar_url} size={30} />
      </button>
      {open && (
        <div className="menu-panel" role="menu" onClick={() => setOpen(false)}>
          <div className="menu-head">
            <strong>{user.username}</strong>
            <span className="muted small">{user.email}</span>
          </div>
          <Link role="menuitem" to={`/u/${user.username}`}>Profile &amp; badges</Link>
          <Link role="menuitem" to="/submissions">My submissions</Link>
          <Link role="menuitem" to="/settings">Settings</Link>
          {(canCreate || user.is_admin) && <div className="menu-sep" />}
          {canCreate && <Link role="menuitem" to="/problems/new">Create a problem</Link>}
          {user.is_admin && <Link role="menuitem" to="/admin">Admin</Link>}
          <div className="menu-sep" />
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
  const { user, loading, config } = useAuth();
  const [theme, toggleTheme] = useTheme();
  const section = sectionOf(useLocation().pathname);
  const item = (key: string) => (section === key ? "active" : "");

  return (
    <header className="nav">
      <Link to="/" className="brand">
        <span className="brand-mark">{"{ }"}</span> PromptCode
      </Link>
      <nav aria-label="Main">
        <Link to="/" className={item("problems")}>Problems</Link>
        <Link to="/daily" className={item("daily")}>Daily</Link>
        <Link to="/leaderboard" className={item("leaderboard")}>Leaderboard</Link>
        {user && (user.is_admin || config?.allow_user_problems) && <Link to="/problems/new" className={item("create")}>Create</Link>}
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

/** Phone navigation: the four main places, always one tap away at the bottom of the screen. */
function TabBar() {
  const { user } = useAuth();
  const section = sectionOf(useLocation().pathname);
  const tab = (key: string) => `tab ${section === key || (key === "problems" && section === "create") ? "active" : ""}`;
  return (
    <nav className="tabbar" aria-label="Main">
      <Link to="/" className={tab("problems")}><Icon name="problems" />Problems</Link>
      <Link to="/daily" className={tab("daily")}><Icon name="daily" />Daily</Link>
      <Link to="/leaderboard" className={tab("leaderboard")}><Icon name="leaderboard" />Ranks</Link>
      <Link to={user ? `/u/${user.username}` : "/login"} className={tab("me")}><Icon name="me" />{user ? "Me" : "Log in"}</Link>
    </nav>
  );
}

function Footer() {
  return (
    <footer className="footer">
      <span><span className="brand-mark">{"{ }"}</span> PromptCode · Prompt engineering practice</span>
      <span className="footer-links">
        <NavLink to="/">Problems</NavLink>
        <NavLink to="/daily">Daily</NavLink>
        <NavLink to="/leaderboard">Leaderboard</NavLink>
      </span>
    </footer>
  );
}

export default function App() {
  const { pathname } = useLocation();
  const isAuthPage = pathname === "/login" || pathname === "/register";
  // The problem workspace fills the screen, so it has no footer.
  const isWorkspace = /^\/problems\/[^/]+$/.test(pathname) && pathname !== "/problems/new";

  useEffect(() => {
    window.scrollTo(0, 0); // a new page starts at its top
  }, [pathname]);

  return (
    <>
      <a href="#main" className="skip-link">Skip to content</a>
      {!isAuthPage && <Nav />}
      {!isAuthPage && <VerifyBanner />}
      <div id="main" className={isAuthPage ? "" : "with-tabbar"}>
        <Routes>
          <Route path="/" element={<ProblemList />} />
          <Route path="/login" element={<GuestOnly><AuthPage mode="login" /></GuestOnly>} />
          <Route path="/register" element={<GuestOnly><AuthPage mode="register" /></GuestOnly>} />
          <Route path="/leaderboard" element={<Leaderboard />} />
          <Route path="/daily" element={<DailyArchive />} />
          <Route path="/forgot-password" element={<GuestOnly><ForgotPassword /></GuestOnly>} />
          <Route path="/reset-password" element={<ResetPassword />} />
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/unsubscribe" element={<Unsubscribe />} />
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
        {!isAuthPage && !isWorkspace && <Footer />}
      </div>
      {!isAuthPage && <TabBar />}
    </>
  );
}
