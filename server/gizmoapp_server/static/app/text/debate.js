import { consumeStartIntent, rememberStartIntent } from "./start-flow.js?v=allowance-78";
import { requestEventStream, requestJson } from "../api.js?v=flow-77";
import { icon, initials, agentName, avatar, avatarStyle, avatarColorPicker, randomAvatarColor, isAvatarColor } from "./ui.js?v=allowance-78";
import { createWorkspace } from "./workspace.js?v=folder-motion-88";
import { renderSummaryCopy } from "./summary-view.js?v=allowance-78";
import { trustedCitations, renderCitedText, streamingText } from "./sources.js?v=web-75";
import { isDialogBackdropClick, mentionAtCaret, mentionedRole, insertRoleMention, discussionTurnDelay, CONVERSATION_STYLES, withConversationStyle } from "./interaction-utils.js?v=nav-73";
import { rememberDraft, recoverDraft, acknowledgeDraft } from "./drafts.js";
import { rememberAttachments, recoverAttachments, acknowledgeAttachments, attachmentIds } from "./attachment-utils.js";
import { createAppNavigation, createViewPositions } from "./navigation.js?v=navigation-87";

const runtime = window.GizmoAppRuntime;
const appView = document.getElementById("app-view");
const stageNavigation = document.getElementById("stage-navigation");
const summaryDialog = document.getElementById("summary-dialog");
const baseElement = document.querySelector("base");
const configuredPath = new URL(baseElement.href, window.location.href).pathname;
const BASE_PATH = configuredPath.endsWith("/") ? configuredPath : `${configuredPath}/`;

const APP_PATH = `${BASE_PATH}app`;

const AVATARS = ["🧭", "🔎", "🌱", "⚖️", "💡", "🪨"];
const ROLE_TYPES = ["Analyst", "Challenger", "Builder", "Skeptic", "Synthesizer"];
const ROLE_TYPE_LABELS = {
  Analyst: "Checks facts",
  Challenger: "Tests the idea",
  Builder: "Plans practical steps",
  Skeptic: "Checks risks",
  Synthesizer: "Connects viewpoints",
};
const MAX_PARTICIPANTS = 16;
const MAX_CHAT_MESSAGES = 100;
const MAX_CHAT_MESSAGE_LENGTH = 2_000;
const MAX_AVATAR_SEED = 2_147_483_647;
const MAX_RECENT_GENERATED_NAMES = 12;
const RECENT_GENERATED_NAMES_KEY = "debate-recent-generated-names-v1";
const AUTO_CLARIFICATION_WAIT_ENABLED = false;
const ROLE_GENERATION_TIMEOUT_MS = 240_000;
const ROLE_REPLY_TIMEOUT_MS = 120_000;
const STREAM_CHUNK_DELAY_MS = 24;
const MESSAGE_LIST_BOTTOM_THRESHOLD_PX = 96;
const REPLY_DIAGNOSTIC_VERSION = "debate_reply_diag_v2";
const REPLY_DIAGNOSTIC_FINISH_REASONS = new Set([
  "stop", "length", "content_filter", "tool_calls", "function_call", "unknown",
]);
const REPLY_DIAGNOSTIC_FAILURE_CATEGORIES = new Set([
  "missing_marker", "invalid_boolean", "incomplete_marker", "empty_body", "invalid_control_line",
  "truncated", "transport_failure", "empty_response", "provider_failure", "oversized_body",
  "invalid_stream", "unknown",
]);
const REPLY_DIAGNOSTIC_PARSE_FAILURES = new Set([
  "not_checked", "none", "empty_response", "missing_marker", "invalid_boolean",
  "incomplete_marker", "empty_body", "invalid_control_line", "unknown",
]);
const DEFAULT_ROLES = [
  {
    id: "role-1",
    avatar: "🔎",
    name: "Avery",
    prompt: "Look for assumptions, evidence, and what would change the conclusion.",
    roleType: "Analyst",
  },
  {
    id: "role-2",
    avatar: "⚖️",
    name: "Jordan",
    prompt: "Test the strongest claim. Name a practical trade-off or counterexample.",
    roleType: "Challenger",
  },
  {
    id: "role-3",
    avatar: "🌱",
    name: "Casey",
    prompt: "Connect the strongest points and name what remains unresolved.",
    roleType: "Synthesizer",
  },
];

function defaultRoles() {
  const colors = [];
  return DEFAULT_ROLES.map(role => {
    const avatarColor = randomAvatarColor(colors);
    colors.push(avatarColor);
    return { ...role, avatarColor };
  });
}

const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

function normalizeReplyDiagnostics(value) {
  if (!value || typeof value !== "object"
    || value.version !== REPLY_DIAGNOSTIC_VERSION
    || value.status !== "failed"
    || !Array.isArray(value.attempts)
    || value.attempts.length < 1
    || value.attempts.length > 2) return null;

  const requestId = value.requestId === "unknown"
    || (typeof value.requestId === "string" && /^[a-f0-9]{16}$/.test(value.requestId))
    ? value.requestId
    : "unknown";
  const attempts = [];
  for (const [index, item] of value.attempts.entries()) {
    if (!item || typeof item !== "object" || item.attempt !== index + 1) return null;
    const finishReason = REPLY_DIAGNOSTIC_FINISH_REASONS.has(item.finishReason)
      ? item.finishReason
      : "unknown";
    const outputChars = item.outputChars === "unknown"
      || (Number.isSafeInteger(item.outputChars) && item.outputChars >= 0 && item.outputChars <= 1_000_000)
      ? item.outputChars
      : "unknown";
    const category = REPLY_DIAGNOSTIC_FAILURE_CATEGORIES.has(item.category) ? item.category : "unknown";
    const structure = item.structure;
    if (!structure || typeof structure !== "object"
      || typeof structure.markerPrefixFound !== "boolean"
      || typeof structure.booleanValueParsed !== "boolean"
      || typeof structure.newlineSeen !== "boolean"
      || typeof structure.bodyAfterMarker !== "boolean") return null;
    const parseFailure = REPLY_DIAGNOSTIC_PARSE_FAILURES.has(item.parseFailure)
      ? item.parseFailure
      : "unknown";
    attempts.push({
      attempt: index + 1,
      finishReason,
      outputChars,
      category,
      structure: {
        markerPrefixFound: structure.markerPrefixFound,
        booleanValueParsed: structure.booleanValueParsed,
        newlineSeen: structure.newlineSeen,
        bodyAfterMarker: structure.bodyAfterMarker,
      },
      parseFailure,
    });
  }
  return { version: REPLY_DIAGNOSTIC_VERSION, status: "failed", requestId, attempts };
}

function formatReplyDiagnostics(value) {
  const diagnostics = normalizeReplyDiagnostics(value);
  if (!diagnostics) return "";
  const attempts = diagnostics.attempts
    .map((item) => {
      const { structure } = item;
      return `${item.attempt}:${item.finishReason}/${item.outputChars}/${item.category}`
        + `/prefix=${Number(structure.markerPrefixFound)},bool=${Number(structure.booleanValueParsed)}`
        + `/newline=${Number(structure.newlineSeen)},body=${Number(structure.bodyAfterMarker)}`
        + `/parse=${item.parseFailure}`;
    })
    .join(";");
  return `${diagnostics.version} status=${diagnostics.status} request=${diagnostics.requestId} attempts=${attempts}`;
}

function stageFromLocation(url = window.location) {
  const pathname = url.pathname;
  const relative = pathname.startsWith(`${APP_PATH}/`)
    ? pathname.slice(APP_PATH.length + 1).replace(/\/+$/, "")
    : "";
  return ["participants", "debate"].includes(relative) ? relative : "question";
}

function sessionIdFromLocation() {
  return new URLSearchParams(window.location.search).get("session") || "";
}

const state = {
  stage: stageFromLocation(),
  readOnly: Boolean(new URLSearchParams(location.search).get("share")),
  archived: false,
  title: "",
  access: "owner",
  legacyLinkActive: false,
  sharingEnabled: false,
  autoSummaryPending: false,
  roundComplete: false,
  replyAbort: null,
  restoreSequence: 0,
  navigationVersion: 0,
  navigationPending: false,
  autoSummarySequence: 0,
  sessionId: sessionIdFromLocation(),
  summarySessionId: "",
  savedParticipantReplyCount: 0,
  summaryOpen: false,
  summaryLoading: false,
  summaryError: "",
  summaryText: "",
  summaryFeedback: null,
  summarySourceRevision: null,
  summaryMessageCount: 0,
  summaryDirty: false,
  summaryRequestSequence: 0,
  summaryContentVersion: 0,
  question: "",
  roles: defaultRoles(),
  selectedRoleId: "role-1",
  groupBuilt: false,
  customizeFirst: false,
  debateStarted: false,
  paused: false,
  waitingForUser: false,
  discussionStartPending: false,
  messages: [],
  pendingUserMessages: [],
  unsavedMessageIds: new Set(),
  streamingReply: null,
  composeDraft: "",
  draftAttachments: [],
  mentionOpen: false,
  mentionQuery: "",
  mentionRange: null,
  mentionActiveIndex: 0,
  nextRoleId: 4,
  saveTimer: null,
  saveQueue: Promise.resolve(),
  saveSequence: 0,
  sessionRevisions: new Map(),
  conflictedSessions: new Set(),
  saveStatus: "",
  saveFailed: false,
  restoreFailed: false,
  restoreMessage: "",
  roleEditVersion: 0,
  roleGenerationSequence: 0,
  activeRoleGenerationRequestId: null,
  roleGenerationPending: false,
  roleGenerationPhase: "",
  roleGenerationMessage: "",
  roleGenerationError: false,
  replySequence: 0,
  activeReplyRequestId: null,
  replyPending: false,
  replyMessage: "",
  replyError: false,
  replyMode: "",
  replyDiagnostics: null,
  replyRetryTarget: null,
  failedReplyBody: "",
  replyBlockedBySave: false,
  discussionLoopTask: null,
  discussionLoopVersion: 0,
  restartDiscussionAfterTask: false,
  priorityReply: null,
  pendingConversationStyle: "",
};

const viewPositions = createViewPositions();
let renderedViewKey = "";
function viewKey() {
  return state.sessionId ? `${workspace.profile()?.id || "viewer"}:${state.sessionId}:${state.stage}` : "";
}
function rememberViewPosition() {
  const list = appView.querySelector(".message-list");
  const roles = appView.querySelector(".role-list");
  viewPositions.save(renderedViewKey, {
    messageTop: list?.scrollTop || 0,
    followLatest: list ? isMessageListNearBottom(list) : true,
    roleTop: roles?.scrollTop || 0,
    roleLeft: roles?.scrollLeft || 0,
  });
}
function setNavigationBusy(busy) {
  state.navigationPending = busy;
  appView.inert = busy;
  stageNavigation.inert = busy;
  if (busy) appView.setAttribute("aria-busy", "true");
  else appView.removeAttribute("aria-busy");
}

function draftStorage() {
  try { return window.sessionStorage; } catch { return null; }
}
function draftKey(sessionId = state.sessionId) {
  return `agora-unsent:${BASE_PATH}:${sessionId}`;
}
function rememberComposeDraft() {
  if (state.sessionId) rememberDraft(draftStorage(), draftKey(), state.composeDraft, state.sessionRevisions.get(state.sessionId));
  rememberAttachments(draftStorage(), draftKey(state.sessionId || "new"), state.draftAttachments, state.sessionId ? state.sessionRevisions.get(state.sessionId) : 0);
}

function apiBase() {
  return runtime.readConfig().apiBase.replace(/\/$/, "");
}

function sessionApi(path = "") {
  return `${apiBase()}/debate/sessions${path}`;
}

function sessionSummaryApi(sessionId) {
  return sessionApi(`/${encodeURIComponent(sessionId)}/summary`);
}

function roleGenerationApi() {
  return `${apiBase()}/debate/roles/generate`;
}

function singleRoleGenerationApi() {
  return `${apiBase()}/debate/roles/generate-one`;
}

function recentGeneratedNames() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(RECENT_GENERATED_NAMES_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((name) => typeof name === "string" && name.trim() && name.trim().length <= 48)
      .map((name) => name.trim())
      .slice(0, MAX_RECENT_GENERATED_NAMES);
  } catch (_error) {
    return [];
  }
}

function rememberGeneratedNames(roles) {
  try {
    const names = [];
    const seen = new Set();
    for (const name of [...roles.map((role) => role.name), ...recentGeneratedNames()]) {
      if (typeof name !== "string" || !name.trim() || name.trim().length > 48) continue;
      const normalized = name.trim().toLocaleLowerCase();
      if (seen.has(normalized)) continue;
      names.push(name.trim());
      seen.add(normalized);
      if (names.length === MAX_RECENT_GENERATED_NAMES) break;
    }
    window.localStorage.setItem(RECENT_GENERATED_NAMES_KEY, JSON.stringify(names));
  } catch (_error) {
    // Browser storage is optional; it must never prevent role generation.
  }
}

function roleReplyStreamApi(sessionId) {
  return sessionApi(`/${encodeURIComponent(sessionId)}/reply`);
}

function jsonOptions(method, value, timeoutMs = 30_000) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
    timeoutMs,
  };
}

