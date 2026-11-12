import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { useTitle } from "../useTitle";

const UPDATED = "12 November 2026";

function Contact() {
  const { config } = useAuth();
  return config?.contact_email ? (
    <a href={`mailto:${config.contact_email}`}>{config.contact_email}</a>
  ) : (
    <>the contact address shown on this page</>
  );
}

function LegalPage({ title, intro, children }: { title: string; intro: ReactNode; children: ReactNode }) {
  useTitle(title);
  return (
    <div className="page legal">
      <header className="legal-head">
        <h1>{title}</h1>
        <p className="muted">Last updated {UPDATED}</p>
        <p className="legal-intro">{intro}</p>
      </header>
      <div className="legal-body">{children}</div>
      <p className="legal-foot muted">
        See also: <Link to="/privacy">Privacy Policy</Link> · <Link to="/terms">Terms of Use</Link>
      </p>
    </div>
  );
}

export function PrivacyPage() {
  return (
    <LegalPage
      title="Privacy Policy"
      intro={<>This page explains what PromptCode collects, why, who it's shared with, and the choices you have. In short: we keep only what the site needs to work, we never sell your data, and we don't use advertising or tracking cookies.</>}
    >
      <section>
        <h2>1. Who we are</h2>
        <p>
          PromptCode is an independent project for practising prompt engineering, run by Abhay Rawat. For any question
          about your data, contact <Contact />.
        </p>
      </section>

      <section>
        <h2>2. What we collect</h2>
        <h3>Your account</h3>
        <ul>
          <li><strong>Username and email address.</strong> Your username is public; your email is never shown to other users.</li>
          <li><strong>Password</strong>, stored only as a one-way Argon2 hash. We can't read it.</li>
          <li><strong>If you sign in with Google:</strong> your Google account ID, email address and profile picture. We never see your Google password.</li>
          <li><strong>Your own Gemini API key</strong>, only if you add one in Settings. It's stored encrypted and only its last 4 characters are ever shown.</li>
          <li><strong>Settings</strong>, such as whether you want streak reminder emails.</li>
        </ul>
        <h3>What you do on the site</h3>
        <ul>
          <li><strong>Prompts you run and submit</strong>, with their results and token counts.</li>
          <li><strong>Progress:</strong> problems solved, daily streaks, badges, hints you opened, ratings and upvotes you gave, and problems you reported.</li>
          <li><strong>Solutions you choose to share</strong>, which other users who solved the same problem can see.</li>
        </ul>
        <h3>Technical data</h3>
        <ul>
          <li><strong>IP address</strong>, used briefly to limit abuse (for example too many login attempts) and kept in our hosting provider's server logs for a limited time.</li>
          <li><strong>AI usage counts</strong> per day, to stay within free-tier limits and keep the site fair for everyone.</li>
        </ul>
      </section>

      <section>
        <h2>3. Your prompts are processed by AI providers</h2>
        <p>
          To grade a prompt, we send it, together with the problem's test inputs, to an AI model provider: <strong>Google
          (Gemini)</strong>, or <strong>Groq</strong> as a backup when Gemini is busy. If you added your own Gemini key, your
          prompts go to Google under your own key.
        </p>
        <p className="legal-callout">
          PromptCode uses these providers' free tiers. On Google's unpaid Gemini API, Google may use submitted content to
          improve its products, and people may review it. <strong>Don't put personal or confidential information in your
          prompts.</strong>
        </p>
        <p>To save quota, identical requests reuse a stored answer for up to 30 days.</p>
      </section>

      <section>
        <h2>4. Cookies and browser storage</h2>
        <ul>
          <li><code>pc_session</code>: keeps you logged in (about 7 days). It can't be read by scripts on the page.</li>
          <li><code>pc_csrf</code>: protects your account from forged requests made by other websites.</li>
          <li>
            Your browser's local storage keeps your <strong>prompt drafts and recent versions</strong>, your <strong>light or dark
            theme</strong> and your <strong>panel layout</strong>. These stay on your device and are never sent to us.
          </li>
          <li>If you use "Sign in with Google", Google's sign-in button may set its own cookies, under Google's privacy policy.</li>
        </ul>
        <p>We don't use analytics, advertising or tracking cookies.</p>
      </section>

      <section>
        <h2>5. What other users can see</h2>
        <p>
          Your username, profile picture, solved count, streaks, badges and position on the leaderboard are public. Your
          prompts stay private unless you choose to share a solution. Your email address and API key are never shown to
          anyone.
        </p>
      </section>

      <section>
        <h2>6. Who we share data with</h2>
        <p>We never sell your data. We use these services to run the site, each only for its purpose:</p>
        <ul>
          <li><strong>Render</strong>: hosting, database and server logs.</li>
          <li><strong>Google</strong>: Sign in with Google, and the Gemini AI models.</li>
          <li><strong>Groq</strong>: backup AI models.</li>
          <li><strong>Resend</strong>: sending account emails.</li>
          <li><strong>Sentry</strong>, if enabled: error reports, configured not to include your personal details.</li>
        </ul>
        <p>We may also disclose data if the law requires it.</p>
      </section>

      <section>
        <h2>7. Emails</h2>
        <p>
          We send emails you need for your account (verifying your address, resetting your password) and, if your daily
          streak is about to end, one reminder that day. Turn reminders off any time in{" "}
          <Link to="/settings">Settings</Link> or with the link in the email. We don't send marketing emails.
        </p>
      </section>

      <section>
        <h2>8. How long we keep data</h2>
        <p>
          We keep your account and activity while your account exists. Stored AI answers expire after 30 days. Server logs
          are kept by our hosting provider for a limited time.
        </p>
      </section>

      <section>
        <h2>9. Your choices and rights</h2>
        <ul>
          <li>Change your username or password, remove your own API key, and turn off reminders in <Link to="/settings">Settings</Link>.</li>
          <li>Stop sharing any solution from the problem's Solutions tab.</li>
          <li>To get a copy of your data, correct it, or <strong>delete your account and all its data</strong>, contact <Contact />. We'll respond within 30 days.</li>
        </ul>
      </section>

      <section>
        <h2>10. Security</h2>
        <p>
          Connections use HTTPS, passwords are hashed with Argon2, API keys are encrypted, and logins are rate-limited. No
          system is perfectly secure, but we work to protect your data and will tell affected users about any serious
          breach.
        </p>
      </section>

      <section>
        <h2>11. Children</h2>
        <p>PromptCode isn't intended for children under 13, and we don't knowingly collect their data.</p>
      </section>

      <section>
        <h2>12. Changes</h2>
        <p>If this policy changes, we'll update the date at the top. For significant changes, we'll let users know on the site or by email.</p>
      </section>
    </LegalPage>
  );
}

