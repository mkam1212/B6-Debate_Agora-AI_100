import { requestJson } from "../api.js";
import {
  collectQuestionForm,
  copyableRecommendation,
  hrefFor,
  renderDiscussionPage,
  renderNewPage,
  renderNotFound,
  renderRouteError,
  renderWorkspace,
} from "./views.js";

const runtime = window.GizmoAppRuntime;
const config = runtime.readConfig();
const API = config.apiBase.replace(/\/$/, "");
const baseElement = document.querySelector("base");
const configuredBasePath = new URL(baseElement.href, window.location.href).pathname;
const BASE_PATH = configuredBasePath.endsWith("/") ? configuredBasePath : `${configuredBasePath}/`;
const appView = document.getElementById("app-view");

const state = {
  modelStatus: null,
  modelStatusAt: 0,
  discussion: null,
  pendingDelete: null,
  flash: null,
  renderGeneration: 0,
  pollTimer: null,
  currentRoute: null,
  scrollPositions: new Map(),
};

if ("scrollRestoration" in window.history) {
  window.history.scrollRestoration = "manual";
}

function apiPath(path) {
  return `${API}${path.startsWith("/") ? path : `/${path}`}`;
}

function jsonOptions(method, value) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
    timeoutMs: 30_000,
  };
}

function currentRoutePath() {
  const pathname = window.location.pathname;
  if (pathname === BASE_PATH.slice(0, -1)) return "/";
  const relative = pathname.startsWith(BASE_PATH) ? pathname.slice(BASE_PATH.length) : "";
  return `/${relative}`;
}

function readRoute() {
  const path = currentRoutePath();
  const search = new URLSearchParams(window.location.search);
  if (path === "/" || path === "/workspace") return { kind: "workspace", key: "workspace" };
  if (path === "/new") return { kind: "new", key: "new" };
  const match = path.match(/^\/discussions\/([^/]+)(?:\/(question|discussion|conclusion))?\/?$/);
  if (match) {
    let id = match[1];
    try {
      id = decodeURIComponent(id);
    } catch (_error) {
      return { kind: "not-found", key: path };
    }
    const versionValue = search.get("version");
    const version = versionValue && /^\d+$/.test(versionValue) ? Number(versionValue) : null;
    return {
      kind: "discussion",
      id,
      tab: match[2] || "question",
      version,
      key: `${path}${window.location.search}`,
    };
  }
  return { kind: "not-found", key: path };
}

function routeHref(path) {
  return hrefFor(path, BASE_PATH);
}

function setPageLocation(label) {
  const location = document.getElementById("page-location");
  if (location) {
    location.textContent = label;
    location.hidden = !label;
  }
}