function normalizeGeneratedRoles(payload) {
  if (!payload || typeof payload !== "object" || !Array.isArray(payload.roles) || payload.roles.length !== 3) {
    throw new Error("The role response must contain three participants.");
  }
  const seenIds = new Set();
  const seenNames = new Set();
  const seenTypes = new Set();
  const colors = [];
  return payload.roles.map((role) => {
    if (!role || typeof role !== "object" || Array.isArray(role)) {
      throw new Error("The role response contains an invalid participant.");
    }
    const { id, name, roleType, prompt, avatarSeed } = role;
    const { identity } = role;
    if (typeof id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$/.test(id) || seenIds.has(id)) {
      throw new Error("The role response contains an invalid participant ID.");
    }
    if (typeof name !== "string" || !name.trim() || name.length > 48) {
      throw new Error("The role response contains an invalid participant name.");
    }
    if (typeof identity !== "string" || !identity.trim() || identity.length > 96) {
      throw new Error("The role response contains an invalid participant identity.");
    }
    if (typeof prompt !== "string" || !prompt.trim() || prompt.length > 1_000) {
      throw new Error("The role response contains an invalid participant prompt.");
    }
    if (!ROLE_TYPES.includes(roleType) || seenTypes.has(roleType)) {
      throw new Error("The role response contains an invalid participant type.");
    }
    if (!Number.isSafeInteger(avatarSeed) || avatarSeed < 0 || avatarSeed > MAX_AVATAR_SEED) {
      throw new Error("The role response contains an invalid avatar seed.");
    }
    const normalizedName = name.trim().toLocaleLowerCase();
    if (seenNames.has(normalizedName)) {
      throw new Error("The role response contains duplicate participant names.");
    }
    seenIds.add(id);
    seenNames.add(normalizedName);
    seenTypes.add(roleType);
    const avatarColor = randomAvatarColor(colors);
    colors.push(avatarColor);
    return {
      ...role,
      identity: identity.trim(),
      avatar: AVATARS[avatarSeed % AVATARS.length],
      avatarColor,
    };
  });
}

function roleGenerationErrorMessage(error) {
  if (error?.status === 401) return "Log in to create your agents.";
  if (/timed out|timeout/i.test(error?.message || "")) return "This is taking longer than usual. Try again.";
  if (error?.status === 503) return "AI agents are temporarily unavailable. Try again.";
  return "Couldn’t create the agents. Try again.";
}

function invalidateRoleGeneration(message = "The previous response will be ignored.") {
  state.roleGenerationSequence += 1;
  state.roleGenerationError = false;
  state.roleGenerationPhase = "";
  if (state.activeRoleGenerationRequestId !== null) {
    state.roleGenerationPending = true;
    state.roleGenerationMessage = message;
  } else {
    state.roleGenerationPending = false;
    state.roleGenerationMessage = "";
  }
}

function roleGenerationIsCurrent(requestId, question, roleEditVersion) {
  return state.activeRoleGenerationRequestId === requestId
    && state.roleGenerationSequence === requestId
    && state.roleGenerationPending
    && state.question === question
    && state.roleEditVersion === roleEditVersion;
}

function completeRoleGeneration(requestId) {
  if (state.activeRoleGenerationRequestId !== requestId) return false;
  state.activeRoleGenerationRequestId = null;
  state.roleGenerationPending = false;
  state.roleGenerationPhase = "";
  state.roleGenerationMessage = "";
  state.roleGenerationError = false;
  return true;
}

function nextRoleNumber(roles) {
  return Math.max(
    3,
    ...roles.map((role) => Number(/^role-(\d+)$/.exec(role.id)?.[1] || 0)),
  ) + 1;
}

