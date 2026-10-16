import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import type { DailyToday } from "../types";
import { useApi } from "../useApi";
import { DifficultyBadge } from "./Badges";
import { Spinner } from "./ui";

/** Daily problems change at midnight UTC; show dates in UTC so "today" matches the server. */
export function formatDay(day: string, opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" }) {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, { ...opts, timeZone: "UTC" });
}

function useCountdown(target: string | undefined): { label: string; done: boolean } {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  if (!target) return { label: "", done: false };
  const ms = Math.max(0, new Date(target).getTime() - now);
  const s = Math.floor(ms / 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return { label: `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`, done: ms === 0 };
}

export function StreakChip({ streak, title }: { streak: number; title?: string }) {
  return (
    <span className={`streak-chip ${streak > 0 ? "on" : ""}`} title={title ?? `${streak}-day daily streak`}>
      <span aria-hidden>🔥</span> {streak}
    </span>
  );
}

export function useDailyToday() {
  const { user } = useAuth();
  return useApi<DailyToday>("/daily/today", user?.id);
}

export function DailyCard() {
  const { user } = useAuth();
  const { data, reload } = useDailyToday();
  const countdown = useCountdown(data?.next_reset);

  // New day: fetch the new problem. While it's being prepared, check back periodically.
  useEffect(() => {
    if (countdown.done) {
      const id = window.setTimeout(reload, 1500);
      return () => window.clearTimeout(id);
    }
  }, [countdown.done, reload]);
  useEffect(() => {
    if (data?.status !== "preparing" && data?.status !== "unavailable") return;
    const id = window.setInterval(reload, data.status === "preparing" ? 15_000 : 5 * 60_000);
    return () => window.clearInterval(id);
  }, [data?.status, reload]);

  if (!data || data.status === "disabled") return null;

  return (
    <section className={`daily-card ${data.solved_today ? "solved" : ""}`}>
      <div className="daily-main">
        <div className="daily-eyebrow">
          <span className="daily-pill">Daily challenge</span>
          <span className="muted small">{formatDay(data.day, { weekday: "long", month: "long", day: "numeric" })}</span>
        </div>
        {data.status === "ready" && data.problem ? (
          <>
            <h2 className="daily-title">
              <Link to={`/problems/${data.problem.slug}`}>{data.problem.title}</Link>
            </h2>
            <div className="meta">
              <DifficultyBadge difficulty={data.problem.difficulty} />
              {data.problem.tags.filter((t) => t !== "daily").map((t) => <span key={t} className="tag">{t}</span>)}
            </div>
          </>
        ) : data.status === "unavailable" ? (
          <div className="daily-preparing">
            Today's problem isn't ready yet - we'll keep trying. Meanwhile, <Link to="/daily">past dailies</Link> are
            open to practice.
          </div>
        ) : (
          <div className="daily-preparing">
            <Spinner small /> Today's problem is being prepared - it'll appear here in a moment.
          </div>
        )}
      </div>

      <div className="daily-side">
        {user && (
          <div className="daily-streak">
            <StreakChip streak={data.current_streak} />
            <span className="muted small">best {data.best_streak}</span>
          </div>
        )}
        {data.status === "ready" && data.problem && (
          data.solved_today ? (
            <span className="daily-done">✓ Solved today</span>
          ) : (
            <Link to={`/problems/${data.problem.slug}`} className="btn primary">
              {user ? "Solve today's problem" : "Try it"}
            </Link>
          )
        )}
        <span className="muted small daily-countdown" title="Daily problems change at midnight UTC">
          Next problem in <strong>{countdown.label}</strong>
        </span>
        <Link to="/daily" className="small">Past dailies →</Link>
      </div>
    </section>
  );
}
