import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Skeleton, Spinner } from "../components/ui";
import { useToast } from "../toast";
import { CHECK_LABELS, EXPECTED_HINTS, type CheckType, type ProblemDetail, type ProblemInput, type TestCaseInput } from "../types";

const MAX_TESTS = 20;

const blankTest = (isSample = false): TestCaseInput => ({
  input: "",
  check_type: "exact",
  expected: "",
  case_sensitive: false,
  is_sample: isSample,
});

const blankProblem: ProblemInput = {
  title: "",
  description: "",
  difficulty: "easy",
  tags: [],
  starter_prompt: "",
  test_cases: [blankTest(true)],
};

function FieldError({ msg }: { msg?: string }) {
  return msg ? <p className="field-error">{msg}</p> : null;
}

export default function ProblemForm() {
  const { slug } = useParams();
  const editing = Boolean(slug);
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  const [toQueue, setToQueue] = useState(false);

  const [form, setForm] = useState<ProblemInput>(blankProblem);
  const [tagsText, setTagsText] = useState("");
  const [loaded, setLoaded] = useState(!editing);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!slug) return;
    api<ProblemInput & { slug: string }>(`/problems/${slug}/full`)
      .then((p) => {
        setForm(p);
        setTagsText(p.tags.join(", "));
        setLoaded(true);
      })
      .catch((e) => setLoadError(e.message));
  }, [slug]);

  const set = <K extends keyof ProblemInput>(key: K, value: ProblemInput[K]) => setForm((f) => ({ ...f, [key]: value }));

  const setTest = (i: number, patch: Partial<TestCaseInput>) =>
    set("test_cases", form.test_cases.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  const testError = (i: number, field: string) => errors[`test_cases.${i}.${field}`] ?? (field === "expected" ? errors[`test_cases.${i}`] : undefined);

  function validate(): Record<string, string> {
    const e: Record<string, string> = {};
    if (form.title.trim().length < 3) e.title = "Give the problem a title (at least 3 characters).";
    if (!form.description.trim()) e.description = "Describe the task so solvers know what to do.";
    form.test_cases.forEach((t, i) => {
      if (!t.expected.trim()) e[`test_cases.${i}.expected`] = "This can't be empty.";
      if (t.check_type === "json_schema" && t.expected.trim()) {
        try {
          JSON.parse(t.expected);
        } catch {
          e[`test_cases.${i}.expected`] = "That isn't valid JSON.";
        }
      }
    });
    if (!form.test_cases.some((t) => t.is_sample)) e.test_cases = "Mark at least one test as a visible sample.";
    return e;
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    const clientErrors = validate();
    setErrors(clientErrors);
    if (Object.keys(clientErrors).length) {
      document.querySelector(".field-error")?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    setSaving(true);
    const body = { ...form, tags: tagsText.split(",").map((t) => t.trim()).filter(Boolean) };
    try {
      if (toQueue) {
        await api("/admin/daily/queue", { method: "POST", body });
        toast("Added to the daily queue. It will be published as an upcoming daily problem.", "success");
        navigate("/daily");
        return;
      }
      const saved = await api<ProblemDetail>(editing ? `/problems/${slug}` : "/problems", {
        method: editing ? "PUT" : "POST",
        body,
      });
      toast(editing ? "Problem updated." : "Problem published!", "success");
      navigate(`/problems/${saved.slug}`);
    } catch (err) {
      const apiErr = err as ApiError;
      if (Object.keys(apiErr.fields).length) {
        setErrors(apiErr.fields);
        setFormError("Please fix the highlighted fields.");
      } else {
        setFormError(apiErr.message);
      }
    } finally {
      setSaving(false);
    }
  }

  if (loadError) return <div className="page"><div className="alert error">{loadError}</div></div>;
  if (!loaded) return <div className="page"><Skeleton rows={10} /></div>;

  return (
    <div className="page">
      <form className="form problem-form" onSubmit={onSubmit} noValidate>
        <div className="page-head">
          <div>
            <h1>{editing ? "Edit problem" : "Create a problem"}</h1>
            <p className="muted">Describe a task, then add test cases that a good prompt should pass.</p>
          </div>
        </div>

        <div className="card">
          <div className="field">
            <label htmlFor="title">Title</label>
            <input id="title" value={form.title} onChange={(e) => set("title", e.target.value)} maxLength={120} aria-invalid={Boolean(errors.title)} placeholder="e.g. Polite Refusal" />
            <FieldError msg={errors.title} />
          </div>
          <div className="row">
            <div className="field">
              <label htmlFor="difficulty">Difficulty</label>
              <select id="difficulty" value={form.difficulty} onChange={(e) => set("difficulty", e.target.value as ProblemInput["difficulty"])}>
                <option value="easy">Easy</option>
                <option value="medium">Medium</option>
                <option value="hard">Hard</option>
              </select>
            </div>
            <div className="field grow">
              <label htmlFor="tags">Tags <span className="muted">(comma-separated, up to 8)</span></label>
              <input id="tags" value={tagsText} onChange={(e) => setTagsText(e.target.value)} placeholder="extraction, json" />
              <FieldError msg={errors.tags} />
            </div>
          </div>
          <div className="field">
            <label htmlFor="description">Description <span className="muted">(Markdown supported)</span></label>
            <textarea id="description" rows={8} value={form.description} onChange={(e) => set("description", e.target.value)} aria-invalid={Boolean(errors.description)}
              placeholder="Explain the task, the required output format, and whether solvers should use {{input}}." />
            <FieldError msg={errors.description} />
          </div>
          <div className="field">
            <label htmlFor="starter">Starter prompt <span className="muted">(optional)</span></label>
            <textarea id="starter" rows={3} value={form.starter_prompt} onChange={(e) => set("starter_prompt", e.target.value)} className="mono" />
          </div>
        </div>

        <div className="section-head">
          <h2>Test cases <span className="muted small">({form.test_cases.length}/{MAX_TESTS})</span></h2>
          <p className="muted small">Visible samples are shown to solvers and used by Run. Hidden tests only run on Submit.</p>
          <FieldError msg={errors.test_cases} />
        </div>

        {form.test_cases.map((t, i) => (
          <div className="card test-card" key={i}>
            <div className="test-card-head">
              <strong>Test {i + 1}</strong>
              <label className="toggle">
                <input type="checkbox" checked={t.is_sample} onChange={(e) => setTest(i, { is_sample: e.target.checked })} />
                <span>{t.is_sample ? "Visible sample" : "Hidden"}</span>
              </label>
              <button type="button" className="btn ghost small" disabled={form.test_cases.length === 1}
                onClick={() => set("test_cases", form.test_cases.filter((_, j) => j !== i))}>
                Remove
              </button>
            </div>
            <div className="field">
              <label>Input <span className="muted">(replaces {"{{input}}"}, or is sent as the user message)</span></label>
              <textarea rows={3} value={t.input} onChange={(e) => setTest(i, { input: e.target.value })} />
            </div>
            <div className="row">
              <div className="field">
                <label>Check</label>
                <select value={t.check_type} onChange={(e) => setTest(i, { check_type: e.target.value as CheckType })}>
                  {(Object.keys(CHECK_LABELS) as CheckType[]).map((c) => <option key={c} value={c}>{CHECK_LABELS[c]}</option>)}
                </select>
              </div>
              {t.check_type !== "json_schema" && t.check_type !== "llm_judge" && (
                <label className="toggle">
                  <input type="checkbox" checked={t.case_sensitive} onChange={(e) => setTest(i, { case_sensitive: e.target.checked })} />
                  <span>Case sensitive</span>
                </label>
              )}
            </div>
            <div className="field">
              <label>
                {t.check_type === "llm_judge" ? "Rubric" : t.check_type === "json_schema" ? "JSON Schema" : t.check_type === "regex" ? "Pattern" : "Expected"}
              </label>
              <textarea rows={t.check_type === "json_schema" ? 6 : 2} value={t.expected}
                className={t.check_type === "json_schema" || t.check_type === "regex" ? "mono" : ""}
                aria-invalid={Boolean(testError(i, "expected"))}
                onChange={(e) => setTest(i, { expected: e.target.value })} />
              {testError(i, "expected") ? <FieldError msg={testError(i, "expected")} /> : <p className="field-hint">{EXPECTED_HINTS[t.check_type]}</p>}
            </div>
          </div>
        ))}

        <button type="button" className="btn ghost dashed" disabled={form.test_cases.length >= MAX_TESTS}
          onClick={() => set("test_cases", [...form.test_cases, blankTest()])}>
          + Add test case
        </button>

        {user?.is_admin && !editing && (
          <label className="toggle queue-toggle">
            <input type="checkbox" checked={toQueue} onChange={(e) => setToQueue(e.target.checked)} />
            <span>Queue as an upcoming <strong>daily problem</strong> instead of publishing now (admin)</span>
          </label>
        )}
        {formError && <div className="alert error" role="alert">{formError}</div>}
        <div className="form-actions sticky-actions">
          <button type="button" className="btn ghost" onClick={() => navigate(-1)}>Cancel</button>
          <button className="btn primary" disabled={saving}>{saving ? <Spinner small /> : editing ? "Save changes" : toQueue ? "Add to daily queue" : "Publish problem"}</button>
        </div>
      </form>
    </div>
  );
}
