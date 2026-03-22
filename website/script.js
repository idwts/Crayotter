(() => {
  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => {
      const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
      return map[ch] || ch;
    });

  const parsePhase3ToolCalls = (logText) => {
    const lines = String(logText || "").split(/\r?\n/);
    const calls = [];
    const marker = "Phase3 工具调用:";

    for (const line of lines) {
      const markerIndex = line.indexOf(marker);
      if (markerIndex < 0) continue;

      const content = line.slice(markerIndex + marker.length).trim();
      if (!content) continue;

      const argsIndex = content.indexOf(" args=");
      const toolName = (argsIndex >= 0 ? content.slice(0, argsIndex) : content).trim();
      const rawArgs = argsIndex >= 0 ? content.slice(argsIndex + 6).trim() : "";
      const argsPreview = rawArgs.length > 96 ? `${rawArgs.slice(0, 96)}...` : rawArgs;

      if (!toolName) continue;
      calls.push({ toolName, argsPreview });
    }
    return calls;
  };

  const fetchWithTimeout = async (url, options = {}, timeoutMs = 10000) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  };

  const renderToolCallList = (calls) => {
    if (!calls.length) {
      return `
        <ol class="tool-path-list">
          <li><span class="tool-call-id">--</span>未从日志中解析到工具调用，请检查日志路径。</li>
        </ol>
      `;
    }

    const rows = calls
      .map((call, index) => {
        const order = String(index + 1).padStart(2, "0");
        const argsHtml = call.argsPreview
          ? `<div class="tool-args">${escapeHtml(call.argsPreview)}</div>`
          : "";
        return `<li><span class="tool-call-id">${order}.</span><code>${escapeHtml(
          call.toolName
        )}</code>${argsHtml}</li>`;
      })
      .join("");

    return `<ol class="tool-path-list">${rows}</ol>`;
  };

  const renderDemoCard = (item, calls) => {
    return `
      <article class="demo-work-card">
        <div class="demo-media">
          <h3>${escapeHtml(item.title)}</h3>
          <p>${escapeHtml(item.description || "")}</p>
          <div class="demo-video-frame">
            <video controls preload="metadata">
              <source src="${escapeHtml(item.videoSrc)}" type="video/mp4">
              当前浏览器不支持视频播放。
            </video>
          </div>
        </div>
        <div class="demo-log-path">
          <h4>完整工具调用路径（按日志顺序）</h4>
          <div class="tool-path-scroll" data-log-container="true" role="region" aria-label="${escapeHtml(
            item.title
          )} 工具调用路径">
            ${
              calls
                ? renderToolCallList(calls)
                : '<div class="log-loading">正在读取工具轨迹...</div>'
            }
          </div>
          <p class="hint">日志来源：<code>${escapeHtml(item.logSrc)}</code>。</p>
          <div class="demo-actions">
            <a class="btn btn-primary btn-sm" href="${escapeHtml(
              item.traceHref
            )}" target="_blank" rel="noopener noreferrer">进入详细日志</a>
          </div>
        </div>
      </article>
    `;
  };

  const loadDemoShowcase = async () => {
    const demoStacks = Array.from(document.querySelectorAll("[data-demo-stack='true']"));
    if (!demoStacks.length) return;

    const configPath = demoStacks[0].getAttribute("data-config");
    if (!configPath) return;
    const configVersion =
      demoStacks[0].getAttribute("data-config-version") ||
      document.querySelector('meta[name="site-version"]')?.getAttribute("content") ||
      Date.now().toString();
    const configUrl = `${configPath}${configPath.includes("?") ? "&" : "?"}v=${encodeURIComponent(
      configVersion
    )}`;

    let config;
    try {
      const configResp = await fetchWithTimeout(configUrl, { cache: "no-store" }, 5000);
      if (!configResp.ok) throw new Error(`配置读取失败: ${configResp.status}`);
      config = await configResp.json();
    } catch (error) {
      demoStacks.forEach((stack) => {
        stack.innerHTML =
          '<div class="demo-empty">读取展示配置失败，请检查 <code>assets/demo-showcase.json</code>。</div>';
      });
      return;
    }

    const items = Array.isArray(config.items) ? config.items : [];
    if (!items.length) {
      demoStacks.forEach((stack) => {
        stack.innerHTML = '<div class="demo-empty">配置文件中没有可展示的 Demo 项。</div>';
      });
      return;
    }

    // 1) 先渲染卡片和视频，避免被轨迹加载阻塞
    const stackItemMap = new Map();
    demoStacks.forEach((stack) => {
      const targetTab = stack.getAttribute("data-demo-tab");
      const item = items.find((entry) => entry.tab === targetTab) || null;
      if (!item) {
        stack.innerHTML = `<div class="demo-empty">未找到 ${escapeHtml(
          targetTab || "当前"
        )} 对应的视频配置。</div>`;
        return;
      }
      stack.innerHTML = renderDemoCard(item, null);
      stackItemMap.set(stack, item);
    });

    const loadedTabs = new Set();
    const loadCallsForStack = async (stack) => {
      const item = stackItemMap.get(stack);
      if (!item) return;
      const targetTab = item.tab || "";
      if (loadedTabs.has(targetTab)) return;

      const callsSrc = item.callsSrc || "";
      const logSrc = item.logSrc || "";
      const dataSrc = callsSrc || logSrc;
      if (!dataSrc) return;

      loadedTabs.add(targetTab);
      try {
        const sourceUrl = `${dataSrc}${dataSrc.includes("?") ? "&" : "?"}v=${encodeURIComponent(
          configVersion
        )}`;
        const resp = await fetchWithTimeout(sourceUrl, { cache: "no-store" }, callsSrc ? 5000 : 12000);
        if (!resp.ok) throw new Error(`轨迹读取失败: ${resp.status}`);

        let calls = [];
        if (callsSrc) {
          const data = await resp.json();
          calls = Array.isArray(data.calls) ? data.calls : [];
        } else {
          const text = await resp.text();
          calls = parsePhase3ToolCalls(text);
        }

        const logContainer = stack.querySelector("[data-log-container='true']");
        if (!logContainer) return;
        logContainer.innerHTML = renderToolCallList(calls);
      } catch (error) {
        const logContainer = stack.querySelector("[data-log-container='true']");
        if (!logContainer) return;
        logContainer.innerHTML = `
          <ol class="tool-path-list">
            <li><span class="tool-call-id">--</span>轨迹读取超时或失败，稍后重试。</li>
          </ol>
        `;
      }
    };

    // 2) 首屏仅加载当前激活 tab 的轨迹
    const activePanel = document.querySelector(".tab-panel.active [data-demo-stack='true']");
    if (activePanel) {
      await loadCallsForStack(activePanel);
    }

    // 3) 用户切换 tab 时按需加载该 tab 轨迹
    const tabButtons = Array.from(document.querySelectorAll(".tab-button"));
    tabButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const target = button.getAttribute("data-tab-target");
        const targetStack = document.querySelector(
          `.tab-panel[data-tab-panel="${target}"] [data-demo-stack='true']`
        );
        if (targetStack) {
          loadCallsForStack(targetStack);
        }
      });
    });
  };

  const body = document.body;
  const themeToggle = document.getElementById("themeToggle");
  const themeStorageKey = "crayotter-theme";

  const applyTheme = (theme) => {
    const isLight = theme === "light";
    body.classList.toggle("theme-light", isLight);
    if (themeToggle) {
      themeToggle.textContent = isLight ? "🌙 切换深色" : "☀️ 切换浅色";
      themeToggle.setAttribute("aria-label", isLight ? "切换深色主题" : "切换浅色主题");
    }
  };

  const savedTheme = localStorage.getItem(themeStorageKey) || "dark";
  applyTheme(savedTheme);

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const nextTheme = body.classList.contains("theme-light") ? "dark" : "light";
      localStorage.setItem(themeStorageKey, nextTheme);
      applyTheme(nextTheme);
    });
  }

  const navToggle = document.querySelector(".nav-toggle");
  const navLinks = document.querySelector(".nav-links");

  if (navToggle && navLinks) {
    navToggle.addEventListener("click", () => {
      navLinks.classList.toggle("open");
    });

    navLinks.querySelectorAll("a").forEach((anchor) => {
      anchor.addEventListener("click", () => {
        navLinks.classList.remove("open");
      });
    });
  }

  const tabButtons = Array.from(document.querySelectorAll(".tab-button"));
  const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));

  tabButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.getAttribute("data-tab-target");
      tabButtons.forEach((b) => b.classList.toggle("active", b === button));
      tabPanels.forEach((panel) => {
        panel.classList.toggle("active", panel.getAttribute("data-tab-panel") === target);
      });
    });
  });

  const yearElement = document.getElementById("current-year");
  if (yearElement) {
    yearElement.textContent = String(new Date().getFullYear());
  }

  loadDemoShowcase();
})();
