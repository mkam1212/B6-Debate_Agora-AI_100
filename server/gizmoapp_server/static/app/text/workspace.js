import { esc, icon, agentName, avatar, avatarColorPicker, randomAvatarColor, syncAvatarColorPreview } from "./ui.js?v=allowance-78";
import { createAccount } from "./account.js?v=delivery-80";
import { collectSources, trustedCitations } from "./sources.js?v=web-75";
import { createAttachments } from "./attachments.js?v=delivery-80";
import { isDialogBackdropClick, updateRecentHistory } from "./interaction-utils.js?v=nav-73";

const HISTORY_KEY = "agora-session-history-v1";
const PROFILE_KEY = "agora-local-profile-v1";
const LOGO_URL = new URL("./assets/debate-agora-wordmark.png", import.meta.url).href;
const read = (key, fallback) => {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
};
const write = (key, value) => {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; } catch { return false; }
};
const STARTERS = [
  ["calendar", "Should we move to a four-day workweek?"],
  ["brush", "Where should we draw the line on AI in creative work?"],
  ["tram", "Should public transit be free?"],
  ["rocket", "Should we launch early or wait for a more polished product?"],
];

export function createWorkspace({ basePath, appPath, getState, beforeNavigate, beforeSignOut, sessionEnded, usageChanged, useQuestion, attachmentsChanged, saveAgent, removeAgent, refresh }) {
  const sidebar = document.getElementById("workspace-sidebar");
  const dialog = document.getElementById("workspace-dialog");
  const toast = document.getElementById("workspace-toast");
  const popover = document.createElement("div");
  popover.id = "workspace-popover";
  popover.setAttribute("popover", "auto");
  document.body.append(popover);
  let profile = read(PROFILE_KEY, { name: "Guest" });
  if (typeof profile?.name !== "string") profile = { name: "Guest" };
  let toastTimer;
  let historyItems = [];
  let historyUserId = "";
  let historyTask = null;
  let historyHasMore = false;
  let historyOffset = 0;
  let searchTimer;
  let searchVersion = 0;
  let searchItems = null;
  let searchArchived = false;
  let searchOffset = 0;
  let searchHasMore = false;
  let dialogTrigger;
  let popoverTrigger;
  let dismissedByTrigger = null;
  const mobileSidebar = window.matchMedia("(max-width: 719px)");
  const mobileDetails = window.matchMedia("(max-width: 1099px)");
  function activeDrawer() {
    if (mobileSidebar.matches && document.body.classList.contains("sidebar-open")) return "sidebar";
    if (mobileDetails.matches && document.body.classList.contains("details-open")) return "details";
    return "";
  }
  function setDrawer(name = "", restoreFocus = true) {
    const previous = activeDrawer();
    closePopover();
    document.body.classList.toggle("sidebar-open", name === "sidebar");
    document.body.classList.toggle("details-open", name === "details");
    document.querySelector('[data-workspace="menu"]')?.setAttribute("aria-expanded", String(name === "sidebar"));
    document.querySelector('[data-workspace="panels"]')?.setAttribute("aria-expanded", String(name === "details"));
    if (name) {
      const drawer = document.querySelector(name === "sidebar" ? "#workspace-sidebar" : "#debate-details");
      drawer?.querySelector(".drawer-close")?.focus({ preventScroll: true });
    } else if (restoreFocus && previous) {
      document.querySelector(previous === "sidebar" ? '[data-workspace="menu"]' : '[data-workspace="panels"]')?.focus({ preventScroll: true });
    }
  }
  mobileSidebar.addEventListener("change", event => {
    if (!event.matches && document.body.classList.contains("sidebar-open")) setDrawer("", false);
  });
  mobileDetails.addEventListener("change", event => {
    if (!event.matches && document.body.classList.contains("details-open")) setDrawer("", false);
  });
  const account = createAccount({
    basePath, open, dialog, notify, changed: refresh, beforeSignOut, usageChanged,
    afterSignOut: () => { historyItems = []; closePopover(); dialog.close(); sessionEnded?.(); refresh(); window.location.replace(appPath); },
  });
  const attachments = createAttachments({ basePath, getState, notify, open, dialog, changed: (saveNow = false) => { refresh(); return attachmentsChanged(saveNow); } });
  const currentProfile = () => account.user() || profile;
  const historyKey = () => account.user() ? `${HISTORY_KEY}:${account.user().id}` : HISTORY_KEY;
  const sources = () => collectSources(getState().messages, getState().roles);

  function notify(message) {
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 4000);
  }
  function history() { return account.user() ? historyItems : []; }
  async function sessionRequest(path = "", method = "GET", data) {
    const response = await fetch(`${basePath}api/debate/sessions${path}`, {
      method, credentials: "same-origin", cache: "no-store",
      headers: { Accept: "application/json", ...(data ? { "Content-Type": "application/json" } : {}) },
      ...(data ? { body: JSON.stringify(data) } : {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Couldn’t load your conversations. Try again.");
    return result;
  }
  async function syncHistory(more = false) {
    if (!account.user()) { historyItems = []; historyUserId = ""; return; }
    if (historyTask) return historyTask;
    const userId = account.user().id;
    historyUserId = userId;
    historyTask = (async () => {
      const old = read(historyKey(), []);
      if (!more && Array.isArray(old) && old.length) {
        const ids = old.filter(item => /^[a-f0-9]{32}$/.test(item?.id)).slice(0, 40).map(item => item.id);
        if (ids.length) {
          const { imported } = await sessionRequest("/import", "POST", { sessionIds: ids });
          if (account.user()?.id !== userId) return;
          const state = getState();
          if (imported[state.sessionId]) {
            state.sessionId = imported[state.sessionId];
            const url = new URL(location.href); url.searchParams.set("session", state.sessionId); url.searchParams.delete("share");
            window.history.replaceState(window.history.state, "", url);
          }
        }
        localStorage.removeItem(historyKey());
      }
      const result = await sessionRequest(`?offset=${more ? historyOffset : 0}`);
      if (account.user()?.id !== userId) return;
      historyItems = more ? [...historyItems, ...result.sessions] : result.sessions;
      historyItems = [...new Map(historyItems.map(item => [item.id, item])).values()];
      historyHasMore = result.hasMore; historyOffset = result.nextOffset;
      refresh();
    })().catch(() => { historyUserId = ""; }).finally(() => { historyTask = null; });
    return historyTask;
  }
  function recordSession(saved) {
    if (saved.archived) { historyItems = historyItems.filter(item => item.id !== saved.id); return; }
    if (saved.access && saved.access !== "owner") return;
    const item = { id: saved.id, question: saved.title || saved.question, started: saved.debateStarted, createdAt: saved.createdAt, updated: saved.updatedAt };
    const existing = historyItems.findIndex(value => value.id === saved.id);
    if (existing >= 0) historyItems[existing] = item;
    else historyItems.push(item);
    historyItems.sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)) || b.id.localeCompare(a.id));
  }
  const ready = account.ready.then(() => syncHistory());
  function closePopover() {
    if (popover.matches(":popover-open")) popover.hidePopover();
  }
  document.addEventListener("pointerdown", event => {
    dismissedByTrigger = popover.matches(":popover-open") && event.target.closest("[data-workspace]") === popoverTrigger ? popoverTrigger : null;
  }, true);
  window.addEventListener("resize", closePopover);
  function showPopover(anchor, body, className = "") {
    if (dismissedByTrigger === anchor) { dismissedByTrigger = null; closePopover(); return; }
    if (popover.matches(":popover-open") && popoverTrigger === anchor) { closePopover(); return; }
    const keyboard = anchor.matches(":focus-visible");
    closePopover();
    popoverTrigger = anchor;
    popover.className = `workspace-popover ${className}`;
    popover.innerHTML = body;
    popover.style.visibility = "hidden";
    popover.showPopover();
    const rect = anchor.getBoundingClientRect();
    const size = popover.getBoundingClientRect();
    const left = Math.max(12, Math.min(rect.right - size.width, innerWidth - size.width - 12));
    const top = rect.bottom + size.height + 8 <= innerHeight - 12 ? rect.bottom + 8 : Math.max(12, rect.top - size.height - 8);
    popover.style.left = `${left}px`;
    popover.style.top = `${top}px`;
    popover.style.visibility = "visible";
    anchor.setAttribute("aria-expanded", "true");
    if (keyboard) popover.querySelector("input, button:enabled, a[href]")?.focus();
  }
  popover.addEventListener("toggle", event => {
    if (event.newState === "closed") popoverTrigger?.setAttribute("aria-expanded", "false");
  });
  popover.addEventListener("keydown", event => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key) || event.target.matches("input")) return;
    const buttons = [...popover.querySelectorAll("button:enabled, a[href]")];
    if (!buttons.length) return;
    event.preventDefault();
    const index = buttons.indexOf(document.activeElement);
    buttons[(index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length].focus();
  });
  async function navigate(path) {
    if (!await beforeNavigate()) return;
    closePopover();
    window.location.assign(path);
  }
  function renderSidebar() {
    const state = getState();
    const profile = currentProfile();
    const signedIn = Boolean(account.user());
    const usage = account.usage();
    if (signedIn && historyUserId !== account.user().id && !historyTask) void syncHistory();
    sidebar.innerHTML = `<div class="drawer-heading sidebar-heading"><a class="brand" href="${esc(basePath)}" data-workspace="home" aria-label="Debate Agora home"><img src="${esc(LOGO_URL)}" alt="Debate Agora" width="2172" height="724"></a><button class="icon-button drawer-close" data-workspace="close-drawers" aria-label="Close sidebar">${icon("close")}</button></div>
      <button class="new-debate" data-workspace="new">${icon("plus")}New debate</button>
      ${signedIn ? `<nav class="primary-nav" aria-label="Workspace"><button data-workspace="search">${icon("search")}<span>Search</span><kbd>⌘ K</kbd></button></nav>` : ""}
      ${signedIn ? `<section class="recent-section" aria-labelledby="recent-title"><h2 id="recent-title">Recent</h2>
        <nav class="recent-list" aria-label="Recent debates">${history().map(item => `<a class="recent-item${state.sessionId === item.id ? " is-current" : ""}" href="${esc(appPath)}/${item.started ? "debate" : "participants"}?session=${encodeURIComponent(item.id)}" data-workspace="session" ${state.sessionId === item.id ? 'aria-current="page"' : ""} title="${esc(item.question)}"><i class="recent-dot" aria-hidden="true"></i><span>${esc(item.question)}</span></a>`).join("") || '<p class="sidebar-empty">Your debates will appear here.</p>'}${historyHasMore ? '<button class="recent-more" data-workspace="history-more">Show more</button>' : ''}</nav>
      </section><div class="sidebar-account"><div class="sidebar-benefits">
        <button data-account="usage" aria-label="Usage: ${usage ? `${usage.remaining} AI replies left` : "loading"}">${icon("usage")}<span>Usage</span><small>${usage ? `${usage.remaining} left` : "…"}</small></button>
        <button data-account="invite">${icon("users")}<span>Invite friends</span></button>
      </div><button class="profile-control" data-workspace="profile-menu" aria-label="Profile: ${esc(profile.name)}" aria-haspopup="menu">${avatar(profile)}<span>${esc(profile.name)}</span>${icon("down")}</button></div>` : `<div class="sidebar-spacer"></div><div class="sidebar-login"><button class="login-button" data-account="sign-in">${icon("login")}<span>Log in</span></button></div>`}`;
  }
  function open(title, body, className = "") {
    closePopover();
    dialogTrigger = document.activeElement;
    dialog.className = `workspace-dialog ${className}`;
    dialog.innerHTML = `<div class="dialog-heading"><h2 id="workspace-dialog-title">${esc(title)}</h2><button class="icon-button" data-workspace="close" aria-label="Close">${icon("close")}</button></div>${body}`;
    if (!dialog.open) dialog.showModal();
    [...dialog.querySelectorAll("input, textarea, button[data-workspace]:not([data-workspace=close])")]
      .find(control => !control.closest("[inert]") && control.getClientRects().length)?.focus({ preventScroll: true });
  }
  dialog.addEventListener("close", () => {
    if (dialogTrigger?.isConnected) dialogTrigger.focus({ preventScroll: true });
    else document.querySelector("[data-chat-input], [data-field=question]")?.focus({ preventScroll: true });
  });
  function waitForAgentEditor() {
    if (!dialog.open || !dialog.classList.contains("agent-dialog")) return Promise.resolve();
    return new Promise(resolve => dialog.addEventListener("close", resolve, { once: true }));
  }
  function searchResults(query = "") {
    return matchingHistory(query).map((item, index) => {
      const date = new Date(item.updated);
      const today = new Date();
      const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
      const label = !Number.isFinite(date.getTime()) ? "" : date.toDateString() === today.toDateString() ? "Today" : date.toDateString() === yesterday.toDateString() ? "Yesterday" : new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(date);
      return `<a class="search-result${!query && !index ? " is-active" : ""}" data-workspace="session" href="${esc(appPath)}/${item.started ? "debate" : "participants"}?session=${encodeURIComponent(item.id)}" title="${esc(item.question)}">${icon("chat")}<span>${esc(item.question)}</span><time ${label ? `datetime="${date.toISOString()}"` : ""}>${esc(label)}</time></a>`;
    }).join("") || '<p class="empty-note">No matching debates.</p>';
  }
  function matchingHistory(query = "") {
    return searchItems ?? history().filter(item => item.question.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  }
  const searchCount = count => `${count} ${count === 1 ? "conversation" : "conversations"}`;
  function showSearch() {
    if (!account.user()) { void account.ensureSignedIn().then(ok => { if (ok) showSearch(); }); return; }
    searchItems = null; searchArchived = false; searchOffset = 0; searchHasMore = false;
    open("Search", `<label class="search-field">${icon("search")}<input type="search" data-history-search placeholder="Search your debates" aria-label="Search your debates" autocomplete="off"></label><div class="search-results-heading"><span data-search-heading>Recent debates</span><button type="button" class="dialog-text-button" data-workspace="search-archive">Archived</button></div><div class="search-results" data-search-results aria-live="polite">${searchResults()}</div><p class="search-footnote"><span data-search-count>${searchCount(history().length)}</span><kbd>esc</kbd></p>`, "polished-dialog search-dialog");
    dialog.classList.toggle("is-empty", history().length === 0);
  }
  function renderSuggestions() {
    return `<ul class="question-suggestions" id="question-suggestions" aria-label="Suggested questions">${STARTERS.map(([symbol, question], index) => `<li><button type="button" data-workspace="starter" data-index="${index}" ${getState().roleGenerationPending ? "disabled" : ""}><span class="suggestion-icon suggestion-icon-${symbol}" aria-hidden="true">${icon(symbol)}</span><span class="suggestion-label">${esc(question)}</span>${icon("right", "suggestion-arrow")}</button></li>`).join("")}</ul>`;
  }
  function showAgentEditor(roleId, add = false) {
    const state = getState();
    const role = add ? { name: "", identity: "", prompt: "", avatarColor: randomAvatarColor(state.roles.map(item => item.avatarColor)) } : state.roles.find(item => item.id === roleId) || state.roles[0];
    if (!role) return;
    const index = add ? state.roles.length : state.roles.indexOf(role);
    open(add ? "Add agent" : "Edit agent", `<form data-workspace-form="agent" data-role-id="${add ? "" : esc(role.id)}">
      <div class="agent-editor-identity"><span data-avatar-preview>${avatar({ ...role, name: role.name || "+" }, index)}</span><div><strong>${add ? "A new perspective" : esc(agentName(role))}</strong><span>${add ? "Give this agent a point of view." : esc(role.identity || "Debate participant")}</span></div></div>
      <label class="field">Name<input name="name" value="${esc(role.name)}" maxlength="48" placeholder="e.g. Maya" required autocomplete="off"></label>
      <label class="field">Perspective<input name="identity" value="${esc(role.identity || "")}" maxlength="96" placeholder="e.g. Optimist"></label>
      ${avatarColorPicker(role, index, { preview: false })}
      <label class="field">Personality<textarea name="prompt" rows="6" maxlength="1000" placeholder="How should this agent think and contribute?" required>${esc(role.prompt)}</textarea><span class="field-help">Describe their approach, expertise, or tone.</span></label>
      <p class="form-error" data-agent-error role="alert" hidden></p>
      <div class="dialog-actions">${!add ? `<button type="button" class="icon-button remove-agent-button" data-workspace="delete-agent" data-role-id="${esc(role.id)}" aria-label="Remove agent" title="Remove agent" ${state.roles.length <= 1 ? "disabled" : ""}>${icon("trash")}</button>` : ""}<span class="action-spacer"></span><button type="button" class="button button-secondary" data-workspace="close">Cancel</button><button type="submit" class="button button-primary">${add ? "Add agent" : "Save changes"}</button></div>
    </form>`, "agent-dialog");
  }
  function showSources() {
    const items = sources();
    open("Sources", items.length ? `<div class="web-source-list">${items.map(item => {
      const title = item.title || item.domain;
      const authors = [...item.authors].map(name => agentName({ name })).join(", ");
      return `<a class="web-source" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer" title="${esc(title)}">${icon(item.format ? "document" : "globe", "source-type-icon")}<div class="web-source-copy"><strong>${esc(title)}</strong><span class="source-meta"><span class="source-domain" title="${esc(item.domain)}">${esc(item.domain)}${item.format ? ` · ${esc(item.format)}` : ""}</span><span class="source-meta-separator" aria-hidden="true">·</span><span class="source-authors" title="${esc(authors)}">${esc(authors)}</span></span></div>${icon("external", "source-open-icon")}</a>`;
    }).join("")}</div>` : `<div class="sources-empty"><p>Sources will appear when an agent cites them.</p></div>`, "polished-dialog sources-dialog");
    if (items.length) dialog.querySelector(".dialog-heading h2").insertAdjacentHTML("beforeend", ` <span class="dialog-count">${items.length}</span>`);
  }
  async function showShare() {
    const state = getState();
    if (!state.sessionId) { notify("Start a debate before sharing it."); return; }
    if (state.readOnly) { notify("This is a read-only conversation."); return; }
    try {
      const status = await sessionRequest(`/${state.sessionId}/sharing`);
      state.sharingEnabled = status.enabled; refresh();
      open("Share debate", `<div class="share-content"><h3 class="share-topic" title="${esc(state.question)}">${esc(state.question)}</h3><p class="share-access">${icon("link")}Only you can edit. Shared links are view-only.</p><div data-share-controls>${status.enabled ? '<p class="dialog-note">A viewing link is active.</p>' : '<p class="dialog-note">Private until you create a viewing link.</p>'}<button class="dialog-primary share-copy-button" data-workspace="create-share">${icon("link")}<span>${status.enabled ? 'Create a new link' : 'Create viewing link'}</span></button>${status.enabled ? '<button class="dialog-text-button share-revoke" data-workspace="revoke-share">Turn off sharing</button>' : ''}</div>${state.legacyLinkActive ? '<div class="legacy-sharing-note"><p>An older viewing link still opens the original conversation.</p><button class="dialog-text-button" data-workspace="retire-legacy">Turn off the older link</button></div>' : ''}</div><div class="share-footer"><button class="dialog-text-button" type="button" data-workspace="export">${icon("download")}Download transcript</button><button class="dialog-text-button" data-workspace="manage">Manage debate</button></div>`, "polished-dialog share-dialog");
    } catch (error) { notify(error.message); }
  }
  function showManage() {
    const state = getState();
    open("Manage debate", `<form data-workspace-form="rename"><label class="field">Title<input name="question" maxlength="2000" required value="${esc(state.title || state.question)}"></label><div class="dialog-actions"><button class="dialog-primary" type="submit">Save title</button></div></form><div class="manage-actions"><button class="dialog-text-button" data-workspace="archive">${state.archived ? 'Restore to Recent' : 'Archive debate'}</button><button class="dialog-text-button destructive" data-workspace="confirm-delete">Delete debate</button></div>`, "polished-dialog manage-dialog");
  }
  function exportTranscript() {
    const state = getState();
    const content = `# ${state.title || state.question}\n\n${state.summaryText ? `## Takeaways\n\n${state.summaryText}\n\n## Conversation\n\n` : ''}${state.messages.map(message => {
      const role = state.roles.find(item => item.id === message.roleId);
      const references = trustedCitations(message.citations).map(citation => `- ${citation.title || new URL(citation.url).hostname}: ${citation.url}`).join("\n");
      return `**${message.kind === "user" ? state.readOnly ? "Host" : currentProfile().name : agentName(role)}**\n\n${message.body}${references ? `\n\nSources:\n${references}` : ''}`;
    }).join("\n\n---\n\n")}\n`;
    const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = "debate-agora-transcript.md"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  document.addEventListener("click", async event => {
    const accountButton = event.target.closest("[data-account]");
    if (accountButton && !accountButton.disabled) { event.preventDefault(); closePopover(); await account.handleAction(accountButton); return; }
    const button = event.target.closest("[data-workspace]");
    if (!button || button.disabled) return;
    event.preventDefault();
    const action = button.dataset.workspace;
    if (action === "home") await navigate(basePath);
    else if (action === "new") await navigate(appPath);
    else if (action === "session") await navigate(button.href);
    else if (action === "search") showSearch();
    else if (action === "sources") showSources();
    else if (action === "share") void showShare();
    else if (action === "history-more") { button.disabled = true; await syncHistory(true); }
    else if (action === "search-more") { button.disabled = true; await runSearch(true); }
    else if (action === "search-archive") { searchArchived = !searchArchived; button.textContent = searchArchived ? "Recent" : "Archived"; void runSearch(); }
    else if (action === "manage") showManage();
    else if (action === "copy-legacy") {
      if (!await account.ensureSignedIn()) return;
      try { const result = await sessionRequest("/import", "POST", { sessionIds: [getState().sessionId] }); const id = result.imported[getState().sessionId]; if (id) window.location.assign(`${appPath}/debate?session=${id}`); }
      catch (error) { notify(error.message); }
    }
    else if (action === "create-share") {
      button.disabled = true;
      try {
        const result = await sessionRequest(`/${getState().sessionId}/sharing`, "POST", {});
        getState().sharingEnabled = true; refresh();
        const url = new URL(`${appPath}/debate`, location.origin); url.searchParams.set("session", getState().sessionId); url.searchParams.set("share", result.token);
        dialog.querySelector("[data-share-controls]").innerHTML = `<div class="share-link-field">${icon("link")}<input data-share-link aria-label="View-only link" readonly value="${esc(url.href)}"></div><button class="dialog-primary share-copy-button" data-workspace="copy-link" data-copy-value="${esc(url.href)}">${icon("copy")}<span>Copy viewing link</span></button><button class="dialog-text-button share-revoke" data-workspace="revoke-share">Turn off sharing</button>`;
        notify("Viewing link created. Previous links no longer work.");
      } catch (error) { notify(error.message); button.disabled = false; }
    }
    else if (action === "retire-legacy") {
      try { await sessionRequest(`/${getState().sessionId}/manage`, "PATCH", { retireLegacyLink: true }); getState().legacyLinkActive = false; notify("Older link turned off. Your private copy is kept."); void showShare(); }
      catch (error) { notify(error.message); }
    }
    else if (action === "revoke-share") {
      try { await sessionRequest(`/${getState().sessionId}/sharing`, "DELETE"); getState().sharingEnabled = false; refresh(); notify("Sharing turned off."); void showShare(); }
      catch (error) { notify(error.message); }
    }
    else if (action === "archive") {
      if (!await beforeNavigate()) return;
      try { await sessionRequest(`/${getState().sessionId}/manage`, "PATCH", { archived: !getState().archived }); window.location.assign(appPath); }
      catch (error) { notify(error.message); }
    }
    else if (action === "confirm-delete") open("Delete this debate?", '<p class="dialog-note">The conversation and its shared links will be removed permanently.</p><div class="dialog-actions"><button class="button button-secondary" data-workspace="manage">Cancel</button><button class="dialog-primary destructive" data-workspace="delete">Delete debate</button></div>', "polished-dialog manage-dialog");
    else if (action === "delete") {
      if (!await beforeNavigate()) return;
      try { await sessionRequest(`/${getState().sessionId}/manage`, "DELETE"); window.location.assign(appPath); }
      catch (error) { notify(error.message); }
    }
    else if (action === "mode") showPopover(button, `<div class="popover-item selected">${icon("check")}Solo</div><button class="popover-item" disabled>${icon("users")}Multiplayer<span>Coming later</span></button>`);
    else if (action === "profile-menu") showPopover(button, account.menu(), "account-popover");
    else if (action === "close") dialog.close();
    else if (action === "export") { closePopover(); exportTranscript(); }
    else if (action === "copy-message") {
      const message = getState().messages[Number(button.dataset.index)];
      if (message) {
        try { await navigator.clipboard.writeText(message.body); button.innerHTML = icon("check"); button.setAttribute("aria-label", "Copied"); setTimeout(() => { if (button.isConnected) { button.innerHTML = icon("copy"); button.setAttribute("aria-label", "Copy text"); } }, 1800); }
        catch { notify("Select the message text to copy it."); }
      }
    }
    else if (action === "menu") {
      setDrawer(activeDrawer() === "sidebar" ? "" : "sidebar");
    }
    else if (action === "panels") {
      setDrawer(activeDrawer() === "details" ? "" : "details");
    }
    else if (action === "close-drawers") setDrawer();
    else if (action === "starter") {
      const starter = STARTERS[Number(button.dataset.index)];
      if (starter && await useQuestion(starter[1]) && dialog.open) dialog.close();
    }
    else if (action === "copy-link") {
      try { await navigator.clipboard.writeText(button.dataset.copyValue); button.innerHTML = `${icon("check")}<span>Copied</span>`; button.setAttribute("aria-label", "Link copied"); }
      catch { (dialog.querySelector("[data-share-link]") || popover.querySelector("[data-share-link]"))?.select(); notify("Select and copy the link."); }
    }
    else if (action === "delete-agent") {
      if (await removeAgent(button.dataset.roleId)) dialog.close();
    }
  });
  dialog.addEventListener("click", event => { if (isDialogBackdropClick(event, dialog)) dialog.close(); });
  async function runSearch(more = false) {
    const input = dialog.querySelector("[data-history-search]");
    if (!input) return;
    const query = input.value;
    const version = ++searchVersion;
    try {
      const result = await sessionRequest(`?q=${encodeURIComponent(query)}&archived=${searchArchived ? 1 : 0}&offset=${more ? searchOffset : 0}`);
      if (version !== searchVersion || !input.isConnected) return;
      searchItems = more ? [...searchItems, ...result.sessions] : result.sessions;
      searchOffset = result.nextOffset; searchHasMore = result.hasMore;
      dialog.classList.toggle("is-empty", !searchItems.length);
      dialog.querySelector("[data-search-results]").innerHTML = searchResults(query) + (searchHasMore ? '<button class="dialog-text-button search-more" data-workspace="search-more">Show more</button>' : '');
      dialog.querySelector("[data-search-heading]").textContent = searchArchived ? "Archived debates" : query.trim() ? "Results" : "Recent debates";
      dialog.querySelector("[data-search-count]").textContent = `${searchItems.length}${result.hasMore ? '+' : ''} conversations`;
    } catch { if (version === searchVersion) notify("Couldn’t search your conversations. Try again."); }
  }
  dialog.addEventListener("input", event => {
    if (event.target.matches("[data-history-search]")) {
      clearTimeout(searchTimer); searchVersion += 1;
      searchTimer = setTimeout(() => runSearch(), 160);
    }
    if (event.target.matches('[name="name"], [data-avatar-color-choice]')) syncAvatarColorPreview(event.target.closest('[data-workspace-form="agent"]'));
  });
  dialog.addEventListener("submit", async event => {
    const accountForm = event.target.closest("[data-account-form]");
    if (accountForm) { event.preventDefault(); await account.handleSubmit(accountForm); return; }
    const form = event.target.closest("[data-workspace-form]");
    if (!form) return;
    event.preventDefault();
    const data = new FormData(form);
    if (form.dataset.workspaceForm === "rename") {
      if (!await beforeNavigate()) return;
      try { await sessionRequest(`/${getState().sessionId}/manage`, "PATCH", { title: String(data.get("question")) }); window.location.reload(); }
      catch (error) { notify(error.message); }
      return;
    }
    if (form.dataset.workspaceForm === "agent") {
      const error = form.querySelector("[data-agent-error]");
      const values = { name: String(data.get("name")).trim(), identity: String(data.get("identity")).trim(), prompt: String(data.get("prompt")).trim(), avatarColor: String(data.get("avatarColor")) };
      if (!values.name || !values.prompt) { error.textContent = "Add a name and instructions."; error.hidden = false; return; }
      const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
      try {
        if (await saveAgent(form.dataset.roleId, values)) dialog.close();
        else { error.textContent = "Finish the current reply before editing agents."; error.hidden = false; }
      } catch { error.textContent = "Could not save this agent. Please try again."; error.hidden = false; }
      finally { submit.disabled = false; }
    }
  });
  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k" && !dialog.open) { event.preventDefault(); showSearch(); }
    if (event.key === "Escape") {
      if (document.querySelector("dialog[open]") || popover.matches(":popover-open")) return;
      if (activeDrawer()) { event.preventDefault(); setDrawer(); }
    }
    if (event.key === "Tab" && activeDrawer() && !document.querySelector("dialog[open]") && !popover.matches(":popover-open")) {
      const drawer = document.querySelector(activeDrawer() === "sidebar" ? "#workspace-sidebar" : "#debate-details");
      const controls = [...drawer.querySelectorAll('a[href], button:enabled, input:enabled, textarea:enabled, select:enabled, [tabindex="0"]')].filter(control => control.getClientRects().length);
      const first = controls[0];
      const last = controls.at(-1);
      if (!drawer.contains(document.activeElement) || (event.shiftKey ? document.activeElement === first : document.activeElement === last)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus({ preventScroll: true });
      }
    }
  });
  return { renderSidebar, renderSuggestions, notify, sources, profile: currentProfile, isSignedIn: () => Boolean(account.user()), ensureSignedIn: account.ensureSignedIn, ensureReplyAvailable: account.ensureReplyAvailable, refreshUsage: account.refreshUsage, ready, recordSession, syncHistory, showSources, showAgentEditor, waitForAgentEditor, renderAttachments: attachments.render, uploading: attachments.busy };
}
