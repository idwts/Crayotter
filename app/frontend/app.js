(() => {
  const STORAGE_KEYS = {
    sidebarCollapsed: "crayotter.sidebarCollapsed",
    uploadsCollapsed: "crayotter.uploadsCollapsed",
    lastMode: "crayotter.lastMode",
    lastPhase2: "crayotter.lastPhase2",
  };

  const state = {
    jobs: [],
    uploads: [],
    selectedJobId: null,
    selectedJob: null,
    selectedEvents: [],
    eventSource: null,
    refreshTimer: null,
    hasSavedConfig: false,
    detailExpanded: false,
    logExpanded: false,
    logShowAll: false,
    enablePhase2Research: true,
    uploading: false,
    uploadsCollapsed: false,
  };

  const LOG_PREVIEW_LIMIT = 40;

  const $ = (selector) => document.querySelector(selector);

  const elements = {
    sidebar: $("#sidebar"),
    toggleSidebarBtn: $("#toggleSidebarBtn"),
    newChatBtn: $("#newChatBtn"),
    jobsMeta: $("#jobsMeta"),
    jobsList: $("#jobsList"),
    refreshJobsBtn: $("#refreshJobsBtn"),
    uploadPanel: $("#uploadPanel"),
    uploadInput: $("#uploadInput"),
    uploadBtn: $("#uploadBtn"),
    toggleUploadsBtn: $("#toggleUploadsBtn"),
    refreshUploadsBtn: $("#refreshUploadsBtn"),
    uploadsList: $("#uploadsList"),
    healthText: $("#healthText"),
    apiSettingsBtn: $("#apiSettingsBtn"),
    settingsModal: $("#settingsModal"),
    closeSettingsBtn: $("#closeSettingsBtn"),
    reloadConfigBtn: $("#reloadConfigBtn"),
    configForm: $("#configForm"),
    apiKeyInput: $("#apiKeyInput"),
    baseUrlInput: $("#baseUrlInput"),
    modelInput: $("#modelInput"),
    videoModelInput: $("#videoModelInput"),
    ttsModelInput: $("#ttsModelInput"),
    configMessage: $("#configMessage"),
    chatMessages: $("#chatMessages"),
    jobForm: $("#jobForm"),
    taskInput: $("#taskInput"),
    modeSelect: $("#modeSelect"),
    phase2ToggleBtn: $("#phase2ToggleBtn"),
    actionJobBtn: $("#actionJobBtn"),
  };

  const trashIcon = "🗑";

  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (char) => {
      const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
      return map[char] || char;
    });

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
        // ignore
      }
      throw new Error(message);
    }

    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }
    return response.text();
  };

  const formatDate = (value) => {
    if (!value) return "--";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
  };

  const formatBytes = (value) => {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const sized = bytes / 1024 ** index;
    return `${sized >= 10 || index === 0 ? sized.toFixed(0) : sized.toFixed(1)} ${units[index]}`;
  };

  const fileUrl = (path) => `/files?path=${encodeURIComponent(path)}`;

  const modeLabel = (mode) => (mode === "agent" ? "真实 Agent" : "演示模式");

  const phase2ModeLabel = (enabled) => (enabled ? "开启 Phase 2 深研" : "跳过 Phase 2，直达 Phase 3");

  const displayTaskTitle = (job) => {
    const task = String(job?.task || "");
    if (job?.mode === "demo" && /^[\x00-\x7F\s_-]+$/.test(task) && /(demo|validation|acceptance|job|simple)/i.test(task)) {
      return "演示任务";
    }
    return task;
  };

  const localizeText = (text) => {
    const raw = String(text || "");
    if (!raw) return raw;
    return raw
      .replace(
        "Demo job completed. Backend service, event bus, and job persistence are working.",
        "演示任务已完成，说明后端服务、事件流和任务持久化都运行正常。",
      )
      .replace("Backend service, event bus, and job persistence are working.", "后端服务、事件流和任务持久化都运行正常。");
  };

  const statusLabel = (status) => {
    const map = {
      pending: "等待中",
      running: "执行中",
      completed: "已完成",
      failed: "失败",
      cancelled: "已停止",
    };
    return map[status] || status || "--";
  };

  const phaseLabel = (phase) => {
    const map = {
      phase1: "正在拆解任务和搜集素材",
      phase2: "正在整理内容和搭建结构",
      phase3: "正在合成最终结果",
    };
    return map[phase] || "正在处理中";
  };

  const toolLabel = (toolName) => {
    const map = {
      search_bilibili_video: "搜索素材",
      rank_video_candidates: "筛选候选素材",
      add_narration_segments: "生成旁白片段",
    };
    return map[toolName] || toolName || "调用工具";
  };

  const isPrimaryArtifact = (artifact) => {
    const name = String(artifact?.name || "").toLowerCase();
    const suffix = String(artifact?.suffix || "").toLowerCase();
    if ([".json", ".jsonl", ".log"].includes(suffix)) return false;
    if (name === "events.jsonl" || name === "summary.json") return false;
    return [".txt", ".md", ".mp4", ".webm", ".mov"].includes(suffix);
  };

  const artifactLabel = (artifact) => {
    const suffix = String(artifact?.suffix || "").toLowerCase();
    if (suffix === ".mp4" || suffix === ".webm" || suffix === ".mov") return "成片结果";
    if (suffix === ".md") return "文稿结果";
    if (suffix === ".txt") return "任务结果";
    return artifact?.name || "结果文件";
  };

  const openSettings = () => {
    elements.settingsModal.classList.remove("hidden");
  };

  const forceCloseSettings = () => {
    elements.settingsModal.classList.add("hidden");
  };

  const setHealth = (connected, label = "") => {
    elements.healthText.textContent = label || (connected ? "服务连接正常" : "服务连接失败");
  };

  const updateSidebarState = (collapsed) => {
    elements.sidebar.classList.toggle("collapsed", collapsed);
    elements.toggleSidebarBtn.textContent = collapsed ? "☰" : "←";
    localStorage.setItem(STORAGE_KEYS.sidebarCollapsed, collapsed ? "1" : "0");
  };

  const updateConfigButton = () => {
    elements.apiSettingsBtn.textContent = state.hasSavedConfig ? "API 设置与更改" : "请先设置 API";
  };

  const updateUploadsPanelState = (collapsed) => {
    state.uploadsCollapsed = collapsed;
    elements.uploadPanel.classList.toggle("collapsed", collapsed);
    elements.toggleUploadsBtn.textContent = collapsed ? "展开素材" : "收起素材";
    elements.toggleUploadsBtn.title = collapsed ? "展开本地素材面板" : "收起本地素材面板";
    elements.toggleUploadsBtn.setAttribute("aria-pressed", collapsed ? "true" : "false");
    localStorage.setItem(STORAGE_KEYS.uploadsCollapsed, collapsed ? "1" : "0");
  };

  const updatePhase2ToggleButton = () => {
    const enabled = state.enablePhase2Research;
    elements.phase2ToggleBtn.textContent = enabled ? "Phase 2 开" : "Phase 2 关";
    elements.phase2ToggleBtn.title = enabled ? "当前启用 Phase 2 深度研究" : "当前跳过 Phase 2，直接进入 Phase 3";
    elements.phase2ToggleBtn.setAttribute("aria-pressed", enabled ? "true" : "false");
    elements.phase2ToggleBtn.classList.toggle("is-disabled", !enabled);
  };

  const updateUploadButton = () => {
    elements.uploadBtn.textContent = state.uploading ? "上传中..." : "上传视频";
    elements.uploadBtn.disabled = state.uploading;
  };

  const renderUploads = () => {
    if (!state.uploads.length) {
      elements.uploadsList.className = "uploads-list empty-state";
      elements.uploadsList.textContent = "还没有上传本地素材。";
      return;
    }

    elements.uploadsList.className = "uploads-list";
    elements.uploadsList.innerHTML = state.uploads
      .map(
        (item) => `
          <article class="upload-card">
            <div class="upload-card-main">
              <div class="upload-card-title">${escapeHtml(item.name || "--")}</div>
              <div class="upload-card-meta">
                ${escapeHtml(item.display_path || item.name || "--")} ·
                ${escapeHtml(formatBytes(item.size_bytes))} ·
                ${escapeHtml(formatDate(item.modified_at))}
              </div>
            </div>
            <div class="upload-card-actions">
              <button class="ghost-button compact" type="button" data-upload-insert="${escapeHtml(item.display_path || "")}">插入任务</button>
              <a class="ghost-button compact" href="${escapeHtml(fileUrl(item.path))}" target="_blank" rel="noreferrer">打开</a>
              <button class="ghost-button compact upload-delete-button" type="button" data-upload-delete="${escapeHtml(item.display_path || "")}">删除</button>
            </div>
          </article>
        `,
      )
      .join("");

    elements.uploadsList.querySelectorAll("[data-upload-insert]").forEach((button) => {
      button.addEventListener("click", () => {
        const displayPath = button.getAttribute("data-upload-insert");
        if (!displayPath) return;
        const prefix = elements.taskInput.value.trim() ? "\n" : "";
        elements.taskInput.value += `${prefix}优先使用我上传的本地素材：${displayPath}`;
        elements.taskInput.focus();
      });
    });

    elements.uploadsList.querySelectorAll("[data-upload-delete]").forEach((button) => {
      button.addEventListener("click", () => {
        const displayPath = button.getAttribute("data-upload-delete");
        if (!displayPath) return;
        deleteUpload(displayPath).catch((error) => window.alert(error.message));
      });
    });
  };

  const captureChatScroll = () => {
    const viewport = elements.chatMessages;
    const distanceFromBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    return {
      scrollTop: viewport.scrollTop,
      stickToBottom: distanceFromBottom < 48,
    };
  };

  const restoreChatScroll = (snapshot) => {
    if (!snapshot) return;
    window.requestAnimationFrame(() => {
      if (snapshot.stickToBottom) {
        elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
        return;
      }
      elements.chatMessages.scrollTop = snapshot.scrollTop;
    });
  };

  const updateActionButton = (running) => {
    elements.actionJobBtn.textContent = running ? "■" : "↑";
    elements.actionJobBtn.title = running ? "停止任务" : "发送任务";
    elements.actionJobBtn.setAttribute("aria-label", running ? "停止任务" : "发送任务");
    elements.actionJobBtn.classList.toggle("is-running", running);
  };

  const buildDetailCardHtml = (job) => {
    if (!job) return "";
    const rows = [
      ["任务状态", statusLabel(job.status)],
      ["运行方式", modeLabel(job.mode)],
      ["Phase 2 设置", phase2ModeLabel(job.enable_phase2_research !== false)],
      ["创建时间", formatDate(job.created_at)],
      ["开始时间", formatDate(job.started_at)],
      ["完成时间", formatDate(job.completed_at)],
      ["事件数量", job.events_count],
      ["错误信息", job.error || "--"],
    ];

    return `
      <article class="mini-card detail-mini-card">
        <div class="bubble-title">任务信息</div>
        <div class="detail-inline-list">
          ${rows
            .map(
              ([label, value]) => `
                <div class="detail-inline-item">
                  <div class="detail-label">${escapeHtml(label)}</div>
                  <div>${escapeHtml(String(value ?? "--"))}</div>
                </div>
              `,
            )
            .join("")}
        </div>
      </article>
    `;
  };

  const buildDetailInfoHtml = (job, eventCount) => {
    if (!job) return "";
    const count = Number(eventCount ?? job.events_count ?? 0);
    const showTruncatedNote = !state.logShowAll && count > LOG_PREVIEW_LIMIT;
    const canExpandAll = count > LOG_PREVIEW_LIMIT;
    const rows = [
      ["任务状态", statusLabel(job.status)],
      ["运行方式", modeLabel(job.mode)],
      ["Phase 2 设置", phase2ModeLabel(job.enable_phase2_research !== false)],
      ["创建时间", formatDate(job.created_at)],
      ["开始时间", formatDate(job.started_at)],
      ["完成时间", formatDate(job.completed_at)],
      ["错误信息", job.error || "--"],
    ];

    return `
      <article class="mini-card detail-mini-card">
        <div class="bubble-title">任务信息</div>
        <div class="detail-inline-list">
          <div class="detail-inline-item detail-log-item">
            <div class="detail-label-row">
              <div class="detail-log-count">已记录日志：${escapeHtml(String(count))} 条</div>
              <button class="detail-inline-link" type="button" data-log-toggle="true">${state.logExpanded ? "收起日志" : "查看日志"}</button>
            </div>
            <div class="detail-log-toolbar">
              <span class="detail-log-note">${escapeHtml(showTruncatedNote ? `当前仅显示最近 ${LOG_PREVIEW_LIMIT} 条` : "当前显示全部")}</span>
              ${
                canExpandAll
                  ? `<button class="detail-inline-link" type="button" data-log-expand-all="true">${state.logShowAll ? `收起到最近 ${LOG_PREVIEW_LIMIT} 条` : "展开全部"}</button>`
                  : ""
              }
              <button class="detail-inline-link" type="button" data-log-copy="true">复制完整日志</button>
              <button class="detail-inline-link" type="button" data-log-download="true">下载日志</button>
            </div>
          </div>
          ${rows
            .map(
              ([label, value]) => `
                <div class="detail-inline-item">
                  <div class="detail-label">${escapeHtml(label)}</div>
                  <div>${escapeHtml(String(value ?? "--"))}</div>
                </div>
              `,
            )
            .join("")}
        </div>
      </article>
    `;
  };

  const buildLogCardHtml = (events) => {
    const visibleEvents = state.logShowAll ? events : events.slice(-LOG_PREVIEW_LIMIT);
    const lines = visibleEvents
      .map((event) => {
        const summary = event.type === "log" ? String(event.payload?.message || "--") : describeEvent(event).body;
        return `
          <div class="log-entry">
            <div class="log-entry-meta">${escapeHtml(formatDate(event.timestamp))}</div>
            <div class="log-entry-body">${escapeHtml(summary || "--")}</div>
          </div>
        `;
      })
      .join("");

    return `
      <article class="mini-card detail-mini-card">
        <div class="bubble-title">任务日志</div>
        <div class="log-list">${lines || '<div class="bubble-body">暂无日志</div>'}</div>
      </article>
    `;
  };

  const getMeaningfulEvents = (events) =>
    events.filter((event) => !["log", "heartbeat"].includes(event.type));

  const getVisibleLogEvents = (events) =>
    events.filter((event) => event.type !== "heartbeat");

  const buildLogPlainText = (events) =>
    events
      .map((event) => {
        const summary = event.type === "log" ? String(event.payload?.message || "--") : describeEvent(event).body;
        return `${formatDate(event.timestamp)}\n${summary || "--"}`;
      })
      .join("\n\n");

  const copyText = async (text) => {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "absolute";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    textarea.remove();
  };

  const copyFullLog = async () => {
    const logEvents = getVisibleLogEvents(state.selectedEvents);
    const text = buildLogPlainText(logEvents);
    if (!text.trim()) {
      window.alert("当前没有可复制的日志。");
      return;
    }
    await copyText(text);
    window.alert("完整日志已复制。");
  };

  const downloadFullLog = () => {
    const job = state.selectedJob;
    if (!job) return;
    const logEvents = getVisibleLogEvents(state.selectedEvents);
    const text = buildLogPlainText(logEvents);
    if (!text.trim()) {
      window.alert("当前没有可下载的日志。");
      return;
    }

    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const href = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = `${job.job_id}_logs.txt`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  };

  const getLatestPhase = (events) => {
    const latest = [...events].reverse().find((event) => event.payload?.phase);
    return latest?.payload?.phase || "";
  };

  const describeEvent = (event) => {
    const payload = event.payload || {};
    const phase = payload.phase ? phaseLabel(payload.phase) : "";

    switch (event.type) {
      case "job_created":
        return { title: "任务已创建", body: "系统已经收到你的任务，准备开始执行。" };
      case "job_started":
        return { title: "任务开始执行", body: "Agent 已启动，正在进入工作流程。" };
      case "phase_started":
        return { title: phase || "进入新阶段", body: "任务已经进入新的处理阶段。" };
      case "thinking_summary":
        return { title: phase || "过程摘要", body: payload.summary || "Agent 正在整理下一步思路。" };
      case "step_started":
        return { title: "开始执行步骤", body: payload.description || "开始执行新的步骤。" };
      case "step_completed":
        return { title: "步骤已完成", body: payload.result || "这个步骤已经完成。" };
      case "tool_called":
        return { title: toolLabel(payload.tool_name), body: "Agent 正在调用工具处理当前任务。" };
      case "tool_result":
        return { title: toolLabel(payload.tool_name), body: payload.summary || "工具已经返回结果。" };
      case "job_completed":
        return { title: "任务完成", body: payload.final_output || "任务已经成功完成。" };
      case "job_failed":
        return { title: "任务失败", body: payload.error || "执行过程中出现错误。" };
      case "job_cancelled":
        return { title: "任务已停止", body: "当前任务已被停止。" };
      default:
        return {
          title: "过程更新",
          body:
            payload.summary ||
            payload.description ||
            payload.result ||
            payload.message ||
            payload.final_output ||
            "任务状态已更新。",
        };
    }
  };

  const closeEventStream = () => {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  };

  const requestUploads = async (files) => {
    const form = new FormData();
    Array.from(files).forEach((file) => {
      form.append("files", file);
    });

    const response = await fetch("/uploads", {
      method: "POST",
      body: form,
    });

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

    return response.json();
  };

  const loadUploads = async () => {
    const payload = await request("/uploads");
    state.uploads = Array.isArray(payload.items) ? payload.items : [];
    renderUploads();
  };

  const uploadSelectedFiles = async (files) => {
    if (!files?.length) return;
    state.uploading = true;
    updateUploadButton();
    try {
      await requestUploads(files);
      await loadUploads();
    } finally {
      state.uploading = false;
      updateUploadButton();
      elements.uploadInput.value = "";
    }
  };

  const deleteUpload = async (displayPath) => {
    if (!window.confirm(`删除素材 ${displayPath} 后，将无法在后续任务中继续引用。继续吗？`)) {
      return;
    }
    await request(`/uploads?path=${encodeURIComponent(displayPath)}`, {
      method: "DELETE",
    });
    await loadUploads();
  };

  const renderJobs = () => {
    elements.jobsMeta.textContent = `${state.jobs.length} 个任务`;

    if (!state.jobs.length) {
      elements.jobsList.className = "jobs-list empty-state";
      elements.jobsList.textContent = "还没有历史任务。";
      return;
    }

    elements.jobsList.className = "jobs-list";
    elements.jobsList.innerHTML = state.jobs
      .map((job) => {
      const active = job.job_id === state.selectedJobId ? " active" : "";
      const phase2Text = phase2ModeLabel(job.enable_phase2_research !== false);
        return `
          <article class="job-card${active}" data-job-id="${escapeHtml(job.job_id)}">
            <button class="job-delete-button" type="button" data-delete-job-id="${escapeHtml(job.job_id)}" aria-label="删除任务" title="删除任务">${trashIcon}</button>
            <div class="job-card-main">
              <div class="job-card-title">${escapeHtml(displayTaskTitle(job))}</div>
              <div class="job-card-meta">${escapeHtml(modeLabel(job.mode))} · ${escapeHtml(phase2Text)} · ${escapeHtml(formatDate(job.created_at))}</div>
            </div>
            <span class="status-pill ${escapeHtml(job.status)}">${escapeHtml(statusLabel(job.status))}</span>
          </article>
        `;
      })
      .join("");

    elements.jobsList.querySelectorAll("[data-job-id]").forEach((card) => {
      card.addEventListener("click", () => selectJob(card.getAttribute("data-job-id")));
    });
    elements.jobsList.querySelectorAll("[data-delete-job-id]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const jobId = button.getAttribute("data-delete-job-id");
        if (!jobId) return;
        deleteJob(jobId).catch((error) => window.alert(error.message));
      });
    });
  };

  const renderChat = () => {
    const job = state.selectedJob;
    const events = getMeaningfulEvents(state.selectedEvents);
    const logEvents = getVisibleLogEvents(state.selectedEvents);
    const scrollSnapshot = captureChatScroll();

    if (!job) {
      elements.chatMessages.innerHTML = `
        <article class="bubble assistant">
          <div class="bubble-title">你好，我已经准备好了。</div>
          <div class="bubble-body">在下方输入任务，我会自动拆解、执行，并把关键结果清楚地展示给你。</div>
        </article>
        <article class="mini-card">
          <div class="bubble-title">使用建议</div>
          <div class="bubble-body">第一次不确定配置是否正确时，先用演示模式；确认没问题后，再切到真实 Agent。</div>
        </article>
      `;
      updateActionButton(false);
      restoreChatScroll(scrollSnapshot);
      return;
    }

    const latest = [...events].reverse()[0];
    const summary = localizeText(job.final_output || (latest ? describeEvent(latest).body : "任务已经创建，正在等待更多进度。"));
    const phase = phaseLabel(getLatestPhase(events));
    const progressCards = events
      .filter((event) => ["thinking_summary", "step_completed", "tool_result", "job_completed", "job_failed"].includes(event.type))
      .slice(-3)
      .map((event) => {
        const item = describeEvent(event);
        return `
          <article class="mini-card">
            <div class="bubble-title">${escapeHtml(item.title)}</div>
            <div class="bubble-body">${escapeHtml(item.body)}</div>
            <div class="bubble-meta">${escapeHtml(formatDate(event.timestamp))}</div>
          </article>
        `;
      })
      .join("");

    const detailLinkLabel = state.detailExpanded ? "收起信息" : "查看更多信息";
    const detailCard = state.detailExpanded ? buildDetailInfoHtml(job, logEvents.length) : "";
    const logCard = state.detailExpanded && state.logExpanded ? buildLogCardHtml(logEvents) : "";

    elements.chatMessages.innerHTML = `
      <article class="bubble user">
        <div class="bubble-title">我的任务</div>
        <div class="bubble-body">${escapeHtml(displayTaskTitle(job))}</div>
      </article>
      <article class="bubble assistant">
        <div class="bubble-title">${escapeHtml(statusLabel(job.status))}</div>
        <div class="bubble-body">${escapeHtml(summary)}</div>
        <div class="bubble-meta">
          ${escapeHtml(modeLabel(job.mode))} · ${escapeHtml(phase)} · ${escapeHtml(formatDate(job.started_at || job.created_at))}
          <button class="inline-link-button" type="button" data-chat-detail-toggle="true">${escapeHtml(detailLinkLabel)}</button>
        </div>
      </article>
      ${detailCard}
      ${logCard}
      ${progressCards}
    `;

    const detailToggle = elements.chatMessages.querySelector("[data-chat-detail-toggle='true']");
    if (detailToggle) {
      detailToggle.addEventListener("click", () => {
        state.detailExpanded = !state.detailExpanded;
        renderChat();
        renderArtifacts().catch(() => {});
      });
    }

    const logToggle = elements.chatMessages.querySelector("[data-log-toggle='true']");
    if (logToggle) {
      logToggle.addEventListener("click", () => {
        state.logExpanded = !state.logExpanded;
        if (!state.logExpanded) {
          state.logShowAll = false;
        }
        renderChat();
      });
    }

    const logExpandAll = elements.chatMessages.querySelector("[data-log-expand-all='true']");
    if (logExpandAll) {
      logExpandAll.addEventListener("click", () => {
        state.logShowAll = !state.logShowAll;
        renderChat();
      });
    }

    const logCopy = elements.chatMessages.querySelector("[data-log-copy='true']");
    if (logCopy) {
      logCopy.addEventListener("click", () => {
        copyFullLog().catch((error) => window.alert(error.message));
      });
    }

    const logDownload = elements.chatMessages.querySelector("[data-log-download='true']");
    if (logDownload) {
      logDownload.addEventListener("click", () => {
        downloadFullLog();
      });
    }

    restoreChatScroll(scrollSnapshot);
    updateActionButton(job.status === "running");
  };

  const renderArtifacts = async () => {
    if (!state.selectedJob || state.selectedJob.status !== "completed") {
      const existing = elements.chatMessages.querySelector("[data-role='artifact-result']");
      if (existing) existing.remove();
      return;
    }

    const artifacts = (state.selectedJob?.artifacts || []).filter(isPrimaryArtifact);
    const scrollSnapshot = captureChatScroll();
    const existing = elements.chatMessages.querySelector("[data-role='artifact-result']");
    if (existing) existing.remove();

    if (!artifacts.length) {
      restoreChatScroll(scrollSnapshot);
      return;
    }

    const artifact = artifacts[0];
    const suffix = String(artifact?.suffix || "").toLowerCase();
    let body = "结果已经生成，你可以直接预览或打开。";

    if ([".txt", ".md"].includes(suffix)) {
      const content = await fetch(fileUrl(artifact.path)).then((response) => response.text());
      body = localizeText(content.slice(0, 320));
    }

    const resultCard = document.createElement("article");
    resultCard.className = "mini-card";
    resultCard.dataset.role = "artifact-result";
    resultCard.innerHTML = `
      <div class="bubble-title">${escapeHtml(artifactLabel(artifact))}</div>
      <div class="bubble-body">${escapeHtml(body)}</div>
      <div class="artifact-actions">
        <a class="ghost-button compact" href="${escapeHtml(fileUrl(artifact.path))}" target="_blank" rel="noreferrer">打开结果</a>
      </div>
    `;
    elements.chatMessages.appendChild(resultCard);
    restoreChatScroll(scrollSnapshot);
  };

  const fetchJobEvents = async (jobId) => {
    const payload = await request(`/jobs/${jobId}/events`);
    state.selectedEvents = Array.isArray(payload.items) ? payload.items : [];
    renderChat();
  };

  const attachEventStream = (jobId) => {
    closeEventStream();

    if (!state.selectedJob || state.selectedJob.job_id !== jobId || state.selectedJob.status !== "running") {
      return;
    }

    const after = state.selectedEvents.reduce((max, event) => Math.max(max, event.sequence || 0), 0);
    const source = new EventSource(`/jobs/${jobId}/events/stream?after=${after}`);
    state.eventSource = source;

    source.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (!state.selectedEvents.some((item) => item.sequence === event.sequence)) {
        state.selectedEvents.push(event);
        renderChat();
      }

      if (["job_completed", "job_failed", "job_cancelled"].includes(event.type)) {
        refreshJobs(true);
      }
    };

    source.addEventListener("end", () => {
      closeEventStream();
      refreshJobs(true);
    });

    source.onerror = () => {
      closeEventStream();
    };
  };

  const selectJob = async (jobId, options = {}) => {
    const preserveDetail = Boolean(options.preserveDetail);
    const switchingJob = state.selectedJobId !== jobId;
    state.selectedJobId = jobId;
    if (!preserveDetail) {
      state.detailExpanded = false;
    }
    if (switchingJob) {
      state.logExpanded = false;
      state.logShowAll = false;
    }
    renderJobs();
    state.selectedJob = await request(`/jobs/${jobId}`);
    renderChat();
    await fetchJobEvents(jobId);
    await renderArtifacts();
    attachEventStream(jobId);
  };

  const clearSelection = async () => {
    state.selectedJobId = null;
    state.selectedJob = null;
    state.selectedEvents = [];
    state.detailExpanded = false;
    state.logExpanded = false;
    state.logShowAll = false;
    closeEventStream();
    renderJobs();
    renderChat();
    await renderArtifacts();
  };

  const refreshJobs = async (keepSelection = false) => {
    const payload = await request("/jobs");
    state.jobs = Array.isArray(payload.items) ? payload.items : [];
    renderJobs();

    if (!keepSelection && !state.selectedJobId) {
      await clearSelection();
      return;
    }

    let targetId = state.selectedJobId;
    if (!keepSelection) {
      const running = state.jobs.find((job) => job.status === "running");
      targetId = running?.job_id || targetId || state.jobs[0]?.job_id || null;
    }

    if (targetId && state.jobs.some((job) => job.job_id === targetId)) {
      await selectJob(targetId, { preserveDetail: targetId === state.selectedJobId });
      return;
    }

    if (state.jobs.length) {
      await selectJob(state.jobs[0].job_id, { preserveDetail: state.jobs[0].job_id === state.selectedJobId });
      return;
    }

    await clearSelection();
  };

  const refreshJobsListOnly = async () => {
    const payload = await request("/jobs");
    state.jobs = Array.isArray(payload.items) ? payload.items : [];
    renderJobs();
  };

  const loadConfig = async () => {
    const config = await request("/config");
    const profile = config.profiles?.[config.active_profile] || {};
    elements.apiKeyInput.value = profile.api_key || "";
    elements.baseUrlInput.value = profile.base_url || "";
    elements.modelInput.value = profile.model_name || "";
    elements.videoModelInput.value = profile.video_model_name || "";
    elements.ttsModelInput.value = profile.tts_model_name || "";
    state.hasSavedConfig = Boolean(profile.api_key);
    updateConfigButton();
    if (!state.hasSavedConfig) {
      openSettings();
    }
  };

  const submitConfig = async (event) => {
    event.preventDefault();

    const payload = {
      profiles: {
        default: {
          api_key: elements.apiKeyInput.value.trim(),
          base_url: elements.baseUrlInput.value.trim(),
          model_name: elements.modelInput.value.trim(),
          video_model_name: elements.videoModelInput.value.trim(),
          tts_model_name: elements.ttsModelInput.value.trim(),
        },
      },
    };

    await request("/config", {
      method: "PUT",
      body: JSON.stringify(payload),
    });

    state.hasSavedConfig = Boolean(payload.profiles.default.api_key);
    updateConfigButton();
    elements.configMessage.textContent = state.hasSavedConfig ? "设置已保存，后续会自动记住。" : "设置已清空。";

    window.setTimeout(() => {
      elements.configMessage.textContent = "";
    }, 2200);

    if (state.hasSavedConfig) {
      forceCloseSettings();
    }
  };

  const submitJob = async () => {
    const task = elements.taskInput.value.trim();
    if (!task) {
      window.alert("先输入你想完成的任务。");
      return;
    }

    const mode = elements.modeSelect.value;
    const enablePhase2Research = state.enablePhase2Research;
    localStorage.setItem(STORAGE_KEYS.lastMode, mode);
    localStorage.setItem(STORAGE_KEYS.lastPhase2, enablePhase2Research ? "1" : "0");

    if (mode === "agent" && !state.hasSavedConfig) {
      openSettings();
      window.alert("真实 Agent 模式需要先配置 API。");
      return;
    }

    const created = await request("/jobs", {
      method: "POST",
      body: JSON.stringify({ task, mode, enable_phase2_research: enablePhase2Research }),
    });

    elements.taskInput.value = "";
    await refreshJobs();
    await selectJob(created.job_id);
  };

  const cancelSelectedJob = async () => {
    if (!state.selectedJobId) return;
    if (state.selectedJob) {
      state.selectedJob.status = "cancelled";
    }
    updateActionButton(false);
    renderJobs();
    renderChat();
    await request(`/jobs/${state.selectedJobId}/cancel`, {
      method: "POST",
      body: "{}",
    });
    await refreshJobs(true);
  };

  const deleteJob = async (jobId) => {
    if (!window.confirm("删除后，这条任务和它的本地结果都会被移除。继续吗？")) {
      return;
    }

    await request(`/jobs/${jobId}`, {
      method: "DELETE",
    });

    if (state.selectedJobId === jobId) {
      await clearSelection();
    }
    await refreshJobs(false);
  };

  const bootstrap = async () => {
    updateSidebarState(true);
    updateUploadsPanelState(false);

    const lastMode = localStorage.getItem(STORAGE_KEYS.lastMode);
    if (lastMode && ["demo", "agent"].includes(lastMode)) {
      elements.modeSelect.value = lastMode;
    }
    const uploadsCollapsed = localStorage.getItem(STORAGE_KEYS.uploadsCollapsed);
    if (uploadsCollapsed !== null) {
      updateUploadsPanelState(uploadsCollapsed === "1");
    }
    const lastPhase2 = localStorage.getItem(STORAGE_KEYS.lastPhase2);
    if (lastPhase2 !== null) {
      state.enablePhase2Research = lastPhase2 !== "0";
    }
    updatePhase2ToggleButton();
    updateUploadButton();

    try {
      await request("/health");
      setHealth(true);
      await loadConfig();
      await refreshJobs();
      await loadUploads();
    } catch (error) {
      setHealth(false, error.message);
      elements.jobsList.className = "jobs-list empty-state";
      elements.jobsList.textContent = error.message;
      elements.uploadsList.className = "uploads-list empty-state";
      elements.uploadsList.textContent = error.message;
    }

    if (state.refreshTimer) {
      clearInterval(state.refreshTimer);
    }
    state.refreshTimer = window.setInterval(() => {
      const refreshAction = state.selectedJob?.status === "running" ? refreshJobs(true) : refreshJobsListOnly();
      refreshAction
        .then(() => setHealth(true))
        .catch(() => setHealth(false, "服务连接失败"));
    }, 4000);
  };

  elements.toggleSidebarBtn.addEventListener("click", () => {
    updateSidebarState(!elements.sidebar.classList.contains("collapsed"));
  });

  elements.newChatBtn.addEventListener("click", () => {
    clearSelection().catch(() => {});
    elements.taskInput.focus();
  });

  elements.refreshJobsBtn.addEventListener("click", () => {
    refreshJobs(true).catch((error) => window.alert(error.message));
  });

  elements.toggleUploadsBtn.addEventListener("click", () => {
    updateUploadsPanelState(!state.uploadsCollapsed);
  });

  elements.uploadBtn.addEventListener("click", () => {
    if (state.uploading) return;
    elements.uploadInput.click();
  });

  elements.uploadInput.addEventListener("change", () => {
    uploadSelectedFiles(elements.uploadInput.files).catch((error) => {
      state.uploading = false;
      updateUploadButton();
      window.alert(error.message);
    });
  });

  elements.refreshUploadsBtn.addEventListener("click", () => {
    loadUploads().catch((error) => window.alert(error.message));
  });

  elements.phase2ToggleBtn.addEventListener("click", () => {
    state.enablePhase2Research = !state.enablePhase2Research;
    localStorage.setItem(STORAGE_KEYS.lastPhase2, state.enablePhase2Research ? "1" : "0");
    updatePhase2ToggleButton();
  });

  elements.apiSettingsBtn.addEventListener("click", openSettings);
  elements.closeSettingsBtn.addEventListener("click", forceCloseSettings);
  elements.settingsModal.addEventListener("click", (event) => {
    if (event.target instanceof HTMLElement && event.target.dataset.closeModal === "true") {
      forceCloseSettings();
    }
  });

  elements.reloadConfigBtn.addEventListener("click", () => {
    loadConfig().catch((error) => {
      elements.configMessage.textContent = `读取配置失败：${error.message}`;
    });
  });

  elements.configForm.addEventListener("submit", (event) => {
    submitConfig(event).catch((error) => {
      elements.configMessage.textContent = `保存失败：${error.message}`;
    });
  });

  elements.jobForm.addEventListener("submit", (event) => {
    event.preventDefault();
  });

  elements.taskInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    if (state.selectedJob?.status === "running") return;
    submitJob().catch((error) => window.alert(error.message));
  });

  elements.actionJobBtn.addEventListener("click", () => {
    const running = state.selectedJob?.status === "running";
    const action = running ? cancelSelectedJob() : submitJob();
    action.catch((error) => window.alert(error.message));
  });

  bootstrap();
})();
