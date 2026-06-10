import React, { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Archive,
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleStop,
  Clock3,
  Download,
  FileVideo2,
  FolderOpen,
  History,
  Languages,
  LayoutDashboard,
  Menu,
  Paperclip,
  Play,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  Sparkles,
  ScrollText,
  Trash2,
  Upload,
  Wrench,
  WandSparkles,
  Workflow,
  X,
} from "lucide-react";
import artifactCompactImage from "../assets/artifact-empty-compact.webp";
import artifactWideImage from "../assets/artifact-empty-wide.webp";
import brandMascotImage from "../assets/brand-mascot.png";
import workflowEmptyImage from "../assets/workflow-empty.webp";

export const cx = (...classes) => classes.filter(Boolean).join(" ");

const NAV_ITEMS = [
  { id: "workbench", icon: LayoutDashboard, labelKey: "navWorkbench" },
  { id: "jobs", icon: History, labelKey: "navJobs" },
  { id: "materials", icon: FolderOpen, labelKey: "navMaterials" },
  { id: "artifacts", icon: Archive, labelKey: "navArtifacts" },
];

function BrandMark({ button = false, onClick }) {
  const content = <img src={brandMascotImage} alt="" aria-hidden="true" />;
  if (button) {
    return (
      <button className="brand-mark" onClick={onClick} type="button" aria-label="Crayotter">
        {content}
      </button>
    );
  }
  return <span className="brand-mark">{content}</span>;
}

export function AppSidebar({
  collapsed,
  setCollapsed,
  currentView,
  setCurrentView,
  jobs,
  selectedJobId,
  selectJob,
  displayTaskTitle,
  statusLabel,
  setSettingsOpen,
  notify,
  t,
}) {
  return (
    <aside
      className={cx(
        "app-sidebar hidden min-h-0 lg:flex",
        collapsed ? "w-[84px]" : "w-[272px]"
      )}
    >
      <div className="flex h-full min-h-0 w-full flex-col px-3 py-4">
        <div className={cx("flex items-center", collapsed ? "justify-center" : "justify-between px-2")}>
          <BrandMark button onClick={() => setCurrentView("workbench")} />
          {!collapsed && (
            <div className="min-w-0 flex-1 px-3">
              <div className="truncate text-[15px] font-bold text-slate-900">Crayotter</div>
              <div className="truncate text-[11px] text-slate-400">AI Video Studio</div>
            </div>
          )}
          {!collapsed && (
            <button className="icon-button" onClick={() => setCollapsed(true)} type="button" aria-label={t("collapseSidebar")}>
              <ChevronLeft size={17} />
            </button>
          )}
        </div>

        {collapsed && (
          <button className="icon-button mx-auto mt-3" onClick={() => setCollapsed(false)} type="button" aria-label={t("expandSidebar")}>
            <ChevronRight size={17} />
          </button>
        )}

        <nav className="mt-7 grid gap-1.5">
          {NAV_ITEMS.map(({ id, icon: Icon, labelKey }) => (
            <button
              key={id}
              className={cx("nav-item", currentView === id && "nav-item-active", collapsed && "justify-center px-0")}
              onClick={() => setCurrentView(id)}
              type="button"
              title={collapsed ? t(labelKey) : undefined}
            >
              <Icon size={18} strokeWidth={2} />
              {!collapsed && <span>{t(labelKey)}</span>}
            </button>
          ))}
        </nav>

        {!collapsed && (
          <div className="mt-6 flex min-h-0 flex-1 flex-col">
            <div className="flex items-center justify-between px-3">
              <span className="text-[11px] font-semibold uppercase text-slate-400">{t("recentTasks")}</span>
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-semibold text-indigo-500">{jobs.length}</span>
            </div>
            <div className="mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
              {jobs.slice(0, 8).map((job) => (
                <button
                  key={job.job_id}
                  className={cx("sidebar-job", selectedJobId === job.job_id && "sidebar-job-active")}
                  onClick={() => selectJob(job.job_id).catch((error) => notify("error", t("operationFailed", { message: error.message })))}
                  type="button"
                >
                  <span className={cx("status-dot", `status-${job.status}`)} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs font-medium text-slate-700">{displayTaskTitle(job)}</span>
                    <span className="mt-0.5 block truncate text-[10px] text-slate-400">{statusLabel(job.status)}</span>
                  </span>
                </button>
              ))}
              {!jobs.length && <div className="px-3 py-4 text-xs leading-5 text-slate-400">{t("noJobs")}</div>}
            </div>
          </div>
        )}

        <div className="mt-auto grid gap-1.5 border-t border-slate-100 pt-3">
          <button
            className={cx("nav-item", collapsed && "justify-center px-0")}
            onClick={() => setSettingsOpen(true)}
            type="button"
            title={collapsed ? t("settings") : undefined}
          >
            <Settings size={18} />
            {!collapsed && <span>{t("settings")}</span>}
          </button>
        </div>
      </div>
    </aside>
  );
}

