import { useEffect, useMemo, useRef } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import { DifficultyBadge } from "../components/Badges";
import { DailyCard } from "../components/Daily";
import { EmptyState, ErrorState, Skeleton } from "../components/ui";
import type { Difficulty, ProblemSummary } from "../types";
import { useApi } from "../useApi";
import { useTitle } from "../useTitle";

const DIFFICULTIES: Difficulty[] = ["easy", "medium", "hard"];
const RANK: Record<Difficulty, number> = { easy: 0, medium: 1, hard: 2 };
const STATUSES = [
  { value: "todo", label: "To do" },
  { value: "attempted", label: "Attempted" },
  { value: "solved", label: "Solved" },
];
const SORTS: { value: string; label: string; compare?: (a: ProblemSummary, b: ProblemSummary) => number }[] = [
  { value: "", label: "Sort: default" },
  { value: "newest", label: "Newest first", compare: (a, b) => b.id - a.id },
  { value: "liked", label: "Most liked", compare: (a, b) => (b.like_pct ?? -1) - (a.like_pct ?? -1) || b.ratings - a.ratings },
  { value: "acceptance", label: "Highest acceptance", compare: (a, b) => (b.acceptance ?? -1) - (a.acceptance ?? -1) },
  { value: "easiest", label: "Easiest first", compare: (a, b) => RANK[a.difficulty] - RANK[b.difficulty] },
  { value: "hardest", label: "Hardest first", compare: (a, b) => RANK[b.difficulty] - RANK[a.difficulty] },
];

function HowItWorks() {
  const steps = [
    ["1", "Read the task", "Each problem describes what the AI model must do and the exact output it must give."],
    ["2", "Write a prompt", "Write the instructions you'd give the model, then run them on the sample tests."],
    ["3", "Pass every test", "Submit to check the hidden tests too. Pass them all to solve the problem and grow your streak."],
  ];
  return (
    <section className="how" aria-label="How it works">
      {steps.map(([n, title, text]) => (
        <div key={n} className="how-step">
          <span className="how-num">{n}</span>
          <div>
            <strong>{title}</strong>
            <p className="muted">{text}</p>
          </div>
        </div>
      ))}
    </section>
  );
}

