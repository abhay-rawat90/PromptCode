import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api, apiStream, ApiError } from "../api";
import { useAuth } from "../auth";
import { DifficultyBadge, StatusText } from "../components/Badges";
import ResultsPanel from "../components/ResultsPanel";
import { formatDay } from "../components/Daily";
import { Avatar, Celebration, ConfirmDialog, EmptyState, ErrorState, Skeleton, Spinner } from "../components/ui";
import { useToast } from "../toast";
import {
  CHECK_LABELS,
  type DifficultyVote,
  type ProblemDetail,
  type RatingSummary,
  type ReportReason,
  type RunProgress,
  type RunResult,
  type Solutions,
  type Submission,
  type SubmissionDetail,
} from "../types";
import { useTitle } from "../useTitle";

// The editor library is only needed here, so it loads with this page, not with the whole site.
const PromptEditor = lazy(() => import("../components/PromptEditor"));

const MAX_PROMPT = 8000;
const HISTORY_SIZE = 10;
const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl";

// ---------- browser storage: the current draft, and the last few versions that were run ----------

interface Version {
  at: number;
  prompt: string;
}

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage full or blocked: drafts just aren't kept */
  }
}

const draftKey = (slug: string) => `promptcode.draft.${slug}`;
const historyKey = (slug: string) => `promptcode.history.${slug}`;

function loadDraft(slug: string): string | null {
  try {
    return localStorage.getItem(draftKey(slug)); // stored as plain text (older versions did too)
  } catch {
    return null;
  }
}

function saveDraft(slug: string, prompt: string) {
  try {
    localStorage.setItem(draftKey(slug), prompt);
  } catch {
    /* ignore */
  }
}

function remember(slug: string, prompt: string): Version[] {
  const versions = read<Version[]>(historyKey(slug), []);
  if (versions[0]?.prompt === prompt) return versions;
  const next = [{ at: Date.now(), prompt }, ...versions.filter((v) => v.prompt !== prompt)].slice(0, HISTORY_SIZE);
  write(historyKey(slug), next);
  return next;
}

// ---------- small pieces ----------

type Tab = "description" | "solutions" | "submissions";
type Kind = "run" | "submit";

function DailyBanner({ day }: { day: string }) {
  const isToday = day === new Date().toISOString().slice(0, 10);
  return (
    <div className={`daily-banner ${isToday ? "today" : ""}`}>
      <span aria-hidden>📅</span>
      {isToday ? (
        <span><strong>Today's daily challenge.</strong> Solve it before midnight UTC to extend your streak.</span>
      ) : (
        <span>Daily challenge from {formatDay(day, { month: "long", day: "numeric", year: "numeric" })}. <Link to="/daily">See all dailies</Link></span>
      )}
    </div>
  );
}

interface Progress {
  done: number;
  total: number;
  waiting: { seconds: number; since: number } | null;
}

/** Live status while tests run: a progress bar, and a countdown if the tests are queued. */
function RunningPanel({ kind, progress }: { kind: Kind; progress: Progress }) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!progress.waiting) return;
    const id = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [progress.waiting]);

  const { done, total, waiting } = progress;
  const left = waiting ? Math.max(0, Math.ceil(waiting.seconds - (Date.now() - waiting.since) / 1000)) : 0;
  return (
    <div className="running-panel" role="status" aria-live="polite">
      <div className="running">
        <Spinner />
        <span>
          {total ? `${kind === "submit" ? "Testing" : "Running"} ${Math.min(done + 1, total)} of ${total}…` : "Starting…"}
          {kind === "submit" && <span className="muted small"> Stops at the first failing test.</span>}
        </span>
      </div>
      <div className="progress"><div className="progress-fill diff-bg-easy" style={{ width: `${total ? (100 * done) / total : 0}%` }} /></div>
      {waiting && left > 0 && (
        <p className="waiting-note">⏳ It's busy right now. Your tests are queued and will continue in about {left}s.</p>
      )}
    </div>
  );
}

const REPORT_REASONS: { value: ReportReason; label: string }[] = [
  { value: "wrong_tests", label: "A test is wrong or impossible to pass" },
  { value: "unclear", label: "The description is unclear" },
  { value: "other", label: "Something else" },
];

