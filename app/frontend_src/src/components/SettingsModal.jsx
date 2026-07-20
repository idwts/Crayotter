import React, { useState } from "react";
import { Eye, EyeOff, KeyRound, RefreshCw, Save, Settings2, SlidersHorizontal, Workflow, X } from "lucide-react";
import settingsVisualImage from "../assets/settings-visual.webp";

export function SettingsModal({ configForm, setConfigForm, configMessage, setConfigMessage, submitConfig, loadConfig, setSettingsOpen, notify, t }) {
  const [activeSection, setActiveSection] = useState("api");
  const [reloading, setReloading] = useState(false);
  const update = (key, value) => setConfigForm((current) => ({ ...current, [key]: value }));
  const reload = async () => {
    if (reloading) return;
    setReloading(true);
    try {
      await Promise.all([
        loadConfig(),
        new Promise((resolve) => window.setTimeout(resolve, 350)),
      ]);
      setConfigMessage("");
    } catch (error) {
      const message = t("configReadFailed", { message: error.message });
      setConfigMessage(message);
      notify("error", message);
    } finally {
      setReloading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] grid place-items-center p-3 sm:p-6">
      <button className="absolute inset-0 bg-slate-900/25 backdrop-blur-sm" type="button" onClick={() => setSettingsOpen(false)} aria-label={t("close")} />
      <section className="settings-modal motion-enter relative grid max-h-full w-full max-w-5xl grid-rows-[auto_minmax(0,1fr)] overflow-hidden">
        <header>
          <div className="flex min-w-0 items-center gap-3">
            <span className="settings-title-icon"><Settings2 size={20} /></span>
            <div className="min-w-0">
              <h2>{t("settingsTitle")}</h2>
            </div>
          </div>
          <button className="icon-button" onClick={() => setSettingsOpen(false)} type="button" aria-label={t("close")}><X size={18} /></button>
        </header>
        <form className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto]" onSubmit={(event) => submitConfig(event).catch((error) => setConfigMessage(t("configSaveFailed", { message: error.message })))}>
          <div className="grid min-h-0 md:grid-cols-[220px_minmax(0,1fr)]">
            <aside className="hidden bg-[#F5F7FF] p-4 md:block">
              <img src={settingsVisualImage} alt="" aria-hidden="true" />
              <div className="mt-4 grid gap-2">
                <SettingsNavButton active={activeSection === "api"} icon={KeyRound} label={t("apiKey")} onClick={() => setActiveSection("api")} />
                <SettingsNavButton active={activeSection === "advanced"} icon={SlidersHorizontal} label={t("advanced")} onClick={() => setActiveSection("advanced")} />
              </div>
            </aside>
            <div className="min-h-0 overflow-y-auto bg-[#FAFBFF] p-4 sm:p-6">
              <div className="settings-mobile-tabs md:hidden">
                <SettingsNavButton active={activeSection === "api"} icon={KeyRound} label={t("apiKey")} onClick={() => setActiveSection("api")} />
                <SettingsNavButton active={activeSection === "advanced"} icon={SlidersHorizontal} label={t("advanced")} onClick={() => setActiveSection("advanced")} />
              </div>
              <div className="settings-section-stage" key={activeSection}>
                {activeSection === "api" ? (
                  <SettingSection icon={KeyRound} title={t("apiKey")}>
                    <Field
                      label={t("apiKey")}
                      value={configForm.apiKey}
                      onChange={(value) => update("apiKey", value)}
                      placeholder={t("apiKeyPlaceholder")}
                      type="password"
                      showSecretLabel={t("showSecret")}
                      hideSecretLabel={t("hideSecret")}
                    />
                  </SettingSection>
                ) : (
                  <>
                    <SettingSection icon={Workflow} title={t("workflowOptions")}>
                      <div className="settings-workflow-grid">
                        <WorkflowSetting
                          checked={configForm.enablePhase2Research}
                          disabled={configForm.directPhase3Execution}
                          label={t("phase2Setting")}
                          onChange={(value) => update("enablePhase2Research", value)}
                        />
                        <WorkflowSetting
                          checked={configForm.directPhase3Execution}
                          label={t("directP3")}
                          onChange={(value) => update("directPhase3Execution", value)}
                        />
                        <WorkflowSetting
                          checked={configForm.preferLocalMaterials}
                          disabled={configForm.directPhase3Execution}
                          label={t("localFirst")}
                          onChange={(value) => update("preferLocalMaterials", value)}
                        />
                      </div>
                    </SettingSection>
                    <SettingSection icon={SlidersHorizontal} title={t("advanced")}>
                      <div className="grid gap-4 md:grid-cols-2">
                        <Field label={t("baseUrl")} value={configForm.baseUrl} onChange={(value) => update("baseUrl", value)} placeholder="https://.../v1" />
                        <Field label={t("model")} value={configForm.model} onChange={(value) => update("model", value)} placeholder="qwen-plus" />
                        <Field
                          label={t("videoApiKey")}
                          value={configForm.videoApiKey}
                          onChange={(value) => update("videoApiKey", value)}
                          placeholder={t("videoApiKeyPlaceholder")}
                          type="password"
                          showSecretLabel={t("showSecret")}
                          hideSecretLabel={t("hideSecret")}
                        />
                        <Field label={t("videoBaseUrl")} value={configForm.videoBaseUrl} onChange={(value) => update("videoBaseUrl", value)} placeholder={t("optionalBaseUrlPlaceholder")} />
                        <Field label={t("videoModel")} value={configForm.videoModel} onChange={(value) => update("videoModel", value)} placeholder="qwen-vl-max-latest" />
                        <Field
                          label={t("ttsApiKey")}
                          value={configForm.ttsApiKey}
                          onChange={(value) => update("ttsApiKey", value)}
                          placeholder={t("ttsApiKeyPlaceholder")}
                          type="password"
                          showSecretLabel={t("showSecret")}
                          hideSecretLabel={t("hideSecret")}
                        />
                        <Field label={t("ttsBaseUrl")} value={configForm.ttsBaseUrl} onChange={(value) => update("ttsBaseUrl", value)} placeholder={t("optionalBaseUrlPlaceholder")} />
                        <Field label={t("ttsModel")} value={configForm.ttsModel} onChange={(value) => update("ttsModel", value)} placeholder="qwen-tts-latest" />
                        <Field label={t("stallTimeout")} value={configForm.stallTimeout} onChange={(value) => update("stallTimeout", value)} placeholder="150" type="number" />
                        <Field label="YouTube 模式 (auto/on/off)" value={configForm.youtubeMode} onChange={(value) => update("youtubeMode", value)} placeholder="auto" />
                        <Field label="素材平台（逗号分隔）" value={configForm.enabledMaterialPlatforms} onChange={(value) => update("enabledMaterialPlatforms", value)} placeholder="bilibili,douyin,xiaohongshu,youtube" />
                        <Field label="默认处理时限（秒）" value={configForm.defaultDeadlineSeconds} onChange={(value) => update("defaultDeadlineSeconds", value)} placeholder="600" type="number" />
                        <Field label="处理模式 (auto/speed/quality)" value={configForm.processingMode} onChange={(value) => update("processingMode", value)} placeholder="auto" />
                        <Field label="输出规格" value={configForm.outputProfile} onChange={(value) => update("outputProfile", value)} placeholder="auto" />
                        <Field label="授权浏览器 (chrome/msedge)" value={configForm.browserAuthBrowser} onChange={(value) => update("browserAuthBrowser", value)} placeholder="chrome" />
                        <Field label="浏览器 Profile 目录" value={configForm.browserAuthProfile} onChange={(value) => update("browserAuthProfile", value)} placeholder="请输入 Default/Profile 目录的完整路径" />
                      </div>
                    </SettingSection>
                    <SettingSection icon={SlidersHorizontal} title={t("resourcePools")}>
                      <div className="settings-env-note">
                        <strong>{t("resourcePoolsEnvOnlyTitle")}</strong>
                        <p>{t("resourcePoolsEnvOnlyBody")}</p>
                        <code>CRAYOTTER_SEARCH_POOL_SIZE / CRAYOTTER_DOWNLOAD_POOL_SIZE / CRAYOTTER_VIDEO_ANALYSIS_POOL_SIZE / CRAYOTTER_LLM_POOL_SIZE / CRAYOTTER_FFMPEG_POOL_SIZE / CRAYOTTER_TTS_POOL_SIZE / CRAYOTTER_EXPORT_POOL_SIZE</code>
                      </div>
                    </SettingSection>
                  </>
                )}
              </div>
            </div>
          </div>
          <footer>
            <div className="min-w-0 text-xs text-slate-500">{configMessage || ""}</div>
            <div className="flex shrink-0 gap-2">
              <button className="secondary-button" disabled={reloading} type="button" onClick={reload} aria-busy={reloading}>
                <RefreshCw className={reloading ? "icon-spin" : undefined} size={16} />{t("reload")}
              </button>
              <button className="primary-button" type="submit"><Save size={16} />{t("save")}</button>
            </div>
          </footer>
        </form>
      </section>
    </div>
  );
}

