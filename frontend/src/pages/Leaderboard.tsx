import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { Avatar, EmptyState, ErrorState, Skeleton } from "../components/ui";
import type { LeaderboardEntry } from "../types";
import { useApi } from "../useApi";

const MEDALS = ["🥇", "🥈", "🥉"];

export default function Leaderboard() {
  const { user } = useAuth();
  const { data: rows, error, loading, reload } = useApi<LeaderboardEntry[]>("/leaderboard");

  return (
    <div className="page narrow-wide">
      <h1>Leaderboard</h1>
      <p className="muted">Ranked by problems solved, then by fewest submissions.</p>
      {error && <ErrorState message={error} onRetry={reload} busy={loading} />}
      {!rows && !error && <Skeleton rows={6} />}
      {rows?.length === 0 && <EmptyState title="Nobody's on the board yet">Solve a problem to claim the top spot.</EmptyState>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>#</th><th>User</th><th className="num">Solved</th><th className="num">Submissions</th></tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.username} className={user?.username === r.username ? "highlight" : ""}>
                  <td>{MEDALS[i] ?? i + 1}</td>
                  <td>
                    <Link to={`/u/${r.username}`} className="user-cell">
                      <Avatar name={r.username} size={24} /> {r.username}
                    </Link>
                  </td>
                  <td className="num">{r.solved}</td>
                  <td className="num">{r.submissions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
