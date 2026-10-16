import { useState, type InputHTMLAttributes } from "react";
import { passwordRules, passwordStrength } from "../validation";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type">;

export function PasswordInput(props: Props) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="input-with-action">
      <input {...props} type={visible ? "text" : "password"} />
      <button
        type="button"
        className="input-action"
        onClick={() => setVisible((v) => !v)}
        aria-label={visible ? "Hide password" : "Show password"}
        tabIndex={-1}
      >
        {visible ? "Hide" : "Show"}
      </button>
    </div>
  );
}

export function PasswordChecklist({ password, username = "", email = "" }: { password: string; username?: string; email?: string }) {
  const rules = passwordRules(password, username, email);
  const strength = passwordStrength(password, rules);
  return (
    <div className="pw-check" aria-live="polite">
      <div className="pw-meter" data-score={strength.score}>
        {[1, 2, 3, 4].map((i) => <span key={i} className={i <= strength.score ? "on" : ""} />)}
        <em>{strength.label}</em>
      </div>
      <ul>
        {rules.map((r) => (
          <li key={r.label} className={r.ok ? "ok" : ""}>
            <span aria-hidden>{r.ok ? "✓" : "○"}</span> {r.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function allPasswordRulesPass(password: string, username = "", email = "") {
  return passwordRules(password, username, email).every((r) => r.ok);
}
