import React, { useState } from "react";
import { Clapperboard, FolderOpen, History, KeyRound, LayoutDashboard, X } from "lucide-react";
import brandMascotImage from "../assets/brand-mascot.png";
import { cx } from "./AuthPages";

const STEPS = [
  { icon: LayoutDashboard, titleKey: "onboardingStep1Title", descKey: "onboardingStep1Desc" },
  { icon: FolderOpen, titleKey: "onboardingStep2Title", descKey: "onboardingStep2Desc" },
  { icon: History, titleKey: "onboardingStep3Title", descKey: "onboardingStep3Desc" },
  { icon: Clapperboard, titleKey: "onboardingStep4Title", descKey: "onboardingStep4Desc" },
  { icon: KeyRound, titleKey: "onboardingStep5Title", descKey: "onboardingStep5Desc" },
];

/** 首次登录使用引导：五步轮播，可跳过；完成后由调用方落 localStorage 标记。 */
export function OnboardingDialog({ onDone, t }) {
  const [step, setStep] = useState(0);
  const last = step === STEPS.length - 1;
  const Current = STEPS[step];

  return (
    <div className="fixed inset-0 z-[80] grid place-items-center bg-slate-900/45 p-4" role="dialog" aria-modal="true" aria-label={t("onboardingTitle")}>
      <div className="w-full max-w-md rounded-2xl bg-app-surface p-8 shadow-xl">
        <div className="mb-6 flex items-start justify-between">
          <div className="flex items-center gap-3">
            <img src={brandMascotImage} alt="" className="h-12 w-12" />
            <div>
              <h2 className="text-lg font-bold text-app-ink">{t("onboardingTitle")}</h2>
              <p className="text-xs text-app-soft">{t("onboardingSubtitle")}</p>
            </div>
          </div>
          <button type="button" className="icon-button" onClick={onDone} aria-label={t("onboardingSkip")}>
            <X size={18} />
          </button>
        </div>

        <div className="onboarding-step motion-enter" key={step}>
          <div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-app-panel text-app-brand">
            <Current.icon size={26} />
          </div>
          <h3 className="text-base font-semibold text-app-ink">{t(Current.titleKey)}</h3>
          <p className="mt-2 text-sm leading-6 text-app-soft">{t(Current.descKey)}</p>
        </div>

        <div className="mt-6 flex items-center justify-center gap-1.5">
          {STEPS.map((item, index) => (
            <button
              key={item.titleKey}
              type="button"
              onClick={() => setStep(index)}
              aria-label={`${index + 1}`}
              className={cx(
                "h-1.5 rounded-full transition-all",
                index === step ? "w-6 bg-app-brand" : "w-1.5 bg-app-line",
              )}
            />
          ))}
        </div>

        <div className="mt-6 flex items-center justify-between">
          <button type="button" className="text-sm text-app-soft hover:text-app-ink" onClick={onDone}>
            {t("onboardingSkip")}
          </button>
          <div className="flex gap-2">
            {step > 0 && (
              <button type="button" className="secondary-button" onClick={() => setStep(step - 1)}>
                {t("onboardingPrev")}
              </button>
            )}
            <button
              type="button"
              className="primary-button"
              onClick={() => (last ? onDone() : setStep(step + 1))}
            >
              {last ? t("onboardingStart") : t("onboardingNext")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
