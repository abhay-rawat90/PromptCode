import { useEffect, useRef, useState } from "react";

interface GoogleAccountsId {
  initialize(options: { client_id: string; callback: (r: { credential: string }) => void; ux_mode?: string; use_fedcm_for_prompt?: boolean }): void;
  renderButton(el: HTMLElement, options: Record<string, unknown>): void;
}
declare global {
  interface Window {
    google?: { accounts: { id: GoogleAccountsId } };
  }
}

const SCRIPT_SRC = "https://accounts.google.com/gsi/client";
let scriptPromise: Promise<void> | null = null;

function loadScript(): Promise<void> {
  if (window.google?.accounts?.id) return Promise.resolve();
  scriptPromise ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT_SRC;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => {
      scriptPromise = null;
      reject(new Error("Google sign-in failed to load"));
    };
    document.head.appendChild(s);
  });
  return scriptPromise;
}

interface Props {
  clientId: string;
  onCredential: (credential: string) => void;
  text?: "signin_with" | "signup_with" | "continue_with";
  theme: "light" | "dark";
}

export default function GoogleButton({ clientId, onCredential, text = "continue_with", theme }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const callback = useRef(onCredential);
  callback.current = onCredential;
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadScript()
      .then(() => {
        if (cancelled || !ref.current || !window.google) return;
        window.google.accounts.id.initialize({
          client_id: clientId,
          callback: (r) => callback.current(r.credential),
          ux_mode: "popup",
        });
        ref.current.innerHTML = "";
        window.google.accounts.id.renderButton(ref.current, {
          type: "standard",
          theme: theme === "dark" ? "filled_black" : "outline",
          size: "large",
          text,
          shape: "rectangular",
          logo_alignment: "center",
          width: Math.min(ref.current.offsetWidth || 360, 400),
        });
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [clientId, text, theme]);

  if (failed) return <p className="muted small center">Google sign-in couldn't load (blocked by an extension?).</p>;
  return <div ref={ref} className="google-btn" />;
}
