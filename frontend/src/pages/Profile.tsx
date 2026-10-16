import { Link, useParams } from "react-router-dom";
import { DifficultyBadge } from "../components/Badges";
import { StreakChip } from "../components/Daily";
import { Avatar, EmptyState, ErrorState, Skeleton } from "../components/ui";
import type { Difficulty, Profile as ProfileData } from "../types";
import { useApi } from "../useApi";

const DIFFICULTIES: Difficulty[] = ["easy", "medium", "hard"];

export default function Profile() {
  const { username = "" } = useParams();
  const { data: fetched, error, loading, reload } = useApi<ProfileData>(`/users/${encodeURIComponent(username)}`);
  // Don't flash the previous user's profile while navigating between profiles.
  const profile = fetched && fetched.username.toLowerCase() === username.toLowerCase() ? fetched : null;

  if (error && !profile) {
    return (
      <div className="page">
        {error === "User not found" ? <EmptyState title="User not found">No one goes by that name.</EmptyState> : <ErrorState message={error} onRetry={reload} busy={loading} />}
      </div>
    );
  }
  if (!profile) return <div className="page"><Skeleton rows={8} /></div>;

  return (
    <div className="page narrow-wide">
      <div className="profile-header">
        <Avatar name={profile.username} url={profile.avatar_url} size={72} />
        <div>
          <h1>{profile.username}</h1>
          <p className="muted">Joined {new Date(profile.created_at).toLocaleDateString(undefined, { month: "long", year: "numeric" })}</p>
          <div className="profile-streak">
            <StreakChip streak={profile.current_streak} />
            <span className="muted small">current streak · best {profile.best_streak} · {profile.dailies_solved} dailies solved on the day</span>
          </div>
        </div>
      </div>

      <div className="stats">
        <div className="stat"><span className="stat-value">{profile.solved}</span><span className="stat-label">Solved</span></div>
        <div className="stat"><span className="stat-value">{profile.submissions}</span><span className="stat-label">Submissions</span></div>
        <div className="stat"><span className="stat-value">{profile.acceptance_rate === null ? "–" : `${profile.acceptance_rate}%`}</span><span className="stat-label">Acceptance</span></div>
        <div className="stat"><span className="stat-value">{profile.problems_authored}</span><span className="stat-label">Problems created</span></div>
      </div>

      <div className="card">
        <h2>Progress</h2>
        {DIFFICULTIES.map((d) => {
          const solved = profile.solved_by_difficulty[d];
          const total = profile.total_by_difficulty[d];
          return (
            <div key={d} className="progress-row">
              <DifficultyBadge difficulty={d} />
              <div className="progress"><div className={`progress-fill diff-bg-${d}`} style={{ width: total ? `${(100 * solved) / total}%` : 0 }} /></div>
              <span className="muted small">{solved} / {total}</span>
            </div>
          );
        })}
      </div>

      <div className="card">
        <h2>Recently solved</h2>
        {profile.recent_solved.length === 0 ? (
          <p className="muted">Nothing solved yet.</p>
        ) : (
          <ul className="plain-list">
            {profile.recent_solved.map((p) => (
              <li key={p.slug}><Link to={`/problems/${p.slug}`}>{p.title}</Link> <DifficultyBadge difficulty={p.difficulty} /></li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
