# PromptCode

A LeetCode-style platform for practicing **prompt writing**. Each problem describes a task; you
write a prompt, it is run against test cases on Google Gemini, and you need every test to pass.
Anyone with an account can also publish their own problems.

## Stack

- **Backend:** FastAPI, SQLAlchemy and Alembic, Argon2 password hashing, cookie sessions,
  Google sign-in, the `google-genai` SDK, and `limits` for rate limiting.
- **Frontend:** React, TypeScript and Vite, with no UI framework. It has light and dark themes
  and works on mobile.

## Running locally

### 1. Backend (port 8000)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
#   then set GEMINI_API_KEY and JWT_SECRET (see the comments in .env)
uvicorn app.main:app --reload --reload-dir app
```

(`--reload-dir app` makes the server restart only when app code changes, and not when packages
are installed into `.venv`. While the server restarts, the frontend retries quietly and recovers
on its own.)

On startup, the backend applies the database migrations and adds 5 starter problems. API docs
are at http://localhost:8000/docs (development only).

### 2. Frontend (port 5173)

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite forwards `/api` requests to the backend, so the browser sees one
origin.

### Tests

```bash
cd backend
.venv\Scripts\python -m pytest
```

The tests mock Gemini and Google, so they're free and don't need a network connection.

## Daily challenge

A new problem goes live every day at **midnight UTC**:

- Each day's problem comes from the **curated queue** if it has items. Admins add to the queue
  with the "Queue as an upcoming daily problem" checkbox on the Create page.
- If the queue is empty, **Gemini writes a problem** (`DAILY_MODEL`). It's only published after
  passing three checks:
  1. the same validation as a user-created problem;
  2. its reference prompt passes **every** test;
  3. a do-nothing prompt (`{{input}}`) **fails** at least one test.

  A rejected attempt goes back to Gemini with the reasons it failed, up to
  `DAILY_GENERATION_ATTEMPTS` times.
- Difficulty follows the week: easy at the weekend and on Monday, medium and hard midweek. The
  topic rotates through prompting skills.
- Problems are prepared **a day ahead** and stay hidden until their day starts.
- **Streaks:** solving the day's problem on that day extends your streak. Solving it later still
  counts as solved, but not toward the streak. Streaks show in the nav bar, on the home page card
  and on your profile.
- `/daily` lists every past daily problem, marked solved on the day, solved later, or unsolved.

Without a scheduler, run `python -m app.daily` from cron (say every 30 minutes) to prepare
today's and tomorrow's problems. On Gemini's free tier, rate limits and overloads are waited out
automatically.

**Making yourself an admin:**

```bash
cd backend
.venv\Scripts\python -m app.admin make-admin <your-username>
```

## Enabling "Sign in with Google"

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials).
2. If prompted, configure the OAuth consent screen. "External" with your email as a test user is
   enough for development.
3. Create an **OAuth client ID** of type **Web application**.
4. Under **Authorized JavaScript origins**, add `http://localhost:5173` and `http://localhost`, plus
   your production URL later. You don't need any redirect URIs.
5. Copy the client ID (`....apps.googleusercontent.com`) into `GOOGLE_CLIENT_ID` in `backend/.env`
   and restart the backend.

The Google button then appears on the login and signup pages. If `GOOGLE_CLIENT_ID` is empty,
it's hidden.

How sign-in works:
- The backend checks Google's sign-in token itself: the signature, the audience and the expiry.
  It only accepts verified email addresses.
- If an account with the same email already exists, Google is linked to it.
- Otherwise a new account is created with a username based on the person's name, which they can
  change in Settings.
- People who sign in with Google can also set a password later.

## Accounts and security

| Area | What's in place |
|---|---|
| Passwords | 8–128 characters with upper and lower case, a number and a symbol. It can't contain the username or email, or be a common password. The signup page shows a live checklist and strength meter. Passwords are hashed with **Argon2id**. |
| Usernames | 3–20 characters, starting with a letter; letters, numbers and `_` only, with no `__` and no trailing `_`. Reserved names (`admin`, `root`, …) are blocked. Usernames are unique regardless of case, and the signup page checks availability as you type. |
| Sessions | A signed token in an **httpOnly, SameSite=Lax** cookie, marked Secure in production. JavaScript never sees it, so a cross-site scripting bug can't steal a session. Changing your password or choosing "Sign out of all devices" ends every other session. |
| CSRF | Double-submit token (`pc_csrf` cookie plus `X-CSRF-Token` header) on every state-changing request, together with an Origin check. |
| Brute force | Login is limited per IP address and per account. Signup, Google sign-in and the username check are limited per IP. Failed logins give the same message whether or not the account exists, and take the same time. |
| Cost abuse | Run and submit are limited per user per minute, plus a daily budget of test executions per user (`DAILY_TEST_BUDGET`). There's also a cap on Gemini calls in flight across the whole server. |
| Headers | CSP, `X-Frame-Options: DENY`, `nosniff`, Referrer-Policy, Permissions-Policy, HSTS (over HTTPS), and `no-store` on API responses. |
| Inputs | Every request body is checked against a schema with length limits. Regex checks time out after 1 second. Search escapes `%` and `_`. Problem descriptions are rendered as Markdown without raw HTML. |
| Email | New accounts get a verification link, which is required before publishing problems. "Forgot password?" emails a link that works once and expires in an hour; using it signs out every other session. Tokens are stored only as SHA-256 hashes, and the forgot-password reply is the same whether or not an account exists. |
| Spend | Every Gemini call is counted per day, per user and per kind of work. Admins see it at `/admin`, and `DAILY_TOKEN_CAP` stops runs for the rest of the day once reached. |
| Production guard | With `ENVIRONMENT=production`, the server won't start with a weak `JWT_SECRET`, `COOKIE_SECURE` off, SQLite, a non-https `APP_URL` or a missing Resend key. `/docs` is disabled. |

