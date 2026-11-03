import { useEffect, type ReactNode } from "react";

/** A modal in the design language: blurred overlay, a centred panel that fades and scales in, Escape closes. */
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 backdrop-blur-md" style={{ background: "rgb(10 10 10 / 0.3)" }} onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-card border bg-bg p-7"
        style={{ boxShadow: "var(--shadow-modal)", animation: "rise 0.2s ease-out" }}
      >
        <h2 className="mb-3 font-medium">{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function Confirm({ title, children, confirmLabel, danger, busy, onConfirm, onClose }: {
  title: string; children: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Modal title={title} onClose={onClose}>
      <div className="text-sm text-g500">{children}</div>
      <div className="mt-6 flex gap-2">
        <button className={`btn ${danger ? "btn-alarm" : "btn-primary"}`} disabled={busy} onClick={onConfirm} autoFocus>{busy ? "working…" : confirmLabel}</button>
        <button className="btn" onClick={onClose}>cancel</button>
      </div>
    </Modal>
  );
}