function SettingsNavButton({ active, icon: Icon, label, onClick }) {
  return (
    <button className={`settings-nav${active ? " active" : ""}`} type="button" onClick={onClick}>
      <Icon size={16} />
      <span>{label}</span>
    </button>
  );
}

function SettingSection({ icon: Icon, title, children }) {
  return (
    <section className="settings-section">
      <h3><Icon size={16} />{title}</h3>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function WorkflowSetting({ checked, disabled = false, label, onChange }) {
  return (
    <label className={`settings-workflow-option${disabled ? " disabled" : ""}`}>
      <span>{label}</span>
      <input
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
        type="checkbox"
      />
      <span className="settings-switch" aria-hidden="true"><span /></span>
    </label>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  showSecretLabel = "Show secret",
  hideSecretLabel = "Hide secret",
}) {
  const [secretVisible, setSecretVisible] = useState(false);
  const isSecret = type === "password";
  const inputType = isSecret && secretVisible ? "text" : type;

  return (
    <label className="grid gap-2">
      <span className="text-xs font-medium text-slate-500">{label}</span>
      <span className={isSecret ? "settings-secret-field" : undefined}>
        <input type={inputType} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
        {isSecret && (
          <button
            type="button"
            onClick={() => setSecretVisible((visible) => !visible)}
            aria-label={secretVisible ? hideSecretLabel : showSecretLabel}
            title={secretVisible ? hideSecretLabel : showSecretLabel}
          >
            {secretVisible ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        )}
      </span>
    </label>
  );
}