function newChatMessageId() {
  if (typeof window.crypto?.randomUUID === "function") return `msg-${window.crypto.randomUUID()}`;
  return `msg-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function sessionSnapshot() {
  return {
    question: state.question,
    participants: state.roles.map((role) => ({ ...role })),
    messages: state.messages.map((message) => ({ ...message })),
    pendingUserMessages: state.pendingUserMessages.map((message) => ({ ...message })),
    draft: state.composeDraft,
    draftAttachments: state.draftAttachments.map(item => ({ ...item })),
    debateStarted: state.debateStarted,
    paused: state.paused,
  };
}

function isMockConversationMessage(message) {
  return message?.isMock === true
    || message?.is_mock === true
    || message?.mock === true
    || message?.source === "mock"
    || message?.origin === "mock";
}

function countSavedParticipantReplies(messages) {
  return Array.isArray(messages)
    ? messages.filter((message) => message?.kind === "participant" && !isMockConversationMessage(message)).length
    : 0;
}

function markSummaryDirty() {
  state.summaryContentVersion += 1;
  if (state.summaryText) state.summaryDirty = true;
}

function resetSummaryForSession(sessionId) {
  state.summaryRequestSequence += 1;
  state.summarySessionId = sessionId;
  state.savedParticipantReplyCount = 0;
  state.summaryOpen = false;
  state.summaryLoading = false;
  state.summaryError = "";
  state.summaryText = "";
  state.summaryFeedback = null;
  state.summarySourceRevision = null;
  state.summaryMessageCount = 0;
  state.summaryDirty = false;
  state.summaryContentVersion = 0;
}

function setSaveStatus(message, failed = false) {
  state.saveStatus = message;
  state.saveFailed = failed;
  updatePendingSaveLabels();
  const node = appView.querySelector("[data-save-status]");
  if (!failed) {
    node?.remove();
    return;
  }
  if (!node) {
    render();
    return;
  }
  node.innerHTML = saveStatusContent();
  node.setAttribute("role", "alert");
  node.classList.add("is-error");
}

function renderSaveStatus() {
  if (!state.saveFailed || !state.saveStatus) return "";
  return `<div class="save-status save-notice is-error" data-save-status role="alert">${saveStatusContent()}</div>`;
}
function saveStatusContent() {
  const conflict = state.conflictedSessions.has(state.sessionId);
  return `<div><p>${esc(state.saveStatus)}</p><span>${conflict ? "Copy any unsaved replies before reopening the conversation." : "Your unsent draft is kept on this device."}</span></div>${!conflict ? '<button class="dialog-text-button" type="button" data-action="retry-save">Try again</button>' : ""}`;
}

function markSessionConflict(sessionId) {
  state.conflictedSessions.add(sessionId);
  if (state.sessionId !== sessionId) return;
  if (state.saveTimer !== null) {
    window.clearTimeout(state.saveTimer);
    state.saveTimer = null;
  }
  setSaveStatus(
    "This conversation changed in another tab.",
    true,
  );
}

async function saveSessionNow() {
  const sessionId = state.sessionId;
  if (state.readOnly) return true;
  if (!sessionId || state.conflictedSessions.has(sessionId)) return false;
  if (state.saveTimer !== null) {
    window.clearTimeout(state.saveTimer);
    state.saveTimer = null;
  }
  const sequence = ++state.saveSequence;
  state.saveErrorStatus = 0;
  const ownerId = workspace.profile()?.id;
  const snapshot = sessionSnapshot();
  const snapshotMessageIds = new Set([
    ...snapshot.messages.map((message) => message.id).filter(Boolean),
    ...snapshot.pendingUserMessages.map((message) => message.id),
  ]);
  setSaveStatus("");
  const operation = state.saveQueue.then(async () => {
    if (!workspace.isSignedIn() || workspace.profile()?.id !== ownerId) return "cancelled";
    if (state.conflictedSessions.has(sessionId)) return "conflicted";
    const expectedRevision = state.sessionRevisions.get(sessionId);
    try {
      const result = await requestJson(
        sessionApi(`/${encodeURIComponent(sessionId)}`),
        jsonOptions("PUT", { ...snapshot, expectedRevision }),
      );
      if (!workspace.isSignedIn() || workspace.profile()?.id !== ownerId) return "cancelled";
      state.sessionRevisions.set(sessionId, result.session.revision);
      if (state.sessionId === sessionId && result.session.paused) state.paused = true;
      workspace.recordSession(result.session);
      acknowledgeDraft(draftStorage(), draftKey(sessionId), result.session.draft, result.session.revision);
      acknowledgeAttachments(draftStorage(), draftKey(sessionId), result.session.draftAttachments, result.session.revision);
      if (state.sessionId === sessionId) {
        state.savedParticipantReplyCount = countSavedParticipantReplies(result.session.messages);
      }
      for (const messageId of snapshotMessageIds) state.unsavedMessageIds.delete(messageId);
      return "saved";
    } catch (error) {
      state.saveErrorStatus = error?.status || 0;
      if (error?.status === 409) markSessionConflict(sessionId);
      throw error;
    }
  });
  state.saveQueue = operation.catch(() => {});
  try {
    const outcome = await operation;
    if (outcome === "conflicted") {
      markSessionConflict(sessionId);
      return false;
    }
    if (sequence === state.saveSequence && outcome === "saved") setSaveStatus("");
    updatePendingSaveLabels();
    return outcome === "saved";
  } catch (error) {
    if (state.sessionId !== sessionId || !workspace.isSignedIn() || workspace.profile()?.id !== ownerId) return false;
    if (error?.status === 409) {
      markSessionConflict(sessionId);
    } else if (sequence === state.saveSequence) {
      setSaveStatus("Changes haven’t been saved.", true);
    }
    return false;
  }
}

function updatePendingSaveLabels() {
  for (const node of appView.querySelectorAll("[data-message-save-id]")) {
    const messageId = node.dataset.messageSaveId;
    if (state.unsavedMessageIds.has(messageId)) {
      node.textContent = state.saveFailed ? "Not saved" : "Saving…";
    } else {
      node.remove();
    }
  }
}

function scheduleSessionSave(immediate = false) {
  if (state.readOnly || !state.sessionId || state.conflictedSessions.has(state.sessionId)) return;
  if (state.saveTimer !== null) {
    window.clearTimeout(state.saveTimer);
    state.saveTimer = null;
  }
  setSaveStatus("");
  if (immediate) return saveSessionNow();
  state.saveTimer = window.setTimeout(() => {
    state.saveTimer = null;
    void saveSessionNow();
  }, 350);
}

function currentRole() {
  return state.roles.find((role) => role.id === state.selectedRoleId) || state.roles[0];
}

function roleById(id) {
  return state.roles.find((role) => role.id === id) || state.roles[0];
}

function renderStepper() {
  if (state.restoreFailed) return `<div class="header-content"><span></span></div>`;
  const title = state.stage === "question" ? "" : state.title || state.question;
  return `<div class="header-content"><button class="icon-button mobile-menu" data-workspace="menu" aria-label="Open sidebar" aria-controls="workspace-sidebar" aria-expanded="${document.body.classList.contains("sidebar-open")}">${icon("menu")}</button>
    ${state.stage === "participants" ? `<button class="icon-button setup-back" data-action="go-stage" data-stage="question" aria-label="Edit question" ${state.replyPending ? "disabled" : ""}>${icon("back")}</button>` : ""}
    ${title ? `<h1 id="debate-title" class="workspace-title" title="${esc(title)}">${esc(title)}</h1>` : '<span class="workspace-title"></span>'}
    <div class="header-tools">${state.stage === "debate" && !state.readOnly ? `<button class="pause-button" type="button" data-action="toggle-pause" aria-pressed="${state.paused}">${icon(state.paused ? "play" : "pause")}<span>${state.paused ? "Resume" : "Pause"}</span></button>` : ""}
    ${state.stage === "participants" ? `<button class="button button-primary setup-start" type="button" data-action="${state.debateStarted ? "return-debate" : "start-debate"}" ${state.roles.length && state.question.trim() && !state.replyPending ? "" : "disabled"}>${state.debateStarted ? "Return to debate" : "Start debate"}</button>` : ""}
    ${state.stage === "debate" ? `<button class="icon-button mobile-panels" data-workspace="panels" aria-label="Show debate details" aria-controls="debate-details" aria-expanded="${document.body.classList.contains("details-open")}">${icon("panel")}</button>` : ""}</div></div>`;
}

function conversationStyle() {
  return state.pendingConversationStyle || /\n\n\[Conversation style: (\w+)\]/.exec(state.roles[0]?.prompt || "")?.[1] || "Balanced";
}
function renderDock() {
  const canEdit = !state.readOnly && state.groupBuilt && !state.replyPending && !state.roleGenerationPending;
  const profile = workspace.profile();
  return `<aside id="debate-details" class="details-dock" aria-label="Debate details">
    <div class="drawer-heading details-heading"><h2>Debate details</h2><button class="icon-button drawer-close" data-workspace="close-drawers" aria-label="Close debate details">${icon("close")}</button></div>
    <section class="dock-card room-card" aria-label="Sharing and participants">
      <div class="room-top"><span class="room-mode">${state.readOnly ? 'View only' : state.sharingEnabled ? 'Link sharing on' : 'Private debate'}</span>${!state.readOnly ? `<button class="share-button" data-workspace="share" ${state.sessionId ? "" : "disabled"}>${icon("share")}Share</button>` : ''}</div>
      ${state.readOnly ? '<p class="viewer-meta">Shared conversation · Updates live</p>' : `<div class="people-stack" aria-label="1 person: ${esc(profile.name)}"><span title="${esc(profile.name)}">${avatar(profile)}</span></div>`}
    </section>
    <section class="dock-card agents-card" aria-labelledby="agents-title">
      <div class="card-heading"><h2 id="agents-title">Agents <span class="count-badge">${state.roles.length}</span></h2></div><p class="agent-transparency" title="These are simulated perspectives using a shared AI model. Agreement between agents is not independent verification.">AI perspectives · shared model</p>
      <ul class="dock-agents">${state.roles.map((role, index) => `<li>${state.readOnly ? '<div class="dock-agent-row viewer-agent">' : `<button class="dock-agent-row" data-action="edit-agents" data-role-id="${esc(role.id)}" aria-label="Edit ${esc(agentName(role))}" ${canEdit ? "" : "disabled"}>`}${avatar(role, index)}<span class="dock-agent-copy"><strong data-role-name="${esc(role.id)}">${esc(agentName(role))}</strong><span data-role-identity="${esc(role.id)}">${esc(roleIdentityLabel(role))}</span></span>${state.readOnly ? '</div>' : `${icon("more")}</button>`}</li>`).join("")}</ul>
      ${!state.readOnly ? `<button class="add-agent" data-action="dock-add-role" ${canEdit && state.roles.length < MAX_PARTICIPANTS ? "" : "disabled"}>${icon("plus")}Add agent</button>` : ''}
      <div class="style-control"><label for="conversation-style">Conversation style</label><div class="select-shell"><select id="conversation-style" data-conversation-style ${state.groupBuilt && !state.readOnly ? "" : "disabled"}>${Object.keys(CONVERSATION_STYLES).map(style => `<option ${conversationStyle() === style ? "selected" : ""}>${style}</option>`).join("")}</select>${icon("right")}</div></div>
    </section>
    <section class="dock-card resources-card" aria-label="Conversation resources">
      <button data-action="summary" ${summaryActionDisabled() ? "disabled" : ""} title="${esc(summaryWaitMessage() || "Summarize this debate")}">${icon("document")}<span>${state.summaryLoading ? "Summarizing…" : "Summary"}</span>${icon("right")}</button>
      <button data-workspace="sources">${icon("link")}<span>Sources <span class="count-badge">${workspace.sources().length}</span></span>${icon("right")}</button>
    </section>
  </aside>`;
}

function renderQuestion() {
  const status = state.roleGenerationPending || state.roleGenerationError ? state.roleGenerationMessage : "";
  return `<section class="screen question-screen" aria-labelledby="question-title">
    <form class="question-form" id="question-form" data-form="question" aria-busy="${state.roleGenerationPending}">
      <h1 id="question-title" class="screen-intro">Think it through.</h1>
      <p class="question-intro">A question worth seeing from more than one side.</p>
      <label class="sr-only" for="question-input">Your question</label>
      <div class="question-input-shell">
        ${workspace.renderAttachments(state.draftAttachments, true)}
        <textarea id="question-input" name="question" data-field="question" maxlength="2000" required placeholder="A decision, an idea, a question…" ${state.roleGenerationPhase === "saving" ? "disabled" : ""}>${esc(state.question)}</textarea>
        <div class="question-composer-footer"><div class="composer-tools"><button class="icon-button" type="button" data-attachment-action="images" aria-label="Upload images (up to 6)">${icon("plus")}</button><button class="icon-button" type="button" data-attachment-action="files" aria-label="Upload files">${icon("paperclip")}</button><button class="icon-button customize-agents-button${state.customizeFirst ? " is-active" : ""}" type="button" data-action="customize-first" aria-label="Customize agents" title="${state.customizeFirst ? "Customize agents: on" : "Customize agents: off"}" aria-pressed="${state.customizeFirst}" ${state.roleGenerationPending ? "disabled" : ""}>${icon("agent-customize")}</button>${workspace.renderFolderChip()}</div><button class="button button-primary build-group-button" type="submit" aria-label="Start conversation" ${state.question.trim() && !state.roleGenerationPending && !workspace.uploading() ? "" : "disabled"}>
          ${state.roleGenerationPending
            ? '<span class="button-loading-spinner" aria-hidden="true"></span>'
            : icon("send")}
        </button></div>
      </div>
      ${status ? `<p class="role-generation-status${state.roleGenerationError ? " is-error" : ""}" data-role-generation-status role="${state.roleGenerationError ? "alert" : "status"}">${esc(status)}</p>` : ""}
    </form>
    ${workspace.renderSuggestions()}
  </section>`;
}

function renderRoleList() {
  return state.roles.map((role, index) => {
    const selected = role.id === state.selectedRoleId;
    return `<li><button class="role-choice${selected ? " is-selected" : ""}" type="button" data-action="select-role" data-role-id="${esc(role.id)}" aria-pressed="${selected}" ${state.replyPending ? "disabled" : ""}>
      ${avatar(role, index)}
      <span class="role-choice-copy"><strong data-role-name="${esc(role.id)}">${esc(agentName(role))}</strong><span data-role-identity="${esc(role.id)}">${esc(roleIdentityLabel(role))}</span></span>
    </button></li>`;
  }).join("");
}


function renderRoleEditor(role) {
  return `<section class="role-editor" aria-label="Edit selected participant">
    <div class="editor-heading"><div class="editor-identity">${avatar(role, state.roles.indexOf(role))}<h2 data-selected-role-name>${esc(role.name || "Unnamed participant")}</h2></div><button class="icon-button remove-agent-button" type="button" data-action="remove-role" aria-label="Remove agent" title="Remove agent" ${state.roles.length <= 1 || state.replyPending ? "disabled" : ""}>${icon("trash")}</button></div>
    <div class="field"><label for="role-name">Name</label><input id="role-name" type="text" data-role-field="name" maxlength="48" value="${esc(role.name)}" placeholder="Participant name" ${state.replyPending ? "disabled" : ""}></div>
    <div class="field"><label for="role-identity">Perspective</label><input id="role-identity" type="text" data-role-field="identity" maxlength="96" value="${esc(role.identity || "")}" placeholder="e.g. Optimist, economist, product designer" ${state.replyPending ? "disabled" : ""}></div>
    ${avatarColorPicker(role, state.roles.indexOf(role), { disabled: state.replyPending })}
    <div class="field role-prompt-field"><label for="role-prompt">Personality & instructions</label><textarea id="role-prompt" data-role-field="prompt" rows="6" maxlength="1000" placeholder="How should this participant think and contribute?" ${state.replyPending ? "disabled" : ""}>${esc(role.prompt)}</textarea></div>
  </section>`;
}

function renderReplyStatus() {
  if (!state.replyError || !state.replyMessage) {
    return "";
  }
  const retryButton = state.replyRetryTarget
    ? `<button class="dialog-text-button" type="button" data-action="retry-role-reply" ${state.replyPending ? "disabled" : ""}>Try again</button>`
    : "";
  return `<div class="reply-notice" data-reply-status role="alert">${state.failedReplyBody ? `<p class="reply-partial">${esc(state.failedReplyBody)}</p>` : ""}<p>${esc(state.replyMessage)}</p>${retryButton}</div>`;
}

function renderParticipants() {
  const role = currentRole();
  return `<section class="screen participants-screen" aria-label="Participants">
    ${renderReplyStatus()}
    <div class="participants-layout">
      <aside class="participant-rail" aria-label="Participants">
        <div class="rail-heading"><h2>Agents <span class="count-badge">${state.roles.length}</span></h2><button class="icon-button add-button" type="button" data-action="add-role" aria-label="Add agent" ${state.roles.length >= MAX_PARTICIPANTS ? "disabled title=\"Maximum of 16 participants\"" : state.replyPending ? "disabled" : ""}>${icon("plus")}</button></div>
        <ul class="role-list">${renderRoleList()}</ul>
      </aside>
      ${renderRoleEditor(role)}
    </div>
  </section>`;
}

function renderMessage(message) {
  const actions = `<div class="message-actions"><button class="icon-button" data-workspace="copy-message" data-index="${state.messages.indexOf(message)}" aria-label="Copy text" title="Copy text">${icon("copy")}</button></div>`;
  if (message.kind === "user") {
    const saveStatus = message.id && state.unsavedMessageIds.has(message.id)
      ? `<span class="message-save-status" data-message-save-id="${esc(message.id)}">${state.saveFailed ? "Not saved" : "Saving…"}</span>`
      : "";
    return `<li class="message message-user"><div class="user-bubble"><div class="user-meta"><span class="message-author">${state.readOnly ? 'Host' : 'You'}</span>${state.readOnly ? avatar({ name: 'Host' }) : avatar(workspace.profile())}</div>${workspace.renderAttachments(message.attachments)}${message.body ? `<p>${esc(message.body)}</p>` : ""}${saveStatus}${message.body ? actions : ""}</div></li>`;
  }
  const role = state.roles.find(item => item.id === message.roleId) || { name: "Former agent" };
  return `<li class="message message-participant">${avatar(role, state.roles.indexOf(role))}<div class="message-copy"><div class="message-meta"><strong>${esc(agentName(role))}</strong></div><p>${renderCitedText(message.body, message.citations)}</p>${actions}</div></li>`;
}

function renderStreamingReply() {
  if (!state.streamingReply) return "";
  const role = state.roles.find(item => item.id === state.streamingReply.roleId);
  if (!role) return '<li class="reply-preparing" role="status">Preparing a reply…</li>';
  return `<li class="message message-participant is-streaming" data-streaming-reply>
    ${avatar(role, state.roles.indexOf(role))}
    <div class="message-copy"><div class="message-meta"><strong data-streaming-name>${esc(agentName(role))}</strong><span class="sr-only" data-streaming-identity>${esc(roleIdentityLabel(role))}</span><span class="agent-activity" data-agent-activity role="status">${esc(({searching: 'Searching the web…', reading: 'Reviewing sources…', unavailable: 'Couldn’t check online'})[state.streamingReply.phase] || '')}</span></div><p data-streaming-body aria-live="off">${renderCitedText(state.streamingReply.citations || state.replyMode !== 'web_tool_text_v1' ? state.streamingReply.body : streamingText(state.streamingReply.body), state.streamingReply.citations)}</p></div>
  </li>`;
}

function renderPendingChatMessage(message) {
  const saveStatus = state.unsavedMessageIds.has(message.id)
    ? `<span class="message-save-status" data-message-save-id="${esc(message.id)}">${state.saveFailed ? "Not saved" : "Saving…"}</span>`
    : "";
  return `<li class="message message-user"><div class="user-bubble"><div class="user-meta"><span class="message-author">${state.readOnly ? 'Host' : 'You'}</span>${state.readOnly ? avatar({ name: 'Host' }) : avatar(workspace.profile())}</div>${workspace.renderAttachments(message.attachments)}${message.body ? `<p>${esc(message.body)}</p>` : ""}${saveStatus}</div></li>`;
}

function matchingMentionRoles() {
  const query = state.mentionQuery.toLocaleLowerCase();
  return state.roles.filter(role => role.name.toLocaleLowerCase().startsWith(query));
}
function renderMentionMenu() {
  const roles = matchingMentionRoles();
  return `<div class="mention-menu" id="mention-menu" role="listbox" aria-label="Mention an agent" ${state.mentionOpen ? "" : "hidden"}>${roles.map((role, index) => `<button id="mention-option-${index}" type="button" role="option" aria-selected="${index === state.mentionActiveIndex}" data-action="mention-role" data-role-id="${esc(role.id)}">${avatar(role, state.roles.indexOf(role))}<span>${esc(agentName(role))}</span></button>`).join("") || '<p class="mention-empty">No matching agents</p>'}</div>`;
}
function syncMentionMenu() {
  const menu = appView.querySelector("#mention-menu");
  if (!menu) return;
  const fresh = document.createElement("div");
  fresh.innerHTML = renderMentionMenu();
  const next = fresh.firstElementChild;
  // Keep the popup itself mounted while filtering so its position and motion stay stable.
  const scrollTop = menu.scrollTop;
  menu.innerHTML = next.innerHTML;
  menu.hidden = next.hidden;
  menu.scrollTop = scrollTop;
  const input = appView.querySelector("[data-chat-input]");
  input?.setAttribute("aria-expanded", String(state.mentionOpen));
  if (state.mentionOpen && matchingMentionRoles().length) input?.setAttribute("aria-activedescendant", `mention-option-${state.mentionActiveIndex}`);
  else input?.removeAttribute("aria-activedescendant");
  appView.querySelector('[data-action="toggle-mentions"]')?.setAttribute("aria-expanded", String(state.mentionOpen));
}
function resizeComposer() {
  const input = appView.querySelector("[data-chat-input]");
  if (!input) return;
  input.style.height = "0px";
  input.style.height = `${Math.min(160, Math.max(44, input.scrollHeight))}px`;
}
function resizeQuestionInput() {
  const input = appView.querySelector("[data-field='question']");
  if (!input) return;
  input.style.height = "0px";
  input.style.height = `${Math.min(156, Math.max(52, input.scrollHeight))}px`;
}

function renderDebate() {
  return `<section class="screen debate-screen" aria-labelledby="debate-title">
    <ol class="message-list" aria-label="Conversation messages" aria-live="polite" aria-relevant="additions">${state.messages.map(renderMessage).join("")}${renderStreamingReply()}${state.replyError ? `<li class="message reply-failed">${renderReplyStatus()}</li>` : ""}${state.pendingUserMessages.map(renderPendingChatMessage).join("")}${!state.messages.length && !state.streamingReply && !state.replyError ? '<li class="conversation-empty">Your agents are ready to begin.</li>' : ""}</ol>
    ${state.readOnly ? `<div class="viewer-footer"><span>${state.access === 'legacy' ? 'Older conversation · read only' : 'View-only conversation'}</span>${state.access === 'legacy' ? '<button class="button button-primary" data-workspace="copy-legacy">Save a private copy</button>' : `<a class="button button-primary" href="${APP_PATH}">Start your own</a>`}</div>` : `<div class="composer-shell">${renderMentionMenu()}
    <form class="composer-form" data-form="chat" aria-busy="${state.replyPending}">
      ${workspace.renderAttachments(state.draftAttachments, true)}
      <label class="sr-only" for="chat-input">Your message</label>
      <textarea id="chat-input" data-chat-input rows="3" maxlength="2000" placeholder="Add your perspective…" aria-autocomplete="list" aria-controls="mention-menu" aria-expanded="${state.mentionOpen}">${esc(state.composeDraft)}</textarea>
      <div class="composer-footer"><div class="composer-tools"><button class="icon-button" type="button" data-attachment-action="images" aria-label="Upload images (up to 6)">${icon("plus")}</button><button class="icon-button" type="button" data-attachment-action="files" aria-label="Upload files">${icon("paperclip")}</button><div class="mention-control"><button class="icon-button mention-button" type="button" data-action="toggle-mentions" aria-label="Mention an agent" aria-expanded="${state.mentionOpen}" aria-controls="mention-menu">${icon("at")}</button></div></div><div class="composer-send"><button class="send-button" type="submit" aria-label="Send message" ${(state.composeDraft.trim() || state.draftAttachments.length) && !workspace.uploading() ? "" : "disabled"}>${icon("send")}</button></div></div>
    </form></div>`}
  </section>`;
}

function renderSummaryDialog() {
  if (!summaryDialog) return;
  if (!state.summaryOpen) {
    if (summaryDialog.open) summaryDialog.close();
    summaryDialog.innerHTML = "";
    return;
  }
  const updateLabel = state.summaryLoading
    ? "Updating…"
    : state.summaryError ? "Try again" : state.summaryText ? "Update summary" : "Generate summary";
  const summaryDisabled = summaryActionDisabled();
  const participants = state.roles.filter(role => state.messages.some(message => message.kind === "participant" && message.roleId === role.id));
  summaryDialog.innerHTML = `<div class="summary-dialog-content">
    <header class="summary-dialog-header"><h2 id="summary-dialog-title">Summary</h2><button class="icon-button" type="button" data-action="close-summary" aria-label="Close summary">${icon("close")}</button></header>
    <div class="summary-context"><h3 title="${esc(state.question)}">${esc(state.question)}</h3><p><span>From this conversation</span><span class="summary-people" aria-label="${esc(participants.map(role => agentName(role)).join(", "))}">${participants.slice(0, 4).map(role => avatar(role)).join("")}</span></p></div>
    ${state.summaryDirty ? `<p class="summary-stale" role="status">New replies added. Update when ready.</p>` : ""}
    ${state.summaryError ? `<div class="summary-error" role="alert"><p>${esc(state.summaryError)}</p>${state.summaryText ? '<p>Your last summary is still here.</p>' : ""}</div>` : ""}
    ${state.summaryLoading && !state.summaryText ? `<p class="summary-loading" role="status">Generating summary…</p>` : ""}
    ${state.summaryText ? `<div class="summary-copy">${renderSummaryCopy(state.summaryText)}</div>` : ""}
    ${!state.summaryText && !state.summaryLoading && !state.summaryError ? `<p class="summary-empty">See the key trade-off, what the evidence supports, and a useful next step.</p>` : ""}
    <footer class="summary-dialog-actions"><button class="dialog-text-button" type="button" data-action="copy-summary" ${state.summaryText ? "" : "disabled"}>${icon("copy")}<span>Copy</span></button>${state.summaryText && !state.readOnly ? `<div class="summary-feedback" data-summary-feedback>${state.summaryFeedback !== null ? '<span>Feedback saved. Thank you.</span>' : '<span>Useful?</span><button type="button" data-action="summary-feedback" data-helpful="true">Yes</button><button type="button" data-action="summary-feedback" data-helpful="false">Not yet</button>'}</div>` : ''}<button class="dialog-primary" type="button" data-action="regenerate-summary" ${summaryDisabled || state.readOnly ? "disabled" : ""}>${state.readOnly ? 'View only' : updateLabel}</button></footer>
  </div>`;
  if (!summaryDialog.open) summaryDialog.showModal();
}

async function copySummary(button) {
  if (!state.summaryText) return;
  try {
    await navigator.clipboard.writeText(state.summaryText);
    if (button.isConnected) button.innerHTML = `${icon("check")}<span>Copied</span>`;
  } catch { workspace.notify("Select the summary text to copy it."); }
}

function summaryWaitMessage() {
  if (!summaryDialog) return "Summary isn’t available yet. Try again.";
  if (!state.sessionId) return "Save this conversation before creating a summary.";
  if (state.readOnly && !state.summaryText) return "The host hasn’t saved a summary yet.";
  if (state.savedParticipantReplyCount < 3) {
    return `Available after 3 AI replies.`;
  }
  return "";
}

function summaryActionDisabled() {
  return !summaryDialog
    || !state.sessionId
    || state.savedParticipantReplyCount < 3
    || (state.readOnly && !state.summaryText)
    || state.summaryLoading;
}

function summaryRequestIsCurrent(requestId, sessionId) {
  return state.summaryRequestSequence === requestId
    && state.sessionId === sessionId
    && state.summarySessionId === sessionId;
}

function closeSummaryDialog() {
  state.summaryOpen = false;
  render();
  appView.querySelector('[data-action="summary"]')?.focus();
}

async function requestSummary(regenerate = false) {
  if (state.readOnly) { state.summaryOpen = Boolean(state.summaryText); render(); return Boolean(state.summaryText); }
  const navigation = state.navigationVersion;
  if (!await workspace.ensureSignedIn()) return false;
  if (navigation !== state.navigationVersion || state.navigationPending) return false;
  if (summaryActionDisabled()) return false;
  if (state.summaryText && !state.summaryDirty && !regenerate) {
    state.summaryOpen = true;
    state.summaryError = "";
    render();
    summaryDialog?.querySelector('[data-action="close-summary"]')?.focus();
    return true;
  }
  const sessionId = state.sessionId;
  const requestId = ++state.summaryRequestSequence;
  const contentVersion = state.summaryContentVersion;
  state.summaryOpen = true;
  state.summaryLoading = true;
  state.summaryError = "";
  render();
  summaryDialog?.querySelector('[data-action="close-summary"]')?.focus();

  try {
    const hasPendingSave = state.saveTimer !== null || state.unsavedMessageIds.size > 0 || state.saveFailed;
    if (hasPendingSave) {
      if (!await saveSessionNow()) {
        throw new Error(state.saveStatus || "The current conversation could not be saved. Please retry.");
      }
    } else {
      await state.saveQueue;
      if (state.sessionId !== sessionId || state.conflictedSessions.has(sessionId) || state.saveFailed) {
        throw new Error(state.saveStatus || "The current conversation could not be saved. Please retry.");
      }
    }
    if (!summaryRequestIsCurrent(requestId, sessionId)) return false;
    const result = await requestJson(
      sessionSummaryApi(sessionId),
      jsonOptions("POST", {}, ROLE_REPLY_TIMEOUT_MS),
    );
    if (!summaryRequestIsCurrent(requestId, sessionId)) return false;
    state.summaryText = result.summary;
    state.summarySourceRevision = result.sourceRevision;
    state.summaryMessageCount = result.summarizedMessageCount;
    state.summaryDirty = state.summaryContentVersion !== contentVersion || Boolean(result.isStale);
    return true;
  } catch (error) {
    if (summaryRequestIsCurrent(requestId, sessionId)) {
      state.summaryError = state.summaryText ? "Couldn’t update the summary." : "Couldn’t create the summary. Try again.";
    }
    return false;
  } finally {
    if (summaryRequestIsCurrent(requestId, sessionId)) {
      state.summaryLoading = false;
      render();
      summaryDialog?.querySelector('[data-action="close-summary"]')?.focus();
    }
  }
}

function isMessageListNearBottom(list) {
  return !list
    || list.scrollHeight - list.scrollTop - list.clientHeight < MESSAGE_LIST_BOTTOM_THRESHOLD_PX;
}

function render() {
  workspace.renderSidebar();
  document.body.dataset.debateStage = state.stage;
  if (state.restoreFailed) {
    renderedViewKey = "";
    stageNavigation.hidden = true;
    stageNavigation.innerHTML = "";
    appView.innerHTML = `<section class="screen restore-error" aria-labelledby="restore-error-title"><div class="restore-error-content"><h1 id="restore-error-title">This conversation won’t open.</h1><p class="restore-error-description" role="alert">${esc(state.restoreMessage)}</p><div class="page-actions"><button class="button button-primary" type="button" data-action="retry-restore">Try again</button></div></div></section>`;
    renderSummaryDialog();
    return;
  }
  const screen = state.stage === "participants" ? renderParticipants()
    : state.stage === "debate" ? renderDebate()
      : renderQuestion();
  const previousMessageList = appView.querySelector(".message-list");
  const nextViewKey = viewKey();
  const changedView = nextViewKey !== renderedViewKey;
  const savedPosition = changedView ? viewPositions.get(nextViewKey) : null;
  const previousMessageScroll = previousMessageList?.scrollTop ?? 0;
  const shouldFollowLatest = !previousMessageList || isMessageListNearBottom(previousMessageList);
  const previousRoleList = appView.querySelector(".role-list");
  const previousRoleScroll = {
    top: previousRoleList?.scrollTop ?? 0,
    left: previousRoleList?.scrollLeft ?? 0,
    selectedId: previousRoleList?.querySelector(".is-selected")?.dataset.roleId,
    count: previousRoleList?.children.length,
  };
  const previousChatInput = appView.querySelector("[data-chat-input]");
  const preserveChatInputFocus = document.activeElement === previousChatInput;
  const previousSelection = preserveChatInputFocus
    ? {
      start: previousChatInput.selectionStart,
      end: previousChatInput.selectionEnd,
      direction: previousChatInput.selectionDirection,
    }
    : null;
  stageNavigation.hidden = false;
  stageNavigation.innerHTML = renderStepper();
  appView.innerHTML = `<div class="workspace-layout${state.stage === "debate" ? "" : " is-setup"}"><div class="main-column">${renderSaveStatus()}${screen}</div>${state.stage === "debate" ? renderDock() : ""}</div>`;
  const roleList = appView.querySelector(".role-list");
  if (roleList) {
    roleList.scrollTop = changedView ? savedPosition?.roleTop || 0 : previousRoleScroll.top;
    roleList.scrollLeft = changedView ? savedPosition?.roleLeft || 0 : previousRoleScroll.left;
    if ((!changedView || !savedPosition) && (previousRoleScroll.selectedId !== state.selectedRoleId || previousRoleScroll.count !== state.roles.length)) {
      const selected = roleList.querySelector(".is-selected");
      if (selected) {
        const listBounds = roleList.getBoundingClientRect();
        const selectedBounds = selected.getBoundingClientRect();
        if (selectedBounds.top < listBounds.top) roleList.scrollTop -= listBounds.top - selectedBounds.top;
        else if (selectedBounds.bottom > listBounds.bottom) roleList.scrollTop += selectedBounds.bottom - listBounds.bottom;
        if (selectedBounds.left < listBounds.left) roleList.scrollLeft -= listBounds.left - selectedBounds.left;
        else if (selectedBounds.right > listBounds.right) roleList.scrollLeft += selectedBounds.right - listBounds.right;
      }
    }
  }
  resizeComposer();
  resizeQuestionInput();
  for (const button of appView.querySelectorAll("button[aria-label]")) button.title = button.getAttribute("aria-label");
  const messageList = appView.querySelector(".message-list");
  if (messageList) {
    const follow = changedView ? savedPosition?.followLatest !== false : shouldFollowLatest;
    messageList.scrollTop = follow ? messageList.scrollHeight
      : Math.min(changedView ? savedPosition.messageTop : previousMessageScroll, messageList.scrollHeight);
  }
  renderedViewKey = nextViewKey;
  if (previousSelection) {
    const chatInput = appView.querySelector("[data-chat-input]");
    if (chatInput) {
      chatInput.focus({ preventScroll: true });
      chatInput.setSelectionRange(
        previousSelection.start,
        previousSelection.end,
        previousSelection.direction,
      );
    }
  }
  renderSummaryDialog();
  const titles = { question: "Question", participants: "Participants", debate: "Debate" };
  const skip = document.querySelector('.skip-link');
  if (skip) skip.href = `${location.pathname}${location.search}#app-view`;
  document.title = `${titles[state.stage]} · Debate Agora`;
  appNavigation.sync();
}

