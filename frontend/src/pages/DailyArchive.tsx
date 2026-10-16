import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { DifficultyBadge } from "../components/Badges";
import { DailyCard, formatDay, StreakChip } from "../components/Daily";
import { EmptyState, ErrorState, Skeleton } from "../components/ui";
import type { DailyArchiveItem } from "../types";
import { useApi } from "../useApi";

function StatusIcon({ item }: { item: DailyArchiveItem }) {
  if (item.solved_on_day) return <span className="arch-status on-day" title="Solved on the day">✓</span>;
  if (item.solved) return <span className="arch-status late" title="Solved later (doesn't count toward streaks)">✓</span>;
  return <span className="arch-status open" title="Not solved yet">○</span>;
}

export default function DailyArchive() {
  const { user } = useAuth();
  const { data, error, loading, reload } = useApi<DailyArchiveItem[]>("/daily/archive?limit=120", user?.id);
  const onDay = data?.filter((d) => d.solved_on_day).length ?? 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Daily challenges</h1>
          <p className="muted">
            A new problem every day at midnight UTC. Solve it the same day to grow your streak - past
            problems stay open to practice anytime.
          </p>
        </div>
        {user && (
          <div className="arch-stats">
            <StreakChip streak={user.current_streak} />
            <span className="muted small">best {user.best_streak} · {onDay} on the day</span>
          </div>
        )}
      </div>

      <DailyCard />

      <h2 className="section-title">All daily problems</h2>
      {error && <ErrorState message={error} onRetry={reload} busy={loading} />}
      {!data && !error && <Skeleton rows={6} />}
      {data?.length === 0 && <EmptyState title="No daily problems yet">The first one is on its way.</EmptyState>}
      {data && data.length > 0 && (
        <div className="archive-grid">
          {data.map((item) => (
            <Link key={item.day} to={`/problems/${item.problem.slug}`} className="archive-item">
              <div className="archive-date">
                <span className="archive-dow">{formatDay(item.day, { weekday: "short" })}</span>
                <span className="archive-dom">{formatDay(item.day, { day: "numeric" })}</span>
                <span className="archive-mon">{formatDay(item.day, { month: "short" })}</span>
              </div>
              <div className="archive-body">
                <span className="archive-title">{item.problem.title}</span>
                <DifficultyBadge difficulty={item.problem.difficulty} />
              </div>
              {user && <StatusIcon item={item} />}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