function setNavCurrent(route) {
  const current = route.kind === "workspace" ? "topics" : "";
  for (const link of document.querySelectorAll("[data-nav]")) {
    if (link.dataset.nav === current) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
}

function setDocumentTitle(title = "Agent Debate") {
  document.title = title === "Agent Debate" ? title : `${title} · Agent Debate`;
}

async function loadModelStatus(force = false) {
  if (!force && state.modelStatus && Date.now() - state.modelStatusAt < 30_000) return state.modelStatus;
  try {
    const response = await requestJson(apiPath("/debate/status"));
    state.modelStatus = response;
    state.modelStatusAt = Date.now();
  } catch (error) {
    state.modelStatus = {
      mode: "sample",
      modelAvailable: false,
      message: error instanceof Error ? error.message : "Live model status is unavailable.",
    };
  }
  return state.modelStatus;
}

function rememberScroll() {
  if (state.currentRoute) state.scrollPositions.set(state.currentRoute.key, window.scrollY);
}

function clearPoll() {
  if (state.pollTimer !== null) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function applyFlash() {
  if (!state.flash) return;
  const notice = document.createElement("div");
  notice.className = state.flash.error ? "error-notice" : "notice";
  notice.setAttribute("role", state.flash.error ? "alert" : "status");
  notice.setAttribute("data-global-feedback", "");
  notice.textContent = state.flash.message;
  appView.prepend(notice);
  state.flash = null;
}

function showGlobalFeedback(message, error = false) {
  state.flash = { message, error };
  const existing = appView.querySelector("[data-global-feedback]");
  if (existing) existing.remove();
  applyFlash();
}

function showFormFeedback(form, message, error = false) {
  const node = form.querySelector("[data-feedback]");
  if (!node) {
    showGlobalFeedback(message, error);
    return;
  }
  node.textContent = message;
  node.hidden = false;
  node.classList.toggle("error-notice", error);
  node.classList.toggle("notice", !error);
  node.setAttribute("role", error ? "alert" : "status");
}

function setBusy(form, busy) {
  form.dataset.busy = busy ? "true" : "false";
  for (const button of form.querySelectorAll("button[type='submit']")) {
    button.disabled = busy;
  }
}

function safeVersionPath(id, tab, version) {
  return `/discussions/${encodeURIComponent(id)}/${tab}?version=${encodeURIComponent(version || 1)}`;
}

async function pushRoute(path, { flash = null, focus = true } = {}) {
  rememberScroll();
  const destination = routeHref(path);
  window.history.pushState({}, "", destination);
  if (flash) state.flash = flash;
  await renderRoute({ restoreScroll: true, focus });
}

async function fetchDiscussion(route) {
  const query = route.version ? `?version=${encodeURIComponent(route.version)}` : "";
  const response = await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(route.id)}${query}`));
  return response.discussion;
}

function startPolling(discussion, route, generation) {
  clearPoll();
  if (discussion.mode === "sample" || discussion.run?.state !== "running") return;
  state.pollTimer = window.setTimeout(async () => {
    if (generation !== state.renderGeneration || state.currentRoute?.kind !== "discussion" || state.currentRoute.id !== route.id) return;
    const scrollY = window.scrollY;
    try {
      const latest = await fetchDiscussion(route);
      if (generation !== state.renderGeneration) return;
      state.discussion = latest;
      appView.innerHTML = renderDiscussionPage(latest, {
        tab: route.tab,
        basePath: BASE_PATH,
        apiBase: API,
        pendingDelete: state.pendingDelete,
      });
      applyFlash();
      window.scrollTo(0, scrollY);
      startPolling(latest, route, generation);
    } catch (error) {
      if (generation !== state.renderGeneration) return;
      showGlobalFeedback(`Could not refresh the running discussion. ${error instanceof Error ? error.message : "Try again shortly."}`, true);
      state.pollTimer = window.setTimeout(() => startPolling(discussion, route, generation), 2_500);
    }
  }, 1_500);
}

async function renderRoute({ restoreScroll = false, preserveScroll = false, focus = false } = {}) {
  clearPoll();
  const route = readRoute();
  const generation = ++state.renderGeneration;
  const oldScroll = window.scrollY;
  state.currentRoute = route;
  setNavCurrent(route);
  appView.setAttribute("aria-busy", "true");

  const renderPage = async () => {
    if (route.kind === "workspace") {
      setPageLocation("");
      setDocumentTitle("Topics");
      const discussionsResult = await requestJson(apiPath("/debate/discussions"));
      return renderWorkspace({ discussions: discussionsResult.discussions, basePath: BASE_PATH });
    }
    if (route.kind === "new") {
      setPageLocation("New topic");
      setDocumentTitle("New topic");
      return renderNewPage({ modelStatus: await loadModelStatus() });
    }
    if (route.kind === "discussion") {
      const discussion = await fetchDiscussion(route);
      if (generation !== state.renderGeneration) return "";
      state.discussion = discussion;
      setPageLocation(`${discussion.title} / ${route.tab[0].toUpperCase()}${route.tab.slice(1)}`);
      setDocumentTitle(discussion.title);
      return renderDiscussionPage(discussion, {
        tab: route.tab,
        basePath: BASE_PATH,
        apiBase: API,
        pendingDelete: state.pendingDelete,
      });
    }
    setPageLocation("Page not found");
    setDocumentTitle("Page not found");
    return renderNotFound(BASE_PATH);
  };

  try {
    const html = await renderPage();
    if (generation !== state.renderGeneration) return;
    appView.innerHTML = html;
    applyFlash();
    appView.removeAttribute("aria-busy");
    if (preserveScroll) {
      window.scrollTo(0, oldScroll);
    } else if (restoreScroll) {
      window.scrollTo(0, state.scrollPositions.get(route.key) || 0);
    }
    if (focus) appView.focus({ preventScroll: true });
    if (route.kind === "discussion" && state.discussion) startPolling(state.discussion, route, generation);
  } catch (error) {
    if (generation !== state.renderGeneration) return;
    appView.innerHTML = renderRouteError(error instanceof Error ? error.message : "Try again.", BASE_PATH);
    applyFlash();
    appView.removeAttribute("aria-busy");
  }
}

async function createVersionAndRun(id, baseVersion, payload, fallbackQuestion = "question") {
  const created = await requestJson(
    apiPath(`/debate/discussions/${encodeURIComponent(id)}/versions`),
    jsonOptions("POST", { ...payload, baseVersion }),
  );
  const version = created.discussion.version;
  try {
    await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(id)}/run`), { method: "POST", timeoutMs: 30_000 });
    await pushRoute(safeVersionPath(id, "discussion", version));
  } catch (error) {
    const message = `Version ${version} was saved. ${error instanceof Error ? error.message : "No generated response was created."}`;
    await pushRoute(safeVersionPath(id, fallbackQuestion, version), { flash: { message, error: true } });
  }
}