function syncRoleGenerationControls() {
  if (state.stage !== "question") return;
  const form = appView.querySelector("[data-form='question']");
  if (!form) return;
  form.setAttribute("aria-busy", String(state.roleGenerationPending));
  const questionInput = form.querySelector("[data-field='question']");
  if (questionInput) questionInput.disabled = state.roleGenerationPhase === "saving";
  const submit = form.querySelector("button[type='submit']");
  if (submit) {
    const buttonLabel = state.roleGenerationPending ? "Finding perspectives" : "Start conversation";
    submit.disabled = state.roleGenerationPending || !state.question.trim() || workspace.uploading();
    submit.setAttribute("aria-label", buttonLabel);
  }
  const message = state.roleGenerationPending || state.roleGenerationError ? state.roleGenerationMessage : "";
  let status = form.querySelector("[data-role-generation-status]");
  if (!message) {
    status?.remove();
    return;
  }
  if (!status) {
    status = document.createElement("p");
    status.className = "role-generation-status";
    status.dataset.roleGenerationStatus = "";
    form.append(status);
  }
  status.textContent = message;
  status.setAttribute("role", state.roleGenerationError ? "alert" : "status");
  status.classList.toggle("is-error", state.roleGenerationError);
}

function stageUrl(stage) {
  const path = stage === "question" ? APP_PATH : `${APP_PATH}/${stage}`;
  const query = new URLSearchParams();
  if (state.sessionId) query.set("session", state.sessionId);
  const share = new URLSearchParams(location.search).get("share");
  if (share) query.set("share", share);
  const search = query.toString();
  return search ? `${path}?${search}` : path;
}

async function setStage(stage, updateHistory = true, roleGenerationRequestId = null) {
  const navigation = state.navigationVersion;
  const allowed = stage === "question"
    || (stage === "participants" && state.groupBuilt && Boolean(state.question.trim()))
    || (stage === "debate" && state.debateStarted);
  if (!allowed) return;
  if (state.replyPending && stage !== state.stage) return false;
  if (
    stage !== state.stage
    && state.activeRoleGenerationRequestId !== null
    && state.activeRoleGenerationRequestId !== roleGenerationRequestId
  ) {
    invalidateRoleGeneration("Navigation changed; the previous response will be ignored.");
  }
  if (state.sessionId && !await saveSessionNow()) {
    if (roleGenerationRequestId !== null) completeRoleGeneration(roleGenerationRequestId);
    render();
    return false;
  }
  if (navigation !== state.navigationVersion || state.navigationPending) return false;
  if (
    roleGenerationRequestId !== null
    && (state.roleGenerationSequence !== roleGenerationRequestId
      || state.activeRoleGenerationRequestId !== roleGenerationRequestId)
  ) {
    completeRoleGeneration(roleGenerationRequestId);
    return false;
  }
  if (roleGenerationRequestId !== null) completeRoleGeneration(roleGenerationRequestId);
  state.stage = stage;
  if (updateHistory) window.history.pushState({ debateStage: stage }, "", stageUrl(stage));
  render();
  if (stage === "debate" && state.debateStarted && !state.paused) {
    void ensureDiscussionLoop(nextReplyForSavedConversation(), true);
  }
  return true;
}

function roleForChatMessage(message) {
  return mentionedRole(message, state.roles);
}

function updateComposeDraft(value) {
  if (state.replyMessage && value.trim() !== state.composeDraft.trim()) {
    state.replyRetryTarget = null;
    state.replyMessage = "";
    state.replyError = false;
    state.replyDiagnostics = null;
    appView.querySelector("[data-reply-status]")?.remove();
  }
  state.composeDraft = value;
  rememberComposeDraft();
}