export function AppTopbar({
  currentView,
  language,
  setLanguage,
  uploading,
  uploadSelectedFiles,
  setSettingsOpen,
  setMobileDrawerOpen,
  t,
}) {
  const inputRef = useRef(null);
  return (
    <header className="app-topbar">
      <div className="flex min-w-0 items-center gap-3">
        <button className="icon-button lg:hidden" onClick={() => setMobileDrawerOpen(true)} type="button" aria-label={t("mobileMenu")}>
          <Menu size={19} />
        </button>
        <div className="min-w-0">
          <h1 className="truncate text-lg font-bold text-slate-900 sm:text-xl">{t(`view_${currentView}`)}</h1>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <div className="language-switch hidden sm:flex" aria-label={t("languageSwitch")}>
          <Languages size={15} className="text-slate-400" />
          <button className={language === "zh" ? "active" : ""} onClick={() => setLanguage("zh")} type="button">中</button>
          <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")} type="button">EN</button>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".mp4,.mov,.mkv,.avi,.webm,.m4v,.mpeg,.mpg,video/*"
          multiple
          hidden
          onChange={(event) => uploadSelectedFiles(event.target.files).finally(() => { event.target.value = ""; })}
        />
        <button className="topbar-action hidden sm:inline-flex" onClick={() => inputRef.current?.click()} disabled={uploading} type="button">
          <Upload size={16} />
          <span>{uploading ? t("uploading") : t("upload")}</span>
        </button>
        <button className="avatar-button" onClick={() => setSettingsOpen(true)} type="button" aria-label={t("settings")}>
          CR
        </button>
      </div>
    </header>
  );
}

export function MobileDrawer({
  open,
  setOpen,
  currentView,
  setCurrentView,
  jobs,
  selectedJobId,
  selectJob,
  displayTaskTitle,
  setSettingsOpen,
  notify,
  t,
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 lg:hidden">
      <button className="absolute inset-0 bg-slate-900/25 backdrop-blur-sm" onClick={() => setOpen(false)} type="button" aria-label={t("close")} />
      <aside className="drawer-enter absolute inset-y-0 left-0 flex w-[min(84vw,320px)] flex-col bg-white p-4 shadow-2xl">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <BrandMark />
            <div>
              <div className="font-bold text-slate-900">Crayotter</div>
              <div className="text-[11px] text-slate-400">AI Video Studio</div>
            </div>
          </div>
          <button className="icon-button" onClick={() => setOpen(false)} type="button"><X size={18} /></button>
        </div>
        <nav className="mt-6 grid gap-1.5">
          {NAV_ITEMS.map(({ id, icon: Icon, labelKey }) => (
            <button
              key={id}
              className={cx("nav-item", currentView === id && "nav-item-active")}
              onClick={() => { setCurrentView(id); setOpen(false); }}
              type="button"
            >
              <Icon size={18} />
              <span>{t(labelKey)}</span>
            </button>
          ))}
        </nav>
        <div className="mt-6 min-h-0 flex-1 overflow-y-auto">
          <div className="px-3 text-[11px] font-semibold uppercase text-slate-400">{t("recentTasks")}</div>
          <div className="mt-2 grid gap-1">
            {jobs.map((job) => (
              <button
                key={job.job_id}
                className={cx("sidebar-job", selectedJobId === job.job_id && "sidebar-job-active")}
                onClick={() => {
                  selectJob(job.job_id).then(() => {
                    setCurrentView("workbench");
                    setOpen(false);
                  }).catch((error) => notify("error", t("operationFailed", { message: error.message })));
                }}
                type="button"
              >
                <span className={cx("status-dot", `status-${job.status}`)} />
                <span className="truncate text-xs font-medium">{displayTaskTitle(job)}</span>
              </button>
            ))}
          </div>
        </div>
        <button className="nav-item mt-3 border-t border-slate-100 pt-4" onClick={() => { setOpen(false); setSettingsOpen(true); }} type="button">
          <Settings size={18} />
          <span>{t("settings")}</span>
        </button>
      </aside>
    </div>
  );
}

export function MobileBottomNav({ currentView, setCurrentView, t }) {
  return (
    <nav className="mobile-bottom-nav lg:hidden">
      {NAV_ITEMS.map(({ id, icon: Icon, labelKey }) => (
        <button className={currentView === id ? "active" : ""} key={id} onClick={() => setCurrentView(id)} type="button">
          <Icon size={19} />
          <span>{t(labelKey)}</span>
        </button>
      ))}
    </nav>
  );
}

export function WorkbenchView(props) {
  const {
    selectedJob,
    selectedMeaningfulEvents,
    logEvents,
    summary,
    currentPhase,
    currentPhaseCode,
    progressCards,
    primaryArtifacts,
    uploads,
    activeTab,
    setActiveTab,
    displayTaskTitle,
    modeLabel,
    statusLabel,
    formatDate,
    describeEvent,
    artifactLabel,
    fileUrl,
    downloadFileUrl,
    uploadAnalysisBadgeLabel,
    uploadAnalysisHint,
    formatBytes,
    formatDuration,
    setTaskText,
    deleteUpload,
    loadUploads,
    composerProps,
    copyFullLog,
    downloadFullLog,
    notify,
    t,
  } = props;

  const latestCards = progressCards.length
    ? progressCards
    : selectedMeaningfulEvents.slice(-5).map((event) => ({ event, item: describeEvent(event) }));

  return (
    <div className="workspace-view">
      <OverviewStrip
        selectedJob={selectedJob}
        eventsCount={logEvents.length}
        uploadsCount={uploads.length}
        artifactsCount={primaryArtifacts.length}
        currentPhase={currentPhase}
        statusLabel={statusLabel}
        t={t}
      />

      <section className="workspace-grid">
        <div className="execution-column">
          <TaskHero
            selectedJob={selectedJob}
            summary={summary}
            displayTaskTitle={displayTaskTitle}
            statusLabel={statusLabel}
            modeLabel={modeLabel}
            formatDate={formatDate}
            t={t}
          />
          <PhaseTracker selectedJob={selectedJob} currentPhaseCode={currentPhaseCode} t={t} />
          <EventTimeline
            events={latestCards}
            selectedJob={selectedJob}
            formatDate={formatDate}
            t={t}
          />
        </div>

        <ContextPanel activeTab={activeTab} setActiveTab={setActiveTab} uploadsCount={uploads.length} artifactsCount={primaryArtifacts.length} t={t}>
          {activeTab === "details" && (
            <DetailsTab
              selectedJob={selectedJob}
              logEvents={logEvents}
              modeLabel={modeLabel}
              statusLabel={statusLabel}
              formatDate={formatDate}
              describeEvent={describeEvent}
              copyFullLog={copyFullLog}
              downloadFullLog={downloadFullLog}
              t={t}
            />
          )}
          {activeTab === "materials" && (
            <MaterialsList
              uploads={uploads}
              uploadAnalysisBadgeLabel={uploadAnalysisBadgeLabel}
              uploadAnalysisHint={uploadAnalysisHint}
              formatBytes={formatBytes}
              formatDate={formatDate}
              fileUrl={fileUrl}
              setTaskText={setTaskText}
              deleteUpload={deleteUpload}
              loadUploads={loadUploads}
              notify={notify}
              compact
              t={t}
            />
          )}
          {activeTab === "artifacts" && (
            <ArtifactsList
              artifacts={primaryArtifacts}
              artifactLabel={artifactLabel}
              fileUrl={fileUrl}
              downloadFileUrl={downloadFileUrl}
              formatBytes={formatBytes}
              formatDuration={formatDuration}
              compact
              t={t}
            />
          )}
          {activeTab === "trace" && (
            <AgentTrace events={selectedMeaningfulEvents} formatDate={formatDate} describeEvent={describeEvent} t={t} />
          )}
        </ContextPanel>
      </section>

      <Composer {...composerProps} t={t} />
    </div>
  );
}

function OverviewStrip({ selectedJob, eventsCount, uploadsCount, artifactsCount, currentPhase, statusLabel, t }) {
  const stats = [
    {
      icon: Activity,
      label: t("overviewStatus"),
      value: selectedJob ? statusLabel(selectedJob.status) : t("idle"),
      tone: "indigo",
    },
    { icon: Workflow, label: t("overviewPhase"), value: selectedJob ? currentPhase : t("notStarted"), tone: "violet" },
    { icon: ScrollText, label: t("overviewEvents"), value: String(eventsCount), suffix: t("itemsUnit"), tone: "sky" },
    { icon: FolderOpen, label: t("overviewAssets"), value: String(uploadsCount + artifactsCount), suffix: t("itemsUnit"), tone: "emerald" },
  ];
  return (
    <section className="overview-grid">
      {stats.map(({ icon: Icon, label, value, suffix, tone }, index) => (
        <article className={cx("metric-card", `metric-${tone}`)} key={label} style={{ animationDelay: `${index * 55}ms` }}>
          <span className="metric-icon"><Icon size={18} /></span>
          <div className="min-w-0">
            <div className="text-[11px] font-medium text-slate-400">{label}</div>
            <div className="mt-1 flex min-w-0 items-baseline gap-1">
              <strong className="truncate text-[15px] font-bold text-slate-800">{value}</strong>
              {suffix && <span className="text-[10px] text-slate-400">{suffix}</span>}
            </div>
          </div>
        </article>
      ))}
    </section>
  );
}

function TaskHero({ selectedJob, summary, displayTaskTitle, statusLabel, modeLabel, formatDate, t }) {
  return (
    <section className="task-hero motion-enter">
      <div className="task-hero-content relative z-10 min-w-0">
        <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold text-indigo-500">
          <WandSparkles size={15} />
          <span>{t("currentTask")}</span>
        </div>
        <h2 className="mt-3 max-w-3xl break-words text-xl font-bold leading-8 text-slate-900 sm:text-2xl">
          {selectedJob ? displayTaskTitle(selectedJob) : t("readyTitle")}
        </h2>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
          {selectedJob ? summary : t("readyBody")}
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <StatusPill status={selectedJob?.status || "pending"} label={selectedJob ? statusLabel(selectedJob.status) : t("idle")} />
          {selectedJob && <span className="soft-chip">{modeLabel(selectedJob.mode)}</span>}
          {selectedJob && <span className="soft-chip"><Clock3 size={13} />{formatDate(selectedJob.started_at || selectedJob.created_at)}</span>}
        </div>
      </div>
      <div className="hero-orbit hidden sm:block" aria-hidden="true">
        <span />
        <span />
        <WandSparkles size={28} />
      </div>
    </section>
  );
}

function PhaseTracker({ selectedJob, currentPhaseCode, t }) {
  const jobStatus = selectedJob?.status;
  const completed = jobStatus === "completed";
  const running = jobStatus === "running";
  const terminalStatus = jobStatus === "failed" ? "failed" : jobStatus === "cancelled" ? "stopped" : null;
  const activePhaseIndex = currentPhaseCode === "phase3" ? 2 : currentPhaseCode === "phase2" ? 1 : 0;
  const labels = [t("materialPrep"), t("editingResearch"), t("finalAssembly")];
  const steps = labels.map((label, index) => {
    let status = "idle";
    if (completed || index < activePhaseIndex) {
      status = "done";
    } else if (index === activePhaseIndex && running) {
      status = "active";
    } else if (index === activePhaseIndex && terminalStatus) {
      status = terminalStatus;
    }
    return { label, status };
  });
  const stepStatusLabel = (status) => {
    if (status === "done") return t("done");
    if (status === "active") return t("active");
    if (status === "failed") return t("statusFailed");
    if (status === "stopped") return t("statusCancelled");
    return t("idle");
  };
  return (
    <section className="soft-section phase-tracker-section motion-enter">
      <div className="section-heading">
        <div>
          <div className="eyebrow">{t("phaseTrack")}</div>
          <h3>{t("workflowProgress")}</h3>
        </div>
        <span className="text-xs text-slate-400">{steps.filter((step) => step.status === "done").length}/3</span>
      </div>
      <div className="phase-grid">
        {steps.map((step, index) => (
          <div className={cx("phase-step", `phase-${step.status}`)} key={step.label}>
            <div className="phase-number">
              {step.status === "done"
                ? <Check size={15} />
                : ["failed", "stopped"].includes(step.status)
                  ? <X size={15} />
                  : index + 1}
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-slate-700">{step.label}</div>
              <div className="mt-1 text-[11px] text-slate-400">{stepStatusLabel(step.status)}</div>
            </div>
            {index < steps.length - 1 && <span className="phase-connector" />}
          </div>
        ))}
      </div>
    </section>
  );
}

function EventTimeline({ events, selectedJob, formatDate, t }) {
  return (
    <section className="soft-section execution-stream-section motion-enter">
      <div className="section-heading">
        <div>
          <div className="eyebrow">{t("executionStream")}</div>
          <h3>{t("recentActivity")}</h3>
        </div>
        {selectedJob?.status === "running" && <span className="live-label"><span />LIVE</span>}
      </div>
      <div className={cx("execution-stream-body", !events.length && "empty")}>
        {events.length ? (
          <div className="timeline-list">
            {events.map(({ event, item }, index) => (
              <article className="timeline-row" key={event.sequence || `${event.type}-${event.timestamp}`} style={{ animationDelay: `${index * 45}ms` }}>
                <div className="timeline-marker"><span /></div>
                <div className="timeline-content min-w-0 flex-1 pb-4">
                  <div className="timeline-heading flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                    <h4 className="timeline-title text-sm font-semibold text-slate-700">{item.title}</h4>
                    <time className="timeline-time whitespace-nowrap text-[10px] text-slate-400">{formatDate(event.timestamp)}</time>
                  </div>
                  <p className="timeline-description mt-1 break-words text-xs leading-5 text-slate-500">{item.body}</p>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-stage">
            <img src={workflowEmptyImage} alt="" aria-hidden="true" />
            <div>
              <h4>{t("readyTitle")}</h4>
              <p>{t("noEvents")}</p>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function ContextPanel({ activeTab, setActiveTab, uploadsCount, artifactsCount, children, t }) {
  const tabs = [
    { id: "details", label: t("details") },
    { id: "trace", label: t("agentTrace") },
    { id: "materials", label: t("navMaterials"), count: uploadsCount },
    { id: "artifacts", label: t("artifacts"), count: artifactsCount },
  ];
  return (
    <aside className="context-panel">
      <div className="context-tabs">
        {tabs.map((tab) => (
          <button className={activeTab === tab.id ? "active" : ""} key={tab.id} onClick={() => setActiveTab(tab.id)} type="button">
            <span>{tab.label}</span>
            {tab.count > 0 && <small>{tab.count}</small>}
          </button>
        ))}
      </div>
      <div className={cx("context-panel-scroll", activeTab === "trace" && "context-panel-scroll-trace")}>
        {children}
      </div>
    </aside>
  );
}

function DetailsTab({ selectedJob, logEvents, modeLabel, statusLabel, formatDate, describeEvent, copyFullLog, downloadFullLog, t }) {
  if (!selectedJob) {
    return (
      <div className="context-start-state">
        <ContextEmpty icon={Sparkles} title={t("notStarted")} body={t("waitingMore")} />
        <div className="start-checklist">
          <div className="start-checklist-title">{t("beforeStart")}</div>
          <StartCheck index="1" label={t("beforeStartTask")} />
          <StartCheck index="2" label={t("beforeStartMaterials")} />
          <StartCheck index="3" label={t("beforeStartMode")} />
        </div>
      </div>
    );
  }
  const rows = [
    [t("status"), statusLabel(selectedJob.status)],
    [t("mode"), modeLabel(selectedJob.mode)],
    [t("createdAt"), formatDate(selectedJob.created_at)],
  ];
  return (
    <div className="grid gap-4">
      <div className="detail-list">
        {rows.map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong title={String(value)}>{value}</strong>
          </div>
        ))}
      </div>
      <div className="mini-section">
        <h4>{t("logTitle")}</h4>
        <div className="mt-3 max-h-48 space-y-2 overflow-y-auto">
          {logEvents.slice(-6).map((event) => (
            <div className="log-preview-row" key={event.sequence || `${event.type}-${event.timestamp}`}>
              <span>{formatDate(event.timestamp)}</span>
              <p>{describeEvent(event).body}</p>
            </div>
          ))}
          {!logEvents.length && <p className="text-xs leading-5 text-slate-400">{t("noEvents")}</p>}
        </div>
        <div className="mt-3 flex gap-2">
          <button className="text-action" onClick={copyFullLog} type="button">{t("copyFullLog")}</button>
          <button className="text-action" onClick={downloadFullLog} type="button">{t("downloadLog")}</button>
        </div>
      </div>
    </div>
  );
}

function AgentTrace({ events, formatDate, describeEvent, t }) {
  const [filter, setFilter] = useState("all");
  const supportedTypes = new Set([
    "phase_started",
    "phase_state",
    "plan_created",
    "plan_summary",
    "thinking_summary",
    "step_scheduled",
    "dependency_waiting",
    "step_started",
    "step_completed",
    "step_failed",
    "step_result",
    "tool_called",
    "tool_result",
    "blueprint_created",
    "artifact_created",
    "job_completed",
    "task_completed",
    "job_failed",
    "task_failed",
    "job_stalled",
    "job_cancelled",
  ]);
  const items = [];
  const unmatchedTools = [];

  events.filter((event) => supportedTypes.has(event.type)).forEach((event) => {
    const phase = event.payload?.phase || (event.type.startsWith("job_") || event.type.startsWith("task_") ? "general" : "general");
    if (event.type === "tool_called") {
      const item = { event, phase, category: "tools", result: null };
      items.push(item);
      unmatchedTools.push(item);
      return;
    }
    if (event.type === "tool_result") {
      const toolName = event.payload?.tool_name;
      const matched = [...unmatchedTools].reverse().find((item) => (
        !item.result && (!toolName || item.event.payload?.tool_name === toolName)
      ));
      if (matched) {
        matched.result = event;
        return;
      }
    }
    const category = ["job_failed", "task_failed", "job_stalled", "step_failed"].includes(event.type)
      ? "errors"
      : ["phase_started", "phase_state", "plan_created", "plan_summary", "thinking_summary", "blueprint_created"].includes(event.type)
        ? "phases"
        : event.type.startsWith("tool_")
          ? "tools"
          : "steps";
    items.push({ event, phase, category, result: null });
  });

  const visible = filter === "all" ? items : items.filter((item) => item.category === filter);
  const groups = visible.reduce((result, item) => {
    const key = item.phase || "general";
    if (!result.has(key)) result.set(key, []);
    result.get(key).push(item);
    return result;
  }, new Map());
  const filters = [
    ["all", t("traceAll")],
    ["phases", t("tracePhases")],
    ["tools", t("traceTools")],
    ["errors", t("traceErrors")],
  ];
  const itemPresentation = (item) => {
    if (item.category === "errors") return { icon: AlertTriangle, title: t("traceError"), tone: "error" };
    if (item.event.type.startsWith("tool_")) return { icon: Wrench, title: t("traceTool"), tone: "tool" };
    if (item.event.type.includes("plan") || item.event.type === "blueprint_created") return { icon: ScrollText, title: t("tracePlan"), tone: "phase" };
    if (item.category === "phases") return { icon: Workflow, title: describeEvent(item.event).title, tone: "phase" };
    return { icon: Check, title: t("traceStep"), tone: "step" };
  };

  return (
    <div className="agent-trace">
      <div className="trace-filters" role="tablist" aria-label={t("agentTrace")}>
        {filters.map(([value, label]) => (
          <button className={filter === value ? "active" : ""} key={value} onClick={() => setFilter(value)} type="button">
            {label}
          </button>
        ))}
      </div>
      {groups.size ? (
        <div className="trace-groups">
          {[...groups.entries()].map(([phase, phaseItems]) => (
            <section className="trace-group" key={phase}>
              <h4>{phase === "general" ? t("traceGeneralGroup") : t("tracePhaseGroup", { phase: phase.replace("phase", "") })}</h4>
              <div className="trace-items">
                {phaseItems.map((item) => {
                  const presentation = itemPresentation(item);
                  const Icon = presentation.icon;
                  const described = describeEvent(item.event);
                  const result = item.result ? describeEvent(item.result) : null;
                  return (
                    <article className={`trace-item trace-${presentation.tone}`} key={item.event.sequence || `${item.event.type}-${item.event.timestamp}`}>
                      <span><Icon size={15} /></span>
                      <div className="min-w-0">
                        <div className="trace-item-heading">
                          <strong>{presentation.title}</strong>
                          <time>{formatDate(item.event.timestamp)}</time>
                        </div>
                        <p>{described.body}</p>
                        {result && <div className="trace-tool-result"><Check size={13} />{result.body}</div>}
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <ContextEmpty icon={Bot} title={t("traceEmptyTitle")} body={t("traceEmptyBody")} />
      )}
    </div>
  );
}

function StartCheck({ index, label }) {
  return (
    <div className="start-check-row">
      <span>{index}</span>
      <p>{label}</p>
    </div>
  );
}

export function JobsView({
  jobs,
  selectedJob,
  selectedJobId,
  selectJob,
  deleteJob,
  stopSelectedJob,
  openWorkbench,
  createTask,
  displayTaskTitle,
  statusLabel,
  modeLabel,
  formatDate,
  notify,
  t,
}) {
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const chooseJob = async (jobId) => {
    try {
      await selectJob(jobId);
      setMobileDetailOpen(true);
    } catch (error) {
      notify("error", t("operationFailed", { message: error.message }));
    }
  };
  const detailRows = selectedJob ? [
    [t("status"), statusLabel(selectedJob.status)],
    [t("mode"), modeLabel(selectedJob.mode)],
    [t("createdAt"), formatDate(selectedJob.created_at)],
    [t("phase2Short"), selectedJob.enable_phase2_research ? t("enabled") : t("disabled")],
    [t("directP3"), selectedJob.direct_phase3_execution ? t("enabled") : t("disabled")],
    [t("localFirst"), selectedJob.prefer_local_materials ? t("enabled") : t("disabled")],
    [t("taskArtifacts"), String(selectedJob.artifacts?.length || 0)],
  ] : [];
  return (
    <LibraryShell icon={History} title={t("navJobs")} subtitle={t("jobsLibrarySubtitle")} count={jobs.length} t={t}>
      {!jobs.length ? (
        <ContextEmpty
          icon={History}
          title={t("noJobs")}
          body={t("jobsLibrarySubtitle")}
          actionLabel={t("createTask")}
          onAction={createTask}
        />
      ) : (
        <div className={cx("jobs-master-detail", mobileDetailOpen && "show-detail")}>
          <div className="jobs-master library-list">
            {jobs.map((job) => (
              <button
                className={cx("library-row job-select-row", selectedJobId === job.job_id && "selected")}
                key={job.job_id}
                onClick={() => chooseJob(job.job_id)}
                type="button"
              >
                <span className={cx("library-icon", `job-${job.status}`)}><FileVideo2 size={18} /></span>
                <span className="min-w-0 flex-1">
                  <strong className="block truncate text-sm text-slate-700">{displayTaskTitle(job)}</strong>
                  <span className="mt-1 block truncate text-xs text-slate-400">{modeLabel(job.mode)} · {formatDate(job.created_at)}</span>
                </span>
                <StatusPill status={job.status} label={statusLabel(job.status)} />
                <ChevronRight size={17} className="job-row-chevron" />
              </button>
            ))}
          </div>
          <section className="job-detail-panel">
            <button className="job-detail-back" onClick={() => setMobileDetailOpen(false)} type="button">
              <ArrowLeft size={16} />{t("backToList")}
            </button>
            {selectedJob ? (
              <>
                <header className="job-detail-header">
                  <div>
                    <span>{t("taskDetails")}</span>
                    <h3>{displayTaskTitle(selectedJob)}</h3>
                  </div>
                  <StatusPill status={selectedJob.status} label={statusLabel(selectedJob.status)} />
                </header>
                <div className="job-detail-summary">
                  <span>{t("taskSummary")}</span>
                  <p>{displayTaskTitle(selectedJob)}</p>
                </div>
                <div className="job-detail-grid">
                  {detailRows.map(([label, value]) => (
                    <div key={label}><span>{label}</span><strong>{value}</strong></div>
                  ))}
                </div>
                <div className="job-detail-actions">
                  <button className="primary-button" onClick={openWorkbench} type="button">
                    <LayoutDashboard size={16} />{t("viewWorkbench")}
                  </button>
                  {selectedJob.status === "running" && (
                    <button className="secondary-button" onClick={() => stopSelectedJob().catch((error) => notify("error", t("operationFailed", { message: error.message })))} type="button">
                      <CircleStop size={16} />{t("stopJobFirst")}
                    </button>
                  )}
                  <button
                    className="danger-button"
                    disabled={selectedJob.status === "running"}
                    onClick={() => deleteJob(selectedJob.job_id)}
                    type="button"
                  >
                    <Trash2 size={16} />{t("deleteJob")}
                  </button>
                </div>
              </>
            ) : (
              <ContextEmpty icon={History} title={t("noTaskSelected")} body={t("jobsLibrarySubtitle")} />
            )}
          </section>
        </div>
      )}
    </LibraryShell>
  );
}

export function MaterialsView(props) {
  const inputRef = useRef(null);
  const upload = (files) => {
    props.uploadSelectedFiles(files).catch(() => {}).finally(() => {
      if (inputRef.current) inputRef.current.value = "";
    });
  };
  return (
    <LibraryShell
      icon={FolderOpen}
      title={props.t("navMaterials")}
      subtitle={props.t("materialsLibrarySubtitle")}
      count={props.uploads.length}
      actions={(
        <>
          <input
            ref={inputRef}
            type="file"
            accept=".mp4,.mov,.mkv,.avi,.webm,.m4v,.mpeg,.mpg,video/*"
            multiple
            hidden
            onChange={(event) => upload(event.target.files)}
          />
          <button className="primary-button" disabled={props.uploading} onClick={() => inputRef.current?.click()} type="button">
            <Upload size={16} />{props.uploading ? props.t("uploading") : props.t("uploadMaterial")}
          </button>
        </>
      )}
      t={props.t}
    >
      <MaterialsList {...props} />
    </LibraryShell>
  );
}

function MaterialsList({
  uploads,
  uploadAnalysisBadgeLabel,
  uploadAnalysisHint,
  formatBytes,
  formatDate,
  fileUrl,
  setTaskText,
  deleteUpload,
  loadUploads,
  notify,
  compact = false,
  t,
}) {
  const [refreshing, setRefreshing] = useState(false);
  const refresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await Promise.all([
        loadUploads(),
        new Promise((resolve) => window.setTimeout(resolve, 350)),
      ]);
    } catch (error) {
      notify("error", t("operationFailed", { message: error.message }));
    } finally {
      setRefreshing(false);
    }
  };

  return (
    <div className="grid gap-3">
      <div className="flex items-center justify-end">
        <button className="icon-button" onClick={refresh} type="button" aria-label={t("refreshUploads")} aria-busy={refreshing} aria-disabled={refreshing}>
          <RefreshCw className={refreshing ? "icon-spin" : undefined} size={15} />
        </button>
      </div>
      {uploads.map((item) => (
        <article className="material-row" key={item.display_path || item.path || item.name}>
          <span className="material-thumb"><FileVideo2 size={22} /></span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <strong className="truncate text-sm text-slate-700">{item.name || "--"}</strong>
              <span className={cx("analysis-badge", item.has_analysis && "ready")}>{uploadAnalysisBadgeLabel(item)}</span>
            </div>
            <p className="mt-1 truncate text-xs text-slate-400">{formatBytes(item.size_bytes)} · {formatDate(item.modified_at)}</p>
            {!compact && <p className="mt-2 text-xs leading-5 text-slate-500">{uploadAnalysisHint(item)}</p>}
            <div className="mt-2 flex flex-wrap gap-2">
              <button className="text-action" onClick={() => {
                const displayPath = item.display_path || "";
                if (!displayPath) return;
                setTaskText((current) => `${current.trim() ? `${current}\n` : ""}${t("insertUploadPrefix", { path: displayPath })}`);
              }} type="button">{t("insertTask")}</button>
              <a className="text-action" href={fileUrl(item.path)} target="_blank" rel="noreferrer">{t("open")}</a>
              <button className="text-action danger" onClick={() => deleteUpload(item.display_path || "", item.has_analysis)} type="button">{t("delete")}</button>
            </div>
          </div>
        </article>
      ))}
      {!uploads.length && (
        <ContextEmpty
          icon={FolderOpen}
          title={t("noUploads")}
          body={t("materialsLibrarySubtitle")}
        />
      )}
    </div>
  );
}

export function ArtifactsView({
  artifacts,
  selectedJob,
  artifactLabel,
  fileUrl,
  downloadFileUrl,
  formatBytes,
  formatDuration,
  selectTask,
  backToWorkbench,
  t,
}) {
  return (
    <LibraryShell icon={Archive} title={t("navArtifacts")} subtitle={t("artifactsLibrarySubtitle")} count={artifacts.length} t={t}>
      {!selectedJob ? (
        <ContextEmpty
          icon={Archive}
          title={t("noTaskSelected")}
          body={t("noTaskSelectedBody")}
          actionLabel={t("selectTask")}
          onAction={selectTask}
        />
      ) : (
        <ArtifactsList
          artifacts={artifacts}
          artifactLabel={artifactLabel}
          fileUrl={fileUrl}
          downloadFileUrl={downloadFileUrl}
          formatBytes={formatBytes}
          formatDuration={formatDuration}
          emptyAction={backToWorkbench}
          emptyActionLabel={t("backToWorkbench")}
          t={t}
        />
      )}
    </LibraryShell>
  );
}

function ArtifactsList({
  artifacts,
  artifactLabel,
  fileUrl,
  downloadFileUrl,
  formatBytes,
  formatDuration,
  emptyAction,
  emptyActionLabel,
  compact = false,
  t,
}) {
  const [previewArtifact, setPreviewArtifact] = useState(null);
  if (!artifacts.length) {
    return (
      <div className={cx("artifact-empty", compact && "compact")}>
        <img src={compact ? artifactCompactImage : artifactWideImage} alt="" aria-hidden="true" />
        <div>
          <h4>{t("noArtifactsTitle")}</h4>
          <p>{t("noArtifacts")}</p>
          {!compact && emptyAction && (
            <button className="primary-button mt-4" onClick={emptyAction} type="button">
              <LayoutDashboard size={16} />{emptyActionLabel}
            </button>
          )}
        </div>
      </div>
    );
  }
  if (!compact) {
    return (
      <>
        <div className="artifact-grid">
          {artifacts.map((artifact) => {
            const isVideo = artifact.kind === "video" || [".mp4", ".webm", ".mov"].includes(String(artifact.suffix || "").toLowerCase());
            return (
              <article className={cx("artifact-card", isVideo && "video")} key={artifact.path}>
                {isVideo ? (
                  <button className="artifact-video-thumb" onClick={() => setPreviewArtifact(artifact)} type="button" aria-label={t("preview")}>
                    <video src={fileUrl(artifact.path)} muted playsInline preload="metadata" />
                    <span><Play size={20} fill="currentColor" /></span>
                  </button>
                ) : (
                  <span className="artifact-file-thumb"><ScrollText size={28} /></span>
                )}
                <div className="artifact-card-body">
                  <strong>{artifactLabel(artifact)}</strong>
                  <p title={artifact.name}>{artifact.name}</p>
                  <div className="artifact-meta">
                    {isVideo && <span>{t("duration")} {formatDuration(artifact.duration_seconds)}</span>}
                    <span>{t("fileSize")} {formatBytes(artifact.size_bytes)}</span>
                  </div>
                  <div className="artifact-actions">
                    {isVideo ? (
                      <button className="secondary-button" onClick={() => setPreviewArtifact(artifact)} type="button">
                        <Play size={15} />{t("preview")}
                      </button>
                    ) : (
                      <a className="secondary-button" href={fileUrl(artifact.path)} target="_blank" rel="noreferrer">
                        <ChevronRight size={15} />{t("open")}
                      </a>
                    )}
                    <a className="primary-button" href={downloadFileUrl(artifact.path)}>
                      <Download size={15} />{t("download")}
                    </a>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
        {previewArtifact && (
          <VideoPreviewModal artifact={previewArtifact} fileUrl={fileUrl} downloadFileUrl={downloadFileUrl} setArtifact={setPreviewArtifact} t={t} />
        )}
      </>
    );
  }
  return (
    <div className="grid gap-3">
      {artifacts.map((artifact) => (
        <a className="artifact-row" href={fileUrl(artifact.path)} key={artifact.path} target="_blank" rel="noreferrer">
          <span><FileVideo2 size={20} /></span>
          <div className="min-w-0 flex-1">
            <strong className="block truncate text-sm text-slate-700">{artifactLabel(artifact)}</strong>
            <small className="mt-1 block truncate text-slate-400">{artifact.name}</small>
          </div>
          <ChevronRight size={17} className="text-slate-300" />
        </a>
      ))}
    </div>
  );
}

function VideoPreviewModal({ artifact, fileUrl, downloadFileUrl, setArtifact, t }) {
  useEffect(() => {
    const close = (event) => {
      if (event.key === "Escape") setArtifact(null);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [setArtifact]);
  return (
    <div className="dialog-layer">
      <button className="dialog-backdrop" onClick={() => setArtifact(null)} type="button" aria-label={t("closePreview")} />
      <section className="video-preview-modal motion-enter" role="dialog" aria-modal="true" aria-label={t("videoPreview")}>
        <header>
          <div className="min-w-0"><h2>{t("videoPreview")}</h2><p>{artifact.name}</p></div>
          <button className="icon-button" onClick={() => setArtifact(null)} type="button" aria-label={t("closePreview")}><X size={18} /></button>
        </header>
        <video src={fileUrl(artifact.path)} controls autoPlay playsInline />
        <footer>
          <a className="primary-button" href={downloadFileUrl(artifact.path)}>
            <Download size={16} />{t("download")}
          </a>
        </footer>
      </section>
    </div>
  );
}

function LibraryShell({ icon: Icon, title, subtitle, count, actions, children, t }) {
  return (
    <div className="library-view motion-enter">
      <header className="library-header">
        <div className="flex min-w-0 items-center gap-3">
          <span className="library-title-icon"><Icon size={21} /></span>
          <div className="min-w-0">
            <h2>{title}</h2>
            <p>{subtitle}</p>
          </div>
        </div>
        <div className="library-header-actions">
          {actions}
          <span className="count-chip">{count} {t("itemsUnit")}</span>
        </div>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">{children}</div>
    </div>
  );
}

function ContextEmpty({ icon: Icon, title, body, actionLabel, onAction }) {
  return (
    <div className="context-empty">
      <span><Icon size={23} /></span>
      <h4>{title}</h4>
      <p>{body}</p>
      {actionLabel && onAction && (
        <button className="primary-button mt-4" onClick={onAction} type="button">{actionLabel}</button>
      )}
    </div>
  );
}

export function Composer({
  taskText,
  setTaskText,
  mode,
  setMode,
  selectedJob,
  submitJob,
  stopSelectedJob,
  enablePhase2Research,
  directPhase3Execution,
  preferLocalMaterials,
  setEnablePhase2Research,
  setDirectPhase3Execution,
  setPreferLocalMaterials,
  syncWorkflowConfig,
  workflowConfigSaving,
  uploads,
  uploading,
  uploadSelectedFiles,
  notify,
  t,
}) {
  const running = selectedJob?.status === "running";
  const [attachOpen, setAttachOpen] = useState(false);
  const attachRootRef = useRef(null);
  const attachInputRef = useRef(null);
  const workflowSaveLockRef = useRef(false);

  useEffect(() => {
    if (!attachOpen) return undefined;
    const closeOnPointerDown = (event) => {
      if (!attachRootRef.current?.contains(event.target)) setAttachOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setAttachOpen(false);
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [attachOpen]);

  const attachMaterial = (item) => {
    const displayPath = item?.display_path || "";
    if (!displayPath) return;
    setTaskText((current) => {
      if (current.includes(displayPath)) return current;
      const prefix = t("insertUploadPrefix", { path: displayPath });
      return `${current.trim() ? `${current.trim()}\n` : ""}${prefix}`;
    });
    setAttachOpen(false);
  };

  const uploadAndAttach = async (files) => {
    if (!files?.length) return;
    try {
      const items = await uploadSelectedFiles(files);
      (items || []).forEach(attachMaterial);
    } catch (_) {
      // Upload errors are reported by the shared toast channel.
    }
  };

  const toggle = async (setter, previous, patch) => {
    if (workflowSaveLockRef.current || workflowConfigSaving) return;
    workflowSaveLockRef.current = true;
    setter(!previous);
    try {
      await syncWorkflowConfig(patch(!previous));
    } catch (error) {
      setter(previous);
      notify("error", t("operationFailed", { message: error.message }));
    } finally {
      workflowSaveLockRef.current = false;
    }
  };
  return (
    <section className="composer-shell">
      <form onSubmit={(event) => {
        event.preventDefault();
        Promise.resolve(running ? stopSelectedJob() : submitJob())
          .catch((error) => notify("error", t("operationFailed", { message: error.message })));
      }}>
        <textarea
          value={taskText}
          onChange={(event) => setTaskText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              Promise.resolve(running ? stopSelectedJob() : submitJob())
                .catch((error) => notify("error", t("operationFailed", { message: error.message })));
            }
          }}
          placeholder={t("composerPlaceholder")}
        />
        <div className="composer-toolbar">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <div className={cx("material-attach", attachOpen && "open")} ref={attachRootRef}>
              <input
                ref={attachInputRef}
                type="file"
                accept=".mp4,.mov,.mkv,.avi,.webm,.m4v,.mpeg,.mpg,video/*"
                multiple
                hidden
                onChange={(event) => {
                  uploadAndAttach(event.target.files).finally(() => { event.target.value = ""; });
                }}
              />
              <button
                className="composer-icon"
                type="button"
                aria-label={t("attachMaterial")}
                aria-haspopup="menu"
                aria-expanded={attachOpen}
                onClick={() => setAttachOpen((current) => !current)}
              >
                <Paperclip size={17} />
              </button>
              {attachOpen && (
                <div className="material-attach-menu motion-enter" role="menu" aria-label={t("attachMaterial")}>
                  <div className="material-attach-heading">
                    <strong>{t("attachMaterialTitle")}</strong>
                    <span>{t("attachMaterialHint")}</span>
                  </div>
                  <div className="material-attach-list">
                    {uploads.map((item) => (
                      <button
                        className="material-attach-row"
                        key={item.display_path || item.path}
                        onClick={() => attachMaterial(item)}
                        type="button"
                        role="menuitem"
                      >
                        <FileVideo2 size={17} />
                        <span>
                          <strong>{item.name}</strong>
                          <small>{item.display_path}</small>
                        </span>
                        <Plus size={15} />
                      </button>
                    ))}
                    {!uploads.length && <p className="material-attach-empty">{t("noUploads")}</p>}
                  </div>
                  <button
                    className="material-upload-action"
                    onClick={() => attachInputRef.current?.click()}
                    disabled={uploading}
                    type="button"
                  >
                    <Upload size={16} />
                    <span>{uploading ? t("uploading") : t("uploadNewMaterial")}</span>
                  </button>
                </div>
              )}
            </div>
            <WorkflowToggle
              active={enablePhase2Research}
              label={t("phase2Setting")}
              busy={workflowConfigSaving}
              disabled={workflowConfigSaving || directPhase3Execution}
              onClick={() => toggle(setEnablePhase2Research, enablePhase2Research, (value) => ({ enable_phase2_research: value }))}
            />
            <WorkflowToggle
              active={directPhase3Execution}
              label={t("directP3")}
              busy={workflowConfigSaving}
              disabled={workflowConfigSaving}
              onClick={() => toggle(setDirectPhase3Execution, directPhase3Execution, (value) => ({ direct_phase3_execution: value }))}
            />
            <WorkflowToggle
              active={preferLocalMaterials}
              label={t("localFirst")}
              busy={workflowConfigSaving}
              disabled={workflowConfigSaving || directPhase3Execution}
              onClick={() => toggle(setPreferLocalMaterials, preferLocalMaterials, (value) => ({ prefer_local_materials: value }))}
            />
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <ModeSelector mode={mode} setMode={setMode} t={t} />
            <button className={cx("send-button", running && "stop")} type="submit" aria-label={running ? t("stopTask") : t("sendTask")}>
              {running ? <CircleStop size={18} /> : <Send size={18} />}
            </button>
          </div>
        </div>
      </form>
    </section>
  );
}

function ModeSelector({ mode, setMode, t }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const options = [
    { value: "demo", label: t("demoMode") },
    { value: "agent", label: t("agentMode") },
  ];

  useEffect(() => {
    if (!open) return undefined;
    const closeOnPointerDown = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", closeOnPointerDown);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointerDown);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const activeOption = options.find((option) => option.value === mode) || options[0];
  return (
    <div className={cx("mode-selector", open && "open")} ref={rootRef}>
      <button
        className="mode-selector-trigger"
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="mode-selector-dot" />
        <span>{activeOption.label}</span>
        <ChevronDown size={14} />
      </button>
      {open && (
        <div className="mode-selector-menu motion-enter" role="listbox" aria-label={t("mode")}>
          {options.map((option) => (
            <button
              className={cx("mode-selector-option", mode === option.value && "selected")}
              key={option.value}
              type="button"
              role="option"
              aria-selected={mode === option.value}
              onClick={() => {
                setMode(option.value);
                setOpen(false);
              }}
            >
              <span>{option.label}</span>
              {mode === option.value && <Check size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function WorkflowToggle({ active, label, busy = false, disabled = false, onClick }) {
  return (
    <button
      className={cx("workflow-toggle", active && "active")}
      disabled={disabled || busy}
      onClick={onClick}
      type="button"
      aria-busy={busy}
    >
      <span />
      <span>{label}</span>
    </button>
  );
}

export function StatusPill({ status, label }) {
  return <span className={cx("status-pill", `status-${status}`, status === "running" && "status-live")}>{label}</span>;
}