async function handleFormSubmit(event) {
  const form = event.target.closest("form[data-form]");
  if (!form) return;
  event.preventDefault();
  if (!form.reportValidity()) return;
  if (form.dataset.busy === "true") return;

  const formType = form.dataset.form;
  const submitAction = event.submitter?.value || "";
  setBusy(form, true);
  try {
    if (formType === "create-discussion") {
      const payload = collectQuestionForm(form);
      const created = await requestJson(apiPath("/debate/discussions"), jsonOptions("POST", payload));
      try {
        await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(created.discussion.id)}/run`), {
          method: "POST",
          timeoutMs: 30_000,
        });
        await pushRoute(safeVersionPath(created.discussion.id, "discussion", created.discussion.version), { focus: true });
      } catch (error) {
        const message = `Topic saved. ${error instanceof Error ? error.message : "Start again when model access is available."}`;
        await pushRoute(safeVersionPath(created.discussion.id, "question", created.discussion.version), {
          flash: { message, error: true },
          focus: true,
        });
      }
      return;
    }

    if (formType === "save-question") {
      const id = form.dataset.id;
      const version = Number(form.dataset.version || 1);
      const payload = collectQuestionForm(form);
      await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(id)}`), jsonOptions("PUT", payload));
      if (submitAction === "start") {
        try {
          await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(id)}/run`), { method: "POST", timeoutMs: 30_000 });
          await pushRoute(safeVersionPath(id, "discussion", version), { focus: true });
        } catch (error) {
          showFormFeedback(form, error instanceof Error ? error.message : "The live discussion could not start.", true);
        }
      } else {
        showFormFeedback(form, "Question and perspectives saved.");
      }
      return;
    }

    if (formType === "new-version") {
      const id = form.dataset.id;
      const baseVersion = Number(form.dataset.version || 1);
      const payload = collectQuestionForm(form);
      await createVersionAndRun(id, baseVersion, payload);
      return;
    }

    if (formType === "followup") {
      const id = form.dataset.id;
      const version = Number(form.dataset.version || 1);
      const text = String(new FormData(form).get("text") || "").trim();
      const created = await requestJson(
        apiPath(`/debate/discussions/${encodeURIComponent(id)}/followups`),
        jsonOptions("POST", { text, baseVersion: version }),
      );
      const newVersion = created.discussion.version;
      try {
        await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(id)}/run`), { method: "POST", timeoutMs: 30_000 });
        await pushRoute(safeVersionPath(id, "discussion", newVersion));
      } catch (error) {
        const message = `Follow-up saved as version ${newVersion}. ${error instanceof Error ? error.message : "No generated response was created."}`;
        await pushRoute(safeVersionPath(id, "question", newVersion), { flash: { message, error: true } });
      }
      return;
    }

    if (formType === "clarify") {
      const id = form.dataset.id;
      const version = Number(form.dataset.version || 1);
      const answer = String(new FormData(form).get("text") || "").trim();
      if (!answer) {
        showFormFeedback(form, "Add your answer, or choose to continue with the listed assumptions.", true);
        return;
      }
      const discussion = state.discussion;
      const question = discussion.question;
      const extraContext = `Clarification for this version:\n${answer}`;
      const payload = {
        title: question.title,
        question: question.question,
        goal: question.goal,
        constraints: question.constraints,
        options: question.options,
        context: [question.context, extraContext].filter(Boolean).join("\n\n"),
        currentView: question.currentView,
        sources: discussion.sources,
        perspectives: discussion.perspectives,
      };
      await createVersionAndRun(id, version, payload);
      return;
    }

    if (formType === "reflection") {
      const id = form.dataset.id;
      const version = Number(form.dataset.version || 1);
      const reflection = String(new FormData(form).get("reflection") || "").trim();
      await requestJson(
        apiPath(`/debate/discussions/${encodeURIComponent(id)}/reflection`),
        jsonOptions("PUT", { version, reflection }),
      );
      showFormFeedback(form, "Personal note saved.");
    }
  } catch (error) {
    showFormFeedback(form, error instanceof Error ? error.message : "The request could not be completed.", true);
  } finally {
    if (form.isConnected) setBusy(form, false);
  }
}

