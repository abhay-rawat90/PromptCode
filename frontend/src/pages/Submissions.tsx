import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { StatusText } from "../components/Badges";
import ResultsPanel from "../components/ResultsPanel";
import { EmptyState, ErrorState, Skeleton } from "../components/ui";
import { useToast } from "../toast";
import type { Submission, SubmissionDetail } from "../types";
import { useApi } from "../useApi";

export default function Submissions() {
  const { data: items, error, loading, reload } = useApi<Submission[]>("/submissions?limit=100");
  const [open, setOpen] = useState<SubmissionDetail | null>(null);
  const toast = useToast();

  async function toggle(id: number) {
    if (open?.id === id) return setOpen(null);
    try {
      setOpen(await api<SubmissionDetail>(`/submissions/${id}`));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  return (
    <div className="page">
      <h1>My submissions</h1>
      {error && <ErrorState message={error} onRetry={reload} busy={loading} />}
      {!items && !error && <Skeleton rows={6} />}
      {items?.length === 0 && <EmptyState title="No submissions yet"><Link to="/">Pick a problem</Link> to get started.</EmptyState>}
      {items && items.length > 0 && (
        <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Problem</th><th>Status</th><th>Passed</th><th className="num">Tokens</th><th>When</th><th></th></tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <Fragment key={s.id}>
                <tr>
                  <td><Link to={`/problems/${s.problem_slug}`}>{s.problem_title}</Link></td>
                  <td><StatusText status={s.status} /></td>
                  <td>{s.passed}/{s.total}</td>
                  <td className="num">{(s.input_tokens + s.output_tokens).toLocaleString()}</td>
                  <td className="muted small">{new Date(s.created_at).toLocaleString()}</td>
                  <td><button className="btn ghost small" onClick={() => toggle(s.id)}>{open?.id === s.id ? "Hide" : "View"}</button></td>
                </tr>
                {open?.id === s.id && (
                  <tr className="detail-row">
                    <td colSpan={6}>
                      <label>Prompt</label>
                      <pre>{open.prompt}</pre>
                      <ResultsPanel result={{ ...open, submission_id: open.id }} kind="submit" />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  );
}
