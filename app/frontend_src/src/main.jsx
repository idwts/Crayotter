import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import {
  AppSidebar,
  AppTopbar,
  ArtifactsView,
  JobsView,
  MaterialsView,
  MobileBottomNav,
  MobileDrawer,
  WorkbenchView,
} from "./components/DashboardUI";
import { SettingsModal as RedesignedSettingsModal } from "./components/SettingsModal";
import { ConfirmDialog, ToastViewport } from "./components/FeedbackUI";
import { MESSAGES } from "./i18n";
import {
  getHighestPhase,
  mergeRuntimeEvents,
  normalizeRuntimeEvent,
} from "./eventState";
import { jobEventsDownloadUrl } from "./logDownload";
import { buildConfigPayload, shouldRefreshCurrentPlan } from "./workbenchFlow";

const STORAGE_KEYS = {
  language: "crayotter.language",
  sidebarCollapsed: "crayotter.sidebarCollapsed",
  lastMode: "crayotter.lastMode",
  currentView: "crayotter.currentView",
  contextTab: "crayotter.contextTab",
};

const template = (value, params = {}) =>
  String(value || "").replace(/\{(\w+)\}/g, (_, key) => params[key] ?? "");

const request = async (url, options = {}) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      message = body.error || message;
    } catch (_) {
      // ignore non-json errors
    }
    throw new Error(message);
  }
  const contentType = response.headers.get("content-type") || "";
  return contentType.includes("application/json") ? response.json() : response.text();
};

const fileUrl = (path) => `/files?path=${encodeURIComponent(path)}`;
const downloadFileUrl = (path) => `${fileUrl(path)}&download=1`;

const DIAGNOSTIC_TEXT_PATTERN = /(?:status\s*[=:]\s*\d{3}|request[_\s-]?id|file:\/\/|[A-Za-z]:[\\/]|dashscope|api\s+error|traceback|connectionreseterror|demo job completed|backend service|event bus|job persistence|https?:\/\/help\.aliyun\.com|```|\|\s*-{3,}\s*\|)/i;

const safeDisplayText = (value, fallback, maxLength = 180) => {
  const raw = String(value || "").trim();
  if (!raw || DIAGNOSTIC_TEXT_PATTERN.test(raw) || raw.length > 600) return fallback;
  const text = raw
    .replace(/^[#>*_\s-]+/gm, "")
    .replace(/[`*_]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return fallback;
  return text.length <= maxLength ? text : `${text.slice(0, maxLength - 1).trimEnd()}…`;
};

const summarizeTaskTitle = (value, maxLength = 24) => {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  const clauses = text
    .split(/[，,。.!！?？;；:：、\n\r]+/)
    .map((part) => part
      .replace(/^(?:请(?:你)?|麻烦(?:你)?|可以)?\s*(?:(?:帮|替|为)我)?\s*(?:制作|生成|剪辑|创建|做)\s*(?:一个|一段|一条|一期|个|段|条)?\s*/i, "")
      .replace(/(?:时长|长度)?\s*(?:约|大约|控制在)?\s*\d+(?:\.\d+)?\s*(?:小时|分钟|分|秒钟|秒)\s*(?:左右|以内|以上)?/gi, "")
      .replace(/(?:需要|要求|并且|同时|以及|和|并|要|带|含|有|添加|配上|包含)?\s*(?:中文字幕|中英字幕|字幕|旁白|配音|背景音乐|音乐|转场)\s*/gi, "")
      .replace(/^(?:短片|视频|成片)?\s*(?:中|里)?\s*(?:需要|要求|要|并且|同时|突出)?\s*(?:体现出?|展现出?|展示|突出|反映|介绍)\s*/i, "")
      .replace(/\s+/g, " ")
      .replace(/^的/, "")
      .trim())
    .filter(Boolean);
  const uniqueClauses = [...new Set(clauses)];
  const titleParts = [];
  for (const clause of uniqueClauses) {
    let candidate = clause;
    for (const existing of titleParts) {
      let index = 0;
      const limit = Math.min(existing.length, candidate.length);
      while (index < limit && existing[index] === candidate[index]) index += 1;
      if (index >= 2) {
        candidate = candidate.slice(index).replace(/^的/, "");
        break;
      }
    }
    if (candidate) titleParts.push(candidate);
    if (titleParts.length >= 2) break;
  }
  const title = titleParts.join(" · ") || text;
  return title.length <= maxLength ? title : `${title.slice(0, maxLength - 1).replace(/[ ·]+$/, "")}…`;
};

