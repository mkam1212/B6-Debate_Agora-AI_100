import { requestEventStream, requestJson } from "../api.js";

const runtime = window.GizmoAppRuntime;
const appView = document.getElementById("app-view");
const stageNavigation = document.getElementById("stage-navigation");
const summaryDialog = document.getElementById("summary-dialog");
const baseElement = document.querySelector("base");
const configuredPath = new URL(baseElement.href, window.location.href).pathname;
const BASE_PATH = configuredPath.endsWith("/") ? configuredPath : `${configuredPath}/`;

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
const DISCUSSION_TURN_DELAY_MS = 900;
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

function stageFromLocation() {
  const pathname = window.location.pathname;
  const relative = pathname.startsWith(BASE_PATH)
    ? pathname.slice(BASE_PATH.length).replace(/\/+$/, "")
    : "";
  return ["participants", "debate"].includes(relative) ? relative : "question";
}

function sessionIdFromLocation() {
  return new URLSearchParams(window.location.search).get("session") || "";
}

const state = {
  stage: stageFromLocation(),
  sessionId: sessionIdFromLocation(),
  summarySessionId: "",
  savedParticipantReplyCount: 0,
  summaryOpen: false,
  summaryLoading: false,
  summaryError: "",
  summaryText: "",
  summarySourceRevision: null,
  summaryMessageCount: 0,
  summaryDirty: false,
  summaryRequestSequence: 0,
  summaryContentVersion: 0,
  question: "",
  roles: DEFAULT_ROLES.map((role) => ({ ...role })),
  selectedRoleId: "role-1",
  groupBuilt: false,
  debateStarted: false,
  paused: false,
  waitingForUser: false,
  discussionStartPending: false,
  messages: [],
  pendingUserMessages: [],
  unsavedMessageIds: new Set(),
  streamingReply: null,
  composeDraft: "",
  mentionOpen: false,
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
  discussionLoopTask: null,
  discussionLoopVersion: 0,
  restartDiscussionAfterTask: false,
  priorityReply: null,
};

function apiBase() {
  return runtime.readConfig().apiBase.replace(/\/$/, "");
}

function sourceArchiveApi() {
  return `${apiBase()}/debate/source.zip`;
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
    return {
      ...role,
      identity: identity.trim(),
      avatar: AVATARS[avatarSeed % AVATARS.length],
    };
  });
}

function roleGenerationErrorMessage(error) {
  const detail = error instanceof Error ? error.message.toLowerCase() : "";
  const diagnostic = error?.diagnostics && typeof error.diagnostics === "object" ? error.diagnostics : {};
  const safeReasons = new Set([
    "invalid_response_type", "invalid_json", "invalid_top_level_shape", "invalid_roles_type",
    "invalid_role_count", "invalid_role_type", "invalid_role_fields", "invalid_field_type",
    "empty_field", "field_too_long", "time_bound_quantity", "duplicate_name", "duplicate_prompt",
    "invalid_enum", "duplicate_enum", "field_out_of_range", "model_truncated", "model_error",
  ]);
  const safeFields = new Set(["response", "roles", "name", "identity", "roleType", "prompt", "avatarSeed"]);
  const safeTypes = new Set(["string", "integer", "number", "boolean", "array", "object", "null"]);
  const reason = safeReasons.has(diagnostic.reason) ? diagnostic.reason : "";
  const field = safeFields.has(diagnostic.field) ? diagnostic.field : "";
  const expectedType = safeTypes.has(diagnostic.expectedType) ? diagnostic.expectedType : "";
  const actualType = safeTypes.has(diagnostic.actualType) ? diagnostic.actualType : "";
  const finishReason = ["stop", "length", "content_filter", "tool_calls", "function_call", "other", "unknown"]
    .includes(diagnostic.finishReason)
    ? diagnostic.finishReason
    : "";
  const outputChars = Number.isSafeInteger(diagnostic.outputChars) && diagnostic.outputChars >= 0
    ? diagnostic.outputChars
    : null;
  const allowedRoleTypes = Array.isArray(diagnostic.allowedValues)
    ? diagnostic.allowedValues.filter((value) => ROLE_TYPES.includes(value))
    : [];
  const diagnosticParts = [
    reason && `reason: ${reason}`,
    field && `field: ${field}`,
    expectedType && actualType && `type: expected ${expectedType}, got ${actualType}`,
    allowedRoleTypes.length && `allowed roleType: ${allowedRoleTypes.join(", ")}`,
    finishReason && `finish: ${finishReason}`,
    outputChars !== null && `output characters: ${outputChars}`,
  ].filter(Boolean);
  if (error?.status === 503 && /not configured|not set|missing|required environment variable/.test(detail)) {
    return "AI role generation is not configured. Ask the server owner to set GIZMO_LLM_API_KEY, GIZMO_LLM_BASE_URL, and GIZMO_LLM_MODEL.";
  }
  if (error?.status === 503 && reason === "model_truncated") {
    return `AI role generation was truncated (${diagnosticParts.join(", ")}). Please try again.`;
  }
  if (error?.status === 503) return "AI role generation is temporarily unavailable. Please try again.";
  if (error?.status === 502) {
    const suffix = diagnosticParts.length ? ` (${diagnosticParts.join(", ")})` : "";
    return `The AI returned an unusable role format${suffix}. Please try again.`;
  }
  return "Could not build the group. Please try again.";
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
  node.textContent = message;
  node.setAttribute("role", "alert");
  node.classList.add("is-error");
}

