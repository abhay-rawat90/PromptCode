import { useState } from "react";
import { CHECK_LABELS, type RunResult, type TestResult } from "../types";
import { StatusText } from "./Badges";

function CaseDetail({ r }: { r: TestResult }) {
  if (r.skipped) {
    return (
      <div className="case-detail">
        <p className="muted">Not run. An earlier test failed, so checking stopped there. Fix that test first.</p>
      </div>
    );
  }
  // Hidden tests come without input/output, except when the author verifies their own problem.
  if (!r.is_sample && r.input === null) {
    return (
      <div className="case-detail">
        <p className="muted">
          {r.passed ? "Hidden test passed." : "Hidden test failed."} Its input and output aren't shown - re-read the
          description for a case your prompt doesn't handle yet.
        </p>
        {r.detail && <p className="detail error-text">{r.detail}</p>}
      </div>
    );
  }
  return (
    <div className="case-detail">
      <label>Input</label>
      <pre>{r.input || <span className="muted">(empty)</span>}</pre>
      <label>Model output</label>
      <pre className={r.passed ? "" : "pre-fail"}>{r.output || <span className="muted">(empty)</span>}</pre>
      <label>{CHECK_LABELS[r.check_type]} - expected</label>
      <pre>{r.expected}</pre>
      {r.detail && (
        <>
          <label>{r.check_type === "llm_judge" ? "Judge says" : "Why"}</label>
          <p className={`detail ${r.passed ? "" : "error-text"}`}>{r.detail}</p>
        </>
      )}
    </div>
  );
}

export default function ResultsPanel({ result, kind }: { result: RunResult; kind: "run" | "submit" }) {
  const firstFailing = result.results.findIndex((r) => !r.passed && !r.skipped);
  const [selected, setSelected] = useState(firstFailing === -1 ? 0 : firstFailing);
  const current = result.results[selected];
  const skipped = result.results.filter((r) => r.skipped).length;

  return (
    <div className="results">
      <div className="results-head">
        <StatusText status={result.status} />
        <span className="muted">
          {result.passed} / {result.total} {kind === "run" ? "sample " : ""}tests passed
          {skipped > 0 && ` · ${skipped} not run`} · {(result.input_tokens + result.output_tokens).toLocaleString()} tokens
        </span>
      </div>
      {kind === "run" && result.status === "accepted" && (
        <p className="results-tip">Samples pass. <strong>Submit</strong> to check the hidden tests too.</p>
      )}
      <div className="case-tabs" role="tablist" aria-label="Test cases">
        {result.results.map((r, i) => (
          <button
            key={r.test_id}
            role="tab"
            aria-selected={i === selected}
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
