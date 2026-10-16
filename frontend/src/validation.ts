// Mirrors backend/app/validation.py - keep them in sync. The server is the source of truth;
// these checks just give instant feedback while typing.

export const USERNAME_MIN = 3;
export const USERNAME_MAX = 20;
export const PASSWORD_MIN = 8;
export const PASSWORD_MAX = 128;

const RESERVED = new Set([
  "admin", "administrator", "root", "system", "sysadmin", "support", "help", "api", "www",
  "mail", "email", "promptcode", "moderator", "mod", "staff", "official", "security",
  "login", "logout", "register", "signup", "signin", "settings", "profile", "account",
  "problems", "problem", "submissions", "leaderboard", "user", "users", "new", "edit",
  "null", "undefined", "anonymous", "guest", "test", "owner", "billing",
]);

export function usernameError(name: string): string | null {
  if (name.length < USERNAME_MIN || name.length > USERNAME_MAX)
    return `Must be ${USERNAME_MIN}–${USERNAME_MAX} characters.`;
  if (!/^[A-Za-z]/.test(name)) return "Must start with a letter.";
  if (!/^[A-Za-z0-9_]+$/.test(name)) return "Only letters, numbers and underscores.";
  if (name.includes("__")) return "No consecutive underscores.";
  if (name.endsWith("_")) return "Can't end with an underscore.";
  if (RESERVED.has(name.toLowerCase())) return "That name is reserved.";
  return null;
}

export interface PasswordRule {
  label: string;
  ok: boolean;
}

export function passwordRules(password: string, username = "", email = ""): PasswordRule[] {
  const lower = password.toLowerCase();
  const personal = [username.toLowerCase(), email.split("@")[0].toLowerCase()].filter((p) => p.length >= 3);
  return [
    { label: `At least ${PASSWORD_MIN} characters`, ok: password.length >= PASSWORD_MIN && password.length <= PASSWORD_MAX },
    { label: "Upper and lowercase letters", ok: /[a-z]/.test(password) && /[A-Z]/.test(password) },
    { label: "A number", ok: /\d/.test(password) },
    { label: "A symbol (e.g. ! @ # -)", ok: /[^A-Za-z0-9]/.test(password) },
    { label: "Doesn't contain your username or email", ok: password.length > 0 && !personal.some((p) => lower.includes(p)) },
  ];
}

export type Strength = { score: 0 | 1 | 2 | 3 | 4; label: string };

export function passwordStrength(password: string, rules: PasswordRule[]): Strength {
  if (!password) return { score: 0, label: "" };
  const passed = rules.filter((r) => r.ok).length;
  let score = passed <= 2 ? 1 : passed <= 4 ? 2 : 3;
  if (passed === rules.length && password.length >= 12) score = 4;
  const labels = ["", "Weak", "Fair", "Good", "Strong"];
  return { score: score as Strength["score"], label: labels[score] };
}

export function emailError(email: string): string | null {
  return /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email) ? null : "Enter a valid email address.";
}
