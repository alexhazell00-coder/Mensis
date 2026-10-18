(() => {
  "use strict";

  const activeStatuses = new Set(["queued", "planning", "running", "verifying"]);
  const attentionStatuses = new Set(["blocked", "failed"]);
  const state = {
    tasks: [],
    selectedId: null,
    selectedUpdatedAt: null,
    view: "workspace",
    filter: "all",
    loading: false,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[character]);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* non-JSON error */ }
    if (!response.ok) {
      const detail = payload && payload.detail ? payload.detail : `Request failed (${response.status}).`;
      throw new Error(detail);
    }
    return payload;
  }

  function formatDate(value, options = {}) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat(undefined, options).format(date);
  }

  function relativeTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "just now";
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (seconds < 45) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    return formatDate(value, { month: "short", day: "numeric" });
  }

  function statusLabel(status) {
    const labels = {
      queued: "Queued", planning: "Planning", running: "Running", verifying: "Verifying",
      completed: "Completed", blocked: "Blocked", failed: "Failed", cancelled: "Cancelled",
    };
    return labels[status] || status || "Unknown";
  }

  function statusIcon(status) {
    return ({
      queued: "↗", planning: "◌", running: "◌", verifying: "⌕",
      completed: "✓", blocked: "!", failed: "×", cancelled: "–",
    })[status] || "·";
  }

  function updateClock() {
    const now = new Date();
    const clock = $("#clock");
    const footerClock = $("#footer-clock");
    const dateLabel = $("#date-label");
    if (clock) clock.textContent = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(now);
    if (footerClock) footerClock.textContent = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(now);
    if (dateLabel) dateLabel.textContent = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" }).format(now).toUpperCase();
  }

  function updateMetrics() {
    const active = state.tasks.filter((task) => activeStatuses.has(task.status)).length;
    const verified = state.tasks.filter((task) => task.status === "completed" && task.verification === "verified").length;
    const attention = state.tasks.filter((task) => attentionStatuses.has(task.status)).length;
    $("#metric-active").textContent = String(active).padStart(2, "0");
    $("#metric-verified").textContent = String(verified).padStart(2, "0");
    $("#metric-attention").textContent = String(attention).padStart(2, "0");
    $("#nav-task-count").textContent = String(active);
  }

  function filteredTasks() {
    if (state.filter === "active") return state.tasks.filter((task) => activeStatuses.has(task.status));
    if (state.filter === "needs-attention") return state.tasks.filter((task) => attentionStatuses.has(task.status));
    if (state.filter === "completed") return state.tasks.filter((task) => ["completed", "cancelled"].includes(task.status));
    return state.tasks;
  }

  function renderTaskRows(element, tasks, emptyMessage) {
    if (!element) return;
    if (!tasks.length) {
      const message = state.tasks.length === 0
        ? '<span class="empty-icon">⌁</span><span><strong>No tasks on record.</strong><br>Start with a system check or search the configured workspace.</span>'
        : escapeHtml(emptyMessage || "No tasks match this filter.");
      element.innerHTML = `<div class="empty-list">${message}</div>`;
      return;
    }
    element.innerHTML = tasks.map((task) => {
      const selected = task.id === state.selectedId ? " is-selected" : "";
      const statusClass = `status-${escapeHtml(task.status)}`;
      const verification = task.verification === "verified" ? '<span class="meta-dot"></span><span>evidence recorded</span>' : "";
      return `<button class="task-row${selected}" type="button" data-task-id="${escapeHtml(task.id)}" aria-label="Inspect task: ${escapeHtml(task.request)}">
        <span class="task-icon ${statusClass}">${escapeHtml(statusIcon(task.status))}</span>
        <span class="task-main"><span class="task-title">${escapeHtml(task.request)}</span><span class="task-meta"><span>${escapeHtml(relativeTime(task.created_at))}</span><span class="meta-dot"></span><span>attempt ${escapeHtml(task.attempt)}</span>${verification}</span></span>
        <span class="status-badge ${statusClass}">${escapeHtml(statusLabel(task.status))}</span>
      </button>`;
    }).join("");
  }

  function renderLists() {
    const recent = state.tasks.slice(0, 6);
    renderTaskRows($("#task-list"), recent, "No recent tasks.");
    renderTaskRows($("#task-list-full"), filteredTasks(), "No tasks match this filter.");
  }

  function renderTaskDetail(task, target) {
    if (!target) return;
    if (!task) {
      target.innerHTML = '<div class="inspector-empty"><div class="empty-orbit" aria-hidden="true"><span></span></div><h3>Choose a task.</h3><p>Activity and results will appear here.</p></div>';
      return;
    }
    const isActive = activeStatuses.has(task.status);
    const canResume = ["blocked", "failed"].includes(task.status);
    const result = task.result;
    const plan = Array.isArray(task.plan) ? task.plan : [];
    const events = Array.isArray(task.events) ? task.events : [];
    let resultHtml = "";
    if (result && result.answer) {
      const badge = result.verification === "verified"
        ? '<span class="status-badge status-completed">Tool evidence</span>'
        : '<span class="status-badge status-blocked">Unverified</span>';
      const evidence = Array.isArray(result.evidence) ? result.evidence : [];
      resultHtml = `<div class="detail-label">Outcome ${badge}</div><div class="result-block">${escapeHtml(result.answer)}</div>${result.verification_note ? `<div class="result-note">${escapeHtml(result.verification_note)}</div>` : ""}`;
      if (evidence.length) {
        resultHtml += `<details class="event-details"><summary>Inspect ${evidence.length} evidence item${evidence.length === 1 ? "" : "s"}</summary><pre>${escapeHtml(JSON.stringify(evidence, null, 2))}</pre></details>`;
      }
    } else if (task.error) {
      resultHtml = `<div class="detail-label">Failure detail</div><div class="result-block">${escapeHtml(task.error)}</div>`;
    }
    const planHtml = plan.length
      ? `<div class="detail-label">Execution plan</div><ul class="plan-list">${plan.map((step) => `<li>${escapeHtml(step)}</li>`).join("")}</ul>`
      : "";
    const eventsHtml = events.length
      ? `<div class="detail-label">Activity · ${events.length}</div><div class="event-list">${events.slice(-40).map((event) => {
          const timestamp = formatDate(event.created_at, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
          const details = event.data && Object.keys(event.data).length
            ? `<details class="event-details"><summary>View recorded data</summary><pre>${escapeHtml(JSON.stringify(event.data, null, 2))}</pre></details>`
            : "";
          return `<div class="event-item event-${escapeHtml(event.type)}"><div class="event-title">${escapeHtml(event.message)}</div><div class="event-time">${escapeHtml(timestamp)}</div>${details}</div>`;
        }).join("")}</div>`
      : "";
    const controls = `${isActive ? `<button class="control-button is-danger" data-action="cancel" data-task-id="${escapeHtml(task.id)}" type="button">Cancel task</button>` : ""}${canResume ? `<button class="control-button" data-action="resume" data-task-id="${escapeHtml(task.id)}" type="button">Resume task</button>` : ""}`;
    target.innerHTML = `<div class="inspector-task">
      <h3 class="inspector-task-title">${escapeHtml(task.request)}</h3>
      <div class="inspector-status-line"><span class="status-badge status-${escapeHtml(task.status)}">${escapeHtml(statusLabel(task.status))}</span><span class="inspector-date">${escapeHtml(relativeTime(task.updated_at))}</span></div>
      ${controls ? `<div class="inspector-controls">${controls}</div>` : ""}
      ${planHtml}${resultHtml}${eventsHtml}
    </div>`;
  }

  async function selectTask(taskId) {
    state.selectedId = taskId;
    renderLists();
    try {
      const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
      state.selectedUpdatedAt = task.updated_at;
      $("#inspector-id").textContent = task.id.toUpperCase();
      renderTaskDetail(task, $("#inspector-content"));
      renderTaskDetail(task, $("#queue-detail"));
      renderLists();
    } catch (error) {
      showMessage(error.message, true);
    }
  }

  function clearSelection() {
    state.selectedId = null;
    state.selectedUpdatedAt = null;
    $("#inspector-id").textContent = "NO TASK SELECTED";
    renderTaskDetail(null, $("#inspector-content"));
    renderTaskDetail(null, $("#queue-detail"));
    renderLists();
  }

  function showMessage(message, isError = false) {
    const element = $("#form-message");
    element.textContent = message || "";
    element.style.color = isError ? "var(--red)" : "var(--green)";
  }

  async function loadHealth() {
    try {
      const health = await api("/api/health");
      $("#core-dot").classList.remove("is-error");
      $("#core-state").textContent = "Core online";
      $("#health-indicator").classList.remove("is-error");
      $("#health-indicator").innerHTML = "<span></span> OPERATIONAL";
      $("#workspace-name").textContent = health.workspace_label || "workspace";
      $("#tool-count").textContent = String(health.available_tools ?? "—");
      $("#model-name").textContent = health.provider && health.provider.configured ? health.provider.model : "Not configured";
      const configured = Boolean(health.provider && health.provider.configured);
      $("#provider-mini-state").textContent = configured ? `Local · ${health.provider.model}` : "No model connected";
      $("#provider-dot").classList.toggle("is-ready", configured);
    } catch (_) {
      $("#core-dot").classList.add("is-error");
      $("#core-state").textContent = "Core unavailable";
      $("#health-indicator").classList.add("is-error");
      $("#health-indicator").innerHTML = "<span></span> OFFLINE";
      $("#provider-mini-state").textContent = "Connection unavailable";
    }
  }

  async function loadTools() {
    const grid = $("#tool-grid");
    try {
      const response = await api("/api/tools");
      const tools = response.tools || [];
      $("#registry-count").textContent = String(tools.length).padStart(2, "0");
      $("#tool-count").textContent = String(tools.length);
      if (!tools.length) {
        grid.innerHTML = '<div class="empty-list">No tools are currently registered.</div>';
        return;
      }
      grid.innerHTML = tools.map((tool, index) => `<article class="tool-card">
        <div class="tool-card-top"><span class="tool-index">CAPABILITY / ${String(index + 1).padStart(2, "0")}</span><span class="permission-badge">${escapeHtml(tool.permission)}</span></div>
        <h3>${escapeHtml(tool.name)}</h3><p>${escapeHtml(tool.description)}</p>
        <div class="tool-meta"><span>${escapeHtml(tool.id)}</span><span class="tool-version">v${escapeHtml(tool.version)}</span></div>
      </article>`).join("");
    } catch (error) {
      grid.innerHTML = `<div class="empty-list">${escapeHtml(error.message)}</div>`;
    }
  }

  async function refresh({ quiet = false } = {}) {
    if (state.loading) return;
    state.loading = true;
    try {
      const response = await api("/api/tasks?limit=100");
      state.tasks = response.tasks || [];
      updateMetrics();
      renderLists();
      if (state.selectedId) {
        const summary = state.tasks.find((task) => task.id === state.selectedId);
        if (!summary) {
          clearSelection();
        } else if (summary.updated_at !== state.selectedUpdatedAt) {
          await selectTask(state.selectedId);
        }
      }
      if (!quiet) showMessage("");
    } catch (error) {
      if (!quiet) showMessage(error.message, true);
      $("#task-list").innerHTML = `<div class="empty-list">Could not load tasks: ${escapeHtml(error.message)}</div>`;
    } finally {
      state.loading = false;
    }
  }

  const viewCopy = {
    workspace: {
      breadcrumb: "Overview", title: "Your work, <em>accounted for.</em>",
      description: "Delegate a task. Follow the evidence. Stay in control of what happens next.",
      eyebrow: 'PERSONAL TASK SPACE <span class="eyebrow-index">01 / 03</span>',
    },
    queue: {
      breadcrumb: "Task queue", title: "Work, <em>in motion.</em>",
      description: "Every request has a status, an execution trail, and an honest outcome.",
      eyebrow: 'PERSISTENT EXECUTION <span class="eyebrow-index">02 / 03</span>',
    },
    tools: {
      breadcrumb: "Tool registry", title: "Every capability, <em>on record.</em>",
      description: "Inspect the contracts, inputs, and permissions available to this MENSIS installation.",
      eyebrow: 'CAPABILITY REGISTRY <span class="eyebrow-index">03 / 03</span>',
    },
  };

  function setView(view) {
    if (!viewCopy[view]) return;
    state.view = view;
    $$("[data-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
    $("#workspace-view").classList.toggle("is-hidden", view !== "workspace");
    $("#queue-view").classList.toggle("is-hidden", view !== "queue");
    $("#tools-view").classList.toggle("is-hidden", view !== "tools");
    $("#breadcrumb-current").textContent = viewCopy[view].breadcrumb;
    $("#page-title").innerHTML = viewCopy[view].title;
    $("#page-description").textContent = viewCopy[view].description;
    $("#heading-eyebrow").innerHTML = viewCopy[view].eyebrow;
    if (view === "tools") loadTools();
    if (view === "queue") renderLists();
  }

  async function submitTask(request) {
    const submitButton = $("#submit-button");
    submitButton.disabled = true;
    showMessage("Adding task to the persistent queue…");
    try {
      const task = await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ request }),
      });
      state.selectedId = task.id;
      state.selectedUpdatedAt = null;
      setView("workspace");
      await refresh({ quiet: true });
      await selectTask(task.id);
      $("#task-input").value = "";
      updateCounter();
      showMessage("Task queued. Its progress and evidence will appear in the inspector.");
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      submitButton.disabled = false;
    }
  }

  function updateCounter() {
    $("#input-counter").textContent = `${$("#task-input").value.length} / 2000`;
  }

  async function handleTaskAction(action, taskId) {
    const button = document.querySelector(`[data-action="${action}"][data-task-id="${CSS.escape(taskId)}"]`);
    if (button) button.disabled = true;
    try {
      const response = await api(`/api/tasks/${encodeURIComponent(taskId)}/${action}`, { method: "POST" });
      if (action === "cancel" && !response.accepted) showMessage("This task is already finished.");
      else showMessage(action === "cancel" ? "Cancellation requested." : "Task re-queued.");
      await refresh({ quiet: true });
      await selectTask(taskId);
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("[data-view]");
    if (nav) {
      setView(nav.dataset.view);
      return;
    }
    const taskRow = event.target.closest("[data-task-id]");
    if (taskRow && !taskRow.dataset.action) {
      selectTask(taskRow.dataset.taskId);
      return;
    }
    const action = event.target.closest("[data-action]");
    if (action) {
      handleTaskAction(action.dataset.action, action.dataset.taskId);
      return;
    }
    const prompt = event.target.closest("[data-prompt]");
    if (prompt) {
      const input = $("#task-input");
      input.value = prompt.dataset.prompt;
      updateCounter();
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    }
  });

  $("#task-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const request = $("#task-input").value.trim();
    if (request) submitTask(request);
    else showMessage("Enter a task before submitting.", true);
  });
  $("#task-input").addEventListener("input", updateCounter);
  $("#task-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#task-form").requestSubmit();
    }
  });
  $("#refresh-button").addEventListener("click", () => { refresh(); loadHealth(); });
  $("#view-all-button").addEventListener("click", () => setView("queue"));
  $("#clear-selection").addEventListener("click", clearSelection);
  $("#task-filter").addEventListener("change", (event) => {
    state.filter = event.target.value;
    renderLists();
  });

  updateClock();
  refresh({ quiet: true });
  loadHealth();
  setInterval(updateClock, 30_000);
  setInterval(() => {
    if (document.visibilityState === "visible") {
      refresh({ quiet: true });
      loadHealth();
    }
  }, 4_000);
})();