async function copyRecommendation() {
  if (!state.discussion?.synthesis) return;
  const value = copyableRecommendation(state.discussion);
  let copied = false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      copied = true;
    }
  } catch (_error) {
    copied = false;
  }
  if (!copied) {
    const input = document.createElement("textarea");
    input.value = value;
    input.setAttribute("readonly", "");
    input.style.position = "fixed";
    input.style.left = "-9999px";
    document.body.append(input);
    input.select();
    try {
      copied = document.execCommand("copy");
    } catch (_error) {
      copied = false;
    }
    input.remove();
  }
  const status = appView.querySelector("[data-copy-status]");
  if (status) status.textContent = copied ? "Recommendation copied." : "Copy is unavailable here. Use a Markdown or plain-text export instead.";
}

async function handleAction(event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const id = button.dataset.id || state.discussion?.id;

  if (action === "add-source") {
    const container = button.closest("form")?.querySelector("[data-source-editors]");
    if (!container) return;
    const count = container.querySelectorAll("[data-source-editor]").length;
    if (count >= 8) {
      showGlobalFeedback("A discussion can include up to 8 sources.", true);
      return;
    }
    const editor = document.createElement("div");
    editor.className = "source-editor";
    editor.setAttribute("data-source-editor", "");
    editor.innerHTML = `<div class="field"><label for="source-name-added-${count}">Source name</label><input id="source-name-added-${count}" type="text" data-source-name maxlength="120" placeholder="For example, Project notes"></div><div class="field"><label for="source-text-added-${count}">Pasted text</label><textarea id="source-text-added-${count}" data-source-text maxlength="12000" placeholder="Paste a passage or notes"></textarea></div><input type="hidden" data-source-id value="">`;
    container.append(editor);
    editor.querySelector("input")?.focus();
    return;
  }

  if (action === "ask-delete") {
    state.pendingDelete = id;
    await renderRoute({ preserveScroll: true });
    return;
  }

  if (action === "cancel-delete") {
    state.pendingDelete = null;
    await renderRoute({ preserveScroll: true });
    return;
  }

  if (action === "delete-discussion") {
    button.disabled = true;
    try {
      await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(id)}`), { method: "DELETE" });
      state.pendingDelete = null;
      await pushRoute("/workspace", { flash: { message: "Discussion deleted." } });
    } catch (error) {
      button.disabled = false;
      showGlobalFeedback(error instanceof Error ? error.message : "The discussion could not be deleted.", true);
    }
    return;
  }

  if (action === "cancel-run") {
    button.disabled = true;
    try {
      await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(id)}/cancel`), jsonOptions("POST", {}));
      showGlobalFeedback("Stop requested. Any response already in progress will finish before the run stops.");
      await renderRoute({ preserveScroll: true });
    } catch (error) {
      button.disabled = false;
      showGlobalFeedback(error instanceof Error ? error.message : "The discussion could not be stopped.", true);
    }
    return;
  }

  if (action === "retry-run") {
    button.disabled = true;
    try {
      const run = await requestJson(apiPath(`/debate/discussions/${encodeURIComponent(id)}/run`), { method: "POST", timeoutMs: 30_000 });
      await pushRoute(safeVersionPath(id, "discussion", run.discussion.version));
    } catch (error) {
      button.disabled = false;
      showGlobalFeedback(error instanceof Error ? error.message : "The discussion could not be retried.", true);
    }
    return;
  }

  if (action === "continue-assumptions") {
    button.disabled = true;
    try {
      const result = await requestJson(
        apiPath(`/debate/discussions/${encodeURIComponent(id)}/continue`),
        jsonOptions("POST", { useAssumptions: true }),
      );
      await pushRoute(safeVersionPath(id, "discussion", result.discussion.version));
    } catch (error) {
      button.disabled = false;
      showGlobalFeedback(error instanceof Error ? error.message : "The discussion could not continue.", true);
    }
    return;
  }

  if (action === "copy-recommendation") {
    await copyRecommendation();
  }
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-route]");
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const path = link.dataset.route;
  void pushRoute(path, { focus: true });
});

document.addEventListener("click", (event) => {
  void handleAction(event);
});

document.addEventListener("submit", (event) => {
  void handleFormSubmit(event);
});

window.addEventListener("popstate", () => {
  rememberScroll();
  void renderRoute({ restoreScroll: true, focus: true });
});

try {
  await renderRoute();
  runtime.markReady();
} catch (error) {
  runtime.showFatalError(error);
}