function roleReplyIsCurrent(requestId, sessionId, roleEditVersion) {
  return state.activeReplyRequestId === requestId
    && state.replySequence === requestId
    && state.replyPending
    && state.sessionId === sessionId
    && state.roleEditVersion === roleEditVersion;
}

async function toggleDiscussionPause() {
  if (!state.paused) {
    state.paused = true;
    state.discussionLoopVersion += 1;
    state.restartDiscussionAfterTask = false;
    render();
    if (!await saveSessionNow()) {
      workspace.notify("Paused in this tab. Couldn’t save the change yet.");
    }
    return;
  }

  if (!await workspace.ensureReplyAvailable()) return;
  const shouldContinue = state.pendingUserMessages.length > 0 || !state.replyError;
  state.roundComplete = false;
  state.paused = false;
  render();
  if (!await saveSessionNow()) {
    state.restartDiscussionAfterTask = false;
    showReplyError("Couldn’t save the conversation. Try again.", { roleId: null, initial: false });
    return;
  }
  if (!shouldContinue) return;
  const pendingReply = state.pendingUserMessages.length
    ? null
    : state.priorityReply || (state.replyPending ? null : nextReplyForSavedConversation());
  void ensureDiscussionLoop(pendingReply, true);
}

function roleReplyErrorMessage(error) {
  if (error?.status === 401) return "Log in to continue the conversation.";
  if (error?.status === 409 && /another tab/i.test(error.message || '')) return "This debate is running in another tab. Continue there, or try again when its current reply finishes.";
  if (error?.status === 402) return "You’re out of AI replies. Open Usage to invite a friend or request more.";
  if (error?.status === 404) return "This conversation or agent is no longer available.";
  if (state.failedReplyBody) return "This reply stopped early.";
  const role = state.roles.find(item => item.id === state.streamingReply?.roleId);
  return role ? `${role.name.replace(/\.ai$/i, "")} couldn’t reply.` : "Couldn’t get a reply.";
}

function showReplyError(message, retryTarget = null, diagnostics = null) {
  state.replyError = true;
  state.replyMessage = message;
  state.replyDiagnostics = normalizeReplyDiagnostics(diagnostics);
  state.replyRetryTarget = retryTarget;
  render();
}

async function requestRoleReply(roleId, initial = false, clearDraft = false) {
  if (state.readOnly) return false;
  if (state.replyPending) return false;
  if (!await workspace.ensureReplyAvailable()) {
    if (state.sessionId && !state.readOnly && workspace.isSignedIn()) {
      state.paused = true; state.discussionLoopVersion += 1;
      state.restartDiscussionAfterTask = false; render(); scheduleSessionSave();
    }
    return false;
  }
  if (state.replyPending) return false;
  if (state.paused) {
    showReplyError("Resume the conversation before requesting a participant reply.", { roleId, initial });
    return false;
  }
  const role = roleId ? state.roles.find((candidate) => candidate.id === roleId) : null;
  if (roleId && !role) {
    showReplyError("Choose an available participant before requesting a reply.");
    return false;
  }
  if (state.messages.length >= MAX_CHAT_MESSAGES) {
    showReplyError("This conversation is full. Start a new debate to keep going.");
    return false;
  }
  const sessionId = state.sessionId;
  if (!sessionId) {
    showReplyError("Couldn’t save this conversation. Try again.");
    return false;
  }

  const requestId = ++state.replySequence;
  const roleEditVersion = state.roleEditVersion;
  const shouldEnterDebate = state.stage !== "debate";
  const draftAtRequest = state.composeDraft;
  state.activeReplyRequestId = requestId;
  state.replyPending = true;
  state.replyError = false;
  state.replyMessage = "";
  state.replyMode = "";
  state.replyDiagnostics = null;
  state.failedReplyBody = "";
  state.replyBlockedBySave = false;
  state.streamingReply = { roleId: roleId || "", body: "" };
  state.replyRetryTarget = null;
  render();

  try {
    if (!await saveSessionNow()) {
      if (roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) {
        state.replyBlockedBySave = true;
        showReplyError("Couldn’t save the conversation. Try again.", { roleId, initial, clearDraft });
      }
      return false;
    }
    if (!roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) return false;
    if (state.paused) return false;

    if (shouldEnterDebate) {
      state.stage = "debate";
      window.history.pushState({ debateStage: "debate" }, "", stageUrl("debate"));
    }
    state.streamingReply = { roleId: roleId || "", body: "" };
    render();

    let streamedSpeakerId = "";
    let streamedReplyMode = "";
    let streamComplete = false;
    let roundComplete = false;
    let remaining = null;
    let waitingForUser = null;
    let committedMessage = null;
    state.replyAbort = new AbortController();
    await requestEventStream(
      roleReplyStreamApi(sessionId),
      { ...jsonOptions("POST", roleId ? { participantId: roleId } : {}, ROLE_REPLY_TIMEOUT_MS), signal: state.replyAbort.signal },
      async (event) => {
        if (!roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) {
          throw new Error("The participant reply is no longer current.");
        }
        if (event.type === "start") {
          if (!state.roles.some((candidate) => candidate.id === event.speakerId)
            || (roleId && event.speakerId !== roleId)
            || typeof event.replyMode !== "string"
            || !/^[a-z0-9_]{1,64}$/.test(event.replyMode)) {
            throw new Error("The server returned an invalid participant reply.");
          }
          streamedSpeakerId = event.speakerId;
          streamedReplyMode = event.replyMode;
          state.replyMode = event.replyMode;
          state.streamingReply.roleId = event.speakerId;
          render();
          return;
        }
        if (event.type === "delta") {
          if (typeof event.text !== "string" || !event.text) return;
          if (state.streamingReply.body.length + event.text.length > (streamedReplyMode === "web_tool_text_v1" ? 8000 : MAX_CHAT_MESSAGE_LENGTH)) {
            throw new Error("The participant reply exceeded the message length limit.");
          }
          state.streamingReply.body += event.text;
          const list = appView.querySelector(".message-list");
          const nearBottom = isMessageListNearBottom(list);
          const body = appView.querySelector("[data-streaming-body]");
          if (body) body.textContent = streamedReplyMode === "web_tool_text_v1" ? streamingText(state.streamingReply.body) : state.streamingReply.body;
          if (list && nearBottom) list.scrollTop = list.scrollHeight;
          await new Promise((resolve) => window.setTimeout(resolve, STREAM_CHUNK_DELAY_MS));
          return;
        }
        if (event.type === "activity") {
          if (event.speakerId !== streamedSpeakerId || !["searching", "reading", "answering", "unavailable"].includes(event.phase)) throw new Error("Invalid agent activity.");
          state.streamingReply.phase = event.phase;
          const label = appView.querySelector('[data-agent-activity]');
          if (label) label.textContent = ({searching: 'Searching the web…', reading: 'Reviewing sources…', unavailable: 'Couldn’t check online'})[event.phase] || '';
          return;
        }
        if (event.type === "citations") {
          if (event.speakerId !== streamedSpeakerId || typeof event.text !== "string" || event.text.length > MAX_CHAT_MESSAGE_LENGTH) throw new Error("Invalid reply sources.");
          state.streamingReply.citations = trustedCitations(event.citations);
          state.streamingReply.body = event.text;
          const body = appView.querySelector('[data-streaming-body]');
          if (body) body.innerHTML = renderCitedText(event.text, state.streamingReply.citations);
          return;
        }
        if (event.type === "done") {
          if (event.speakerId !== streamedSpeakerId
            || (roleId && event.speakerId !== roleId)
            || event.replyMode !== streamedReplyMode) {
            throw new Error("The server returned an invalid participant reply.");
          }
          if (typeof event.waitingForUser !== "boolean") {
            throw new Error("The server returned an invalid reply control marker.");
          }
          waitingForUser = AUTO_CLARIFICATION_WAIT_ENABLED ? event.waitingForUser : false;
          roundComplete = event.roundComplete === true;
          remaining = Number.isSafeInteger(event.remaining) ? event.remaining : null;
          if (event.message?.kind === "participant" && event.message.roleId === streamedSpeakerId
            && typeof event.message.id === "string" && /^reply-[a-f0-9]{32}$/.test(event.message.id)
            && typeof event.message.body === "string" && event.message.body.trim()) {
            committedMessage = { ...event.message, ...(event.message.citations ? { citations: trustedCitations(event.message.citations) } : {}) };
          }
          streamComplete = true;
          return;
        }
        if (event.type === "error") {
          const error = new Error(event.message || "The participant reply is unavailable.");
          error.diagnostics = event.diagnostics;
          throw error;
        }
        throw new Error("The server sent an unknown participant reply event.");
      },
    );
    if (!roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) return false;
    if (!streamComplete || !streamedSpeakerId || waitingForUser === null || !state.streamingReply?.body.trim()) {
      throw new Error("The server returned an invalid participant reply.");
    }

    const roleIndex = state.roles.findIndex((candidate) => candidate.id === streamedSpeakerId);
    const message = committedMessage || {
      kind: "participant",
      roleId: streamedSpeakerId,
      letter: String.fromCharCode(65 + Math.max(0, roleIndex)),
      body: state.streamingReply.body.trim(),
    };
    if (waitingForUser) message.waitingForUser = true;
    if (roundComplete) message.roundEnd = true;
    state.roundComplete = roundComplete && state.pendingUserMessages.length === 0;
    if (state.roundComplete || remaining === 0) {
      state.paused = true;
      state.restartDiscussionAfterTask = false;
    }
    if (state.streamingReply.citations?.length) message.citations = state.streamingReply.citations;
    state.messages.push(message);
    markSummaryDirty();
    if (clearDraft && state.composeDraft === draftAtRequest) state.composeDraft = "";
    state.streamingReply = null;
    state.replyMessage = "";
    state.replyDiagnostics = null;
    render();

    if (!await saveSessionNow()) {
      if (roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) {
        state.waitingForUser = waitingForUser;
        state.replyBlockedBySave = true;
        showReplyError("This reply is visible here, but hasn’t been saved yet.");
      }
      return false;
    }
    if (!roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) return false;

    state.replyError = false;
    state.replyMessage = "";
    state.replyDiagnostics = null;
    state.replyRetryTarget = null;
    state.waitingForUser = waitingForUser;
    return true;
  } catch (error) {
    if (!roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) return false;
    // A connection can end after the server committed the reply but before `done` arrived.
    // Recover that receipt before offering a retry that would create another reply.
    try {
      const result = await requestJson(sessionApi(`/${encodeURIComponent(sessionId)}`));
      if (!roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) return false;
      const added = result.session.messages.slice(state.messages.length);
      if (added.length && added.every(message => message.kind === "participant" && /^reply-[a-f0-9]{32}$/.test(message.id || ""))) {
        state.messages.push(...added);
        state.sessionRevisions.set(sessionId, result.session.revision);
        state.savedParticipantReplyCount = countSavedParticipantReplies(result.session.messages);
        state.roundComplete = added.at(-1).roundEnd === true && !state.pendingUserMessages.length;
        state.paused = state.paused || result.session.paused || state.roundComplete;
        state.streamingReply = null;
        state.replyError = false; state.replyMessage = ""; state.replyDiagnostics = null; state.replyRetryTarget = null;
        state.waitingForUser = false;
        markSummaryDirty(); render();
        return true;
      }
    } catch { /* Keep the normal retry state when recovery is also offline. */ }
    state.discussionLoopVersion += 1;
    state.restartDiscussionAfterTask = false;
    state.failedReplyBody = state.streamingReply?.body || "";
    const failedRoleId = state.streamingReply?.roleId || roleId;
    const message = roleReplyErrorMessage(error);
    state.streamingReply = null;
    showReplyError(message, { roleId: failedRoleId || null, initial, clearDraft }, error?.diagnostics);
    scheduleSessionSave();
    if (error?.status === 401 || error?.status === 402) {
      state.paused = true;
      state.replyError = false;
      state.replyMessage = "";
      render();
      scheduleSessionSave();
      await workspace.refreshUsage();
      await workspace.ensureReplyAvailable();
    }
    return false;
  } finally {
    if (state.activeReplyRequestId === requestId) {
      state.activeReplyRequestId = null;
      state.replyPending = false;
      state.replyAbort = null;
      state.streamingReply = null;
      if (state.pendingConversationStyle) {
        const style = state.pendingConversationStyle;
        state.pendingConversationStyle = "";
        state.roles.forEach(role => { role.prompt = withConversationStyle(role.prompt, style); });
        state.roleEditVersion += 1;
        scheduleSessionSave();
      }
      if (!state.restoreFailed) render();
      void workspace.refreshUsage();
    }
  }
}

function discussionLoopIsCurrent(runId) {
  return state.discussionLoopVersion === runId
    && !state.paused
    && !state.readOnly
    && !state.replyError
    && !state.restoreFailed
    && state.debateStarted
    && state.stage === "debate";
}

async function runDiscussionLoop(runId, firstReply = null) {
  let nextReply = state.pendingUserMessages.length ? null : firstReply;
  while (discussionLoopIsCurrent(runId)) {
    if (!nextReply) {
      let queuedMessage = state.pendingUserMessages.shift();
      if (!queuedMessage) {
        if (state.waitingForUser) return;
        await new Promise((resolve) => window.setTimeout(resolve, discussionTurnDelay(state.messages.at(-1)?.body)));
        if (!discussionLoopIsCurrent(runId)) return;
        nextReply = state.priorityReply;
        state.priorityReply = null;
        queuedMessage = nextReply ? null : state.pendingUserMessages.shift();
      }
      if (queuedMessage) {
        state.waitingForUser = false;
        if (!state.messages.some((message) => message.id === queuedMessage.id)) {
          const userMessage = { kind: "user", id: queuedMessage.id, body: queuedMessage.body };
          if (queuedMessage.attachments?.length) userMessage.attachments = queuedMessage.attachments;
          if (queuedMessage.participantId) userMessage.participantId = queuedMessage.participantId;
          state.messages.push(userMessage);
        }
        state.streamingReply = null;
        nextReply = { roleId: queuedMessage.participantId, initial: false };
        render();
      }
    }
    if (!nextReply) {
      nextReply = { roleId: null, initial: false };
    }

    // Finish editing safely before the next turn, without changing the user's pause choice.
    await workspace.waitForAgentEditor();
    if (!discussionLoopIsCurrent(runId)) return;
    const reply = nextReply;
    nextReply = null;
    if (!await requestRoleReply(reply.roleId, reply.initial, reply.clearDraft)) return;
    if (state.roundComplete) {
      // Closing replies and the pause are already saved before generating the final outcome.
      if (state.autoSummaryTask) await state.autoSummaryTask;
      if (state.discussionLoopVersion === runId && state.roundComplete) await requestSummary(true);
      return;
    }
    state.autoSummaryTask = maybeSaveCheckpoint();
  }
}

