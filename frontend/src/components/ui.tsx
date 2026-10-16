import { useEffect, useRef, type ReactNode } from "react";

export function Avatar({ name, url, size = 32 }: { name: string; url?: string | null; size?: number }) {
  const style = { width: size, height: size, fontSize: size * 0.42 };
  if (url) return <img className="avatar" src={url} alt="" style={style} referrerPolicy="no-referrer" />;
  // Stable hue per name so everyone gets their own colour.
  const hue = [...name].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  return (
    <span className="avatar" style={{ ...style, background: `hsl(${hue} 55% 45%)` }} aria-hidden>
      {name.slice(0, 1).toUpperCase()}
    </span>
  );
}

export function Spinner({ small }: { small?: boolean }) {
  return <span className={`spinner ${small ? "small" : ""}`} aria-label="Loading" />;
}

export function Skeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div className="skeleton" aria-busy="true">
      {Array.from({ length: rows }, (_, i) => <div key={i} className="skeleton-row" style={{ width: `${90 - (i % 3) * 15}%` }} />)}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-mark" aria-hidden>{"{ }"}</div>
      <h3>{title}</h3>
      {children && <div className="muted">{children}</div>}
    </div>
  );
}

export function ErrorState({ message, onRetry, busy }: { message: string; onRetry: () => void; busy?: boolean }) {
  return (
    <div className="alert error error-state" role="alert">
      <span>{message}</span>
      <button className="btn ghost small" onClick={onRetry} disabled={busy}>
        {busy ? <Spinner small /> : "Try again"}
      </button>
    </div>
  );
}

interface ConfirmProps {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({ open, title, children, confirmLabel, danger, busy, onConfirm, onCancel }: ConfirmProps) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  return (
    <dialog ref={ref} className="dialog" onCancel={(e) => { e.preventDefault(); onCancel(); }}>
      <h3>{title}</h3>
      <div className="dialog-body">{children}</div>
      <div className="dialog-actions">
        <button className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className={`btn ${danger ? "danger-solid" : "primary"}`} onClick={onConfirm} disabled={busy}>
          {busy ? <Spinner small /> : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}

export function Celebration() {
  // Pure-CSS confetti burst; removed by the parent after a few seconds.
  return (
    <div className="confetti" aria-hidden>
      {Array.from({ length: 40 }, (_, i) => (
        <i key={i} style={{ "--x": `${(i * 37) % 100}vw`, "--d": `${(i % 7) * 0.12}s`, "--h": `${(i * 47) % 360}` } as React.CSSProperties} />
      ))}
    </div>
  );
}
