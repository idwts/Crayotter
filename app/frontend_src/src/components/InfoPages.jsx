import React from "react";
import { ArrowLeft, FileText, Layers } from "lucide-react";
import brandMascotImage from "../assets/brand-mascot.png";

/** 信息页通用骨架：认证流（未登录）与应用内视图共用。 */
function InfoPage({ icon: Icon, title, onBack, backLabel, children }) {
  return (
    <div className="grid min-h-full place-items-center bg-app-bg p-4">
      <div className="w-full max-w-2xl rounded-2xl bg-app-surface p-8 shadow-sm">
        <div className="mb-6 flex items-center gap-3">
          <img src={brandMascotImage} alt="" className="h-10 w-10" />
          <Icon size={22} className="text-app-brand" />
          <h1 className="text-xl font-bold text-app-ink">{title}</h1>
        </div>
        <div className="grid gap-3 text-sm leading-6 text-app-soft">{children}</div>
        <button
          type="button"
          onClick={onBack}
          className="secondary-button mt-8 inline-flex items-center gap-2"
        >
          <ArrowLeft size={16} />
          {backLabel}
        </button>
      </div>
    </div>
  );
}

export function UserAgreementPage({ onBack, t }) {
  return (
    <InfoPage icon={FileText} title={t("userAgreement")} onBack={onBack} backLabel={t("back")}>
      <h2 className="text-base font-semibold text-app-ink">{t("agreementDevTitle")}</h2>
      <p>{t("agreementDevBody")}</p>
      <p>{t("agreementDevNote")}</p>
    </InfoPage>
  );
}

export function TechOverviewPage({ onBack, t }) {
  return (
    <InfoPage icon={Layers} title={t("techOverview")} onBack={onBack} backLabel={t("back")}>
      <h2 className="text-base font-semibold text-app-ink">{t("techDevTitle")}</h2>
      <p>{t("techDevBody")}</p>
      <p>{t("techDevNote")}</p>
    </InfoPage>
  );
}
