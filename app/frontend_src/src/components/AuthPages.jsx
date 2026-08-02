import React, { useState } from "react";
import { Eye, EyeOff, Loader2 } from "lucide-react";
import brandMascotImage from "../assets/brand-mascot.png";

export const cx = (...classes) => classes.filter(Boolean).join(" ");

function Field({
  id,
  label,
  type = "text",
  value,
  onChange,
  placeholder,
  error,
  required,
  showToggle,
  toggleLabel,
  onToggle,
}) {
  return (
    <div className="form-field">
      <label htmlFor={id} className="field-label">
        {label}
        {required && <span className="text-app-danger"> *</span>}
      </label>
      <div className="relative">
        <input
          id={id}
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className={cx("input w-full", error && "border-app-danger")}
          required={required}
        />
        {showToggle && (
          <button
            type="button"
            onClick={onToggle}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-app-soft hover:text-app-ink"
            tabIndex={-1}
          >
            {type === "password" ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        )}
      </div>
      {error && <p className="mt-1 text-sm text-app-danger">{error}</p>}
    </div>
  );
}

const REMEMBERED_USERNAME_KEY = "crayotter.rememberedUsername";

export function LoginPage({ onLogin, onSwitchToRegister, onSwitchToReset, t, notify }) {
  const [username, setUsername] = useState(() => localStorage.getItem(REMEMBERED_USERNAME_KEY) || "");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [rememberMe, setRememberMe] = useState(false);
  const [rememberUsername, setRememberUsername] = useState(() =>
    Boolean(localStorage.getItem(REMEMBERED_USERNAME_KEY))
  );
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState({});

  const validate = () => {
    const next = {};
    if (!username.trim()) next.username = t("fieldRequired");
    if (!password) next.password = t("fieldRequired");
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!validate()) return;
    setBusy(true);
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username.trim(),
          password,
          remember_me: rememberMe,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || t("loginFailed"));
      // 记住用户名（非机密，仅存 localStorage）；密码绝不落地浏览器存储
      if (rememberUsername) {
        localStorage.setItem(REMEMBERED_USERNAME_KEY, username.trim());
      } else {
        localStorage.removeItem(REMEMBERED_USERNAME_KEY);
      }
      onLogin(data.user);
    } catch (error) {
      notify("error", error.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center bg-app-bg p-4">
      <div className="w-full max-w-md rounded-2xl bg-app-surface p-8 shadow-sm">
        <div className="mb-8 flex flex-col items-center">
          <img src={brandMascotImage} alt="" className="mb-4 h-16 w-16" />
          <h1 className="text-2xl font-bold text-app-ink">{t("loginTitle")}</h1>
          <p className="mt-1 text-sm text-app-soft">{t("loginSubtitle")}</p>
        </div>
        <form onSubmit={submit} className="grid gap-5">
          <Field
            id="username"
            label={t("username")}
            value={username}
            onChange={setUsername}
            placeholder={t("usernamePlaceholder")}
            error={errors.username}
            required
          />
          <Field
            id="password"
            label={t("password")}
            type={showPassword ? "text" : "password"}
            value={password}
            onChange={setPassword}
            placeholder={t("passwordPlaceholder")}
            error={errors.password}
            required
            showToggle
            onToggle={() => setShowPassword((v) => !v)}
          />
          <div className="grid gap-2 text-sm text-app-soft">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={rememberMe}
                onChange={(e) => setRememberMe(e.target.checked)}
                className="h-4 w-4 accent-app-brand"
              />
              {t("rememberMe")}
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={rememberUsername}
                onChange={(e) => setRememberUsername(e.target.checked)}
                className="h-4 w-4 accent-app-brand"
              />
              {t("rememberUsername")}
            </label>
          </div>
          <button
            type="submit"
            disabled={busy}
            className="primary-button flex w-full items-center justify-center gap-2"
          >
            {busy && <Loader2 size={18} className="animate-spin" />}
            {t("loginButton")}
          </button>
        </form>
        <div className="mt-6 text-center text-sm text-app-soft">
          {t("noAccount")}{" "}
          <button
            type="button"
            onClick={onSwitchToRegister}
            className="font-medium text-app-brand hover:underline"
          >
            {t("registerNow")}
          </button>
        </div>
        <div className="mt-2 text-center text-sm">
          <button
            type="button"
            onClick={onSwitchToReset}
            className="text-app-soft hover:text-app-brand hover:underline"
          >
            {t("forgotPassword")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function ResetPasswordPage({ onDone, onBackToLogin, t, notify }) {
  const [username, setUsername] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState({});

  const validate = () => {
    const next = {};
    if (!username.trim()) next.username = t("fieldRequired");
    if (!recoveryCode.trim()) next.recoveryCode = t("fieldRequired");
    if (!newPassword) next.newPassword = t("fieldRequired");
    else if (newPassword.length < 8) next.newPassword = t("passwordTooShort");
    if (newPassword !== confirmPassword) next.confirmPassword = t("passwordMismatch");
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!validate()) return;
    setBusy(true);
    try {
      const response = await fetch("/api/auth/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username.trim(),
          recovery_code: recoveryCode.trim(),
          new_password: newPassword,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || t("resetFailed"));
      notify("success", t("resetSuccess"));
      onDone();
    } catch (error) {
      notify("error", error.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen place-items-center bg-app-bg p-4">
      <div className="w-full max-w-md rounded-2xl bg-app-surface p-8 shadow-sm">
        <div className="mb-8 flex flex-col items-center">
          <img src={brandMascotImage} alt="" className="mb-4 h-16 w-16" />
          <h1 className="text-2xl font-bold text-app-ink">{t("resetTitle")}</h1>
          <p className="mt-1 text-sm text-app-soft">{t("resetSubtitle")}</p>
        </div>
        <form onSubmit={submit} className="grid gap-5">
          <Field
            id="reset-username"
            label={t("username")}
            value={username}
            onChange={setUsername}
            placeholder={t("usernamePlaceholder")}
            error={errors.username}
            required
          />
          <Field
            id="reset-recovery-code"
            label={t("recoveryCode")}
            value={recoveryCode}
            onChange={setRecoveryCode}
            placeholder={t("recoveryCodePlaceholder")}
            error={errors.recoveryCode}
            required
          />
          <Field
            id="reset-new-password"
            label={t("newPassword")}
            type={showPassword ? "text" : "password"}
            value={newPassword}
            onChange={setNewPassword}
            placeholder={t("newPasswordPlaceholder")}
            error={errors.newPassword}
            required
            showToggle
            onToggle={() => setShowPassword((v) => !v)}
          />
          <Field
            id="reset-confirm-password"
            label={t("confirmPassword")}
            type={showPassword ? "text" : "password"}
            value={confirmPassword}
            onChange={setConfirmPassword}
            placeholder={t("confirmPasswordPlaceholder")}
            error={errors.confirmPassword}
            required
          />
          <button
            type="submit"
            disabled={busy}
            className="primary-button flex w-full items-center justify-center gap-2"
          >
            {busy && <Loader2 size={18} className="animate-spin" />}
            {t("resetButton")}
          </button>
        </form>
        <div className="mt-6 text-center text-sm text-app-soft">
          <button
            type="button"
            onClick={onBackToLogin}
            className="font-medium text-app-brand hover:underline"
          >
            {t("backToLogin")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function RegisterPage({ onRegister, onSwitchToLogin, t, notify }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState({});
  const [result, setResult] = useState(null);

  const validate = () => {
    const next = {};
    if (!username.trim()) next.username = t("fieldRequired");
    else if (username.trim().length < 2) next.username = t("usernameTooShort");
    if (!password) next.password = t("fieldRequired");
    else if (password.length < 8) next.password = t("passwordTooShort");
    if (password !== confirmPassword) next.confirmPassword = t("passwordMismatch");
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!validate()) return;
    setBusy(true);
    try {
      const response = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || t("registerFailed"));
      setResult(data);
    } catch (error) {
      notify("error", error.message);
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <div className="grid min-h-screen place-items-center bg-app-bg p-4">
        <div className="w-full max-w-md rounded-2xl bg-app-surface p-8 shadow-sm">
          <div className="mb-6 flex flex-col items-center">
            <img src={brandMascotImage} alt="" className="mb-4 h-16 w-16" />
            <h1 className="text-2xl font-bold text-app-ink">{t("registerSuccessTitle")}</h1>
          </div>
          <p className="mb-4 text-sm text-app-soft">{t("recoveryCodesHint")}</p>
          <div className="rounded-lg bg-app-panel p-4">
            <ul className="grid grid-cols-2 gap-2 font-mono text-sm text-app-ink">
              {result.recovery_codes.map((code) => (
                <li key={code}>{code}</li>
              ))}
            </ul>
          </div>
          <button
            type="button"
            onClick={() => onRegister(result.user)}
            className="primary-button mt-6 w-full"
          >
            {t("enterWorkbench")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="grid min-h-screen place-items-center bg-app-bg p-4">
      <div className="w-full max-w-md rounded-2xl bg-app-surface p-8 shadow-sm">
        <div className="mb-8 flex flex-col items-center">
          <img src={brandMascotImage} alt="" className="mb-4 h-16 w-16" />
          <h1 className="text-2xl font-bold text-app-ink">{t("registerTitle")}</h1>
          <p className="mt-1 text-sm text-app-soft">{t("registerSubtitle")}</p>
        </div>
        <form onSubmit={submit} className="grid gap-5">
          <Field
            id="reg-username"
            label={t("username")}
            value={username}
            onChange={setUsername}
            placeholder={t("usernamePlaceholder")}
            error={errors.username}
            required
          />
          <Field
            id="reg-password"
            label={t("password")}
            type={showPassword ? "text" : "password"}
            value={password}
            onChange={setPassword}
            placeholder={t("passwordPlaceholder")}
            error={errors.password}
            required
            showToggle
            onToggle={() => setShowPassword((v) => !v)}
          />
          <Field
            id="reg-confirm-password"
            label={t("confirmPassword")}
            type={showPassword ? "text" : "password"}
            value={confirmPassword}
            onChange={setConfirmPassword}
            placeholder={t("confirmPasswordPlaceholder")}
            error={errors.confirmPassword}
            required
          />
          <button
            type="submit"
            disabled={busy}
            className="primary-button flex w-full items-center justify-center gap-2"
          >
            {busy && <Loader2 size={18} className="animate-spin" />}
            {t("registerButton")}
          </button>
        </form>
        <div className="mt-6 text-center text-sm text-app-soft">
          {t("hasAccount")}{" "}
          <button
            type="button"
            onClick={onSwitchToLogin}
            className="font-medium text-app-brand hover:underline"
          >
            {t("loginNow")}
          </button>
        </div>
      </div>
    </div>
  );
}