function App() {
  const [language, setLanguage] = useState(() => localStorage.getItem(STORAGE_KEYS.language) || "zh");
  const [jobs, setJobs] = useState([]);
  const [uploads, setUploads] = useState([]);
  const [selectedJobId, setSelectedJobId] = useState(null);
  const [selectedJob, setSelectedJob] = useState(null);
  const [selectedEvents, setSelectedEvents] = useState([]);
  const [selectedMessages, setSelectedMessages] = useState([]);
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [hasSavedConfig, setHasSavedConfig] = useState(false);
  const [healthText, setHealthText] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => localStorage.getItem(STORAGE_KEYS.sidebarCollapsed) === "1");
  const [enablePhase2Research, setEnablePhase2Research] = useState(true);
  const [enablePlanReview, setEnablePlanReview] = useState(true);
  const [directPhase3Execution, setDirectPhase3Execution] = useState(false);
  const [preferLocalMaterials, setPreferLocalMaterials] = useState(false);
  const [workflowConfigSaving, setWorkflowConfigSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [currentView, setCurrentView] = useState(() => localStorage.getItem(STORAGE_KEYS.currentView) || "workbench");
  const [activeContextTab, setActiveContextTab] = useState(() => localStorage.getItem(STORAGE_KEYS.contextTab) || "details");
  const [configMessage, setConfigMessage] = useState("");
  const [mode, setMode] = useState(() => localStorage.getItem(STORAGE_KEYS.lastMode) || "demo");
  const [taskText, setTaskText] = useState("");
  const [toasts, setToasts] = useState([]);
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [configForm, setConfigForm] = useState({
    apiKey: "",
    baseUrl: "",
    model: "",
    videoApiKey: "",
    videoBaseUrl: "",
    videoModel: "",
    ttsApiKey: "",
    ttsBaseUrl: "",
    ttsModel: "",
    stallTimeout: "150",
    searchPoolSize: "4",
    downloadPoolSize: "3",
    videoAnalysisPoolSize: "3",
    llmPoolSize: "4",
    ffmpegPoolSize: "3",
    ttsPoolSize: "3",
    exportPoolSize: "1",
    enablePhase2Research: true,
    enablePlanReview: true,
    directPhase3Execution: false,
    preferLocalMaterials: false,
    youtubeMode: "auto",
    enabledMaterialPlatforms: "bilibili,douyin,xiaohongshu,youtube",
    defaultDeadlineSeconds: "600",
    processingMode: "auto",
    outputProfile: "auto",
    browserAuthBrowser: "",
    browserAuthProfile: "",
  });
  const eventSourceRef = useRef(null);
  const refreshTimerRef = useRef(null);
  const toastTimersRef = useRef(new Map());

  const t = useCallback((key, params) => template((MESSAGES[language] || MESSAGES.zh)[key] || MESSAGES.zh[key] || key, params), [language]);

  useEffect(() => {
    document.documentElement.lang = language === "en" ? "en-US" : "zh-CN";
    localStorage.setItem(STORAGE_KEYS.language, language);
  }, [language]);

  const formatDate = useCallback((value) => {
    if (!value) return "--";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString(language === "en" ? "en-US" : "zh-CN");
  }, [language]);

  const formatBytes = (value) => {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const sized = bytes / 1024 ** index;
    return `${sized >= 10 || index === 0 ? sized.toFixed(0) : sized.toFixed(1)} ${units[index]}`;
  };

  const formatDuration = (value) => {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds <= 0) return "--";
    const total = Math.round(seconds);
    const minutes = Math.floor(total / 60);
    const remainder = total % 60;
    return `${minutes}:${String(remainder).padStart(2, "0")}`;
  };

  const dismissToast = useCallback((id) => {
    setToasts((current) => current.map((toast) => (
      toast.id === id ? { ...toast, exiting: true } : toast
    )));
    const timer = toastTimersRef.current.get(id);
    if (timer) window.clearTimeout(timer);
    const removalTimer = window.setTimeout(() => {
      setToasts((current) => current.filter((toast) => toast.id !== id));
      toastTimersRef.current.delete(id);
    }, 240);
    toastTimersRef.current.set(id, removalTimer);
  }, []);

  const notify = useCallback((type, message) => {
    const id = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    setToasts((current) => [...current.slice(-3), { id, type, message }]);
    const timer = window.setTimeout(() => dismissToast(id), type === "error" ? 6500 : 2000);
    toastTimersRef.current.set(id, timer);
  }, [dismissToast]);

  const confirmAction = useCallback((options, action) => new Promise((resolve) => {
    setConfirmDialog({ ...options, action, resolve, busy: false });
  }), []);

  const closeConfirmDialog = useCallback(async (confirmed) => {
    if (!confirmDialog || confirmDialog.busy) return;
    if (!confirmed) {
      confirmDialog.resolve(false);
      setConfirmDialog(null);
      return;
    }
    setConfirmDialog((current) => current ? { ...current, busy: true } : current);
    try {
      await confirmDialog.action();
      confirmDialog.resolve(true);
      setConfirmDialog(null);
    } catch (error) {
      confirmDialog.resolve(false);
      setConfirmDialog(null);
      notify("error", t("operationFailed", { message: error.message }));
    }
  }, [confirmDialog, notify, t]);

  const openWorkbenchComposer = useCallback(() => {
    setCurrentView("workbench");
    window.setTimeout(() => document.querySelector(".composer-shell textarea")?.focus(), 0);
  }, []);

  const closeEventStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  const statusLabel = useCallback((status) => {
    const key = {
      pending: "statusPending",
      queued: "statusQueued",
      running: "statusRunning",
      interrupted: "statusInterrupted",
      completed: "statusCompleted",
      failed: "statusFailed",
      cancelled: "statusCancelled",
    }[status];
    return key ? t(key) : status || "--";
  }, [t]);

  const modeLabel = useCallback((value) => (value === "agent" ? t("agentMode") : t("demoMode")), [t]);

  const displayTaskTitle = useCallback((job) => {
    const task = String(job?.task || "");
    if (job?.mode === "demo" && /^[\x00-\x7F\s_-]+$/.test(task) && /(demo|validation|acceptance|job|simple)/i.test(task)) {
      return t("demoTask");
    }
    return String(job?.title || "").trim() || summarizeTaskTitle(task) || t("untitledTask");
  }, [t]);

  const phaseLabel = useCallback((phase) => {
    const map = { phase1: "phase1", phase2: "phase2", phase3: "phase3" };
    return t(map[phase] || "phaseUnknown");
  }, [t]);

  const toolLabel = useCallback((toolName) => {
    const map = {
      search_bilibili_video: "toolSearch",
      rank_video_candidates: "toolRank",
      analyze_video: "toolAnalyze",
      add_narration_segments: "toolNarration",
    };
    return map[toolName] ? t(map[toolName]) : toolName || t("toolGeneric");
  }, [t]);

  const describeEvent = useCallback((event) => {
    const payload = event?.payload || {};
    const phase = payload.phase ? phaseLabel(payload.phase) : "";
    switch (event?.type) {
      case "log":
        return { title: t("eventLogRecorded"), body: safeDisplayText(payload.message, t("eventLogRecordedBody")) };
      case "job_created":
        return { title: t("eventCreated"), body: t("eventCreatedBody") };
      case "job_started":
        return { title: t("eventStarted"), body: t("eventStartedBody") };
      case "phase_started":
        return { title: phase || t("eventPhaseStarted"), body: t("eventPhaseStartedBody") };
      case "thinking_summary":
        return { title: phase || t("eventThinking"), body: safeDisplayText(payload.summary, t("eventThinkingBody")) };
      case "step_started":
        return { title: t("eventStepStarted"), body: safeDisplayText(payload.description, t("eventStepStartedBody")) };
      case "step_completed":
        return { title: t("eventStepCompleted"), body: safeDisplayText(payload.result, t("eventStepCompletedBody")) };
      case "step_scheduled":
        return { title: t("eventStepScheduled"), body: `${toolLabel(payload.tool_name)} · depends_on=${payload.depends_on || "[]"}` };
      case "dependency_waiting":
        return { title: t("eventDependencyWaiting"), body: `step=${payload.step_id} · depends_on=${payload.depends_on || "[]"}` };
      case "step_failed":
        return { title: t("eventStepFailed"), body: safeDisplayText(payload.error, t("eventFailedBody")) };
      case "task_plan_started":
        return { title: t("eventPlanStarted"), body: `${payload.phase || ""} · ${payload.task_count || 0} tasks` };
      case "resource_acquired":
        return { title: t("eventResourceAcquired"), body: `${payload.task_id || ""} · ${JSON.stringify(payload.resources || {})}` };
      case "task_retrying":
        return { title: t("eventTaskRetrying"), body: `${payload.task_id || ""} · ${safeDisplayText(payload.error, "")}` };
      case "artifact_registered":
        return { title: t("eventArtifactRegistered"), body: `${payload.kind || ""} · ${payload.path || ""}` };
      case "evaluator_decision":
        return { title: t("eventEvaluatorDecision"), body: `${payload.phase || ""} · ${payload.decision || ""}` };
      case "checkpoint_saved":
        return { title: t("eventCheckpointSaved"), body: payload.path || "" };
      case "tool_called":
        return { title: toolLabel(payload.tool_name), body: t("eventToolCalledBody") };
      case "tool_result":
        return { title: toolLabel(payload.tool_name), body: safeDisplayText(payload.summary, t("eventToolResultBody")) };
      case "editing_plan_created":
        return { title: t("eventEditingPlanCreated"), body: t("eventEditingPlanCreatedBody", { version: payload.version || "" }) };
      case "editing_plan_validated":
        return { title: t("eventEditingPlanValidated"), body: t("eventEditingPlanValidatedBody", { version: payload.version || "" }) };
      case "plan_review_waiting":
        return { title: t("eventPlanReviewWaiting"), body: t("eventPlanReviewWaitingBody", { version: payload.version || "" }) };
      case "plan_revised":
        return { title: t("eventPlanRevised"), body: t("eventPlanRevisedBody", { version: payload.to_version || "" }) };
      case "plan_approved":
      case "editing_plan_frozen":
        return { title: t("eventPlanApproved"), body: t("eventPlanApprovedBody", { version: payload.version || "" }) };
      case "plan_validation_failed":
        return { title: t("eventPlanValidationFailed"), body: t("eventPlanValidationFailedBody") };
      case "job_completed":
        return { title: t("eventCompleted"), body: safeDisplayText(payload.final_output, t("eventCompletedBody")) };
      case "job_failed":
        return { title: t("eventFailed"), body: t("eventFailedBody") };
      case "job_cancelled":
        return { title: t("eventCancelled"), body: t("eventCancelledBody") };
      case "guidance_received":
        return { title: t("eventGuidanceReceived"), body: safeDisplayText(payload.content, t("eventGuidanceReceivedBody")) };
      case "guidance_applied":
        return { title: t("eventGuidanceApplied"), body: `${payload.checkpoint || ""} · ${payload.category || ""}` };
      case "guidance_unsupported":
        return { title: t("eventGuidanceUnsupported"), body: safeDisplayText(payload.reason, t("eventGuidanceUnsupportedBody")) };
      case "steering_replan_started":
        return { title: t("eventReplanStarted"), body: `${payload.required_phase || ""} · ${payload.checkpoint || ""}` };
      case "steering_waiting_user":
        return { title: t("eventWaitingApproval"), body: payload.checkpoint || t("eventWaitingApprovalBody") };
      case "revision_started":
        return { title: t("eventRevisionStarted"), body: t("revisionLabel", { revision: payload.revision || 1 }) };
      case "revision_completed":
        return { title: t("eventRevisionCompleted"), body: t("revisionLabel", { revision: payload.revision || 1 }) };
      default:
        return {
          title: t("eventUpdate"),
          body: safeDisplayText(
            payload.summary || payload.description || payload.result || payload.message || payload.final_output,
            t("eventUpdateBody"),
          ),
        };
    }
  }, [phaseLabel, t, toolLabel]);

  const getMeaningfulEvents = (events) => events.filter((event) => !["log", "heartbeat"].includes(event.type));
  const getVisibleLogEvents = (events) => events.filter((event) => event.type !== "heartbeat");

  const buildLogPlainText = useCallback((events) => events.map((event) => {
    const summary = event.type === "log" ? String(event.payload?.message || "--") : describeEvent(event).body;
    return `${formatDate(event.timestamp)}\n${summary || "--"}`;
  }).join("\n\n"), [describeEvent, formatDate]);

  const loadUploads = useCallback(async () => {
    const payload = await request("/uploads");
    setUploads(Array.isArray(payload.items) ? payload.items : []);
  }, []);

  const loadMessages = useCallback(async (jobId) => {
    if (!jobId) {
      setSelectedMessages([]);
      return [];
    }
    const payload = await request(`/jobs/${jobId}/messages`);
    const items = Array.isArray(payload.items) ? payload.items : [];
    setSelectedMessages(items);
    return items;
  }, []);

  const loadCurrentPlan = useCallback(async (jobId) => {
    if (!jobId) {
      setSelectedPlan(null);
      return null;
    }
    try {
      const payload = await request(`/jobs/${jobId}/plans/current`);
      setSelectedPlan(payload);
      return payload;
    } catch (_) {
      setSelectedPlan(null);
      return null;
    }
  }, []);

  const applyConfig = useCallback((config) => {
    const profile = config.profiles?.[config.active_profile] || config.profiles?.default || {};
    setConfigForm({
      apiKey: profile.api_key || "",
      baseUrl: profile.base_url || "",
      model: profile.model_name || "",
      videoApiKey: profile.video_api_key || "",
      videoBaseUrl: profile.video_base_url || "",
      videoModel: profile.video_model_name || "",
      ttsApiKey: profile.tts_api_key || "",
      ttsBaseUrl: profile.tts_base_url || "",
      ttsModel: profile.tts_model_name || "",
      stallTimeout: String(config.agent_stall_timeout_seconds || 150),
      searchPoolSize: String(config.search_pool_size || 4),
      downloadPoolSize: String(config.download_pool_size || 3),
      videoAnalysisPoolSize: String(config.video_analysis_pool_size || 3),
      llmPoolSize: String(config.llm_pool_size || 4),
      ffmpegPoolSize: String(config.ffmpeg_pool_size || 3),
      ttsPoolSize: String(config.tts_pool_size || 3),
      exportPoolSize: String(config.export_pool_size || 1),
      enablePhase2Research: config.enable_phase2_research !== false,
      enablePlanReview: config.enable_plan_review !== false,
      directPhase3Execution: config.direct_phase3_execution === true,
      preferLocalMaterials: config.prefer_local_materials === true,
      youtubeMode: config.youtube_mode || "auto",
      enabledMaterialPlatforms: (config.enabled_material_platforms || ["bilibili", "douyin", "xiaohongshu", "youtube"]).join(","),
      defaultDeadlineSeconds: String(config.default_deadline_seconds || 600),
      processingMode: config.processing_mode || "auto",
      outputProfile: config.output_profile || "auto",
      browserAuthBrowser: config.browser_auth_browser || "",
      browserAuthProfile: config.browser_auth_profile || "",
    });
    setEnablePhase2Research(config.enable_phase2_research !== false);
    setEnablePlanReview(config.enable_plan_review !== false);
    setDirectPhase3Execution(config.direct_phase3_execution === true);
    setPreferLocalMaterials(config.prefer_local_materials === true);
    setHasSavedConfig(Boolean(profile.api_key));
  }, []);

  const loadConfig = useCallback(async () => {
    const config = await request("/config");
    applyConfig(config);
    return config;
  }, [applyConfig]);

  const refreshJobs = useCallback(async (selectRunning = false) => {
    const payload = await request("/jobs");
    const items = Array.isArray(payload.items) ? payload.items : [];
    setJobs(items);
    let targetId = selectedJobId;
    if (selectRunning) {
      targetId = items.find((job) => job.status === "running")?.job_id || targetId;
    }
    if (!targetId && items.length) targetId = items[0].job_id;
    if (targetId) {
      const job = items.find((item) => item.job_id === targetId);
      if (job) {
        setSelectedJobId(targetId);
        setSelectedJob((current) => current?.job_id === targetId
          ? { ...current, ...job, artifacts: current.artifacts || [] }
          : job);
        if (shouldRefreshCurrentPlan(job, selectedPlan)) {
          loadCurrentPlan(targetId).catch(() => {});
        }
        if (selectRunning || selectedJobId !== targetId) {
          const [detail, eventsPayload, messagesPayload] = await Promise.all([
            request(`/jobs/${targetId}`),
            request(`/jobs/${targetId}/events`),
            request(`/jobs/${targetId}/messages`),
          ]);
          setSelectedJob(detail);
          setSelectedMessages(Array.isArray(messagesPayload.items) ? messagesPayload.items : []);
          loadCurrentPlan(targetId).catch(() => {});
          const fetchedEvents = Array.isArray(eventsPayload.items) ? eventsPayload.items : [];
          setSelectedEvents((current) => selectedJobId === targetId
            ? mergeRuntimeEvents(current, fetchedEvents)
            : mergeRuntimeEvents(fetchedEvents));
        }
      }
    } else {
      setSelectedJobId(null);
      setSelectedJob(null);
      setSelectedEvents([]);
      setSelectedMessages([]);
      setSelectedPlan(null);
    }
  }, [loadCurrentPlan, selectedJobId, selectedPlan]);

  const attachEventStream = useCallback((job, existingEvents = []) => {
    closeEventStream();
    if (!job || job.status !== "running") return;
    const after = existingEvents.reduce((max, event) => Math.max(max, event.sequence || 0), 0);
    const source = new EventSource(`/jobs/${job.job_id}/events/stream?after=${after}`);
    eventSourceRef.current = source;
    source.onmessage = (message) => {
      const event = normalizeRuntimeEvent(JSON.parse(message.data));
      setSelectedEvents((current) => mergeRuntimeEvents(current, [event]));
      if (["guidance_received", "guidance_applied", "revision_started"].includes(event.type)) {
        loadMessages(job.job_id).catch(() => {});
      }
      if (["editing_plan_created", "editing_plan_validated", "plan_review_waiting", "plan_revised", "plan_approved", "editing_plan_frozen", "plan_validation_failed"].includes(event.type)) {
        loadCurrentPlan(job.job_id).catch(() => {});
      }
      if (["artifact_registered", "artifact_created"].includes(event.type)) {
        request(`/jobs/${job.job_id}`)
          .then((detail) => {
            setSelectedJob((current) => current?.job_id === job.job_id ? detail : current);
          })
          .catch(() => {});
      }
      if (event.type === "material_source_authorization_required") {
        notify(
          "warning",
          `${event.payload?.platform || "素材平台"} 需要浏览器授权；其他平台会继续。请在设置中选择浏览器和 Profile，后续重试将使用该授权。`,
        );
      }
      if (["job_completed", "job_failed", "job_cancelled"].includes(event.type)) {
        refreshJobs(true).catch(() => {});
      }
    };
    source.addEventListener("end", () => {
      closeEventStream();
      refreshJobs(true).catch(() => {});
    });
    source.onerror = () => {
      closeEventStream();
    };
  }, [closeEventStream, loadCurrentPlan, loadMessages, notify, refreshJobs]);

  const selectJob = useCallback(async (jobId) => {
    const job = jobs.find((item) => item.job_id === jobId);
    if (!job) return;
    setSelectedJobId(jobId);
    const [detail, eventsPayload, messagesPayload] = await Promise.all([
      request(`/jobs/${jobId}`),
      request(`/jobs/${jobId}/events`),
      request(`/jobs/${jobId}/messages`),
    ]);
    setSelectedJob(detail);
    setSelectedMessages(Array.isArray(messagesPayload.items) ? messagesPayload.items : []);
    loadCurrentPlan(jobId).catch(() => {});
    const fetchedEvents = mergeRuntimeEvents(
      Array.isArray(eventsPayload.items) ? eventsPayload.items : [],
    );
    setSelectedEvents(fetchedEvents);
    attachEventStream(detail, fetchedEvents);
  }, [attachEventStream, jobs]);

  useEffect(() => {
    if (selectedJob?.status === "running" && !eventSourceRef.current) {
      attachEventStream(selectedJob, selectedEvents);
    }
  }, [attachEventStream, selectedJob?.job_id, selectedJob?.status]);

  useEffect(() => {
    if (selectedJob?.job_id && shouldRefreshCurrentPlan(selectedJob, selectedPlan)) {
      loadCurrentPlan(selectedJob.job_id).catch(() => {});
    }
  }, [
    loadCurrentPlan,
    selectedJob?.job_id,
    selectedJob?.current_checkpoint,
    selectedJob?.steering_status,
    selectedPlan?.plan?.version,
  ]);

  useEffect(() => {
    setHealthText(t("subtitleConnecting"));
    Promise.allSettled([loadConfig(), loadUploads(), refreshJobs(true), request("/health")])
      .then((results) => {
        const health = results[3];
        setHealthText(health.status === "fulfilled" ? t("healthOk") : t("healthFail"));
      })
      .catch(() => setHealthText(t("healthFail")));
    refreshTimerRef.current = window.setInterval(() => {
      refreshJobs(false).catch(() => {});
    }, 6000);
    return () => {
      window.clearInterval(refreshTimerRef.current);
      closeEventStream();
      toastTimersRef.current.forEach((timer) => window.clearTimeout(timer));
      toastTimersRef.current.clear();
    };
  }, []);

  useEffect(() => {
    setHealthText((current) => {
      if (!current || current === MESSAGES.zh.subtitleConnecting || current === MESSAGES.en.subtitleConnecting) return t("subtitleConnecting");
      if (current === MESSAGES.zh.healthOk || current === MESSAGES.en.healthOk) return t("healthOk");
      if (current === MESSAGES.zh.healthFail || current === MESSAGES.en.healthFail) return t("healthFail");
      return current;
    });
  }, [t]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.sidebarCollapsed, sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.lastMode, mode);
  }, [mode]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.currentView, currentView);
  }, [currentView]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEYS.contextTab, activeContextTab);
  }, [activeContextTab]);

  const uploadAnalysisBadgeLabel = (item) => (item?.has_analysis ? t("analysisReady") : t("analysisMissing"));
  const uploadAnalysisHint = (item) => {
    if (item?.has_analysis) {
      const countSuffix = Number(item.analysis_count || 0) > 1 ? t("uploadCountSuffix", { count: Number(item.analysis_count) }) : "";
      const pathText = item.analysis_display_path || item.analysis_path || "";
      return t("uploadReadyHint", { path: pathText, countSuffix });
    }
    return t("uploadMissingHint");
  };

  const isPrimaryArtifact = (artifact) => {
    const name = String(artifact?.name || "").toLowerCase();
    const suffix = String(artifact?.suffix || "").toLowerCase();
    if (artifact?.artifact_id) return suffix !== ".log";
    if ([".json", ".jsonl", ".log"].includes(suffix)) return false;
    if (name === "events.jsonl" || name === "summary.json") return false;
    return [".txt", ".md", ".mp4", ".webm", ".mov"].includes(suffix);
  };

  const artifactLabel = (artifact) => {
    if (artifact?.kind && artifact.kind !== "video" && artifact.kind !== "text") {
      return artifact.kind;
    }
    const suffix = String(artifact?.suffix || "").toLowerCase();
    if ([".mp4", ".webm", ".mov"].includes(suffix)) return t("artifactVideo");
    if (suffix === ".md") return t("artifactMd");
    if (suffix === ".txt") return t("artifactTxt");
    return artifact?.name || t("artifactTxt");
  };

  const copyFullLog = async () => {
    const text = buildLogPlainText(getVisibleLogEvents(selectedEvents));
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    notify("success", t("copiedLog"));
  };

  const downloadFullLogUrl = selectedJob ? jobEventsDownloadUrl(selectedJob.job_id) : "";

  const submitConfig = async (event) => {
    event.preventDefault();
    const payload = buildConfigPayload(configForm);
    try {
      const config = await request("/config", { method: "PUT", body: JSON.stringify(payload) });
      applyConfig(config);
      const message = configForm.apiKey.trim() ? t("configSaved") : t("configCleared");
      setConfigMessage(message);
      notify("success", message);
    } catch (error) {
      notify("error", t("configSaveFailed", { message: error.message }));
      throw error;
    }
  };

  const syncWorkflowConfig = async (patch) => {
    setWorkflowConfigSaving(true);
    try {
      const updated = await request("/config", { method: "PUT", body: JSON.stringify(patch) });
      applyConfig(updated);
      notify("success", t("workflowSaved"));
    } finally {
      setWorkflowConfigSaving(false);
    }
  };

  const uploadSelectedFiles = async (files) => {
    if (!files?.length) return;
    setUploading(true);
    try {
      const form = new FormData();
      Array.from(files).forEach((file) => form.append("files", file));
      const response = await fetch("/uploads", { method: "POST", body: form });
      if (!response.ok) {
        let message = `${response.status} ${response.statusText}`;
        try {
          const body = await response.json();
          message = body.error || message;
        } catch (_) {
          // ignore
        }
        throw new Error(message);
      }
      const payload = await response.json();
      await loadUploads();
      const items = Array.isArray(payload.items) ? payload.items : [];
      notify("success", t("uploadSucceeded", { count: items.length }));
      return items;
    } catch (error) {
      notify("error", t("uploadFailed", { message: error.message }));
      throw error;
    } finally {
      setUploading(false);
    }
  };

  const deleteUpload = async (displayPath, hasAnalysis) => {
    const message = hasAnalysis ? t("confirmDeleteUploadAnalysis", { path: displayPath }) : t("confirmDeleteUpload", { path: displayPath });
    await confirmAction(
      {
        title: t("deleteMaterialTitle"),
        message,
        confirmLabel: t("delete"),
      },
      async () => {
        await request(`/uploads?path=${encodeURIComponent(displayPath)}`, { method: "DELETE" });
        await loadUploads();
        notify("success", t("deleteMaterialSucceeded"));
      },
    );
  };

  const deleteJob = async (jobId) => {
    const job = jobs.find((item) => item.job_id === jobId) || (selectedJob?.job_id === jobId ? selectedJob : null);
    if (["queued", "running"].includes(job?.status)) {
      notify("info", t("runningJobDeleteBlocked"));
      return false;
    }
    return confirmAction(
      {
        title: t("deleteJobTitle"),
        message: t("deleteJobMessage", { title: displayTaskTitle(job) }),
        confirmLabel: t("delete"),
      },
      async () => {
        await request(`/jobs/${jobId}`, { method: "DELETE" });
        if (selectedJobId === jobId) {
          setSelectedJobId(null);
          setSelectedJob(null);
          setSelectedEvents([]);
          setSelectedMessages([]);
          setSelectedPlan(null);
          closeEventStream();
        }
        await refreshJobs(false);
        notify("success", t("deleteJobSucceeded"));
      },
    );
  };

  const submitJob = async () => {
    const task = taskText.trim();
    if (!task) return;
    if (mode === "agent" && !hasSavedConfig) {
      setSettingsOpen(true);
      notify("info", t("apiRequired"));
      return;
    }
    const effectiveDirectPhase3 = directPhase3Execution;
    const payload = {
      task,
      mode,
      enable_phase2_research: effectiveDirectPhase3 ? false : enablePhase2Research,
      enable_plan_review: enablePlanReview,
      direct_phase3_execution: effectiveDirectPhase3,
      prefer_local_materials: preferLocalMaterials,
    };
    const job = await request("/jobs", { method: "POST", body: JSON.stringify(payload) });
    setTaskText("");
    setSelectedJobId(job.job_id);
    setSelectedJob(job);
    setSelectedEvents([]);
    setSelectedMessages([]);
    setSelectedPlan(null);
    await refreshJobs(true);
  };

  const stopSelectedJob = async () => {
    if (!selectedJob || selectedJob.status !== "running") return;
    await request(`/jobs/${selectedJob.job_id}/cancel`, { method: "POST", body: JSON.stringify({}) });
    setSelectedJob((job) => job ? { ...job, status: "cancelled" } : job);
    closeEventStream();
    await refreshJobs(false);
  };

  const resumeJob = async (jobId) => {
    const job = await request(`/jobs/${jobId}/resume`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setSelectedJob(job);
    await refreshJobs(true);
  };

  const sendGuidance = async (content) => {
    if (!selectedJob) return;
    const message = await request(`/jobs/${selectedJob.job_id}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    setSelectedMessages((current) => {
      if (current.some((item) => item.message_id === message.message_id)) {
        return current;
      }
      return [...current, message];
    });
    setSelectedJob((job) => job?.job_id === selectedJob.job_id
      ? { ...job, steering_status: "pending" }
      : job);
    loadMessages(selectedJob.job_id).catch(() => {});
    refreshJobs(false).catch(() => {});
  };

  const sendPlanFeedback = async (version, feedback) => {
    if (!selectedJob || !version) return;
    const payload = await request(`/jobs/${selectedJob.job_id}/plans/${version}/feedback`, {
      method: "POST",
      body: JSON.stringify({ feedback }),
    });
    setSelectedPlan((current) => ({ ...(current || {}), ...payload }));
    await loadCurrentPlan(selectedJob.job_id);
    notify("success", t("planFeedbackApplied"));
  };

  const approvePlan = async (version) => {
    if (!selectedJob || !version) return;
    await request(`/jobs/${selectedJob.job_id}/plans/${version}/approve`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadCurrentPlan(selectedJob.job_id);
    await refreshJobs(false);
    notify("success", t("planApproved"));
  };

  const rejectPlan = async (version) => {
    if (!selectedJob || !version) return;
    await request(`/jobs/${selectedJob.job_id}/plans/${version}/reject`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadCurrentPlan(selectedJob.job_id);
    await refreshJobs(false);
    notify("success", t("planRejected"));
  };

  const selectedMeaningfulEvents = getMeaningfulEvents(selectedEvents);
  const logEvents = getVisibleLogEvents(selectedEvents);
  const latestEvent = [...selectedMeaningfulEvents].reverse()[0];
  const summary = selectedJob?.status === "failed"
    ? t("taskFailedSummary")
    : selectedJob?.status === "interrupted"
      ? t("taskInterruptedSummary")
    : selectedJob?.status === "cancelled"
      ? t("taskCancelledSummary")
      : selectedJob?.status === "completed"
        ? selectedJob?.mode === "demo"
          ? t("demoCompletedSummary")
          : safeDisplayText(selectedJob.final_output, t("eventCompletedBody"))
        : latestEvent
          ? describeEvent(latestEvent).body
          : t("waitingMore");
  const currentPhaseCode = getHighestPhase(selectedMeaningfulEvents);
  const currentPhase = selectedJob?.status === "completed"
    ? t("done")
    : selectedJob?.status === "cancelled"
      ? t("statusCancelled")
      : selectedJob?.status === "interrupted"
        ? t("statusInterrupted")
      : selectedJob?.status === "failed"
        ? t("statusFailed")
        : ["pending", "queued"].includes(selectedJob?.status)
          ? t("notStarted")
          : phaseLabel(currentPhaseCode);
  const primaryArtifacts = (selectedJob?.artifacts || []).filter(isPrimaryArtifact);
  const composerProps = {
    taskText,
    setTaskText,
    mode,
    setMode,
    selectedJob,
    submitJob,
    stopSelectedJob,
    messages: selectedMessages,
    sendGuidance,
    enablePhase2Research,
    enablePlanReview,
    directPhase3Execution,
    preferLocalMaterials,
    setEnablePhase2Research,
    setEnablePlanReview,
    setDirectPhase3Execution,
    setPreferLocalMaterials,
    syncWorkflowConfig,
    workflowConfigSaving,
    uploads,
    uploading,
    uploadSelectedFiles,
    notify,
  };

  const materialsProps = {
    uploads,
    uploadAnalysisBadgeLabel,
    uploadAnalysisHint,
    formatBytes,
    formatDate,
    fileUrl,
    setTaskText,
    deleteUpload,
    loadUploads,
    uploading,
    uploadSelectedFiles,
    notify,
    t,
  };

  return (
    <div className="app-shell">
      <AppSidebar
        collapsed={sidebarCollapsed}
        setCollapsed={setSidebarCollapsed}
        currentView={currentView}
        setCurrentView={setCurrentView}
        jobs={jobs}
        selectedJobId={selectedJobId}
        selectJob={selectJob}
        displayTaskTitle={displayTaskTitle}
        statusLabel={statusLabel}
        setSettingsOpen={setSettingsOpen}
        notify={notify}
        t={t}
      />

      <div className="grid min-h-0 min-w-0 grid-rows-[auto_minmax(0,1fr)]">
        <AppTopbar
          currentView={currentView}
          language={language}
          setLanguage={setLanguage}
          uploading={uploading}
          uploadSelectedFiles={uploadSelectedFiles}
          setSettingsOpen={setSettingsOpen}
          setMobileDrawerOpen={setMobileDrawerOpen}
          t={t}
        />

        <main className="min-h-0 min-w-0 overflow-hidden">
          {currentView === "workbench" && (
            <WorkbenchView
              selectedJob={selectedJob}
              selectedMeaningfulEvents={selectedMeaningfulEvents}
              logEvents={logEvents}
              summary={summary}
              currentPhase={currentPhase}
              currentPhaseCode={currentPhaseCode}
              primaryArtifacts={primaryArtifacts}
              activeTab={activeContextTab}
              setActiveTab={setActiveContextTab}
              displayTaskTitle={displayTaskTitle}
              modeLabel={modeLabel}
              statusLabel={statusLabel}
              formatDate={formatDate}
              describeEvent={describeEvent}
              artifactLabel={artifactLabel}
              fileUrl={fileUrl}
              downloadFileUrl={downloadFileUrl}
              formatDuration={formatDuration}
              planInfo={selectedPlan}
              sendPlanFeedback={sendPlanFeedback}
              approvePlan={approvePlan}
              rejectPlan={rejectPlan}
              composerProps={composerProps}
              copyFullLog={copyFullLog}
              downloadFullLogUrl={downloadFullLogUrl}
              notify={notify}
              t={t}
            />
          )}
          {currentView === "jobs" && (
            <JobsView
              jobs={jobs}
              selectedJob={selectedJob}
              selectedJobId={selectedJobId}
              selectJob={selectJob}
              deleteJob={deleteJob}
              stopSelectedJob={stopSelectedJob}
              resumeJob={resumeJob}
              openWorkbench={() => setCurrentView("workbench")}
              createTask={openWorkbenchComposer}
              displayTaskTitle={displayTaskTitle}
              statusLabel={statusLabel}
              modeLabel={modeLabel}
              formatDate={formatDate}
              notify={notify}
              t={t}
            />
          )}
          {currentView === "materials" && <MaterialsView {...materialsProps} />}
          {currentView === "artifacts" && (
            <ArtifactsView
              artifacts={primaryArtifacts}
              selectedJob={selectedJob}
              artifactLabel={artifactLabel}
              fileUrl={fileUrl}
              downloadFileUrl={downloadFileUrl}
              formatBytes={formatBytes}
              formatDuration={formatDuration}
              selectTask={() => setCurrentView("jobs")}
              backToWorkbench={() => setCurrentView("workbench")}
              t={t}
            />
          )}
        </main>
      </div>

      <MobileDrawer
        open={mobileDrawerOpen}
        setOpen={setMobileDrawerOpen}
        currentView={currentView}
        setCurrentView={setCurrentView}
        jobs={jobs}
        selectedJobId={selectedJobId}
        selectJob={selectJob}
        displayTaskTitle={displayTaskTitle}
        setSettingsOpen={setSettingsOpen}
        notify={notify}
        t={t}
      />
      <MobileBottomNav currentView={currentView} setCurrentView={setCurrentView} t={t} />

      {settingsOpen && (
        <RedesignedSettingsModal
          configForm={configForm}
          setConfigForm={setConfigForm}
          configMessage={configMessage}
          setConfigMessage={setConfigMessage}
          submitConfig={submitConfig}
          loadConfig={loadConfig}
          setSettingsOpen={setSettingsOpen}
          notify={notify}
          t={t}
        />
      )}
      <ToastViewport toasts={toasts} dismissToast={dismissToast} />
      <ConfirmDialog dialog={confirmDialog} closeDialog={closeConfirmDialog} t={t} />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
