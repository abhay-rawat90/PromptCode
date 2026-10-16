import { useState } from "react";
import { api } from "../api";
import { DifficultyBadge } from "../components/Badges";
import { formatDay } from "../components/Daily";
import { ConfirmDialog, EmptyState, ErrorState, Skeleton } from "../components/ui";
import { useToast } from "../toast";
import type { QueueItem } from "../types";
import { useApi } from "../useApi";

interface UsageDay {
  day: string;
  requests: number;
  tokens: Record<"run" | "submit" | "daily" | "other", number>;
  total: number;
}
interface UsageReport {
  daily_token_cap: number;
  tokens_today: number;
  days: UsageDay[];
  top_users_today: { username: string; requests: number; tokens: number }[];
  users: number;
  problems: number;
  submissions_today: number;
}

const KINDS = ["run", "submit", "daily", "other"] as const;
const fmt = (n: number) => n.toLocaleString();

function UsageChart({ days }: { days: UsageDay[] }) {
  const max = Math.max(1, ...days.map((d) => d.total));
  const ordered = [...days].reverse(); // oldest -> newest, left to right
  return (
    <div className="usage-chart" role="img" aria-label="Tokens used per day">
      {ordered.map((d) => (
        <div key={d.day} className="usage-col" title={`${d.day}: ${fmt(d.total)} tokens, ${d.requests} requests`}>
          <div className="usage-stack" style={{ height: `${(100 * d.total) / max}%` }}>
            {KINDS.map((k) => d.tokens[k] > 0 && <span key={k} className={`seg seg-${k}`} style={{ flexGrow: d.tokens[k] }} />)}
          </div>
          <span className="usage-label">{formatDay(d.day, { day: "numeric" })}</span>
        </div>
      ))}
    </div>
  );
}

export default function Admin() {
  const toast = useToast();
  const usage = useApi<UsageReport>("/admin/usage?days=14");
  const queue = useApi<QueueItem[]>("/admin/daily/queue");
  const [removing, setRemoving] = useState<QueueItem | null>(null);

  async function remove() {
    if (!removing) return;
    try {
      await api(`/admin/daily/queue/${removing.id}`, { method: "DELETE" });
      toast("Removed from the queue.", "success");
      queue.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setRemoving(null);
    }
  }

  const report = usage.data;
  const capPct = report?.daily_token_cap ? Math.min(100, (100 * report.tokens_today) / report.daily_token_cap) : null;

  return (
    <div className="page">
      <h1>Admin</h1>
      <p className="muted">Gemini spend and the daily-problem queue. Days are in UTC.</p>

      {usage.error && <ErrorState message={usage.error} onRetry={usage.reload} busy={usage.loading} />}
      {!report && !usage.error && <Skeleton rows={5} />}
      {report && (
        <>
          <div className="stats">
            <div className="stat"><span className="stat-value">{fmt(report.tokens_today)}</span><span className="stat-label">Tokens today</span></div>
            <div className="stat"><span className="stat-value">{fmt(report.submissions_today)}</span><span className="stat-label">Submissions today</span></div>
            <div className="stat"><span className="stat-value">{fmt(report.users)}</span><span className="stat-label">Users</span></div>
            <div className="stat"><span className="stat-value">{fmt(report.problems)}</span><span className="stat-label">Problems</span></div>
          </div>

          <div className="card">
            <h2>Daily token budget</h2>
            {capPct === null ? (
              <p className="muted">No site-wide cap. Set <code>DAILY_TOKEN_CAP</code> to stop runs once a day's spend reaches a limit.</p>
            ) : (
              <>
                <div className="progress big"><div className={`progress-fill ${capPct > 85 ? "diff-bg-hard" : capPct > 60 ? "diff-bg-medium" : "diff-bg-easy"}`} style={{ width: `${capPct}%` }} /></div>
                <p className="muted small">{fmt(report.tokens_today)} of {fmt(report.daily_token_cap)} tokens used today ({capPct.toFixed(0)}%).</p>
              </>
            )}
          </div>

          <div className="card">
            <h2>Tokens per day</h2>
            <UsageChart days={report.days} />
            <div className="legend">
              {KINDS.map((k) => <span key={k}><i className={`seg seg-${k}`} /> {k}</span>)}
            </div>
          </div>

          <div className="card">
            <h2>Top users today</h2>
            {report.top_users_today.length === 0 ? <p className="muted">No usage yet today.</p> : (
              <table className="table compact">
                <thead><tr><th>User</th><th className="num">Requests</th><th className="num">Tokens</th></tr></thead>
                <tbody>
                  {report.top_users_today.map((u) => (
                    <tr key={u.username}><td>{u.username}</td><td className="num">{fmt(u.requests)}</td><td className="num">{fmt(u.tokens)}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      <div className="card">
        <h2>Daily problem queue</h2>
        <p className="muted small">Queued problems are used before AI generation, oldest first. Add one from <strong>Create</strong> with the "Queue as an upcoming daily problem" box.</p>
        {queue.error && <ErrorState message={queue.error} onRetry={queue.reload} busy={queue.loading} />}
        {queue.data?.length === 0 && <EmptyState title="The queue is empty">Gemini will write each day's problem.</EmptyState>}
        {queue.data && queue.data.length > 0 && (
          <table className="table compact">
            <thead><tr><th>Title</th><th>Difficulty</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {queue.data.map((q) => (
                <tr key={q.id}>
                  <td>{q.title}</td>
                  <td><DifficultyBadge difficulty={q.difficulty} /></td>
                  <td className="muted small">{q.used_on ? `Published ${formatDay(q.used_on)}` : "Waiting"}</td>
                  <td>{!q.used_on && <button className="btn danger small" onClick={() => setRemoving(q)}>Remove</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <ConfirmDialog open={removing !== null} title="Remove from queue?" confirmLabel="Remove" danger onConfirm={remove} onCancel={() => setRemoving(null)}>
        "{removing?.title}" won't be published as a daily problem.
      </ConfirmDialog>
    </div>
  );
}
