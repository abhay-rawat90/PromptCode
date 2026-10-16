import { useState } from "react";
import { CHECK_LABELS, type RunResult, type TestResult } from "../types";
import { StatusText } from "./Badges";

function CaseDetail({ r }: { r: TestResult }) {
  if (!r.is_sample) {
    return (
      <div className="case-detail">
        <p className="muted">Hidden test - input and output are not shown.</p>
        {r.detail && <p className="detail error-text">{r.detail}</p>}
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
    </div>
  );
}

export default function ResultsPanel({ result, kind }: { result: RunResult; kind: "run" | "submit" }) {
  const firstFailing = result.results.findIndex((r) => !r.passed);
  const [selected, setSelected] = useState(firstFailing === -1 ? 0 : firstFailing);
  const current = result.results[selected];

  return (
    <div className="results">
      <div className="results-head">
        <StatusText status={result.status} />
        <span className="muted">
          {result.passed} / {result.total} {kind === "run" ? "sample " : ""}tests passed ·{" "}
          {result.input_tokens + result.output_tokens} tokens
        </span>
      </div>
      <div className="case-tabs">
        {result.results.map((r, i) => (
          <button
            key={r.test_id}
            className={`case-tab ${r.passed ? "pass" : "fail"} ${i === selected ? "active" : ""}`}
            onClick={() => setSelected(i)}
            title={r.is_sample ? "Sample test" : "Hidden test"}
          >
            {r.passed ? "✓" : "✗"} {r.is_sample ? `Case ${r.position}` : `Hidden ${r.position}`}
          </button>
        ))}
      </div>
      {current && <CaseDetail r={current} />}
    </div>
  );
}