function ensureDiscussionLoop(firstReply = null, resumeAfterExisting = false) {
  if (state.pendingUserMessages.length) firstReply = null;
  if (state.waitingForUser && !state.pendingUserMessages.length) return null;
  if (firstReply) state.priorityReply = firstReply;
  if (state.discussionLoopTask) {
    if (resumeAfterExisting) state.restartDiscussionAfterTask = true;
    return state.discussionLoopTask;
  }
  if (state.paused || state.replyError || state.restoreFailed || !state.debateStarted || state.stage !== "debate") return null;

  const runId = ++state.discussionLoopVersion;
  const openingReply = state.priorityReply;
  state.priorityReply = null;
  const task = Promise.resolve()
    .then(() => runDiscussionLoop(runId, openingReply))
    .catch((error) => {
      if (discussionLoopIsCurrent(runId)) {
        showReplyError("The conversation stopped. Try again.", { roleId: null, initial: false });
      }
    })
    .finally(() => {
      if (state.discussionLoopTask !== task) return;
      state.discussionLoopTask = null;
      const shouldRestart = state.restartDiscussionAfterTask
        && !state.paused
        && !state.waitingForUser
        && !state.restoreFailed
        && !state.replyError
        && state.debateStarted
        && state.stage === "debate";
      state.restartDiscussionAfterTask = false;
      if (shouldRestart) void ensureDiscussionLoop();
    });
  state.discussionLoopTask = task;
  return task;
}

function nextReplyForSavedConversation() {
  const latestMessage = state.messages.at(-1);
  if (latestMessage?.kind === "user") {
    const role = latestMessage.participantId
      ? state.roles.find((candidate) => candidate.id === latestMessage.participantId)
      : roleForChatMessage(latestMessage.body);
    return {
      roleId: role?.id || null,
      initial: false,
      clearDraft: true,
      invalidMention: /(?:^|\s)@/u.test(latestMessage.body) && !role,
    };
  }
  if (!state.messages.some((message) => message.kind === "participant")) {
    return { roleId: null, initial: true };
  }
  return null;
}

async function startDebate() {
  if (state.readOnly || state.navigationPending) return false;
  const navigation = state.navigationVersion, sessionId = state.sessionId;
  const current = () => navigation === state.navigationVersion && sessionId === state.sessionId && !state.navigationPending;
  if (state.replyPending || state.discussionLoopTask || state.discussionStartPending) return false;
  const available = await workspace.ensureReplyAvailable();
  if (!current()) return false;
  if (!available) {
    if (state.sessionId && !state.readOnly && workspace.isSignedIn()) {
      state.paused = true; state.discussionLoopVersion += 1;
      state.restartDiscussionAfterTask = false; render(); scheduleSessionSave();
    }
    return false;
  }
  if (state.replyPending || state.discussionLoopTask || state.discussionStartPending) return false;
  if (!state.question.trim() || !state.roles.length) {
    showReplyError("Add a question and at least one participant before starting the conversation.");
    return false;
  }
  state.discussionStartPending = true;
  state.paused = false;
  state.waitingForUser = false;
  state.replyError = false;
  state.replyMessage = "";
  state.failedReplyBody = "";
  state.replyBlockedBySave = false;
  if (!state.messages.length && state.draftAttachments.length) {
    state.messages.push({ kind: "user", id: newChatMessageId(), body: state.question, attachments: state.draftAttachments });
    state.draftAttachments = [];
    rememberComposeDraft();
    const saved = await saveSessionNow();
    if (!current()) return false;
    if (!saved) { state.discussionStartPending = false; return false; }
  }
  state.debateStarted = true;
  if (state.stage !== "debate") {
    state.stage = "debate";
    window.history.pushState({ debateStage: "debate" }, "", stageUrl("debate"));
    render();
  }
  const firstReply = nextReplyForSavedConversation();
  if (firstReply?.invalidMention) {
    state.discussionStartPending = false;
    showReplyError("The saved message mentions a participant who is no longer available.");
    return false;
  }
  state.discussionStartPending = false;
  return ensureDiscussionLoop(firstReply);
}

async function submitChatMessage() {
  if (state.readOnly || state.navigationPending) return false;
  if (state.stage !== "debate") return false;
  const navigation = state.navigationVersion, sessionId = state.sessionId;
  const current = () => navigation === state.navigationVersion && sessionId === state.sessionId && !state.navigationPending;
  const available = await workspace.ensureReplyAvailable();
  if (!current()) return false;
  if (!available) {
    if (state.sessionId && !state.readOnly && workspace.isSignedIn()) {
      state.paused = true; state.discussionLoopVersion += 1;
      state.restartDiscussionAfterTask = false; render(); scheduleSessionSave();
    }
    return false;
  }
  const message = state.composeDraft.trim();
  if (workspace.uploading()) { workspace.notify("Wait for your attachments to finish uploading."); return false; }
  const attachments = state.draftAttachments.map(item => ({ ...item }));
  if (!message && !attachments.length) return false;
  const role = roleForChatMessage(message);
  if (/(?:^|\s)@/u.test(message) && !role) {
    workspace.notify("Choose an agent from the @ menu, or remove the mention.");
    return false;
  }
  const latestMessage = state.messages.at(-1);
  const retryingUnansweredMessage = !state.replyPending
    && state.pendingUserMessages.length === 0
    && latestMessage?.kind === "user"
    && latestMessage.body === message
    && attachmentIds(latestMessage.attachments) === attachmentIds(attachments);
  const messagesNeeded = (state.replyPending ? 1 : 0)
    + state.pendingUserMessages.length * 2
    + (retryingUnansweredMessage ? 1 : 2);
  if (state.messages.length + messagesNeeded > MAX_CHAT_MESSAGES) {
    showReplyError("This conversation is full. Start a new debate to keep going.");
    return false;
  }
  state.composeDraft = "";
  state.draftAttachments = [];
  state.roundComplete = false;
  rememberComposeDraft();
  state.mentionOpen = false;
  state.replyMessage = "";
  state.replyError = false;
  state.replyDiagnostics = null;
  state.replyRetryTarget = null;
  state.failedReplyBody = "";
  state.replyBlockedBySave = false;
  state.paused = false;
  state.waitingForUser = false;

  const messageId = retryingUnansweredMessage ? latestMessage.id || newChatMessageId() : newChatMessageId();
  const pendingMessage = { id: messageId, body: message, participantId: role?.id || null };
  if (attachments.length) pendingMessage.attachments = attachments;
  if (retryingUnansweredMessage) {
    if (!latestMessage.id) latestMessage.id = messageId;
    if (role?.id) latestMessage.participantId = role.id;
    state.priorityReply = { roleId: role?.id || null, initial: false, clearDraft: true };
  } else {
    state.pendingUserMessages.push(pendingMessage);
    state.unsavedMessageIds.add(messageId);
    markSummaryDirty();
  }
  if (state.saveTimer !== null) {
    window.clearTimeout(state.saveTimer);
    state.saveTimer = null;
  }
  render();
  if (!await saveSessionNow() || !current()) return false;
  void ensureDiscussionLoop(null, true);
  return true;
}

function retryRoleReply() {
  if (!state.replyRetryTarget || state.replyPending) return false;
  const { roleId, initial, clearDraft = false } = state.replyRetryTarget;
  state.paused = false;
  state.waitingForUser = false;
  state.replyError = false;
  void ensureDiscussionLoop({ roleId, initial, clearDraft }, true);
  return true;
}

async function retrySavingConversation() {
  const sessionId = state.sessionId, navigation = state.navigationVersion;
  if (!await saveSessionNow() || sessionId !== state.sessionId || navigation !== state.navigationVersion || state.navigationPending) return false;
  const blockedReply = state.replyBlockedBySave ? state.replyRetryTarget : null;
  if (state.replyBlockedBySave) {
    state.replyBlockedBySave = false;
    state.replyError = false;
    state.replyMessage = "";
    state.replyRetryTarget = null;
    render();
  }
  if (state.stage === "debate" && state.debateStarted && !state.paused && !state.replyError) {
    void ensureDiscussionLoop(blockedReply, true);
  }
  return true;
}

function updateRoleSummary(role) {
  for (const node of appView.querySelectorAll("[data-role-name]")) {
    if (node.dataset.roleName === role.id) node.textContent = agentName(role);
  }
  for (const node of appView.querySelectorAll("[data-role-identity]")) {
    if (node.dataset.roleIdentity === role.id) node.textContent = roleIdentityLabel(role);
  }
  for (const node of appView.querySelectorAll("[data-role-avatar]")) {
    if (node.dataset.roleAvatar === role.id) { node.textContent = initials(role.name); node.style.cssText = avatarStyle(role, state.roles.indexOf(role)); }
  }
  const selectedName = appView.querySelector("[data-selected-role-name]");
  if (selectedName && state.selectedRoleId === role.id) selectedName.textContent = role.name || "Unnamed participant";
}

function roleTypeLabel(roleType) {
  return ROLE_TYPE_LABELS[roleType] || roleType;
}

function roleIdentityLabel(role) {
  return typeof role?.identity === "string" && role.identity.trim() ? role.identity.trim() : "Participant";
}

function handleInput(event) {
  const target = event.target;
  if (state.replyPending && !target.matches("[data-chat-input]")) return;
  if (target.matches("[data-field='question']")) {
    if (target.value !== state.question) {
      invalidateRoleGeneration("Your question changed; the previous response will be ignored.");
    }
    state.question = target.value;
    syncRoleGenerationControls();
    resizeQuestionInput();
    scheduleSessionSave();
    return;
  }
  if (target.matches("[data-role-field]")) {
    const role = currentRole();
    if (!role) return;
    role[target.dataset.roleField] = target.value;
    state.roleEditVersion += 1;
    invalidateRoleGeneration("A participant changed; the previous response will be ignored.");
    updateRoleSummary(role);
    scheduleSessionSave();
    return;
  }
  if (target.matches("[data-chat-input]")) {
    updateComposeDraft(target.value);
    const send = appView.querySelector(".send-button");
    if (send) send.disabled = (!target.value.trim() && !state.draftAttachments.length) || workspace.uploading();
    resizeComposer();
    const mention = mentionAtCaret(target.value, target.selectionStart);
    if (state.mentionQuery !== mention?.query) state.mentionActiveIndex = 0;
    state.mentionOpen = Boolean(mention);
    state.mentionQuery = mention?.query || "";
    state.mentionRange = mention?.range || null;
    syncMentionMenu();
    scheduleSessionSave();
  }
}

function handleChange(event) {
  const target = event.target;
  if (state.replyPending && !target.matches("[data-conversation-style]")) return;
  if (target.matches("[data-avatar-color-choice]")) {
    const role = currentRole();
    if (!role || !isAvatarColor(target.value)) return;
    role.avatarColor = target.value;
    state.roleEditVersion += 1;
    updateRoleSummary(role);
    scheduleSessionSave(true);
    return;
  }
  if (target.matches("[data-conversation-style]")) {
    const style = target.value;
    if (!Object.hasOwn(CONVERSATION_STYLES, style)) return;
    const prompts = state.roles.map(role => withConversationStyle(role.prompt, style));
    if (prompts.some(prompt => prompt.length > 1000)) { workspace.notify("Shorten agent instructions before changing the conversation style."); render(); return; }
    if (state.replyPending) {
      state.pendingConversationStyle = style;
      workspace.notify("This style will apply to the next reply.");
      return;
    }
    state.pendingConversationStyle = "";
    state.roles.forEach((role, index) => { role.prompt = prompts[index]; });
    state.roleEditVersion += 1;
    scheduleSessionSave(true);
    render();
    return;
  }
  if (!target.matches("[data-role-field]")) return;
  const role = currentRole();
  if (!role) return;
  role[target.dataset.roleField] = target.value;
  state.roleEditVersion += 1;
  invalidateRoleGeneration("A participant changed; the previous response will be ignored.");
  updateRoleSummary(role);
  scheduleSessionSave(true);
}

function addRole() {
  void editAgents("", true);
}

function removeSelectedRole() {
  if (state.roles.length <= 1) return;
  const removedIndex = state.roles.findIndex((role) => role.id === state.selectedRoleId);
  state.roles = state.roles.filter((role) => role.id !== state.selectedRoleId);
  state.roleEditVersion += 1;
  invalidateRoleGeneration("A participant changed; the previous response will be ignored.");
  state.selectedRoleId = state.roles[Math.max(0, removedIndex - 1)]?.id || state.roles[0].id;
  render();
  scheduleSessionSave();
}

function insertMention(roleId) {
  const role = roleById(roleId);
  if (!role) return;
  const inserted = insertRoleMention(state.composeDraft, state.mentionRange, role.name || "Agent");
  updateComposeDraft(inserted.text);
  state.mentionQuery = "";
  state.mentionRange = null;
  state.mentionOpen = false;
  const input = appView.querySelector("[data-chat-input]");
  if (input) input.value = state.composeDraft;
  syncMentionMenu();
  resizeComposer();
  const send = appView.querySelector(".send-button");
  if (send) send.disabled = workspace.uploading();
  scheduleSessionSave();
  input?.focus();
  if (input) input.setSelectionRange(inserted.caret, inserted.caret);
}

