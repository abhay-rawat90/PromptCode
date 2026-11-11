import { useState } from "react";
import { CHECK_LABELS, type RunResult, type TestResult } from "../types";
import { StatusText } from "./Badges";

function ModelLine({ r }: { r: TestResult }) {
  if (!r.model && !r.cached) return null;
  return (
    <p className="model-line muted small">
      {r.model && <>Answered by <strong>{r.model}</strong>{r.backup ? " (backup model)" : ""}</>}
      {r.cached && <span className="cached-pill" title="Same prompt and input as an earlier run, so the stored answer was reused - no quota spent.">⚡ cached</span>}
    </p>
  );
}

function CaseDetail({ r }: { r: TestResult }) {
  if (r.skipped) {
    return (
      <div className="case-detail">
        <p className="muted">Not run - an earlier test failed, so grading stopped there (like LeetCode). Fix that test first.</p>
      </div>
    );
  }
  // Hidden tests come without input/output, except when the author verifies their own problem.
  if (!r.is_sample && r.input === null) {
    return (
      <div className="case-detail">
        <p className="muted">Hidden test - input and output are not shown.</p>
        {r.detail && <p className="detail error-text">{r.detail}</p>}
        <ModelLine r={r} />
      </div>
    );
  }
  return (
    <div className="case-detail">
      <label>Input</label>
      <pre>{r.input || <span className="muted">(empty)</span>}</pre>
      <label>Model output</label>
      <pre>{r.output || <span className="muted">(empty)</span>}</pre>
      <label>{CHECK_LABELS[r.check_type]} - expected</label>
      <pre>{r.expected}</pre>
      {r.detail && (
        <>
          <label>{r.check_type === "llm_judge" ? "Judge says" : "Why"}</label>
          <p className={`detail ${r.passed ? "" : "error-text"}`}>{r.detail}</p>
        </>
      )}
      <ModelLine r={r} />
    </div>
  );
}

export default function ResultsPanel({ result, kind }: { result: RunResult; kind: "run" | "submit" }) {
  const firstFailing = result.results.findIndex((r) => !r.passed && !r.skipped);
  const [selected, setSelected] = useState(firstFailing === -1 ? 0 : firstFailing);
  const current = result.results[selected];
  const backups = result.backup_models ?? [];
  const skipped = result.skipped ?? 0;

  return (
    <div className="results">
      <div className="results-head">
        <StatusText status={result.status} />
        <span className="muted">
          {result.passed} / {result.total} {kind === "run" ? "sample " : ""}tests passed
          {skipped > 0 && ` · ${skipped} not run`} · {result.input_tokens + result.output_tokens} tokens
        </span>
      </div>
      {backups.length > 0 && (
        <div className="alert warn small-alert">
          Some tests were graded by a backup model (<strong>{backups.join(", ")}</strong>) because the main model was at its
          free-tier limit. Results can differ slightly between models.
        </div>
      )}
      <div className="case-tabs">
        {result.results.map((r, i) => (
          <button
            key={r.test_id}
            className={`case-tab ${r.skipped ? "skipped" : r.passed ? "pass" : "fail"} ${i === selected ? "active" : ""}`}
            onClick={() => setSelected(i)}
            title={r.skipped ? "Not run" : r.is_sample ? "Sample test" : "Hidden test"}
          >
            {r.skipped ? "–" : r.passed ? "✓" : "✗"} {r.is_sample ? `Case ${r.position}` : `Hidden ${r.position}`}
          </button>
        ))}
      </div>
      {current && <CaseDetail r={current} />}
    </div>
  );
}
