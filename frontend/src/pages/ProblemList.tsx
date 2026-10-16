import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { DifficultyBadge } from "../components/Badges";
import { DailyCard } from "../components/Daily";
import { EmptyState, ErrorState, Skeleton } from "../components/ui";
import type { ProblemSummary } from "../types";
import { useApi } from "../useApi";

export default function ProblemList() {
  const { user } = useAuth();
  // Keyed on the user so solved/attempted marks refresh after login or logout.
  const { data: problems, error, loading, reload } = useApi<ProblemSummary[]>("/problems", user?.id);
  const [query, setQuery] = useState("");
  const [difficulty, setDifficulty] = useState("");
  const [tag, setTag] = useState("");
  const [status, setStatus] = useState("");

  const allTags = useMemo(() => [...new Set((problems ?? []).flatMap((p) => p.tags))].sort(), [problems]);

  const visible = (problems ?? []).filter(
    (p) =>
      (!query || p.title.toLowerCase().includes(query.toLowerCase())) &&
      (!difficulty || p.difficulty === difficulty) &&
      (!tag || p.tags.includes(tag)) &&
      (!status || (status === "solved" ? p.solved : status === "attempted" ? p.attempted && !p.solved : !p.attempted)),
  );
  const solvedCount = (problems ?? []).filter((p) => p.solved).length;
  const filtered = query || difficulty || tag || status;

  return (
    <div className="page">
      <section className="hero">
        <div>
          <h1>Practice prompt engineering</h1>
          <p className="muted">Pick a problem, write a prompt, and make every test case pass.</p>
        </div>
        {user && problems && (
          <div className="hero-progress">
            <div className="ring" style={{ "--pct": problems.length ? (100 * solvedCount) / problems.length : 0 } as React.CSSProperties}>
              <span>{solvedCount}<small>/{problems.length}</small></span>
            </div>
            <span className="muted small">solved</span>
          </div>
        )}
        {!user && (
          <div className="hero-cta">
            <Link to="/register" className="btn primary">Start practicing - it's free</Link>
          </div>
        )}
      </section>

      <DailyCard />

      <div className="filters">
        <input type="search" placeholder="Search problems…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search problems" />
        <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)} aria-label="Difficulty">
          <option value="">All difficulties</option>
          <option value="easy">Easy</option>
          <option value="medium">Medium</option>
          <option value="hard">Hard</option>
        </select>
        <select value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Tag">
          <option value="">All tags</option>
          {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        {user && (
          <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
            <option value="">Any status</option>
            <option value="todo">To do</option>
            <option value="attempted">Attempted</option>
            <option value="solved">Solved</option>
          </select>
        )}
        {filtered && <button className="btn ghost small" onClick={() => { setQuery(""); setDifficulty(""); setTag(""); setStatus(""); }}>Clear</button>}
      </div>

      {error && <ErrorState message={error} onRetry={reload} busy={loading} />}
      {!problems && !error && <Skeleton rows={6} />}

      {problems && visible.length === 0 && (
        <EmptyState title={filtered ? "No problems match" : "No problems yet"}>
          {filtered ? "Try different filters." : user ? <Link to="/problems/new">Create the first one</Link> : null}
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
                <th className="num hide-sm">Acceptance</th>
                <th className="hide-sm">Author</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((p) => (
                <tr key={p.id}>
                  <td className="col-status">
                    {p.solved ? <span className="solved" title="Solved">✓</span> : p.attempted ? <span className="attempted" title="Attempted">◐</span> : null}
                  </td>
                  <td><Link className="row-link" to={`/problems/${p.slug}`}>{p.title}</Link></td>
                  <td><DifficultyBadge difficulty={p.difficulty} /></td>
                  <td className="hide-sm">{p.tags.map((t) => <span key={t} className="tag">{t}</span>)}</td>
                  <td className="num hide-sm">{p.acceptance === null ? "–" : `${p.acceptance}%`}</td>
                  <td className="muted hide-sm">{p.author ? <Link to={`/u/${p.author}`}>{p.author}</Link> : "PromptCode"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