function renderSaveStatus() {
  if (!state.saveFailed || !state.saveStatus) return "";
  return `<p class="save-status is-error" data-save-status role="alert">${esc(state.saveStatus)}</p>`;
}

function markSessionConflict(sessionId) {
  state.conflictedSessions.add(sessionId);
  if (state.sessionId !== sessionId) return;
  if (state.saveTimer !== null) {
    window.clearTimeout(state.saveTimer);
    state.saveTimer = null;
  }
  setSaveStatus(
    "Another tab saved a newer version. Your local changes are still visible but were not saved. Automatic saving has stopped.",
    true,
  );
}

async function saveSessionNow() {
  const sessionId = state.sessionId;
  if (!sessionId || state.conflictedSessions.has(sessionId)) return false;
  if (state.saveTimer !== null) {
    window.clearTimeout(state.saveTimer);
    state.saveTimer = null;
  }
  const sequence = ++state.saveSequence;
  const snapshot = sessionSnapshot();
  const snapshotMessageIds = new Set([
    ...snapshot.messages.map((message) => message.id).filter(Boolean),
    ...snapshot.pendingUserMessages.map((message) => message.id),
  ]);
  setSaveStatus("");
  const operation = state.saveQueue.then(async () => {
    if (state.conflictedSessions.has(sessionId)) return "conflicted";
    const expectedRevision = state.sessionRevisions.get(sessionId);
    try {
      const result = await requestJson(
        sessionApi(`/${encodeURIComponent(sessionId)}`),
        jsonOptions("PUT", { ...snapshot, expectedRevision }),
      );
      state.sessionRevisions.set(sessionId, result.session.revision);
      if (state.sessionId === sessionId) {
        state.savedParticipantReplyCount = countSavedParticipantReplies(result.session.messages);
      }
      for (const messageId of snapshotMessageIds) state.unsavedMessageIds.delete(messageId);
      return "saved";
    } catch (error) {
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
    if (error?.status === 409) {
      markSessionConflict(sessionId);
    } else if (sequence === state.saveSequence) {
      setSaveStatus(`Could not save this session. ${error instanceof Error ? error.message : "Try again."}`, true);
    }
    return false;
  }
}

function updatePendingSaveLabels() {
  for (const node of appView.querySelectorAll("[data-message-save-id]")) {
    const messageId = node.dataset.messageSaveId;
    if (state.unsavedMessageIds.has(messageId)) {
      node.textContent = state.saveFailed ? "Not saved" : "Saving…";
    } else if (state.pendingUserMessages.some((message) => message.id === messageId)) {
      node.textContent = "Saved · queued";
    } else {
      node.remove();
    }
  }
}

function scheduleSessionSave(immediate = false) {
  if (!state.sessionId || state.conflictedSessions.has(state.sessionId)) return;
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
  const steps = [
    ["question", "Question", true],
    ["participants", "Participants", state.groupBuilt && Boolean(state.question.trim())],
    ["debate", "Debate", state.debateStarted],
  ];
  return `<div class="stage-navigation-row"><a class="button button-quiet source-download-button" href="${esc(sourceArchiveApi())}" download aria-label="Download Debate Agora source code">Download code</a><nav class="stepper" aria-label="Debate stages"><ol>${steps.map(([stage, label, enabled], index) => {
    const current = state.stage === stage;
    const complete = (stage === "question" && state.groupBuilt) || (stage === "participants" && state.debateStarted);
    const canNavigate = enabled && !state.roleGenerationPending && !state.replyPending;
    return `<li><button class="step-item${current ? " is-current" : ""}${complete ? " is-complete" : ""}" type="button" data-action="go-stage" data-stage="${stage}" aria-current="${current ? "step" : "false"}" ${canNavigate ? "" : "disabled"}><span class="step-number">0${index + 1}</span><span>${label}</span></button></li>`;
  }).join("")}</ol></nav></div>`;
}

function renderQuestion() {
  const status = state.roleGenerationPending || state.roleGenerationError ? state.roleGenerationMessage : "";
  return `<section class="screen question-screen" aria-labelledby="question-title">
    <form class="question-form" data-form="question" aria-busy="${state.roleGenerationPending}">
      <h1 id="question-title" class="screen-intro">One clear question for the group.</h1>
      <label class="sr-only" for="question-input">Your question</label>
      <div class="question-input-shell">
        <textarea id="question-input" name="question" data-field="question" maxlength="2000" required placeholder="For example: Should our team move to a four-day workweek?" ${state.roleGenerationPhase === "saving" ? "disabled" : ""}>${esc(state.question)}</textarea>
        <button class="button button-primary build-group-button" type="submit" aria-label="Build the group" ${state.question.trim() && !state.roleGenerationPending ? "" : "disabled"}>
          ${state.roleGenerationPending
            ? '<span class="button-loading-spinner" aria-hidden="true"></span>'
            : '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></svg>'}
        </button>
      </div>
      ${status ? `<p class="role-generation-status${state.roleGenerationError ? " is-error" : ""}" data-role-generation-status role="${state.roleGenerationError ? "alert" : "status"}">${esc(status)}</p>` : ""}
    </form>
  </section>`;
}

function renderRoleList() {
  return state.roles.map((role) => {
    const selected = role.id === state.selectedRoleId;
    return `<li><button class="role-choice${selected ? " is-selected" : ""}" type="button" data-action="select-role" data-role-id="${esc(role.id)}" aria-pressed="${selected}" ${state.replyPending ? "disabled" : ""}>
      <span class="role-avatar" data-role-avatar="${esc(role.id)}" aria-hidden="true">${esc(role.avatar)}</span>
      <span class="role-choice-copy"><strong data-role-name="${esc(role.id)}">${esc(role.name || "Unnamed participant")}</strong><span data-role-identity="${esc(role.id)}">${esc(roleIdentityLabel(role))}</span></span>
    </button></li>`;
  }).join("");
}

function renderAvatarChoices(role) {
  return AVATARS.map((avatar) => {
    const selected = role.avatar === avatar;
    return `<button class="avatar-choice${selected ? " is-selected" : ""}" type="button" data-action="set-avatar" data-avatar="${esc(avatar)}" aria-label="Choose avatar ${esc(avatar)}" aria-pressed="${selected}" ${state.replyPending ? "disabled" : ""}>${esc(avatar)}</button>`;
  }).join("");
}

function renderRoleEditor(role) {
  return `<section class="role-editor" aria-label="Edit selected participant">
    <div class="editor-heading"><h2 data-selected-role-name>${esc(role.name || "Unnamed participant")}</h2></div>
    <fieldset class="avatar-field"><legend>Avatar</legend><div class="avatar-picker">${renderAvatarChoices(role)}</div></fieldset>
    <div class="field"><label for="role-name">Name</label><input id="role-name" type="text" data-role-field="name" maxlength="48" value="${esc(role.name)}" placeholder="Participant name" ${state.replyPending ? "disabled" : ""}></div>
    <div class="field"><label for="role-identity">Identity</label><input id="role-identity" type="text" data-role-field="identity" maxlength="96" value="${esc(role.identity || "")}" placeholder="Specific identity; blank for older roles" ${state.replyPending ? "disabled" : ""}></div>
    <div class="field"><label for="role-prompt">Prompt</label><textarea id="role-prompt" data-role-field="prompt" rows="6" maxlength="1000" placeholder="How should this participant think and contribute?" ${state.replyPending ? "disabled" : ""}>${esc(role.prompt)}</textarea></div>
    <div class="editor-actions"><button class="button button-quiet button-danger" type="button" data-action="remove-role" ${state.roles.length <= 1 || state.replyPending ? "disabled" : ""}>Remove participant</button></div>
  </section>`;
}

function renderReplyStatus() {
  if (!state.replyError || !state.replyMessage) {
    return state.replyMode
      ? `<div class="role-generation-status" data-reply-status role="status">Reply mode: ${esc(state.replyMode)}</div>`
      : "";
  }
  const replyModeMarkup = state.replyMode
    ? `<span class="reply-mode" data-reply-mode>Reply mode: ${esc(state.replyMode)}</span>`
    : "";
  const diagnosticText = formatReplyDiagnostics(state.replyDiagnostics);
  const diagnosticMarkup = diagnosticText
    ? `<div class="reply-diagnostic"><label for="reply-diagnostic-text">Safe diagnostic to share</label><div class="reply-diagnostic-controls"><input id="reply-diagnostic-text" type="text" data-reply-diagnostic readonly value="${esc(diagnosticText)}"><button class="button button-quiet" type="button" data-action="copy-reply-diagnostic">Copy</button></div><span class="reply-diagnostic-copy-status" data-reply-diagnostic-copy-status aria-live="polite"></span></div>`
    : "";
  const retryButton = state.replyRetryTarget
    ? `<button class="button button-quiet" type="button" data-action="retry-role-reply" ${state.replyPending || state.paused ? "disabled" : ""}>Retry reply</button>`
    : "";
  return `<div class="role-generation-status is-error" data-reply-status role="alert">${replyModeMarkup}<span>${esc(state.replyMessage)}</span>${diagnosticMarkup}${retryButton}</div>`;
}

function renderParticipants() {
  const role = currentRole();
  return `<section class="screen participants-screen" aria-label="Participants">
    ${renderReplyStatus()}
    <div class="participants-layout">
      <aside class="participant-rail" aria-label="Participants">
        <div class="rail-heading"><h2>Participants <span>${state.roles.length}</span></h2><button class="button button-quiet add-button" type="button" data-action="add-role" ${state.roles.length >= MAX_PARTICIPANTS ? "disabled title=\"Maximum of 16 participants\"" : state.replyPending ? "disabled" : ""}>+ Add</button></div>
        <ul class="role-list">${renderRoleList()}</ul>
      </aside>
      ${renderRoleEditor(role)}
    </div>
    <div class="page-actions"><button class="button button-quiet" type="button" data-action="go-stage" data-stage="question" ${state.replyPending ? "disabled" : ""}>← Back</button><button class="button button-primary" type="button" data-action="start-debate" ${state.roles.length && state.question.trim() && !state.replyPending ? "" : "disabled"}>Start debate <span aria-hidden="true">→</span></button></div>
  </section>`;
}

function renderMessage(message) {
  if (message.kind === "user") {
    const saveStatus = message.id && state.unsavedMessageIds.has(message.id)
      ? `<span class="message-save-status" data-message-save-id="${esc(message.id)}">${state.saveFailed ? "Not saved" : "Saving…"}</span>`
      : "";
    return `<li class="message message-user"><div class="user-bubble"><span class="message-author">You</span><p>${esc(message.body)}</p>${saveStatus}</div></li>`;
  }
  const role = roleById(message.roleId) || { name: "Participant", avatar: "•" };
  return `<li class="message message-participant"><span class="message-avatar" aria-hidden="true">${esc(role.avatar)}</span><div class="message-copy"><div class="message-meta"><strong>${esc(role.name)}</strong><span>${esc(roleIdentityLabel(role))}</span><span class="sequence-tag">Reply ${esc(message.letter)}</span></div><p>${esc(message.body)}</p></div></li>`;
}

function renderStreamingReply() {
  if (!state.streamingReply) return "";
  const role = roleById(state.streamingReply.roleId) || { name: "Participant", avatar: "•" };
  return `<li class="message message-participant is-streaming" data-streaming-reply>
    <span class="message-avatar" data-streaming-avatar aria-hidden="true">${esc(role.avatar)}</span>
    <div class="message-copy"><div class="message-meta"><strong data-streaming-name>${esc(role.name)}</strong><span data-streaming-identity>${esc(roleIdentityLabel(role))}</span></div><p data-streaming-body aria-live="off">${esc(state.streamingReply.body)}</p></div>
  </li>`;
}

function renderPendingChatMessage(message) {
  const status = state.unsavedMessageIds.has(message.id) ? (state.saveFailed ? "Not saved" : "Saving…") : "Saved · queued";
  return `<li class="message message-user"><div class="user-bubble"><span class="message-author">You</span><p>${esc(message.body)}</p><span class="message-save-status" data-message-save-id="${esc(message.id)}">${status}</span></div></li>`;
}

function renderMentionMenu() {
  return `<div class="mention-menu" id="mention-menu" role="listbox" aria-label="Mention a participant" ${state.mentionOpen ? "" : "hidden"}>${state.roles.map((role) => `<button type="button" role="option" data-action="mention-role" data-role-id="${esc(role.id)}"><span aria-hidden="true">${esc(role.avatar)}</span><span>${esc(role.name || "Unnamed participant")}</span></button>`).join("")}</div>`;
}

function renderDebate() {
  const summaryDisabled = summaryActionDisabled();
  const summaryHelp = summaryWaitMessage();
  const summaryLabel = state.summaryLoading
    ? "Summarizing…"
    : state.summaryText && state.summaryDirty
      ? "Update summary"
      : "Summary";
  return `<section class="screen debate-screen" aria-labelledby="debate-title">
    <div class="debate-heading">
      <h1 id="debate-title" class="question-context" title="${esc(state.question)}">${esc(state.question)}</h1>
      <div class="debate-heading-tools">
        <button class="button button-secondary summary-button" type="button" data-action="summary" aria-describedby="summary-availability" ${summaryDisabled ? "disabled" : ""}>${summaryLabel}</button>
        <button class="button button-secondary pause-button" type="button" data-action="toggle-pause" aria-pressed="${state.paused}">${state.paused ? "Resume" : "Pause"}</button>
      </div>
    </div>
    <p class="summary-availability" id="summary-availability" ${summaryHelp ? "" : "hidden"}>${esc(summaryHelp)}</p>
    ${renderReplyStatus()}
    <ol class="message-list" aria-label="Conversation messages" aria-live="polite" aria-relevant="additions">${state.messages.map(renderMessage).join("")}${renderStreamingReply()}${state.pendingUserMessages.map(renderPendingChatMessage).join("")}</ol>
    <form class="composer-form" data-form="chat" aria-busy="${state.replyPending}">
      <label class="sr-only" for="chat-input">Your message</label>
      <textarea id="chat-input" data-chat-input rows="2" maxlength="2000" placeholder="Write a message…">${esc(state.composeDraft)}</textarea>
      <div class="composer-footer"><div class="mention-control">${renderMentionMenu()}<button class="button button-quiet mention-button" type="button" data-action="toggle-mentions" aria-expanded="${state.mentionOpen}" aria-controls="mention-menu">@ <span>mention</span></button></div><button class="button button-primary send-button" type="submit">Send <span aria-hidden="true">↑</span></button></div>
    </form>
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
    : state.summaryText && state.summaryDirty
      ? "Update summary"
      : state.summaryText
        ? "Generate again"
        : "Generate summary";
  const summaryDisabled = summaryActionDisabled();
  summaryDialog.innerHTML = `<div class="summary-dialog-content">
    <header class="summary-dialog-header"><div><p class="eyebrow">Saved conversation</p><h2 id="summary-dialog-title">Summary</h2></div><button class="button button-quiet" type="button" data-action="close-summary" aria-label="Close summary">Close</button></header>
    ${state.summaryDirty ? `<p class="summary-stale" role="status">New conversation content is available. Update this summary when ready.</p>` : ""}
    ${state.summaryError ? `<p class="summary-error" role="alert">${esc(state.summaryError)}</p>` : ""}
    ${state.summaryLoading && !state.summaryText ? `<p class="summary-loading" role="status">Generating summary…</p>` : ""}
    ${state.summaryText ? `<div class="summary-copy">${esc(state.summaryText)}</div><p class="summary-meta">Based on ${state.summaryMessageCount} saved messages · revision ${state.summarySourceRevision}.</p>` : ""}
    ${!state.summaryText && !state.summaryLoading && !state.summaryError ? `<p class="summary-empty">A plain-text summary will cover the main views, disagreements, open questions, and optional next steps.</p>` : ""}
    <footer class="summary-dialog-actions"><button class="button button-primary" type="button" data-action="regenerate-summary" ${summaryDisabled ? "disabled" : ""}>${updateLabel}</button></footer>
  </div>`;
  if (!summaryDialog.open) summaryDialog.showModal();
}

function summaryWaitMessage() {
  if (!summaryDialog) return "Summary panel is unavailable in this page version. Refresh the preview to continue.";
  if (!state.sessionId) return "Save this conversation before creating a summary.";
  if (state.savedParticipantReplyCount < 3) {
    return `Summary unlocks after 3 saved participant replies (${state.savedParticipantReplyCount}/3).`;
  }
  if (state.replyPending || state.pendingUserMessages.length || state.discussionStartPending) {
    return "Wait for the current reply and queued messages to finish saving.";
  }
  return "";
}

function summaryActionDisabled() {
  return !summaryDialog
    || !state.sessionId
    || state.savedParticipantReplyCount < 3
    || state.replyPending
    || state.pendingUserMessages.length > 0
    || state.discussionStartPending
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
    state.summaryDirty = state.summaryContentVersion !== contentVersion;
    return true;
  } catch (error) {
    if (summaryRequestIsCurrent(requestId, sessionId)) {
      state.summaryError = error instanceof Error ? error.message : "Could not create the summary. Please retry.";
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
  document.body.dataset.debateStage = state.stage;
  if (state.restoreFailed) {
    stageNavigation.hidden = true;
    stageNavigation.innerHTML = "";
    appView.innerHTML = `<section class="screen restore-error" aria-labelledby="restore-error-title"><p class="eyebrow">Saved session</p><h1 id="restore-error-title">This session could not be opened</h1><p class="screen-intro" role="alert">${esc(state.restoreMessage)}</p><div class="page-actions"><button class="button button-secondary" type="button" data-action="retry-restore">Try again</button><a class="button button-primary" href="${esc(BASE_PATH)}">Start a new question</a></div></section>`;
    renderSummaryDialog();
    return;
  }
  const screen = state.stage === "participants" ? renderParticipants()
    : state.stage === "debate" ? renderDebate()
      : renderQuestion();
  const previousMessageList = appView.querySelector(".message-list");
  const previousMessageScroll = previousMessageList?.scrollTop ?? 0;
  const shouldFollowLatest = !previousMessageList || isMessageListNearBottom(previousMessageList);
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
  appView.innerHTML = `${renderSaveStatus()}${screen}`;
  const messageList = appView.querySelector(".message-list");
  if (messageList) {
    messageList.scrollTop = shouldFollowLatest
      ? messageList.scrollHeight
      : Math.min(previousMessageScroll, messageList.scrollHeight);
  }
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
  document.title = `${titles[state.stage]} · Debate Agora`;
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
    const buttonLabel = state.roleGenerationPending ? "Building the group" : "Build the group";
    submit.disabled = state.roleGenerationPending || !state.question.trim();
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
  const path = stage === "question" ? BASE_PATH : `${BASE_PATH}${stage}`;
  const query = new URLSearchParams();
  if (state.sessionId) query.set("session", state.sessionId);
  const search = query.toString();
  return search ? `${path}?${search}` : path;
}

async function setStage(stage, updateHistory = true, roleGenerationRequestId = null) {
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
  const mentionIndex = message.indexOf("@");
  if (mentionIndex < 0) return null;
  const mention = message.slice(mentionIndex + 1).trimStart();
  const rolesByNameLength = [...state.roles].sort((left, right) => right.name.length - left.name.length);
  return rolesByNameLength.find((role) => {
    const name = role.name.trim();
    const candidate = mention.slice(0, name.length);
    const boundary = mention[name.length];
    return candidate.toLocaleLowerCase() === name.toLocaleLowerCase()
      && (boundary === undefined || /[\s,.!?;:]/u.test(boundary));
  }) || null;
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
      showReplyError(`Discussion paused, but the pause state could not be saved. ${state.saveStatus || "Resolve the save issue before resuming."}`);
    }
    return;
  }

  const shouldContinue = state.pendingUserMessages.length > 0 || !state.replyError;
  state.paused = false;
  render();
  if (!await saveSessionNow()) {
    state.paused = true;
    state.restartDiscussionAfterTask = false;
    showReplyError(`The discussion could not resume because the session was not saved. ${state.saveStatus || "Resolve the save issue and try again."}`);
    return;
  }
  if (!shouldContinue) return;
  const pendingReply = state.pendingUserMessages.length
    ? null
    : state.priorityReply || (state.replyPending ? null : nextReplyForSavedConversation());
  void ensureDiscussionLoop(pendingReply, true);
}

function roleReplyErrorMessage(error) {
  const detail = error instanceof Error && error.message ? ` ${error.message}` : " Please try again.";
  if (error?.message?.includes("still returned an invalid reply control format after one repair")) {
    return "The model could not format its reply after one repair. No participant reply was added; your existing discussion is preserved. Retry the reply or send a message.";
  }
  if (error?.message?.includes("The course model returned an incomplete reply control marker.")) {
    return "This preview is still running the earlier reply-stream code. Refresh or restart the preview to load the control-marker fix, then retry the reply.";
  }
  if (error?.message === "The server returned an invalid reply control marker.") {
    return "This preview server did not return the reply status required by this version. Refresh or restart the preview, then retry the reply.";
  }
  if (error?.status === 503) return `The participant reply service is unavailable.${detail}`;
  if (error?.status === 404 && error.message?.includes("Unknown or disabled API route:")) {
    return `The participant reply endpoint is not available in the running preview version. Refresh or restart the preview after syncing the latest app code.${detail}`;
  }
  if (error?.status === 404) return `The saved session or participant could not be found.${detail}`;
  return `Could not generate a participant reply.${detail}`;
}

function showReplyError(message, retryTarget = null, diagnostics = null) {
  state.replyError = true;
  state.replyMessage = message;
  state.replyDiagnostics = normalizeReplyDiagnostics(diagnostics);
  state.replyRetryTarget = retryTarget;
  render();
}

async function requestRoleReply(roleId, initial = false, clearDraft = false) {
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
    showReplyError(`This session has reached its ${MAX_CHAT_MESSAGES}-message save limit. The participant reply was not requested.`);
    return false;
  }
  const sessionId = state.sessionId;
  if (!sessionId) {
    showReplyError("This conversation has not been saved yet, so a participant reply cannot be requested.");
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
  state.streamingReply = null;
  state.replyRetryTarget = null;
  render();

  try {
    if (!await saveSessionNow()) {
      if (roleReplyIsCurrent(requestId, sessionId, roleEditVersion)) {
        showReplyError(`The reply was not requested because the conversation could not be saved. ${state.saveStatus || "Resolve the save issue and retry."}`, { roleId, initial, clearDraft });
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
    let waitingForUser = null;
    await requestEventStream(
      roleReplyStreamApi(sessionId),
      jsonOptions("POST", roleId ? { participantId: roleId } : {}, ROLE_REPLY_TIMEOUT_MS),
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
          if (state.streamingReply.body.length + event.text.length > MAX_CHAT_MESSAGE_LENGTH) {
            throw new Error("The participant reply exceeded the message length limit.");
          }
          state.streamingReply.body += event.text;
          const list = appView.querySelector(".message-list");
          const nearBottom = isMessageListNearBottom(list);
          const body = appView.querySelector("[data-streaming-body]");
          if (body) body.textContent += event.text;
          if (list && nearBottom) list.scrollTop = list.scrollHeight;
          await new Promise((resolve) => window.setTimeout(resolve, STREAM_CHUNK_DELAY_MS));
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
    const message = {
      kind: "participant",
      roleId: streamedSpeakerId,
      letter: String.fromCharCode(65 + Math.max(0, roleIndex)),
      body: state.streamingReply.body.trim(),
    };
    if (waitingForUser) message.waitingForUser = true;
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
        showReplyError(`The reply was generated, but your local conversation could not be saved. ${state.saveStatus || "Your local messages remain visible."}`);
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
    showReplyError(roleReplyErrorMessage(error), { roleId, initial, clearDraft }, error?.diagnostics);
    return false;
  } finally {
    if (state.activeReplyRequestId === requestId) {
      state.activeReplyRequestId = null;
      state.replyPending = false;
      state.streamingReply = null;
      if (!state.restoreFailed) render();
    }
  }
}

function discussionLoopIsCurrent(runId) {
  return state.discussionLoopVersion === runId
    && !state.paused
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
        await new Promise((resolve) => window.setTimeout(resolve, DISCUSSION_TURN_DELAY_MS));
        if (!discussionLoopIsCurrent(runId)) return;
        nextReply = state.priorityReply;
        state.priorityReply = null;
        queuedMessage = nextReply ? null : state.pendingUserMessages.shift();
      }
      if (queuedMessage) {
        state.waitingForUser = false;
        if (!state.messages.some((message) => message.id === queuedMessage.id)) {
          const userMessage = { kind: "user", id: queuedMessage.id, body: queuedMessage.body };
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

    const reply = nextReply;
    nextReply = null;
    if (!await requestRoleReply(reply.roleId, reply.initial, reply.clearDraft)) return;
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
  if (state.paused || state.restoreFailed || !state.debateStarted || state.stage !== "debate") return null;

  const runId = ++state.discussionLoopVersion;
  const openingReply = state.priorityReply;
  state.priorityReply = null;
  const task = Promise.resolve()
    .then(() => runDiscussionLoop(runId, openingReply))
    .catch((error) => {
      if (discussionLoopIsCurrent(runId)) {
        showReplyError(`The discussion stopped unexpectedly. ${error instanceof Error ? error.message : "Please retry when ready."}`);
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
      invalidMention: latestMessage.body.includes("@") && !role,
    };
  }
  if (!state.messages.some((message) => message.kind === "participant")) {
    return { roleId: null, initial: true };
  }
  return null;
}

async function startDebate() {
  if (state.replyPending || state.discussionLoopTask || state.discussionStartPending) return false;
  if (!state.question.trim() || !state.roles.length) {
    showReplyError("Add a question and at least one participant before starting the conversation.");
    return false;
  }
  state.discussionStartPending = true;
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
  if (state.stage !== "debate") return false;
  const message = state.composeDraft.trim();
  if (!message) return false;
  const role = roleForChatMessage(message);
  if (message.includes("@") && !role) {
    showReplyError("The @mention does not match a participant. Edit the message or choose a participant and retry.");
    return false;
  }
  const latestMessage = state.messages.at(-1);
  const retryingUnansweredMessage = !state.replyPending
    && state.pendingUserMessages.length === 0
    && latestMessage?.kind === "user"
    && latestMessage.body === message;
  const messagesNeeded = (state.replyPending ? 1 : 0)
    + state.pendingUserMessages.length * 2
    + (retryingUnansweredMessage ? 1 : 2);
  if (state.messages.length + messagesNeeded > MAX_CHAT_MESSAGES) {
    showReplyError(`This session has reached its ${MAX_CHAT_MESSAGES}-message save limit. Your message was not added.`);
    return false;
  }
  state.composeDraft = "";
  state.mentionOpen = false;
  state.replyMessage = "";
  state.replyError = false;
  state.replyDiagnostics = null;
  state.replyRetryTarget = null;

  const messageId = retryingUnansweredMessage ? latestMessage.id || newChatMessageId() : newChatMessageId();
  const pendingMessage = { id: messageId, body: message, participantId: role?.id || null };
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
  if (!await saveSessionNow()) return false;
  if (!state.paused) void ensureDiscussionLoop();
  return true;
}

function retryRoleReply() {
  if (!state.replyRetryTarget || state.replyPending || state.paused) return false;
  const { roleId, initial, clearDraft = false } = state.replyRetryTarget;
  void ensureDiscussionLoop({ roleId, initial, clearDraft });
  return true;
}

function updateRoleSummary(role) {
  for (const node of appView.querySelectorAll("[data-role-name]")) {
    if (node.dataset.roleName === role.id) node.textContent = role.name || "Unnamed participant";
  }
  for (const node of appView.querySelectorAll("[data-role-identity]")) {
    if (node.dataset.roleIdentity === role.id) node.textContent = roleIdentityLabel(role);
  }
  for (const node of appView.querySelectorAll("[data-role-avatar]")) {
    if (node.dataset.roleAvatar === role.id) node.textContent = role.avatar;
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
    scheduleSessionSave();
  }
}

function handleChange(event) {
  if (state.replyPending) return;
  const target = event.target;
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
  if (state.roles.length >= MAX_PARTICIPANTS) return;
  const id = `role-${state.nextRoleId}`;
  state.nextRoleId += 1;
  state.roles.push({
    id,
    avatar: "💡",
    name: "New participant",
    identity: "",
    prompt: "Bring a distinct perspective and respond to specific points.",
    roleType: "Analyst",
  });
  state.roleEditVersion += 1;
  invalidateRoleGeneration("A participant changed; the previous response will be ignored.");
  state.selectedRoleId = id;
  render();
  scheduleSessionSave();
  document.getElementById("role-name")?.focus();
  document.getElementById("role-name")?.select();
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
  const spacer = state.composeDraft && !/\s$/.test(state.composeDraft) ? " " : "";
  updateComposeDraft(`${state.composeDraft}${spacer}@${role.name || "participant"} `);
  state.mentionOpen = false;
  render();
  scheduleSessionSave();
  const input = appView.querySelector("[data-chat-input]");
  input?.focus();
  if (input) input.setSelectionRange(input.value.length, input.value.length);
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

function handleClick(event) {
  if (event.target === summaryDialog && state.summaryOpen) {
    closeSummaryDialog();
    return;
  }
  const button = event.target.closest("button[data-action]");
  if (!button || (!appView.contains(button) && !summaryDialog?.contains(button))) return;
  const action = button.dataset.action;

  if (action === "summary") {
    void requestSummary();
  } else if (action === "regenerate-summary") {
    void regenerateSummary();
  } else if (action === "close-summary") {
    closeSummaryDialog();
  } else if (action === "retry-restore") {
    void restoreSession();
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
    state.mentionOpen = !state.mentionOpen;
    render();
    if (state.mentionOpen) appView.querySelector("[data-action='mention-role']")?.focus();
  } else if (action === "mention-role") {
    insertMention(button.dataset.roleId);
  }
}

function regenerateSummary() {
  if (summaryActionDisabled()) return false;
  void requestSummary(true);
  return true;
}

async function handleSubmit(event) {
  const form = event.target.closest("form[data-form]");
  if (!form || !appView.contains(form)) return;
  event.preventDefault();

  if (form.dataset.form === "question") {
    if (!state.question.trim() || state.roleGenerationPending || state.activeRoleGenerationRequestId !== null) return;
    const question = state.question;
    const requestId = ++state.roleGenerationSequence;
    const roleEditVersion = state.roleEditVersion;
    state.activeRoleGenerationRequestId = requestId;
    state.roleGenerationPending = true;
    state.roleGenerationPhase = "generating";
    state.roleGenerationMessage = "Generating three role profiles. A format repair may be needed.";
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
      state.roleGenerationMessage = "Saving the generated group…";
      render();
      if (!state.sessionId) {
        requestPhase = "session-save";
        const created = await requestJson(
          sessionApi(),
          jsonOptions("POST", { ...sessionSnapshot(), question, participants: generatedRoles }),
        );
        if (!roleGenerationIsCurrent(requestId, question, roleEditVersion)) return;
        state.sessionId = created.session.id;
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
      const enteredParticipants = await setStage("participants", true, requestId);
      if (enteredParticipants) rememberGeneratedNames(generatedRoles);
      return;
    } catch (error) {
      if (!roleGenerationIsCurrent(requestId, question, roleEditVersion)) return;
      state.roleGenerationError = true;
      state.roleGenerationMessage = requestPhase === "generation"
        ? roleGenerationErrorMessage(error)
        : "Could not save the generated group. Please try again.";
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

async function restoreSession() {
  const sessionId = state.sessionId || sessionIdFromLocation();
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
  state.pendingUserMessages = [];
  state.streamingReply = null;
  appView.setAttribute("aria-busy", "true");
  appView.innerHTML = "";
  try {
    const result = await requestJson(sessionApi(`/${encodeURIComponent(sessionId)}`));
    const saved = result.session;
    state.sessionId = saved.id;
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
    state.composeDraft = saved.draft;
    state.groupBuilt = true;
    state.debateStarted = saved.debateStarted;
    state.paused = saved.paused;
    state.selectedRoleId = state.roles[0]?.id || "";
    state.nextRoleId = nextRoleNumber(state.roles);
    const latestMessage = state.messages.at(-1);
    if (state.debateStarted && latestMessage?.kind === "user") {
      const role = latestMessage.participantId
        ? state.roles.find((candidate) => candidate.id === latestMessage.participantId)
        : roleForChatMessage(latestMessage.body);
      if (role) {
        state.replyRetryTarget = { roleId: role.id, initial: false };
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
    state.stage = stageFromLocation();
    if (state.stage === "debate" && !state.debateStarted) state.stage = "participants";
    if (state.stage !== stageFromLocation()) {
      window.history.replaceState({ debateStage: state.stage }, "", stageUrl(state.stage));
    }
    state.saveStatus = "";
    state.saveFailed = false;
    render();
    if (state.stage === "debate" && state.debateStarted && !state.paused && !state.replyError) {
      void ensureDiscussionLoop(nextReplyForSavedConversation(), true);
    }
    return true;
  } catch (error) {
    state.restoreFailed = true;
    state.restoreMessage = error instanceof Error ? error.message : "Try again or start a new question.";
    render();
    return false;
  } finally {
    appView.removeAttribute("aria-busy");
  }
}

async function handlePopState() {
  if (state.activeRoleGenerationRequestId !== null) {
    invalidateRoleGeneration("Navigation changed; the previous response will be ignored.");
  }
  if (state.activeReplyRequestId !== null) {
    state.replySequence += 1;
    state.activeReplyRequestId = null;
    state.replyPending = false;
  }
  const requestedId = sessionIdFromLocation();
  if (requestedId && requestedId !== state.sessionId) {
    if (state.sessionId && state.saveTimer !== null) await saveSessionNow();
    state.sessionId = requestedId;
    await restoreSession();
    return;
  }
  if (!requestedId && state.sessionId) {
    if (state.restoreFailed) {
        state.sessionId = "";
        resetSummaryForSession("");
      state.restoreFailed = false;
      state.restoreMessage = "";
      state.question = "";
      state.roles = DEFAULT_ROLES.map((role) => ({ ...role }));
      state.roleEditVersion += 1;
      state.selectedRoleId = "role-1";
      state.nextRoleId = 4;
      state.messages = [];
      state.waitingForUser = false;
      state.pendingUserMessages = [];
      state.unsavedMessageIds.clear();
      state.streamingReply = null;
      state.composeDraft = "";
      state.groupBuilt = false;
      state.debateStarted = false;
      state.paused = false;
      state.replyMessage = "";
      state.replyError = false;
      state.replyDiagnostics = null;
      state.replyRetryTarget = null;
      state.saveStatus = "";
      state.saveFailed = false;
    } else {
      if (state.saveTimer !== null) await saveSessionNow();
      window.history.replaceState({ debateStage: "question" }, "", stageUrl("question"));
    }
  } else if (state.saveTimer !== null) {
    await saveSessionNow();
  }
  const requested = stageFromLocation();
  if (requested !== "question" && !state.question.trim()) {
    state.stage = "question";
    window.history.replaceState({ debateStage: "question" }, "", stageUrl("question"));
  } else {
    state.stage = requested;
  }
  render();
  if (state.stage === "debate" && state.debateStarted && !state.paused && !state.replyError) {
    void ensureDiscussionLoop(nextReplyForSavedConversation(), true);
  }
}

function handlePageHide() {
  if (state.saveTimer !== null) void saveSessionNow();
}

try {
  runtime.readConfig();
  appView.addEventListener("input", handleInput);
  appView.addEventListener("change", handleChange);
  appView.addEventListener("click", handleClick);
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
  }
} catch (error) {
  runtime.showFatalError(error);
}
