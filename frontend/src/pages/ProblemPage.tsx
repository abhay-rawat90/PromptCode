import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { DifficultyBadge, StatusText } from "../components/Badges";
import ResultsPanel from "../components/ResultsPanel";
import { formatDay } from "../components/Daily";
import { Celebration, ConfirmDialog, EmptyState, ErrorState, Skeleton, Spinner } from "../components/ui";
import { useToast } from "../toast";
import { CHECK_LABELS, type ProblemDetail, type RunResult, type Submission, type SubmissionDetail } from "../types";

const MAX_PROMPT = 8000;
const draftKey = (slug: string) => `promptcode.draft.${slug}`;
const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl";

function loadDraft(slug: string): string | null {
  try {
    return localStorage.getItem(draftKey(slug));
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

type Tab = "description" | "submissions";

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
type Kind = "run" | "submit";

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
  const [busy, setBusy] = useState<Kind | null>(null);
  const [result, setResult] = useState<{ data: RunResult; kind: Kind; id: number } | null>(null);
  const [tab, setTab] = useState<Tab>("description");
  const [history, setHistory] = useState<Submission[] | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [celebrate, setCelebrate] = useState(false);
  const saveTimer = useRef<number>();
  const resultsRef = useRef<HTMLDivElement>(null);
  const editorSlug = useRef<string | null>(null);

  useEffect(() => {
    let current = true;
    setLoadError(null);
    api<ProblemDetail>(`/problems/${slug}`)
      .then((p) => {
        if (!current) return;
        // Only reset the editor when opening a different problem (not on a login refresh).
        if (editorSlug.current !== p.slug) {
          editorSlug.current = p.slug;
          setPrompt(loadDraft(slug) ?? p.starter_prompt);
          setResult(null);
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

  function onPromptChange(value: string) {
    setPrompt(value);
    setSaved(false);
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveDraft(slug, value);
      setSaved(true);
    }, 500);
  }

  const canExecute = !busy && prompt.trim().length > 0 && prompt.length <= MAX_PROMPT;

  const execute = useCallback(
    async (kind: Kind) => {
      if (!user) {
        navigate("/login", { state: { from: location.pathname } });
        return;
      }
      if (!canExecute) return;
      setBusy(kind);
      try {
        const data = await api<RunResult>(`/problems/${slug}/${kind}`, { method: "POST", body: { prompt } });
        setResult({ data, kind, id: Date.now() });
        if (kind === "submit" && data.status === "accepted") {
          setProblem((p) => (p ? { ...p, solved: true } : p));
          setCelebrate(true);
          setTimeout(() => setCelebrate(false), 3500);
          if (data.streak_extended && data.current_streak) {
            const streak = data.current_streak;
            if (user) setUser({ ...user, current_streak: streak, best_streak: Math.max(user.best_streak, streak) });
            toast(streak === 1 ? "🔥 Daily solved - your streak has started!" : `🔥 Daily solved - ${streak}-day streak!`, "success");
          } else {
            toast("Accepted! Every test passed.", "success");
          }
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
    [user, setUser, canExecute, slug, prompt, navigate, location.pathname, toast],
  );

  // Ctrl/⌘+Enter runs samples; Ctrl/⌘+Shift+Enter submits.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        execute(e.shiftKey ? "submit" : "run");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [execute]);

  async function loadSubmission(id: number) {
    try {
      const s = await api<SubmissionDetail>(`/submissions/${id}`);
      onPromptChange(s.prompt);
      setResult({ data: { ...s, submission_id: s.id }, kind: "submit", id: Date.now() });
      toast("Loaded that submission's prompt into the editor.");
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

  if (loadError && loadError.status === 404) {
    return (
      <div className="page">
        <EmptyState title="Problem not found">It may have been deleted. <Link to="/">Back to problems</Link></EmptyState>
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

  return (
    <div className="workspace">
      {celebrate && <Celebration />}
      <section className="pane left">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "description"} className={tab === "description" ? "active" : ""} onClick={() => setTab("description")}>Description</button>
          {user && <button role="tab" aria-selected={tab === "submissions"} className={tab === "submissions" ? "active" : ""} onClick={() => setTab("submissions")}>Submissions</button>}
        </div>

        {tab === "description" ? (
          <div className="pane-body">
            <h1 className="problem-title">
              {problem.title} {problem.solved && <span className="solved-pill">✓ Solved</span>}
            </h1>
            <div className="meta">
              <DifficultyBadge difficulty={problem.difficulty} />
              {problem.tags.map((t) => <span key={t} className="tag">{t}</span>)}
              <span className="muted small">
                by {problem.author ? <Link to={`/u/${problem.author}`}>{problem.author}</Link> : "PromptCode"}
              </span>
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
          </div>
        ) : (
          <div className="pane-body">
            {!history && <Skeleton rows={4} />}
            {history?.length === 0 && <EmptyState title="No submissions yet">Submit a prompt and it will show up here.</EmptyState>}
            {history && history.length > 0 && (
              <table className="table compact">
                <thead>
                  <tr><th>Status</th><th>Passed</th><th>When</th><th></th></tr>
                </thead>
                <tbody>
                  {history.map((s) => (
                    <tr key={s.id}>
                      <td><StatusText status={s.status} /></td>
                      <td>{s.passed}/{s.total}</td>
                      <td className="muted small">{new Date(s.created_at).toLocaleString()}</td>
                      <td><button className="btn ghost small" onClick={() => loadSubmission(s.id)}>Load</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </section>

      <section className="pane right">
        <div className="editor-head">
          <span>Your prompt</span>
          <span className="editor-meta">
            <span className="muted small">{saved ? "Draft saved" : "Saving…"}</span>
            <span className={`small ${prompt.length > MAX_PROMPT ? "error-text" : "muted"}`}>{prompt.length.toLocaleString()} / {MAX_PROMPT.toLocaleString()}</span>
          </span>
        </div>
        <textarea
          className="editor"
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          spellCheck={false}
          aria-label="Your prompt"
          placeholder={"Write your prompt here.\n\nUse {{input}} where the test input should go, or leave it out to send your prompt as the system instruction and the test input as the user message."}
        />
        <div className="editor-actions">
          <span className="mode-hint">
            {prompt.includes("{{input}}") ? "{{input}} will be replaced by each test input" : "Sent as system instruction · input as user message"}
          </span>
          <button className="btn ghost" onClick={() => onPromptChange(problem.starter_prompt)} disabled={!!busy} title="Restore the starter prompt">Reset</button>
          <button className="btn secondary" onClick={() => execute("run")} disabled={!canExecute} title={`Run sample tests (${MOD}+Enter)`}>
            {busy === "run" ? <><Spinner small /> Running</> : "Run"}
          </button>
          <button className="btn primary" onClick={() => execute("submit")} disabled={!canExecute} title={`Submit all tests (${MOD}+Shift+Enter)`}>
            {busy === "submit" ? <><Spinner small /> Submitting</> : "Submit"}
          </button>
        </div>
        <p className="shortcut-hint muted small">
          <kbd>{MOD}</kbd>+<kbd>Enter</kbd> run · <kbd>{MOD}</kbd>+<kbd>Shift</kbd>+<kbd>Enter</kbd> submit
        </p>
        {!user && (
          <div className="alert info">
            <Link to="/login" state={{ from: location.pathname }}>Log in</Link> or <Link to="/register" state={{ from: location.pathname }}>sign up</Link> to run and submit prompts.
          </div>
        )}
        <div ref={resultsRef}>
          {busy && (
            <div className="running">
              <Spinner /> Calling the model for each test case…
            </div>
          )}
          {result && !busy && <ResultsPanel key={result.id} result={result.data} kind={result.kind} />}
        </div>
      </section>

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
