import React, { useEffect, useRef } from "react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";

const TOAST_ICONS = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
};

export function ToastViewport({ toasts, dismissToast }) {
  return (
    <div className="toast-viewport" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => {
        const Icon = TOAST_ICONS[toast.type] || Info;
        return (
          <article
            className={`toast-item toast-${toast.type || "info"}${toast.exiting ? " toast-exiting" : ""}`}
            key={toast.id}
            role="status"
          >
            <Icon size={18} />
            <p>{toast.message}</p>
            <button type="button" onClick={() => dismissToast(toast.id)} aria-label="Close">
              <X size={15} />
            </button>
          </article>
        );
      })}
    </div>
  );
}

export function ConfirmDialog({ dialog, closeDialog, t }) {
  const confirmRef = useRef(null);

  useEffect(() => {
    if (!dialog) return undefined;
    confirmRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape" && !dialog.busy) closeDialog(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [closeDialog, dialog]);

  if (!dialog) return null;
  return (
    <div className="dialog-layer" role="presentation">
      <button
        className="dialog-backdrop"
        type="button"
        onClick={() => !dialog.busy && closeDialog(false)}
        aria-label={t("close")}
      />
      <section className="confirm-dialog motion-enter" role="alertdialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
        <span className="confirm-dialog-icon"><AlertCircle size={22} /></span>
        <div>
          <h2 id="confirm-dialog-title">{dialog.title}</h2>
          <p>{dialog.message}</p>
        </div>
        <div className="confirm-dialog-actions">
          <button className="secondary-button" disabled={dialog.busy} type="button" onClick={() => closeDialog(false)}>
            {t("cancel")}
          </button>
          <button
            className="danger-button"
            disabled={dialog.busy}
            ref={confirmRef}
            type="button"
            onClick={() => closeDialog(true)}
          >
            {dialog.busy ? t("processing") : dialog.confirmLabel || t("confirm")}
          </button>
        </div>
      </section>
    </div>
  );
}