async function copyReplyDiagnostic() {
  const input = appView.querySelector("[data-reply-diagnostic]");
  if (!input) return;
  let copied = false;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(input.value);
      copied = true;
    }
  } catch (_error) {
    copied = false;
  }
  if (!copied) {
    input.focus();
    input.select();
    try {
      copied = document.execCommand("copy");
    } catch (_error) {
      copied = false;
    }
  }
  const status = appView.querySelector("[data-reply-diagnostic-copy-status]");
  if (status) {
    status.textContent = copied
      ? "Diagnostic copied."
      : "Diagnostic selected. Copy it manually.";
  }
}

async function handleClick(event) {
  if (summaryDialog && state.summaryOpen && isDialogBackdropClick(event, summaryDialog)) {
    closeSummaryDialog();
    return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button || button.disabled || (!appView.contains(button) && !summaryDialog?.contains(button) && !stageNavigation.contains(button))) return;
  const action = button.dataset.action;

  if (action === "customize-first") {
    state.customizeFirst = !state.customizeFirst;
    button.classList.toggle("is-active", state.customizeFirst);
    button.setAttribute("aria-pressed", String(state.customizeFirst));
    button.title = state.customizeFirst ? "Customize agents: on" : "Customize agents: off";
    button.innerHTML = icon("agent-customize");
  } else if (action === "edit-agents" || action === "dock-add-role") {
    void editAgents(button.dataset.roleId, action === "dock-add-role");
  } else if (action === "return-debate") {
    state.paused = false;
    state.waitingForUser = false;
    void setStage("debate");
  } else if (action === "summary") {
    void requestSummary();
  } else if (action === "summary-feedback") {
    button.disabled = true;
    try {
      await requestJson(sessionApi(`/${state.sessionId}/feedback`), jsonOptions("POST", { helpful: button.dataset.helpful === "true" }));
      state.summaryFeedback = button.dataset.helpful === "true";
      const feedback = summaryDialog.querySelector("[data-summary-feedback]");
      if (feedback) feedback.innerHTML = '<span>Thanks. Your feedback helps us improve.</span>';
    } catch { button.disabled = false; workspace.notify("Couldn’t save your feedback. Try again."); }
  } else if (action === "regenerate-summary") {
    void regenerateSummary();
  } else if (action === "close-summary") {
    closeSummaryDialog();
  } else if (action === "copy-summary") {
    void copySummary(button);
  } else if (action === "retry-restore") {
    void restoreSession();
  } else if (action === "retry-save") {
    void retrySavingConversation();
  } else if (action === "go-stage") {
    void setStage(button.dataset.stage);
  } else if (action === "start-debate") {
    void startDebate();
  } else if (action === "retry-role-reply") {
    void retryRoleReply();
  } else if (action === "copy-reply-diagnostic") {
    void copyReplyDiagnostic();
  } else if (action === "select-role") {
    state.selectedRoleId = button.dataset.roleId;
    render();
    for (const choice of appView.querySelectorAll("[data-action='select-role']")) {
      if (choice.dataset.roleId === state.selectedRoleId) choice.focus();
    }
  } else if (action === "add-role") {
    addRole();
  } else if (action === "remove-role") {
    removeSelectedRole();
  } else if (action === "set-avatar") {
    const role = currentRole();
    if (!role) return;
    role.avatar = button.dataset.avatar;
    state.roleEditVersion += 1;
    invalidateRoleGeneration("A participant changed; the previous response will be ignored.");
    for (const choice of appView.querySelectorAll("[data-action='set-avatar']")) {
      const selected = choice.dataset.avatar === role.avatar;
      choice.classList.toggle("is-selected", selected);
      choice.setAttribute("aria-pressed", String(selected));
    }
    updateRoleSummary(role);
    scheduleSessionSave();
  } else if (action === "toggle-pause") {
    void toggleDiscussionPause();
  } else if (action === "toggle-mentions") {
    const input = appView.querySelector("[data-chat-input]");
    const mention = input ? mentionAtCaret(input.value, input.selectionStart) : null;
    state.mentionRange = mention?.range || (input ? [input.selectionStart, input.selectionEnd] : null);
    state.mentionQuery = mention?.query || "";
    state.mentionActiveIndex = 0;
    state.mentionOpen = !state.mentionOpen;
    syncMentionMenu();
    input?.focus({ preventScroll: true });
  } else if (action === "mention-role") {
    insertMention(button.dataset.roleId);
  }
}

function regenerateSummary() {
  if (summaryActionDisabled()) return false;
  void requestSummary(true);
  return true;
}

async function maybeSaveCheckpoint() {
  const count = state.savedParticipantReplyCount;
  if (state.readOnly || state.autoSummaryPending || state.summaryLoading || count < 3 || count - state.summaryMessageCount < 6 && state.summaryText) return;
  const sessionId = state.sessionId;
  const requestId = ++state.autoSummarySequence;
  const version = state.summaryContentVersion;
  state.autoSummaryPending = true;
  try {
    const result = await requestJson(sessionSummaryApi(sessionId), jsonOptions("POST", {}, ROLE_REPLY_TIMEOUT_MS));
    if (state.autoSummarySequence !== requestId || state.sessionId !== sessionId || state.summaryLoading) return;
    state.summaryText = result.summary; state.summarySourceRevision = result.sourceRevision;
    state.summaryMessageCount = result.summarizedMessageCount;
    state.summaryDirty = state.summaryContentVersion !== version;
    if (state.summaryOpen) renderSummaryDialog();
  } catch { /* Checkpoints are optional; a failed refresh never interrupts the conversation. */ }
  finally { if (state.autoSummarySequence === requestId) state.autoSummaryPending = false; }
}

async function handleSubmit(event) {
  const form = event.target.closest("form[data-form]");
  if (!form || !appView.contains(form)) return;
  event.preventDefault();

  if (form.dataset.form === "question") {
    const navigation = state.navigationVersion, sessionId = state.sessionId;
    const customizeFirst = state.customizeFirst;
    if (workspace.uploading()) { workspace.notify("Wait for your attachments to finish uploading."); return; }
    if (!state.question.trim() || state.roleGenerationPending || state.activeRoleGenerationRequestId !== null) return;
    if (!await workspace.ensureReplyAvailable()) return;
    await workspace.ready;
    if (navigation !== state.navigationVersion || sessionId !== state.sessionId || state.navigationPending) return;
    if (!state.question.trim() || state.roleGenerationPending || state.activeRoleGenerationRequestId !== null) return;
    const folderId = workspace.selectedFolderId();
    const question = state.question;
    const requestId = ++state.roleGenerationSequence;
    const roleEditVersion = state.roleEditVersion;
    state.activeRoleGenerationRequestId = requestId;
    state.roleGenerationPending = true;
    state.roleGenerationPhase = "generating";
    state.roleGenerationMessage = "";
    state.roleGenerationError = false;
    render();
    let requestPhase = "generation";
    try {
      const avoidNames = recentGeneratedNames();
      const response = await requestJson(
        roleGenerationApi(),
        jsonOptions("POST", {
          question: question.trim(),
          ...(avoidNames.length ? { avoidNames } : {}),
        }, ROLE_GENERATION_TIMEOUT_MS),
      );
      if (!roleGenerationIsCurrent(requestId, question, roleEditVersion)) return;
      const generatedRoles = normalizeGeneratedRoles(response);
      if (!roleGenerationIsCurrent(requestId, question, roleEditVersion)) return;

      state.roleGenerationPhase = "saving";
      state.roleGenerationMessage = "Preparing your conversation…";
      render();
      if (!state.sessionId) {
        requestPhase = "session-save";
        const created = await requestJson(
          sessionApi(),
          jsonOptions("POST", { ...sessionSnapshot(), question, participants: generatedRoles, folderId }),
        );
        if (!roleGenerationIsCurrent(requestId, question, roleEditVersion)) return;
        state.sessionId = created.session.id;
        state.readOnly = false;
        workspace.recordSession(created.session, true);
        acknowledgeAttachments(draftStorage(), draftKey("new"), state.draftAttachments, 0);
        resetSummaryForSession(created.session.id);
        state.sessionRevisions.set(created.session.id, created.session.revision);
        state.savedParticipantReplyCount = countSavedParticipantReplies(created.session.messages);
        window.history.replaceState({ debateStage: state.stage }, "", stageUrl(state.stage));
        setSaveStatus("");
      }
      state.roles = generatedRoles;
      state.selectedRoleId = generatedRoles[0].id;
      state.nextRoleId = nextRoleNumber(generatedRoles);
      state.roleEditVersion += 1;
      state.groupBuilt = true;
      if (!customizeFirst) {
        completeRoleGeneration(requestId);
        if (!await saveSessionNow()) return;
        if (navigation !== state.navigationVersion || state.navigationPending) return;
        rememberGeneratedNames(generatedRoles);
        void startDebate();
      } else {
        const enteredParticipants = await setStage("participants", true, requestId);
        if (enteredParticipants) rememberGeneratedNames(generatedRoles);
      }
      return;
    } catch (error) {
      if (!roleGenerationIsCurrent(requestId, question, roleEditVersion)) return;
      state.roleGenerationError = true;
      state.roleGenerationMessage = requestPhase === "generation"
        ? roleGenerationErrorMessage(error)
        : "Couldn’t save your agents. Try again.";
    } finally {
      if (state.activeRoleGenerationRequestId === requestId) {
        const stale = state.roleGenerationSequence !== requestId;
        state.activeRoleGenerationRequestId = null;
        state.roleGenerationPending = false;
        state.roleGenerationPhase = "";
        if (stale) {
          state.roleGenerationMessage = "";
          state.roleGenerationError = false;
        }
        render();
      }
    }
    return;
  }
  if (form.dataset.form === "chat") {
    await submitChatMessage();
  }
}

async function restoreSession({ url = new URL(location.href), commit = null, isCurrent = () => true } = {}) {
  await workspace.ready;
  const sessionId = url.searchParams.get("session") || state.sessionId;
  const restoreId = ++state.restoreSequence;
  let ownerId = workspace.profile()?.id;
  const current = () => restoreId === state.restoreSequence && isCurrent() && workspace.profile()?.id === ownerId;
  commit ||= () => {
    if (url.href !== new URL(location.href).href) window.history.replaceState(window.history.state, "", url.href);
    return true;
  };
  if (!sessionId) return false;
  if (state.summarySessionId !== sessionId) resetSummaryForSession(sessionId);
  else {
    state.summaryRequestSequence += 1;
    state.summaryLoading = false;
  }
  state.discussionLoopVersion += 1;
  state.restartDiscussionAfterTask = false;
  if (state.activeReplyRequestId !== null) {
    state.replySequence += 1;
    state.activeReplyRequestId = null;
    state.replyPending = false;
  }
  state.restoreFailed = false;
  state.replyMessage = "";
  state.replyError = false;
  state.replyDiagnostics = null;
  state.replyRetryTarget = null;
  state.streamingReply = null;
  state.pendingConversationStyle = "";
  state.replyBlockedBySave = false;
  setNavigationBusy(true);
  try {
    const share = url.searchParams.get("share");
    if (!share && !await workspace.ensureSignedIn()) { if (current()) window.location.assign(APP_PATH); return false; }
    if (!ownerId) ownerId = workspace.profile()?.id;
    if (!current()) return false;
    const result = await requestJson(sessionApi(`/${encodeURIComponent(sessionId)}${share ? `?share=${encodeURIComponent(share)}` : ''}`));
    if (!current()) return false;
    const saved = result.session;
    const requestedStage = stageFromLocation(url);
    const incomingStage = saved.access !== "owner" ? "debate" : requestedStage === "debate" && !saved.debateStarted ? "participants" : requestedStage;
    if (incomingStage !== requestedStage) url.pathname = incomingStage === "question" ? APP_PATH : `${APP_PATH}/${incomingStage}`;
    if (!commit()) return false;
    state.sessionId = saved.id;
    state.access = saved.access;
    state.summaryFeedback = saved.feedback ?? null;
    state.legacyLinkActive = Boolean(saved.legacyLinkActive);
    state.sharingEnabled = Boolean(saved.sharingEnabled);
    state.title = saved.customTitle || "";
    state.readOnly = saved.access !== "owner";
    state.archived = Boolean(saved.archived);
    state.failedReplyBody = "";
    state.discussionStartPending = false;
    state.priorityReply = null;
    state.mentionOpen = false;
    state.mentionQuery = "";
    workspace.syncRoute();
    workspace.recordSession(saved);
    if (saved.summary) {
      state.summaryText = saved.summary.summary; state.summarySourceRevision = saved.summary.sourceRevision;
      state.summaryMessageCount = saved.summary.summarizedMessageCount; state.summaryDirty = saved.summary.isStale;
    }
    state.sessionRevisions.set(saved.id, saved.revision);
    state.conflictedSessions.delete(saved.id);
    state.question = saved.question;
    state.roles = saved.participants;
    state.roleEditVersion += 1;
    state.messages = saved.messages;
    state.savedParticipantReplyCount = countSavedParticipantReplies(saved.messages);
    state.pendingUserMessages = saved.pendingUserMessages || [];
    state.unsavedMessageIds.clear();
    state.waitingForUser = false;
    state.composeDraft = recoverDraft(draftStorage(), draftKey(saved.id), saved.draft, saved.revision);
    state.draftAttachments = recoverAttachments(draftStorage(), draftKey(saved.id), saved.draftAttachments, saved.revision);
    state.groupBuilt = true;
    state.debateStarted = saved.debateStarted;
    state.roundComplete = saved.messages.at(-1)?.roundEnd === true && !state.pendingUserMessages.length;
    state.paused = saved.paused || state.roundComplete;
    state.selectedRoleId = state.roles[0]?.id || "";
    state.nextRoleId = nextRoleNumber(state.roles);
    const latestMessage = state.messages.at(-1);
    if (state.debateStarted && latestMessage?.kind === "user") {
      const role = latestMessage.participantId
        ? state.roles.find((candidate) => candidate.id === latestMessage.participantId)
        : roleForChatMessage(latestMessage.body);
      if (role || !/(?:^|\s)@/u.test(latestMessage.body)) {
        state.replyRetryTarget = { roleId: role?.id || null, initial: false };
        state.replyMessage = "";
        state.replyDiagnostics = null;
      } else {
        state.replyError = true;
        state.replyMessage = "The saved message mentions a participant who is no longer available.";
        state.replyDiagnostics = null;
      }
    } else if (state.debateStarted && !state.messages.some((message) => message.kind === "participant")) {
      const initialRole = state.roles[0];
      if (initialRole) {
        state.replyRetryTarget = { roleId: initialRole.id, initial: true };
        state.replyMessage = "";
        state.replyDiagnostics = null;
      }
    }
    state.stage = incomingStage;
    state.saveStatus = "";
    state.saveFailed = false;
    render();
    await workspace.ready;
    if (current() && state.stage === "debate" && state.debateStarted && !state.paused && !state.replyError && !state.readOnly && workspace.isSignedIn()) {
      void ensureDiscussionLoop(nextReplyForSavedConversation(), true);
    }
    return true;
  } catch (error) {
    if (!current() || !commit()) return false;
    state.sessionId = sessionId;
    state.restoreFailed = true;
    state.restoreMessage = error?.status === 404 ? "Check the link, or choose another conversation." : "Try again in a moment.";
    render();
    return false;
  } finally {
    if (current()) setNavigationBusy(false);
  }
}