export default function ProblemList() {
  useTitle(null);
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  // Keyed on the user so solved/attempted marks refresh after login or logout.
  const { data: problems, error, loading, reload } = useApi<ProblemSummary[]>("/problems", user?.id);
  const searchRef = useRef<HTMLInputElement>(null);

  // Filters live in the address bar: Back returns to the same filtered list, and links can be shared.
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const difficulty = params.get("difficulty") ?? "";
  const tag = params.get("tag") ?? "";
  const status = params.get("status") ?? "";
  const sort = params.get("sort") ?? "";
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const filtered = Boolean(query || difficulty || tag || status);

  // "/" jumps to search, like most developer sites.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable;
      if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const all = useMemo(() => problems ?? [], [problems]);
  const allTags = useMemo(() => [...new Set(all.flatMap((p) => p.tags))].filter((t) => t !== "daily").sort(), [all]);
  const visible = useMemo(() => {
    const rows = all.filter(
      (p) =>
        (!query || p.title.toLowerCase().includes(query.toLowerCase())) &&
        (!difficulty || p.difficulty === difficulty) &&
        (!tag || p.tags.includes(tag)) &&
        (!status || (status === "solved" ? p.solved : status === "attempted" ? p.attempted && !p.solved : !p.attempted)),
    );
    const compare = SORTS.find((s) => s.value === sort)?.compare;
    return compare ? [...rows].sort(compare) : rows;
  }, [all, query, difficulty, tag, status, sort]);

  const solvedCount = all.filter((p) => p.solved).length;
  const inProgress = all.find((p) => p.attempted && !p.solved);
  const surprise = () => {
    const unsolved = all.filter((p) => !p.solved);
    const pool = unsolved.length ? unsolved : all;
    if (pool.length) navigate(`/problems/${pool[Math.floor(Math.random() * pool.length)].slug}`);
  };

  return (
    <div className="page">
      {!user && !authLoading ? (
        <>
          <section className="landing">
            <span className="daily-pill">Free · nothing to install</span>
            <h1>Learn to write prompts that work every time</h1>
            <p className="landing-sub">
              PromptCode is practice for prompt engineering: real tasks, automatic tests, and a new challenge every day.
            </p>
            <div className="landing-actions">
              <Link to="/register" className="btn primary big">Start practicing</Link>
              <a href="#problems" className="btn ghost big">Browse problems</a>
            </div>
          </section>
          <HowItWorks />
        </>
      ) : (
        <section className="hero">
          <div>
            <h1>{user ? `Welcome back, ${user.username}` : "Practice prompt engineering"}</h1>
            <p className="muted">Pick a problem, write a prompt, and make every test pass.</p>
            {problems && (
              <div className="hero-actions">
                {inProgress && (
                  <Link to={`/problems/${inProgress.slug}`} className="btn secondary">
                    Continue: {inProgress.title} →
                  </Link>
                )}
                <button className="btn ghost" onClick={surprise} disabled={!all.length}>🎲 Pick one for me</button>
              </div>
            )}
          </div>
          {user && problems && (
            <div className="hero-progress">
              <div className="ring" style={{ "--pct": all.length ? (100 * solvedCount) / all.length : 0 } as React.CSSProperties}>
                <span>{solvedCount}<small>/{all.length}</small></span>
              </div>
              <div className="hero-breakdown">
                {DIFFICULTIES.map((d) => {
                  const of = all.filter((p) => p.difficulty === d);
                  return (
                    <span key={d} className={`diff-text-${d}`}>
                      {d} <strong>{of.filter((p) => p.solved).length}/{of.length}</strong>
                    </span>
                  );
                })}
              </div>
            </div>
          )}
        </section>
      )}

      <DailyCard />

      <div className="list-head" id="problems">
        <h2>All problems</h2>
        {problems && <span className="muted small">{filtered ? `${visible.length} of ${all.length}` : `${all.length} problems`}</span>}
      </div>

      <div className="filters">
        <div className="search-box">
          <input ref={searchRef} type="search" placeholder="Search problems…" value={query} onChange={(e) => set("q", e.target.value)} aria-label="Search problems" />
          <kbd>/</kbd>
        </div>
        <div className="chips" role="group" aria-label="Difficulty">
          <button className={`chip ${!difficulty ? "on" : ""}`} onClick={() => set("difficulty", "")}>All</button>
          {DIFFICULTIES.map((d) => (
            <button key={d} className={`chip chip-${d} ${difficulty === d ? "on" : ""}`} onClick={() => set("difficulty", difficulty === d ? "" : d)}>
              {d}
            </button>
          ))}
        </div>
        {user && (
          <div className="chips" role="group" aria-label="Status">
            {STATUSES.map((s) => (
              <button key={s.value} className={`chip ${status === s.value ? "on" : ""}`} onClick={() => set("status", status === s.value ? "" : s.value)}>
                {s.label}
              </button>
            ))}
          </div>
        )}
        <select value={tag} onChange={(e) => set("tag", e.target.value)} aria-label="Tag">
          <option value="">All tags</option>
          {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <select value={sort} onChange={(e) => set("sort", e.target.value)} aria-label="Sort">
          {SORTS.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
        </select>
        {filtered && <button className="btn ghost small" onClick={() => setParams(sort ? { sort } : {}, { replace: true })}>Clear filters</button>}
      </div>

      {error && <ErrorState message={error} onRetry={reload} busy={loading} />}
      {!problems && !error && <Skeleton rows={6} />}

      {problems && visible.length === 0 && (
        <EmptyState title={filtered ? "No problems match" : "No problems yet"}>
          {filtered ? (
            <button className="btn ghost small" onClick={() => setParams({}, { replace: true })}>Clear filters</button>
          ) : user?.is_admin ? (
            <Link to="/problems/new">Create the first one</Link>
          ) : null}
        </EmptyState>
      )}

      {problems && visible.length > 0 && (
        <div className="table-wrap">
          <table className="table problems-table">
            <thead>
              <tr>
                <th className="col-status"><span className="sr-only">Status</span></th>
                <th>Title</th>
                <th>Difficulty</th>
                <th className="hide-sm">Tags</th>
                <th className="num hide-sm" title="Share of submissions that were accepted">Acceptance</th>
                <th className="num hide-sm" title="Share of solvers who liked it">Liked</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((p) => (
                <tr key={p.id} className="clickable" onClick={() => navigate(`/problems/${p.slug}`)}>
                  <td className="col-status">
                    {p.solved ? <span className="solved" title="Solved">✓</span> : p.attempted ? <span className="attempted" title="Attempted">◐</span> : <span className="todo" aria-hidden>○</span>}
                  </td>
                  <td>
                    <Link className="row-link" to={`/problems/${p.slug}`} onClick={(e) => e.stopPropagation()}>{p.title}</Link>
                    {p.is_daily && <span className="row-icon" title="Daily challenge">📅</span>}
                    {p.has_hints && <span className="row-icon" title="Has hints">💡</span>}
                  </td>
                  <td><DifficultyBadge difficulty={p.difficulty} /></td>
                  <td className="hide-sm">
                    {p.tags.filter((t) => t !== "daily").map((t) => (
                      <button key={t} className="tag tag-btn" onClick={(e) => { e.stopPropagation(); set("tag", t); }}>{t}</button>
                    ))}
                  </td>
                  <td className="num hide-sm">{p.acceptance === null ? <span className="muted">–</span> : `${p.acceptance}%`}</td>
                  <td className="num hide-sm">{p.like_pct === null ? <span className="muted">–</span> : `👍 ${p.like_pct}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
