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
interface ModelQuota {
  model: string;
  provider: string;
  name: string;
  roles: string[];
  has_key: boolean;
  rpm: number;
  tpm: number;
  rpd: number;
  used_last_minute: number;
  remaining_today: number | null;
  requests_today: number;
  tokens_today: number;
  cache_hits_today: number;
  failures_today: number;
  status: "ok" | "busy" | "exhausted" | "no_key";
  blocked_until: string | null;
  block_reason: string | null;
}
interface UsageReport {
  daily_token_cap: number;
  tokens_today: number;
  days: UsageDay[];
  top_users_today: { username: string; requests: number; tokens: number }[];
  users: number;
  problems: number;
  submissions_today: number;
  models: ModelQuota[];
  warnings: string[];
  cache_hits_today: number;
  users_with_own_key: number;
}

const STATUS_LABEL: Record<ModelQuota["status"], string> = {
  ok: "Available",
  busy: "At per-minute limit",
  exhausted: "Out of quota",
  no_key: "No API key",
};

function ModelQuotas({ models }: { models: ModelQuota[] }) {
  return (
    <div className="card">
      <h2>AI models &amp; free quota</h2>
      <p className="muted small">
        Requests are counted per UTC day by this server. Gemini's free quota resets at midnight Pacific time, and Groq's daily
        count is reported by Groq itself. Tune pacing with <code>MODEL_LIMITS</code>.
      </p>
      <div className="table-wrap">
        <table className="table compact quota-table">
          <thead>
            <tr><th>Model</th><th>Used for</th><th>Status</th><th className="num">Today</th><th className="num">Daily limit</th><th className="num">Last min</th><th className="num">Cache hits</th><th className="num">Errors</th></tr>
          </thead>
          <tbody>
            {models.map((m) => {
              const left = m.remaining_today ?? (m.rpd ? m.rpd - m.requests_today : null);
              return (
                <tr key={m.model}>
                  <td><strong>{m.name}</strong><div className="muted small">{m.provider}</div></td>
                  <td><div className="roles">{m.roles.map((r) => <span key={r} className="tag">{r}</span>)}</div></td>
                  <td>
                    <span className={`status-dot ${m.status}`} />{STATUS_LABEL[m.status]}
                    {m.blocked_until && <div className="muted small">until {new Date(m.blocked_until).toLocaleTimeString()}</div>}
                  </td>
                  <td className="num">{fmt(m.requests_today)}</td>
                  <td className="num">{m.rpd ? <>{fmt(m.rpd)}{left !== null && <div className="muted small">{fmt(Math.max(0, left))} left</div>}</> : <span className="muted">?</span>}</td>
                  <td className="num">{m.used_last_minute}{m.rpm ? ` / ${m.rpm}` : ""}</td>
                  <td className="num">{fmt(m.cache_hits_today)}</td>
                  <td className="num">{fmt(m.failures_today)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
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
      <p className="muted">AI quota and spend, and the daily-problem queue. Days are in UTC.</p>

      {usage.error && <ErrorState message={usage.error} onRetry={usage.reload} busy={usage.loading} />}
      {!report && !usage.error && <Skeleton rows={5} />}
      {report && (
        <>
          {report.warnings.length > 0 && (
            <ul className="warning-list">
              {report.warnings.map((w) => <li key={w} className="alert warn">⚠ {w}</li>)}
            </ul>
          )}
          <div className="stats">
            <div className="stat"><span className="stat-value">{fmt(report.tokens_today)}</span><span className="stat-label">Tokens today</span></div>
            <div className="stat"><span className="stat-value">{fmt(report.submissions_today)}</span><span className="stat-label">Submissions today</span></div>
            <div className="stat"><span className="stat-value">{fmt(report.users)}</span><span className="stat-label">Users</span></div>
            <div className="stat"><span className="stat-value">{fmt(report.problems)}</span><span className="stat-label">Problems</span></div>
            <div className="stat"><span className="stat-value">{fmt(report.cache_hits_today)}</span><span className="stat-label">Calls saved by cache today</span></div>
            <div className="stat"><span className="stat-value">{fmt(report.users_with_own_key)}</span><span className="stat-label">Users with own API key</span></div>
          </div>

          <ModelQuotas models={report.models} />

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