async function handlePopState() {
  await appNavigation.navigate(location.href, { pop: true });
}

async function openAppRoute(url, context) {
  if (!context.isCurrent()) return false;
  if (url.searchParams.get("session")) return restoreSession({ url, ...context });
  state.restoreSequence += 1;
  url.pathname = APP_PATH;
  if (!context.commit()) return false;
  resetSummaryForSession("");
  Object.assign(state, {
    sessionId: "", stage: "question", access: "owner", readOnly: false, archived: false,
    title: "", legacyLinkActive: false, sharingEnabled: false, question: (url.searchParams.get("question") || "").slice(0, 2000),
    roles: defaultRoles(), selectedRoleId: "role-1", nextRoleId: 4, groupBuilt: false, customizeFirst: false, debateStarted: false,
    paused: false, waitingForUser: false, roundComplete: false, discussionStartPending: false, messages: [], pendingUserMessages: [],
    composeDraft: "", draftAttachments: recoverAttachments(draftStorage(), draftKey("new"), [], 0),
    mentionOpen: false, mentionQuery: "", mentionRange: null, mentionActiveIndex: 0,
    replyMessage: "", replyError: false, replyDiagnostics: null, replyRetryTarget: null, failedReplyBody: "",
    replyBlockedBySave: false, priorityReply: null, saveStatus: "", saveFailed: false,
    restoreFailed: false, restoreMessage: "", pendingConversationStyle: "", roleGenerationMessage: "", roleGenerationError: false,
  });
  state.unsavedMessageIds.clear();
  state.roleEditVersion += 1;
  workspace.syncRoute();
  render();
  if (url.searchParams.get("start") === "1" && state.question.trim() && consumeStartIntent(sessionStorage, state.question)) {
    window.setTimeout(() => { if (context.isCurrent()) void handleSubmit({ target: appView.querySelector('[data-form="question"]'), preventDefault() {} }); }, 0);
  }
  return true;
}

function handlePageHide() {
  if (state.saveTimer !== null) void saveSessionNow();
}

async function editAgents(roleId, add = false) {
  if (state.readOnly || state.replyPending || !state.groupBuilt || (add && state.roles.length >= MAX_PARTICIPANTS)) return;
  workspace.showAgentEditor(roleId || state.selectedRoleId, add);
}

async function saveAgent(roleId, values) {
  if (state.readOnly || state.replyPending || !state.groupBuilt || !values.name || !values.prompt) return false;
  if (values.name.length > 48 || values.identity.length > 96 || values.prompt.length > 1000) return false;
  if (!isAvatarColor(values.avatarColor)) return false;
  if (values.roleType && !ROLE_TYPES.includes(values.roleType)) return false;
  if (roleId) {
    const role = state.roles.find(item => item.id === roleId);
    if (!role) return false;
    Object.assign(role, values);
  } else {
    if (state.roles.length >= MAX_PARTICIPANTS) return false;
    roleId = `role-${state.nextRoleId++}`;
    state.roles.push({ id: roleId, avatar: initials(values.name), roleType: "Analyst", ...values });
  }
  state.selectedRoleId = roleId;
  state.roleEditVersion += 1;
  invalidateRoleGeneration();
  render();
  if (state.sessionId && !await saveSessionNow()) workspace.notify("Your edits are kept in this tab; the server could not save them yet.");
  return true;
}
async function generateAgent(roleType) {
  const question = state.question.trim();
  if (!question) return null;
  const avoidNames = state.roles.map(role => role.name).slice(-MAX_RECENT_GENERATED_NAMES);
  const existingParticipants = state.roles.map(({ name, identity = "", prompt = "" }) => ({ name, identity, prompt }));
  const response = await requestJson(
    singleRoleGenerationApi(),
    jsonOptions("POST", { question, roleType, existingParticipants, ...(avoidNames.length ? { avoidNames } : {}) }, ROLE_GENERATION_TIMEOUT_MS),
  );
  return response?.role ?? null;
}
async function removeAgent(roleId) {
  if (state.replyPending || state.roles.length <= 1) return false;
  if (!state.roles.some(role => role.id === roleId)) return false;
  state.roles = state.roles.filter(role => role.id !== roleId);
  state.selectedRoleId = state.roles[0].id;
  state.roleEditVersion += 1;
  invalidateRoleGeneration();
  render();
  if (state.sessionId && !await saveSessionNow()) workspace.notify("Your edits are kept in this tab; the server could not save them yet.");
  return true;
}

function handleComposerKeydown(event) {
  if (event.isComposing || event.keyCode === 229) return;
  if (event.target.matches("[data-chat-input]")) {
    if (["ArrowDown", "ArrowUp"].includes(event.key) && state.mentionOpen) {
      const roles = matchingMentionRoles();
      if (roles.length) {
        event.preventDefault();
        state.mentionActiveIndex = (state.mentionActiveIndex + (event.key === "ArrowDown" ? 1 : -1) + roles.length) % roles.length;
        syncMentionMenu();
        appView.querySelector(`#mention-option-${state.mentionActiveIndex}`)?.scrollIntoView({ block: "nearest" });
      }
    } else if (event.key === "Escape" && state.mentionOpen) {
      state.mentionOpen = false;
      syncMentionMenu();
      event.preventDefault();
    } else if ((event.key === "Enter" && !event.shiftKey) || (event.key === "Tab" && state.mentionOpen)) {
      const candidates = state.mentionOpen ? matchingMentionRoles() : [];
      const role = candidates.length === 1 ? candidates[0] : candidates[state.mentionActiveIndex];
      if (event.key === "Tab" && !role) return;
      event.preventDefault();
      if (role) insertMention(role.id);
      else void submitChatMessage();
    }
  } else if (event.target.matches('[data-action="mention-role"]')) {
    const buttons = [...appView.querySelectorAll('[data-action="mention-role"]')];
    const index = buttons.indexOf(event.target);
    if (["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault(); buttons[(index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
    } else if (event.key === "Escape") {
      state.mentionOpen = false; syncMentionMenu(); appView.querySelector("[data-chat-input]")?.focus();
    }
  }
}

function cancelActiveWork() {
  state.navigationVersion += 1;
  state.discussionLoopVersion += 1;
  state.discussionLoopTask = null;
  state.discussionStartPending = false;
  state.restartDiscussionAfterTask = false;
  state.replyAbort?.abort();
  state.activeReplyRequestId = null;
  state.replyPending = false;
  state.streamingReply = null;
  invalidateRoleGeneration();
  state.activeRoleGenerationRequestId = null;
  state.roleGenerationPending = false;
  state.summaryRequestSequence += 1;
  state.autoSummarySequence += 1;
  state.autoSummaryPending = false;
  state.autoSummaryTask = null;
}

function clearPrivateView() {
  appNavigation.cancel();
  viewPositions.clear();
  renderedViewKey = "";
  cancelActiveWork();
  state.restoreSequence += 1;
  if (state.saveTimer !== null) window.clearTimeout(state.saveTimer);
  state.saveTimer = null;
  state.sessionId = "";
  state.question = "";
  state.messages = [];
  state.pendingUserMessages = [];
  state.composeDraft = "";
  state.draftAttachments = [];
  state.groupBuilt = false;
  state.debateStarted = false;
  state.paused = false;
  state.restoreFailed = false;
  state.replyError = false;
  state.saveFailed = false;
  state.stage = "question";
  resetSummaryForSession("");
  window.history.replaceState({}, "", APP_PATH);
  render();
}

async function prepareNavigation() {
  rememberViewPosition();
  rememberComposeDraft();
  if (state.readOnly || state.restoreFailed || !workspace.isSignedIn()) return true;
  if (workspace.uploading()) { workspace.notify("Wait for the upload to finish, or remove it before switching debates."); return false; }
  cancelActiveWork();
  if (!state.sessionId || state.conflictedSessions.has(state.sessionId)) return true;
  if (state.debateStarted) state.paused = true;
  const saved = await saveSessionNow();
  return saved || [401, 404, 409].includes(state.saveErrorStatus);
}
const appNavigation = createAppNavigation({
  appPath: APP_PATH,
  location: () => location.href,
  history: window.history,
  prepare: prepareNavigation,
  transition: openAppRoute,
  busy: setNavigationBusy,
});
const workspace = createWorkspace({
  basePath: BASE_PATH,
  appPath: APP_PATH,
  getState: () => state,
  refresh: render,
  attachmentsChanged: (saveNow = false) => { rememberComposeDraft(); return state.sessionId ? scheduleSessionSave(saveNow) : true; },
  beforeNavigate: prepareNavigation,
  navigateApp: (path, options) => appNavigation.navigate(path, options),
  routeChanged: () => appNavigation.sync(),
  beforeSignOut: async () => {
    cancelActiveWork();
    if (state.sessionId && !state.readOnly && !state.restoreFailed) {
      state.paused = true;
      rememberComposeDraft();
      await saveSessionNow();
    }
  },
  sessionEnded: clearPrivateView,
  usageChanged: usage => {
    if (usage?.remaining === 0 && state.debateStarted && !state.readOnly && !state.paused) {
      state.paused = true;
      state.discussionLoopVersion += 1;
      state.restartDiscussionAfterTask = false;
      scheduleSessionSave();
    }
  },
  saveAgent,
  removeAgent,
  generateAgent,
  useQuestion: async (question) => {
    if (state.sessionId) {
      rememberStartIntent(sessionStorage, question);
      return appNavigation.navigate(`${APP_PATH}?start=1&question=${encodeURIComponent(question)}`);
    }
    if (state.roleGenerationPending) { workspace.notify("Wait for the current group to finish."); return false; }
    if (question !== state.question) invalidateRoleGeneration();
    state.question = question;
    state.stage = "question";
    render();
    void handleSubmit({ target: appView.querySelector('[data-form="question"]'), preventDefault() {} });
    return true;
  },

});
if (!state.sessionId) {
  state.question = (new URLSearchParams(location.search).get("question") || "").slice(0, 2000);
  state.draftAttachments = recoverAttachments(draftStorage(), draftKey("new"), [], 0);
}

try {
  runtime.readConfig();
  appView.addEventListener("input", handleInput);
  appView.addEventListener("pointerdown", event => {
    if (event.target.closest('[data-action="mention-role"], [data-action="toggle-mentions"]')) event.preventDefault();
  });
  document.addEventListener("pointerdown", event => {
    if (state.mentionOpen && !event.target.closest('#mention-menu, [data-action="toggle-mentions"]')) {
      state.mentionOpen = false;
      syncMentionMenu();
    }
  });
  window.addEventListener("resize", resizeQuestionInput);
  appView.addEventListener("keydown", handleComposerKeydown);
  appView.addEventListener("change", handleChange);
  appView.addEventListener("click", handleClick);
  stageNavigation.addEventListener("click", handleClick);
  appView.addEventListener("submit", handleSubmit);
  summaryDialog?.addEventListener("click", handleClick);
  summaryDialog?.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeSummaryDialog();
  });
  window.addEventListener("popstate", handlePopState);
  window.addEventListener("pagehide", handlePageHide);
  if (state.sessionId) {
    void restoreSession().finally(() => runtime.markReady());
  } else {
    if (state.stage !== "question" && !state.question.trim()) {
      state.stage = "question";
      window.history.replaceState({ debateStage: "question" }, "", stageUrl("question"));
    }
    render();
    runtime.markReady();
    if (new URLSearchParams(location.search).get("start") === "1" && state.question.trim() && consumeStartIntent(sessionStorage, state.question)) {
      void workspace.ready.then(() => handleSubmit({ target: appView.querySelector('[data-form="question"]'), preventDefault() {} }));
    }
  }
} catch (error) {
  runtime.showFatalError(error);
}

// Shared readers follow saved turns, never run agents or spend anyone's allowance.
let viewerPollBusy = false;
window.setInterval(async () => {
  if (!state.readOnly || !state.sessionId || state.restoreFailed || document.hidden || viewerPollBusy) return;
  const sessionId = state.sessionId;
  const share = new URLSearchParams(location.search).get("share");
  viewerPollBusy = true;
  try {
    const result = await requestJson(sessionApi(`/${sessionId}${share ? `?share=${encodeURIComponent(share)}` : ''}`));
    if (state.sessionId !== sessionId) return;
    const saved = result.session;
    if (saved.revision !== state.sessionRevisions.get(sessionId)) {
      state.title = saved.customTitle || ""; state.question = saved.question; state.roles = saved.participants; state.messages = saved.messages;
      state.savedParticipantReplyCount = countSavedParticipantReplies(saved.messages);
      state.sessionRevisions.set(sessionId, saved.revision);
      render();
    }
    if (saved.summary) {
      state.summaryText = saved.summary.summary; state.summaryDirty = saved.summary.isStale;
      if (state.summaryOpen) renderSummaryDialog();
    }
  } catch (error) {
    if (error.status === 404) { state.restoreFailed = true; state.restoreMessage = "This viewing link is no longer available."; render(); }
  } finally { viewerPollBusy = false; }
}, 5000);
