export type Difficulty = "easy" | "medium" | "hard";
export type CheckType = "exact" | "contains" | "not_contains" | "regex" | "json_schema" | "llm_judge";
export type Status = "accepted" | "wrong_answer" | "error";

export interface User {
  id: number;
  username: string;
  email: string;
  avatar_url: string | null;
  has_password: boolean;
  has_google: boolean;
  created_at: string;
  is_admin: boolean;
  current_streak: number;
  best_streak: number;
  /** One-off security/info message returned by some auth endpoints. */
  notice?: string | null;
}

export interface AuthConfig {
  google_client_id: string | null;
  daily_test_budget: number;
}

export interface TestCaseInput {
  input: string;
  check_type: CheckType;
  expected: string;
  case_sensitive: boolean;
  is_sample: boolean;
}

export interface TestCase extends TestCaseInput {
  id: number;
  position: number;
}

export interface ProblemSummary {
  id: number;
  slug: string;
  title: string;
  difficulty: Difficulty;
  tags: string[];
  author: string | null;
  solved: boolean;
  attempted: boolean;
  submissions: number;
  acceptance: number | null;
}

export interface ProblemDetail {
  id: number;
  slug: string;
  title: string;
  description: string;
  difficulty: Difficulty;
  tags: string[];
  starter_prompt: string;
  author: string | null;
  sample_tests: TestCase[];
  hidden_count: number;
  can_edit: boolean;
  solved: boolean;
  daily_day: string | null;
}

export interface ProblemInput {
  title: string;
  description: string;
  difficulty: Difficulty;
  tags: string[];
  starter_prompt: string;
  test_cases: TestCaseInput[];
}

export interface TestResult {
  test_id: number;
  position: number;
  is_sample: boolean;
  check_type: CheckType;
  passed: boolean;
  error: boolean;
  input: string | null;
  expected: string | null;
  output: string | null;
  detail: string | null;
}

export interface RunResult {
  status: Status;
  passed: number;
  total: number;
  results: TestResult[];
  input_tokens: number;
  output_tokens: number;
  submission_id: number | null;
  daily_remaining?: number | null;
  streak_extended?: boolean;
  current_streak?: number | null;
}

export interface Submission {
  id: number;
  problem_slug: string;
  problem_title: string;
  status: Status;
  passed: number;
  total: number;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
}

export interface SubmissionDetail extends Submission {
  prompt: string;
  results: TestResult[];
}

export interface LeaderboardEntry {
  username: string;
  solved: number;
  submissions: number;
}

export interface Profile {
  username: string;
  avatar_url: string | null;
  created_at: string;
  solved: number;
  solved_by_difficulty: Record<Difficulty, number>;
  total_by_difficulty: Record<Difficulty, number>;
  submissions: number;
  acceptance_rate: number | null;
  problems_authored: number;
  recent_solved: { slug: string; title: string; difficulty: Difficulty }[];
  current_streak: number;
  best_streak: number;
  dailies_solved: number;
}

export interface DailyProblem {
  slug: string;
  title: string;
  difficulty: Difficulty;
  tags: string[];
}

export interface DailyToday {
  status: "ready" | "preparing" | "unavailable" | "disabled";
  day: string;
  next_reset: string;
  problem: DailyProblem | null;
  solved_today: boolean;
  current_streak: number;
  best_streak: number;
}

export interface DailyArchiveItem {
  day: string;
  problem: DailyProblem;
  solved_on_day: boolean;
  solved: boolean;
}

export interface QueueItem {
  id: number;
  title: string;
  difficulty: Difficulty;
  created_at: string;
  used_on: string | null;
}

export const CHECK_LABELS: Record<CheckType, string> = {
  exact: "Exact match",
  contains: "Contains",
  not_contains: "Does not contain",
  regex: "Regex",
  json_schema: "JSON schema",
  llm_judge: "LLM judge",
};

export const EXPECTED_HINTS: Record<CheckType, string> = {
  exact: "The exact expected output (whitespace trimmed).",
  contains: "Text that must appear in the output.",
  not_contains: "Text that must NOT appear in the output.",
  regex: "A regex the output must match, e.g. \\A\\d+\\Z for digits only.",
  json_schema: "A JSON Schema the output (parsed as JSON) must satisfy.",
  llm_judge: "A rubric. A judge model decides if the output meets every requirement.",
};