## Email (Resend)

During development, emails aren't sent. They're printed in the backend terminal, links included
(`EMAIL_BACKEND=console`), so you can click verify and reset links locally.

To send real email:

1. Create an account at [resend.com](https://resend.com) and an **API key** with "Sending access".
2. **Add and verify your domain** (Resend → Domains, then add the DNS records it shows). Until
   you do, Resend only delivers to your own address from `onboarding@resend.dev`.
3. Set `RESEND_API_KEY=re_...` and `EMAIL_FROM="PromptCode <no-reply@yourdomain.com>"`. The
   backend switches to Resend automatically when a key is set.

## Admin

Make yourself an admin with `.venv\Scripts\python -m app.admin make-admin <username>`. The
account menu then shows **Admin**, with:
- Gemini tokens used per day, broken down by run, submit and daily generation;
- today's biggest users, and the daily cap;
- the daily-problem queue.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:
- the backend tests on **SQLite and on Postgres 16**;
- a migration check: upgrade, `alembic check` and a downgrade;
- the frontend type-check and build;
- a Docker image build and a check that the container starts and serves the site.

## Deploying to Render

The repo includes a `Dockerfile` and a Render Blueprint (`render.yaml`) that create the web
service, Postgres and Redis (Key Value) together. The web service's Docker image builds the
frontend and serves it, so the site and the API share one origin.

1. Push the repo to GitHub.
2. In Render, choose **New → Blueprint**, pick the repo, and approve the three resources.
3. Fill in the secret values it asks for:
   - `APP_URL`: your service URL, e.g. `https://promptcode.onrender.com`, with no trailing slash.
     You can set it after the first deploy, once you know the URL.
   - `GEMINI_API_KEY`, `GOOGLE_CLIENT_ID`, `RESEND_API_KEY`, `EMAIL_FROM`.
   - Optionally `SENTRY_DSN` and `DAILY_TOKEN_CAP`.
   - `JWT_SECRET`, `DATABASE_URL` and the Redis URL are filled in automatically.
4. In Google Cloud → Credentials, add your Render URL to the OAuth client's **Authorized
   JavaScript origins**.
5. Make yourself an admin: Render → promptcode → **Shell**, then
   `python -m app.admin make-admin <username>`.

Migrations run automatically at startup. Pushing to `main` redeploys.

Things to know about Render's free plans:
- Free web services **sleep after 15 minutes idle** and take about a minute to wake. The daily
  scheduler only runs while the service is awake; the first visit of the day wakes it and
  prepares the problem. The `starter` plan stays awake.
- Free Postgres **expires after 30 days**. Upgrade it, which also turns on daily backups, before
  you have real users.

**Scaling beyond one instance:** keep `--workers 1` and add instances instead. Set
`DAILY_SCHEDULER=false` on all but one, or on all of them plus a Render Cron Job running
`python -m app.daily`. Rate limits are already shared through Redis.

To change the database schema, edit `app/models.py`, run
`alembic revision --autogenerate -m "what changed"`, review the generated file, then run
`alembic upgrade head`.

## How a prompt is run

For each test case:

- If your prompt contains `{{input}}`, the test input replaces it and the whole prompt is sent as
  one user message.
- If it doesn't, your prompt is sent as the **system instruction** and the test input as the
  user message. This mode suits problems like "write a system prompt that never leaks X".

| Check | `expected` field holds | Passes when |
|---|---|---|
| `exact` | expected text | trimmed output equals it (case-insensitive unless set otherwise) |
| `contains` / `not_contains` | text | output does or does not contain it |
| `regex` | pattern | the pattern matches somewhere in the output (`\A...\Z` for a full match) |
| `json_schema` | JSON Schema | output parses as JSON (a ```json fence is allowed) and validates |
| `llm_judge` | rubric | the judge model decides the output meets every rubric point |

- **Run** checks only the visible sample tests and saves nothing.
- **Submit** checks every test, including hidden ones, and saves the submission. Hidden tests
  show only pass or fail.

Keyboard shortcuts: `Ctrl/⌘+Enter` to run, `Ctrl/⌘+Shift+Enter` to submit. Drafts save
automatically in your browser.

## Configuration

Every setting is documented in [backend/.env.example](backend/.env.example).

## Project layout

```
backend/
  alembic/            database migrations
  app/
    main.py           app setup, security middleware, serves the built frontend
    config.py         settings (+ production safety checks)
    security.py       Argon2, cookie sessions, CSRF, current-user dependencies
    validation.py     username/password rules (mirrored in frontend/src/validation.ts)
    ratelimit.py      memory/Redis rate limits and daily budgets
    llm.py            Gemini calls (running prompts, LLM judge)
    grader.py         check logic and parallel evaluation
    routers/          auth, problems, submissions/leaderboard, users (profiles)
frontend/src/
  pages/              ProblemList, ProblemPage, ProblemForm, AuthPage, Settings, Profile, Submissions, Leaderboard
  components/         GoogleButton, PasswordInput, ResultsPanel, ui (dialogs, avatars, skeletons…)
  api.ts auth.tsx toast.tsx theme.tsx validation.ts
```