function ReportDialog({ slug, open, onClose }: { slug: string; open: boolean; onClose: () => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const [reason, setReason] = useState<ReportReason>("wrong_tests");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function send() {
    setBusy(true);
    try {
      const res = await api<{ detail: string }>(`/problems/${slug}/report`, { method: "POST", body: { reason, note } });
      toast(res.detail, "success");
      setNote("");
      onClose();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog open={open} title="Report this problem" confirmLabel="Send report" busy={busy} onConfirm={send} onCancel={onClose}>
      {user && !user.email_verified ? (
        <p>Please verify your email first (see <Link to="/settings">Settings</Link>), then you can report problems.</p>
      ) : (
        <div className="report-form">
          {REPORT_REASONS.map((r) => (
            <label key={r.value} className="radio-row">
              <input type="radio" name="reason" checked={reason === r.value} onChange={() => setReason(r.value)} /> {r.label}
            </label>
          ))}
          <textarea rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="What's wrong? e.g. 'Test 2 expects Paris but the input is about Rome.'" aria-label="Details" />
        </div>
      )}
    </ConfirmDialog>
  );
}

/** Hints open one at a time, gentlest first. */
function Hints({ problem, onOpened }: { problem: ProblemDetail; onOpened: (hints: string[]) => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const location = useLocation();
  const [busy, setBusy] = useState(false);
  if (!problem.hint_count) return null;
  const opened = problem.hints.length;

  async function reveal() {
    setBusy(true);
    try {
      const res = await api<{ hints: string[] }>(`/problems/${problem.slug}/hints/${opened + 1}`, { method: "POST" });
      onOpened(res.hints);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="hints" aria-label="Hints">
      <h3>Hints <span className="muted small">{opened} of {problem.hint_count} shown</span></h3>
      {problem.hints.map((hint, i) => (
        <div key={i} className="hint">
          <span className="hint-num">💡 Hint {i + 1}</span>
          <p>{hint}</p>
        </div>
      ))}
      {opened < problem.hint_count &&
        (user ? (
          <button className="btn ghost small" onClick={reveal} disabled={busy}>
            {busy ? <Spinner small /> : opened === 0 ? "💡 Stuck? Show a hint" : `Show hint ${opened + 1}`}
          </button>
        ) : (
          <Link className="btn ghost small" to="/login" state={{ from: location.pathname }}>Log in to see hints</Link>
        ))}
      {opened === 0 && user && <p className="muted small hint-note">Hints never give the answer away. Using them is free.</p>}
    </section>
  );
}

const DIFFICULTY_VOTES: { value: DifficultyVote; label: string }[] = [
  { value: "easier", label: "Easier" },
  { value: "right", label: "About right" },
  { value: "harder", label: "Harder" },
];

/** Like/dislike and a difficulty vote, for people who solved the problem. */
function Rating({ problem, onRated }: { problem: ProblemDetail; onRated: (r: RatingSummary) => void }) {
  const toast = useToast();
  const mine = problem.my_rating ?? { liked: null, difficulty: null };

  async function rate(change: Partial<typeof mine>) {
    try {
      onRated(await api<RatingSummary>(`/problems/${problem.slug}/rating`, { method: "PUT", body: { ...mine, ...change } }));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  return (
    <div className="rating">
      <div className="rating-row">
        <span className="rating-label">Did you like this problem?</span>
        <button className={`chip ${mine.liked === true ? "on" : ""}`} aria-pressed={mine.liked === true} onClick={() => rate({ liked: mine.liked === true ? null : true })}>
          👍 {problem.likes}
        </button>
        <button className={`chip ${mine.liked === false ? "on" : ""}`} aria-pressed={mine.liked === false} onClick={() => rate({ liked: mine.liked === false ? null : false })}>
          👎 {problem.dislikes}
        </button>
      </div>
      <div className="rating-row">
        <span className="rating-label">For "{problem.difficulty}", it felt:</span>
        {DIFFICULTY_VOTES.map((v) => (
          <button key={v.value} className={`chip ${mine.difficulty === v.value ? "on" : ""}`} aria-pressed={mine.difficulty === v.value}
            onClick={() => rate({ difficulty: mine.difficulty === v.value ? null : v.value })}>
            {v.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/** The official solution and prompts other solvers shared. Locked until you solve the problem. */
function SolutionsTab({ problem, reloadKey, onUse }: { problem: ProblemDetail; reloadKey: number; onUse: (prompt: string) => void }) {
  const { user } = useAuth();
  const toast = useToast();
  const location = useLocation();
  const [data, setData] = useState<Solutions | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    if (!user) return;
    let current = true;
    api<Solutions>(`/problems/${problem.slug}/solutions`)
      .then((d) => current && (setData(d), setError(null)))
      .catch((e) => current && setError(e as ApiError));
    return () => {
      current = false;
    };
  }, [problem.slug, user, reloadKey, version]);

  async function act(run: () => Promise<unknown>, done?: string) {
    setBusy(true);
    try {
      await run();
      if (done) toast(done, "success");
      setVersion((v) => v + 1);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  if (!user) {
    return (
      <EmptyState title="Solutions are for solvers">
        <Link to="/login" state={{ from: location.pathname }}>Log in</Link> and solve this problem to see how others did it.
      </EmptyState>
    );
  }
  if (error?.status === 403) {
    return (
      <div className="locked-state">
        <div className="locked-icon" aria-hidden>🔒</div>
        <h3>Solve it first</h3>
        <p className="muted">The official solution and other people's prompts unlock once your own prompt is accepted.{problem.hint_count > 0 && " Stuck? The hints under the description can help."}</p>
      </div>
    );
  }
  if (error) return <ErrorState message={error.message} onRetry={() => setVersion((v) => v + 1)} />;
  if (!data) return <Skeleton rows={5} />;

  const share = (id: number, shared: boolean) =>
    act(() => api(`/submissions/${id}/share`, { method: "POST", body: { shared } }), shared ? "Your solution is now shared." : "Your solution is no longer shared.");

  return (
    <div className="solutions">
      {data.official && (
        <section>
          <div className="solution-head">
            <h3>Official solution</h3>
            <button className="btn ghost small" onClick={() => onUse(data.official!)}>Open in editor</button>
          </div>
          <pre className="solution-prompt">{data.official}</pre>
        </section>
      )}

      <section>
        <div className="solution-head">
          <h3>Community solutions <span className="muted small">{data.community.length}</span></h3>
          {data.my_shared_id ? (
            <button className="btn ghost small" disabled={busy} onClick={() => share(data.my_shared_id!, false)}>Stop sharing mine</button>
          ) : data.my_best_id ? (
            <button className="btn secondary small" disabled={busy} onClick={() => share(data.my_best_id!, true)}>Share my solution</button>
          ) : null}
        </div>
        <p className="muted small">Shortest first. Sharing is your choice: nothing of yours is shown unless you share it.</p>
        {data.community.length === 0 && <p className="muted">Nobody has shared a solution yet. Be the first.</p>}
        {data.community.map((s) => (
          <article key={s.id} className={`solution ${s.mine ? "mine" : ""}`}>
            <div className="solution-meta">
              <Link to={`/u/${s.username}`} className="user-cell"><Avatar name={s.username} url={s.avatar_url} size={22} /> {s.username}</Link>
              {s.mine && <span className="pill-ok">Yours</span>}
              <span className="muted small">{s.tokens.toLocaleString()} tokens</span>
              <span className="solution-actions">
                <button className={`chip ${s.voted ? "on" : ""}`} disabled={busy || s.mine} aria-pressed={s.voted}
                  title={s.mine ? "You can't upvote your own solution" : s.voted ? "Remove upvote" : "Upvote"}
                  onClick={() => act(() => api(`/solutions/${s.id}/vote`, { method: "POST" }))}>
                  ▲ {s.votes}
                </button>
                <button className="btn ghost small" onClick={() => onUse(s.prompt)}>Open in editor</button>
                {user.is_admin && !s.mine && (
                  <button className="btn danger small" disabled={busy} onClick={() => act(() => api(`/admin/solutions/${s.id}/remove`, { method: "POST" }), "Solution removed.")}>Remove</button>
                )}
              </span>
            </div>
            <pre className="solution-prompt">{s.prompt}</pre>
          </article>
        ))}
      </section>
    </div>
  );
}

/** Shown after an accepted submission: what to do next. */
function SolvedCard({ problem, submissionId, onRated, onSolutions }: {
  problem: ProblemDetail;
  submissionId: number | null;
  onRated: (r: RatingSummary) => void;
  onSolutions: () => void;
}) {
  const toast = useToast();
  const [shared, setShared] = useState(false);

  async function toggleShare(next: boolean) {
    if (!submissionId) return;
    setShared(next);
    try {
      await api(`/submissions/${submissionId}/share`, { method: "POST", body: { shared: next } });
      toast(next ? "Shared in the Solutions tab." : "No longer shared.", "success");
    } catch (e) {
      setShared(!next);
      toast((e as Error).message, "error");
    }
  }

  return (
    <div className="solved-card">
      <h3>🎉 Solved!</h3>
      <Rating problem={problem} onRated={onRated} />
      {submissionId && (
        <label className="toggle share-toggle">
          <input type="checkbox" checked={shared} onChange={(e) => toggleShare(e.target.checked)} />
          <span>Share this prompt in the Solutions tab so others can learn from it</span>
        </label>
      )}
      <div className="solved-actions">
        <button className="btn ghost" onClick={onSolutions}>See solutions</button>
        {problem.next_slug ? (
          <Link className="btn primary" to={`/problems/${problem.next_slug}`}>Next problem →</Link>
        ) : (
          <Link className="btn primary" to="/">Back to problems</Link>
        )}
      </div>
    </div>
  );
}

function when(at: number): string {
  const minutes = Math.round((Date.now() - at) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)} h ago`;
  return new Date(at).toLocaleDateString();
}

function HistoryMenu({ versions, onRestore }: { versions: Version[]; onRestore: (prompt: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  if (!versions.length) return null;
  return (
    <div className="menu" ref={ref}>
      <button className="btn ghost small" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} title="Earlier versions you ran">
        History ▾
      </button>
      {open && (
        <div className="menu-panel history-panel" role="menu">
          <div className="menu-head"><strong>Versions you ran</strong><span className="muted small">Kept in this browser</span></div>
          {versions.map((v) => (
            <button key={v.at} role="menuitem" onClick={() => { onRestore(v.prompt); setOpen(false); }}>
              <span className="muted small">{when(v.at)} · {v.prompt.length} chars</span>
              <span className="history-preview">{v.prompt.trim().slice(0, 70) || "(empty)"}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- the page ----------

export default function ProblemPage() {
  const { slug = "" } = useParams();
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();

  const [problem, setProblem] = useState<ProblemDetail | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [prompt, setPrompt] = useState("");
  const [saved, setSaved] = useState(true);
  const [versions, setVersions] = useState<Version[]>([]);
  const [busy, setBusy] = useState<Kind | null>(null);
  const [result, setResult] = useState<{ data: RunResult; kind: Kind; id: number } | null>(null);
  const [tab, setTab] = useState<Tab>("description");
  const [history, setHistory] = useState<Submission[] | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [celebrate, setCelebrate] = useState(false);
  const [reporting, setReporting] = useState(false);
  const [progress, setProgress] = useState<Progress>({ done: 0, total: 0, waiting: null });
  const [split, setSplit] = useState(() => Math.min(70, Math.max(30, read("promptcode.split", 48))));
  const saveTimer = useRef<number>();
  const resultsRef = useRef<HTMLDivElement>(null);
  const workspaceRef = useRef<HTMLDivElement>(null);
  const editorSlug = useRef<string | null>(null);

  useTitle(problem?.slug === slug ? problem.title : null);

  useEffect(() => {
    let current = true;
    setLoadError(null);
    api<ProblemDetail>(`/problems/${slug}`)
      .then((p) => {
        if (!current) return;
        // Only reset the workspace when opening a different problem (not on a login refresh).
        if (editorSlug.current !== p.slug) {
          editorSlug.current = p.slug;
          setPrompt(loadDraft(slug) ?? p.starter_prompt);
          setVersions(read<Version[]>(historyKey(slug), []));
          setResult(null);
          setTab("description");
        }
        setProblem(p);
      })
      .catch((e) => current && setLoadError(e as ApiError));
    return () => {
      current = false;
    };
  }, [slug, user?.id, loadAttempt]);

  useEffect(() => {
    if (tab !== "submissions" || !user) return;
    setHistory(null);
    api<Submission[]>(`/submissions?problem=${slug}`).then(setHistory).catch(() => setHistory([]));
  }, [tab, slug, user, result]);

  const onPromptChange = useCallback(
    (value: string) => {
      setPrompt(value);
      setSaved(false);
      window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(() => {
        saveDraft(slug, value);
        setSaved(true);
      }, 500);
    },
    [slug],
  );

  const patch = (change: Partial<ProblemDetail>) => setProblem((p) => (p ? { ...p, ...change } : p));
  const canExecute = !busy && prompt.trim().length > 0 && prompt.length <= MAX_PROMPT;

  const execute = useCallback(
    async (kind: Kind) => {
      if (!user) {
        navigate("/login", { state: { from: location.pathname } });
        return;
      }
      if (!canExecute) return;
      setBusy(kind);
      setVersions(remember(slug, prompt));
      setProgress({ done: 0, total: kind === "run" ? problem?.sample_tests.length ?? 0 : 0, waiting: null });
      try {
        const data = await apiStream<RunResult, RunProgress>(`/problems/${slug}/${kind}`, { prompt }, (ev) => {
          if (ev.type === "start") setProgress((p) => ({ ...p, total: ev.total }));
          else if (ev.type === "progress") setProgress({ done: ev.done, total: ev.total, waiting: null });
          else if (ev.type === "waiting") setProgress((p) => ({ ...p, waiting: { seconds: ev.seconds, since: Date.now() } }));
        });
        setResult({ data, kind, id: Date.now() });
        if (kind === "submit" && data.status === "accepted") {
          setProblem((p) => (p ? { ...p, solved: true } : p));
          setCelebrate(true);
          setTimeout(() => setCelebrate(false), 3500);
          if (data.streak_extended && data.current_streak) {
            const streak = data.current_streak;
            setUser({ ...user, current_streak: streak, best_streak: Math.max(user.best_streak, streak) });
            toast(streak === 1 ? "🔥 Daily solved - your streak has started!" : `🔥 Daily solved - ${streak}-day streak!`, "success");
          } else {
            toast("Accepted! Every test passed.", "success");
          }
          for (const badge of data.new_badges ?? []) toast(`${badge.icon} Badge earned: ${badge.name}`, "success");
        }
        if (data.daily_remaining != null && data.daily_remaining <= 20) {
          toast(`${data.daily_remaining} test runs left in today's budget.`, "info");
        }
        setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 50);
      } catch (e) {
        toast((e as Error).message, "error");
      } finally {
        setBusy(null);
      }
    },
    [user, setUser, canExecute, slug, prompt, navigate, location.pathname, toast, problem],
  );

  // Ctrl/⌘+Enter runs samples; Ctrl/⌘+Shift+Enter submits. (Inside the editor, the editor's own
  // key binding handles it and marks the event as handled.)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.defaultPrevented) {
        e.preventDefault();
        execute(e.shiftKey ? "submit" : "run");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [execute]);

  function openInEditor(text: string, message: string) {
    setVersions(remember(slug, prompt)); // the current prompt can be restored from History
    onPromptChange(text);
    toast(message);
  }

  async function loadSubmission(id: number) {
    try {
      const s = await api<SubmissionDetail>(`/submissions/${id}`);
      openInEditor(s.prompt, "Loaded that submission's prompt into the editor.");
      setResult({ data: { ...s, submission_id: s.id }, kind: "submit", id: Date.now() });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  async function onDelete() {
    setDeleting(true);
    try {
      await api(`/problems/${slug}`, { method: "DELETE" });
      toast("Problem deleted.", "success");
      navigate("/");
    } catch (e) {
      toast((e as Error).message, "error");
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  // Drag the divider to resize the two panes; the choice is remembered.
  function startResize(e: React.PointerEvent) {
    e.preventDefault();
    const box = workspaceRef.current!.getBoundingClientRect();
    let latest = split;
    const move = (ev: PointerEvent) => {
      latest = Math.min(70, Math.max(30, (100 * (ev.clientX - box.left)) / box.width));
      setSplit(latest);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.classList.remove("resizing");
      write("promptcode.split", Math.round(latest));
    };
    document.body.classList.add("resizing");
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  if (loadError && loadError.status === 404) {
    return (
      <div className="page">
        <EmptyState title="Problem not found">It may have been removed. <Link to="/">Back to problems</Link></EmptyState>
      </div>
    );
  }
  if (!problem || problem.slug !== slug) {
    return (
      <div className="page">
        {loadError ? <ErrorState message={loadError.message} onRetry={() => setLoadAttempt((n) => n + 1)} /> : <Skeleton rows={10} />}
      </div>
    );
  }

  const accepted = result?.kind === "submit" && result.data.status === "accepted";
  const tokens = Math.ceil(prompt.length / 4);

  return (
    <div className="workspace" ref={workspaceRef} style={{ "--split": `${split}%` } as React.CSSProperties}>
      {celebrate && <Celebration />}
      <section className="pane left">
        <div className="pane-top">
          <Link to="/" className="back-link">← Problems</Link>
          <a href="#editor" className="btn secondary small jump-editor">Write prompt ↓</a>
          <span className="pager">
            {problem.prev_slug ? <Link to={`/problems/${problem.prev_slug}`} className="btn ghost small" title="Previous problem">‹ Prev</Link> : <span className="btn ghost small disabled" aria-disabled>‹ Prev</span>}
            {problem.next_slug ? <Link to={`/problems/${problem.next_slug}`} className="btn ghost small" title="Next problem">Next ›</Link> : <span className="btn ghost small disabled" aria-disabled>Next ›</span>}
          </span>
        </div>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "description"} className={tab === "description" ? "active" : ""} onClick={() => setTab("description")}>Description</button>
          <button role="tab" aria-selected={tab === "solutions"} className={tab === "solutions" ? "active" : ""} onClick={() => setTab("solutions")}>
            Solutions {!problem.solved && !user?.is_admin && <span aria-label="locked">🔒</span>}
          </button>
          {user && <button role="tab" aria-selected={tab === "submissions"} className={tab === "submissions" ? "active" : ""} onClick={() => setTab("submissions")}>My submissions</button>}
        </div>

        {tab === "description" && (
          <div className="pane-body">
            {problem.hidden_reason && (
              <div className="alert error hidden-banner">
                <strong>Hidden from users.</strong> {problem.hidden_reason} Restore it from the <Link to="/admin">admin page</Link>.
              </div>
            )}
            <h1 className="problem-title">
              {problem.title} {problem.solved && <span className="solved-pill">✓ Solved</span>}
              {problem.verified && <span className="verified-pill" title="Its reference solution passes every test, so it's definitely solvable.">✓ Verified</span>}
            </h1>
            <div className="meta">
              <DifficultyBadge difficulty={problem.difficulty} />
              {problem.tags.filter((t) => t !== "daily").map((t) => <Link key={t} to={`/?tag=${encodeURIComponent(t)}`} className="tag">{t}</Link>)}
              {problem.likes + problem.dislikes > 0 && <span className="muted small">👍 {problem.likes} · 👎 {problem.dislikes}</span>}
              {problem.author && <span className="muted small">by <Link to={`/u/${problem.author}`}>{problem.author}</Link></span>}
              {problem.can_edit && (
                <span className="meta-actions">
                  <Link to={`/problems/${slug}/edit`} className="btn ghost small">Edit</Link>
                  <button className="btn danger small" onClick={() => setConfirmDelete(true)}>Delete</button>
                </span>
              )}
            </div>

            {problem.daily_day && <DailyBanner day={problem.daily_day} />}

            <div className="markdown"><Markdown>{problem.description}</Markdown></div>

            <h3>Sample tests</h3>
            {problem.sample_tests.map((t) => (
              <div key={t.id} className="sample">
                <div className="sample-head">
                  Case {t.position} <span className="tag">{CHECK_LABELS[t.check_type]}</span>
                </div>
                <label>Input</label>
                <pre>{t.input || <span className="muted">(empty)</span>}</pre>
                <label>Expected</label>
                <pre>{t.expected}</pre>
              </div>
            ))}
            {problem.hidden_count > 0 && (
              <p className="muted small">🔒 {problem.hidden_count} hidden test{problem.hidden_count > 1 ? "s" : ""} also run when you submit.</p>
            )}

            <Hints problem={problem} onOpened={(hints) => patch({ hints })} />

            {problem.solved && (
              <section>
                <h3>Your rating</h3>
                <Rating problem={problem} onRated={patch} />
              </section>
            )}

            {user && (
              <p className="report-line">
                <button className="link-btn" onClick={() => setReporting(true)}>⚑ Report an issue with this problem</button>
              </p>
            )}
          </div>
        )}

        {tab === "solutions" && (
          <div className="pane-body">
            <SolutionsTab problem={problem} reloadKey={result?.id ?? 0}
              onUse={(text) => openInEditor(text, "Opened in the editor. Your previous prompt is in History.")} />
          </div>
        )}

        {tab === "submissions" && (
          <div className="pane-body">
            {!history && <Skeleton rows={4} />}
            {history?.length === 0 && <EmptyState title="No submissions yet">Submit a prompt and it will show up here.</EmptyState>}
            {history && history.length > 0 && (
              <table className="table compact">
                <thead>
                  <tr><th>Status</th><th>Passed</th><th className="num">Tokens</th><th>When</th><th></th></tr>
                </thead>
                <tbody>
                  {history.map((s) => (
                    <tr key={s.id}>
                      <td><StatusText status={s.status} /> {s.shared && <span className="pill-ok" title="Shown in the Solutions tab">Shared</span>}</td>
                      <td>{s.passed}/{s.total}</td>
                      <td className="num">{(s.input_tokens + s.output_tokens).toLocaleString()}</td>
                      <td className="muted small">{new Date(s.created_at).toLocaleString()}</td>
                      <td><button className="btn ghost small" onClick={() => loadSubmission(s.id)}>Open</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </section>

      <div className="divider-handle" onPointerDown={startResize} role="separator" aria-orientation="vertical" aria-label="Resize panels" />

      <section className="pane right" id="editor">
        <div className="editor-head">
          <span>Your prompt</span>
          <span className="editor-meta">
            <span className="muted small hide-sm">{saved ? "Draft saved" : "Saving…"}</span>
            <span className={`small ${prompt.length > MAX_PROMPT ? "error-text" : "muted"}`} title="Tokens are estimated at about 4 characters each">
              {prompt.length.toLocaleString()} / {MAX_PROMPT.toLocaleString()} chars · ~{tokens.toLocaleString()} tokens
            </span>
            <HistoryMenu versions={versions} onRestore={(text) => openInEditor(text, "Restored that version.")} />
          </span>
        </div>
        <div className="editor-frame">
          <Suspense fallback={<div className="editor-loading"><Spinner /></div>}>
            <PromptEditor
              key={slug}
              value={prompt}
              onChange={onPromptChange}
              onRun={() => execute("run")}
              onSubmit={() => execute("submit")}
              placeholder={"Write your prompt here.\n\nUse {{input}} where the test input should go, or leave it out to send your prompt as the system instruction and the test input as the user message."}
            />
          </Suspense>
        </div>
        <div className="editor-actions">
          <span className="mode-hint">
            {prompt.includes("{{input}}") ? "{{input}} will be replaced by each test input" : "Sent as system instruction · test input as user message"}
          </span>
          <button className="btn ghost" onClick={() => openInEditor(problem.starter_prompt, "Reset to the starter prompt. Your previous prompt is in History.")} disabled={!!busy} title="Restore the starter prompt">Reset</button>
          <button className="btn secondary" onClick={() => execute("run")} disabled={!canExecute} title={`Run the sample tests (${MOD}+Enter)`}>
            {busy === "run" ? <><Spinner small /> Running</> : "Run"}
          </button>
          <button className="btn primary" onClick={() => execute("submit")} disabled={!canExecute} title={`Check every test (${MOD}+Shift+Enter)`}>
            {busy === "submit" ? <><Spinner small /> Submitting</> : "Submit"}
          </button>
        </div>
        <p className="shortcut-hint muted small hide-sm">
          <kbd>{MOD}</kbd>+<kbd>Enter</kbd> run samples · <kbd>{MOD}</kbd>+<kbd>Shift</kbd>+<kbd>Enter</kbd> submit
        </p>
        {!user && (
          <div className="alert info">
            <Link to="/login" state={{ from: location.pathname }}>Log in</Link> or <Link to="/register" state={{ from: location.pathname }}>sign up</Link> to run and submit prompts.
          </div>
        )}
        <div ref={resultsRef}>
          {busy && <RunningPanel kind={busy} progress={progress} />}
          {!busy && accepted && (
            <SolvedCard key={`solved-${result!.id}`} problem={problem} submissionId={result!.data.submission_id} onRated={patch} onSolutions={() => setTab("solutions")} />
          )}
          {result && !busy && <ResultsPanel key={result.id} result={result.data} kind={result.kind} />}
          {!result && !busy && user && (
            <p className="muted small results-empty">Run checks the sample tests. Submit checks the hidden tests too and counts as an attempt.</p>
          )}
        </div>
      </section>

      <ReportDialog slug={slug} open={reporting} onClose={() => setReporting(false)} />
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete "${problem.title}"?`}
        confirmLabel="Delete problem"
        danger
        busy={deleting}
        onConfirm={onDelete}
        onCancel={() => setConfirmDelete(false)}
      >
        This also deletes every submission to it. This can't be undone.
      </ConfirmDialog>
    </div>
  );
}
