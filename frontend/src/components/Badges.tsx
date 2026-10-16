import type { Difficulty, Status } from "../types";

export function DifficultyBadge({ difficulty }: { difficulty: Difficulty }) {
  return <span className={`badge diff-${difficulty}`}>{difficulty}</span>;
}

const STATUS_LABELS: Record<Status, string> = {
  accepted: "Accepted",
  wrong_answer: "Wrong Answer",
  error: "Error",
};

export function StatusText({ status }: { status: Status }) {
  return <span className={`status status-${status}`}>{STATUS_LABELS[status]}</span>;
}