export function TermsPage() {
  return (
    <LegalPage
      title="Terms of Use"
      intro={<>These terms apply when you use PromptCode. By creating an account or using the site, you agree to them.</>}
    >
      <section>
        <h2>1. The service</h2>
        <p>
          PromptCode is a free site for practising prompt engineering: you write prompts, they're run on AI models, and
          automatic tests check the results. It's provided as is, and features may change or stop at any time.
        </p>
      </section>

      <section>
        <h2>2. Your account</h2>
        <ul>
          <li>Give accurate information and keep your password safe. You're responsible for activity on your account.</li>
          <li>You must be at least 13 years old to use PromptCode.</li>
          <li>One person, one account. Don't create accounts to get around limits.</li>
        </ul>
      </section>

      <section>
        <h2>3. Acceptable use</h2>
        <p>Please don't:</p>
        <ul>
          <li>submit prompts or content that is illegal, hateful, harassing, sexually explicit, or meant to produce harmful output;</li>
          <li>put other people's personal or confidential information in prompts;</li>
          <li>try to break, overload or get around the site's limits and security, or scrape it automatically;</li>
          <li>use the site to access the AI models for purposes other than practising on its problems.</li>
        </ul>
        <p>
          AI providers have their own usage rules, which also apply to your prompts: for example Google's Generative AI
          Prohibited Use Policy.
        </p>
      </section>

      <section>
        <h2>4. Your content</h2>
        <p>
          You own the prompts you write. When you choose to share a solution, you allow other PromptCode users to view it and
          copy it into their own editor. You can stop sharing it at any time.
        </p>
      </section>

      <section>
        <h2>5. AI output</h2>
        <p>
          Outputs come from AI models and can be wrong. Grades are automated and may occasionally be mistaken. Don't rely on
          outputs for important decisions.
        </p>
      </section>

      <section>
        <h2>6. Limits and fair use</h2>
        <p>
          AI usage runs on limited free quotas, so there are per-user limits on how often you can run prompts each day. We
          may suspend accounts that abuse the service.
        </p>
      </section>

      <section>
        <h2>7. Ending your use</h2>
        <p>
          You can stop using PromptCode at any time and ask us to delete your account (see the{" "}
          <Link to="/privacy">Privacy Policy</Link>). We may suspend or close accounts that break these terms.
        </p>
      </section>

      <section>
        <h2>8. No warranty and liability</h2>
        <p>
          PromptCode is free and provided without warranties of any kind. To the extent the law allows, we aren't liable for
          any loss arising from using the site.
        </p>
      </section>

      <section>
        <h2>9. Changes and contact</h2>
        <p>
          We may update these terms and will change the date at the top when we do. Questions? Contact <Contact />.
        </p>
      </section>
    </LegalPage>
  );
}
