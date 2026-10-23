import { esc, icon } from "./ui.js?v=navigation-87";

const sorted = items => [...new Map(items.map(item => [item.id, item])).values()]
  .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)) || b.id.localeCompare(a.id));
const sortedFolders = items => [...items].sort((a, b) =>
  String(b.latestConversationAt || b.createdAt || "").localeCompare(String(a.latestConversationAt || a.createdAt || "")) || b.id.localeCompare(a.id));
const emptyGroup = () => ({ items: [], offset: 0, hasMore: false, loaded: false, busy: false, error: false, request: 0 });

// Measure the visible height, including an interrupted animation, before sidebar updates.
export function captureFolderPanels(root) {
  return new Map([...root.querySelectorAll('.folder-disclosure')].map(panel => [panel.id, panel.getBoundingClientRect().height]));
}
export function animateFolderPanels(root, previous, reducedMotion = false) {
  if (reducedMotion) return;
  for (const panel of root.querySelectorAll('.folder-disclosure')) {
    const from = previous.get(panel.id), to = panel.getBoundingClientRect().height;
    if (from === undefined || Math.abs(from - to) < .5 || !panel.animate) continue;
    panel.animate([{ height: `${from}px` }, { height: `${to}px` }], { duration: 180, easing: 'cubic-bezier(.2,.7,.2,1)' });
  }
}

// Filing has its own state so it never changes a conversation's revision or reply loop.
export function createFolderStore({ request, userId, changed = () => {} }) {
  let owner = null, generation = 0, folders = [], task = null;
  const groups = new Map(), overrides = new Map();
  function reset() {
    owner = userId(); generation += 1; folders = []; task = null; groups.clear(); overrides.clear();
  }
  function group(id = null) {
    const key = id || "none";
    if (!groups.has(key)) groups.set(key, emptyGroup());
    return groups.get(key);
  }
  function current(version) { return generation === version && owner === userId(); }
  async function loadGroup(id = null, more = false) {
    const page = group(id), version = generation;
    if (!owner || page.busy) return;
    const sequence = ++page.request;
    const valid = () => current(version) && page.request === sequence;
    page.busy = true; page.error = false; changed();
    try {
      const result = await request(`sessions?folder=${encodeURIComponent(id || "none")}&offset=${more ? page.offset : 0}`);
      if (!valid()) return;
      page.items = sorted(more ? [...page.items, ...result.sessions] : result.sessions);
      page.offset = result.nextOffset; page.hasMore = result.hasMore; page.loaded = true;
    } catch (error) { if (valid()) page.error = true; }
    finally { if (valid()) { page.busy = false; changed(); } }
  }
  async function load() {
    if (owner !== userId()) reset();
    if (!owner) return;
    if (task) return task;
    const version = generation;
    task = (async () => {
      const result = await request("folders");
      if (!current(version)) return;
      folders = sortedFolders(result.folders);
      for (const [id, folderId] of overrides) if (folderId && !folders.some(folder => folder.id === folderId)) overrides.set(id, null);
      await loadGroup();
      changed();
    })().finally(() => { if (current(version)) task = null; });
    return task;
  }
  function observe(saved, created = false) {
    if (!owner || owner !== userId() || (saved.access && saved.access !== "owner")) return;
    const folderId = overrides.has(saved.id) ? overrides.get(saved.id) : saved.folderId || null;
    const item = { id: saved.id, question: saved.title || saved.question, started: saved.debateStarted,
      createdAt: saved.createdAt, updated: saved.updatedAt, folderId, archived: Boolean(saved.archived) };
    let previous, known = false;
    for (const page of groups.values()) {
      const existing = page.items.find(value => value.id === saved.id);
      if (existing) { previous = existing.folderId || null; known = true; }
      page.items = page.items.filter(value => value.id !== saved.id);
    }
    if (!saved.archived) {
      const page = group(folderId);
      page.items = sorted([...page.items, item]);
    }
    if (!known && created && !saved.archived && folderId) {
      const folder = folders.find(value => value.id === folderId);
      if (folder) {
        folder.count += 1;
        if (!folder.latestConversationAt || saved.createdAt > folder.latestConversationAt) folder.latestConversationAt = saved.createdAt;
        folders = sortedFolders(folders);
      }
    } else if (known && (saved.archived || previous !== folderId)) {
      const old = folders.find(value => value.id === previous);
      if (old) old.count = Math.max(0, old.count - 1);
      const next = !saved.archived && folders.find(value => value.id === folderId);
      if (next) next.count += 1;
    }
  }
  async function move(item, folderId) {
    const version = generation;
    await request(`sessions/${item.id}/folder`, "PATCH", { folderId });
    if (!current(version)) return false;
    for (const id of new Set([item.folderId || null, folderId])) {
      const page = group(id); page.request += 1; page.busy = false;
    }
    overrides.set(item.id, folderId);
    observe({ ...item, title: item.question, debateStarted: item.started, folderId, access: "owner" });
    // Reload the source page because removing a row changes its pagination offset.
    await loadGroup(item.folderId || null);
    if (folderId && group(folderId).loaded) await loadGroup(folderId);
    try {
      const result = await request("folders");
      if (current(version)) folders = sortedFolders(result.folders);
    } catch { /* The filing succeeded; keep the local result if counts cannot refresh. */ }
    if (current(version)) changed();
    return true;
  }
  return { load, loadGroup, group, observe, move, reset, folders: () => folders,
    owner: () => owner, request, valid: id => folders.some(item => item.id === id) };
}

export function createFolders({ basePath, appPath, getUser, getState, open, dialog, notify, showPopover, closePopover, navigate, changed }) {
  let selected = new URL(location.href).searchParams.get("folder"), expanded = new Set(), expandedOwner = "";
  let picker = null, editing = null, operation = false, metadataFailed = false;
  const lastSeen = new Map();
  const model = createFolderStore({ userId: () => getUser()?.id || null, changed, request: async (path, method = "GET", data) => {
    const response = await fetch(`${basePath}api/debate/${path}`, { method, credentials: "same-origin", cache: "no-store",
      headers: { Accept: "application/json", ...(data ? { "Content-Type": "application/json" } : {}) },
      ...(data ? { body: JSON.stringify(data) } : {}) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Couldn’t update your folders. Try again.");
    return result;
  } });
  function syncExpanded() {
    const owner = getUser()?.id || "";
    if (owner === expandedOwner) return;
    expandedOwner = owner;
    try { expanded = new Set(JSON.parse(localStorage.getItem(`agora-folders-open:${owner}`) || "[]").filter(value => typeof value === "string")); }
    catch { expanded = new Set(); }
  }
  function rememberExpanded() {
    try { localStorage.setItem(`agora-folders-open:${expandedOwner}`, JSON.stringify([...expanded].filter(id => model.valid(id)))); } catch { /* Optional UI preference. */ }
  }
  async function load() {
    syncExpanded(); metadataFailed = false;
    try {
      await model.load();
      if (selected && !model.valid(selected)) { selected = null; notify("That folder is unavailable. This debate will stay ungrouped."); }
      await Promise.all([...expanded].filter(id => model.valid(id)).map(id => model.loadGroup(id)));
    } catch { metadataFailed = true; changed(); }
  }
  function sessionRow(item) {
    const current = getState().sessionId === item.id;
    return `<div class="recent-item filed-session${current ? " is-current" : ""}"><a class="filed-session-link" href="${esc(appPath)}/${item.started ? "debate" : "participants"}?session=${encodeURIComponent(item.id)}" data-workspace="session" data-session-id="${esc(item.id)}" ${current ? 'aria-current="page"' : ""} title="${esc(item.question)}"><i class="recent-dot" aria-hidden="true"></i><span>${esc(item.question)}</span></a><button class="folder-row-menu" data-workspace="folder-session-menu" data-session-id="${esc(item.id)}" aria-label="Options for ${esc(item.question)}" aria-haspopup="menu">${icon("more")}</button></div>`;
  }
  function sessionList(id = null) {
    const page = model.group(id);
    return page.items.map(sessionRow).join("") + (page.error ? `<button class="folder-list-note" data-workspace="folder-load" data-folder-id="${id || ""}">Couldn’t load. Try again</button>` :
      !page.loaded && page.busy ? '<p class="folder-list-note" role="status">Loading…</p>' : !page.items.length ? `<p class="folder-list-note">${id ? "No debates yet" : "Your debates will appear here."}</p>` : "") +
      (page.hasMore ? `<button class="recent-more" data-workspace="folder-more" data-folder-id="${id || ""}" ${page.busy ? "disabled" : ""}>${page.busy ? "Loading…" : "Show more"}</button>` : "");
  }
  function render() {
    syncExpanded();
    if (model.owner() !== getUser()?.id) return '<section class="recent-section folder-library"><p class="folder-list-note" role="status">Loading conversations…</p></section>';
    const folders = model.owner() === getUser()?.id ? model.folders() : [];
    return `<section class="recent-section folder-library" aria-label="Your conversations"><div class="folder-section-heading"><h2>Folders</h2><button class="folder-create-button" data-workspace="folder-create" aria-label="New folder">${icon("plus")}</button></div>
      ${metadataFailed ? '<button class="folder-list-note" data-workspace="folder-retry">Couldn’t load folders. Try again</button>' : ""}
      <nav class="folder-list" aria-label="Folders">${folders.map(folder => `<div class="folder-group"><div class="folder-row"><button class="folder-toggle" data-workspace="folder-toggle" data-folder-id="${folder.id}" aria-expanded="${expanded.has(folder.id)}" aria-controls="folder-${folder.id}" title="${esc(folder.name)}">${icon(expanded.has(folder.id) ? "folder-open" : "folder")}<span>${esc(folder.name)}</span></button><button class="folder-row-menu" data-workspace="folder-menu" data-folder-id="${folder.id}" aria-label="Options for ${esc(folder.name)}" aria-haspopup="menu">${icon("more")}</button></div><div class="folder-disclosure" id="folder-${folder.id}" ${expanded.has(folder.id) ? "" : 'inert aria-hidden="true"'}><div class="folder-sessions">${expanded.has(folder.id) || model.group(folder.id).loaded ? sessionList(folder.id) : ""}</div></div></div>`).join("")}</nav>
      <h2 class="ungrouped-heading">${folders.length ? "Ungrouped" : "Recent"}</h2><nav class="recent-list" aria-label="Ungrouped debates">${sessionList()}</nav></section>`;
  }
  function chip() {
    if (!getUser()) return "";
    const folder = model.owner() === getUser()?.id && model.folders().find(value => value.id === selected);
    return `<button type="button" class="composer-folder${folder ? " has-folder" : ""}" data-workspace="folder-select" aria-label="Save to: ${esc(folder?.name || "Ungrouped")}" title="${esc(folder?.name || "Choose a folder")}" ${getState().roleGenerationPending ? "disabled" : ""}>${icon(folder ? "folder" : "inbox")}<span>${esc(folder?.name || "No folder")}</span>${icon("down")}</button>`;
  }
  function lookup(id) {
    for (const folderId of [null, ...model.folders().map(value => value.id)]) {
      const found = model.group(folderId).items.find(value => value.id === id);
      if (found) return found;
    }
    return lastSeen.get(id) || null;
  }
  function showPicker(item = null) {
    picker = { item, owner: getUser()?.id };
    const current = item ? item.folderId : selected;
    open(item ? "Move to" : "Save to", `${item ? `<p class="folder-picker-topic" title="${esc(item.question)}">${esc(item.question)}</p>` : ""}<div class="folder-picker-list">${[{ id: null, name: "Ungrouped" }, ...model.folders()].map(folder => `<button class="folder-choice${current === folder.id ? " is-selected" : ""}" data-workspace="folder-pick" data-folder-id="${folder.id || ""}" aria-pressed="${current === folder.id}" title="${esc(folder.name)}">${icon(folder.id ? "folder" : "inbox")}<span>${esc(folder.name)}</span>${current === folder.id ? icon("check") : ""}</button>`).join("")}</div><button class="folder-picker-create" data-workspace="folder-create-from-picker">${icon("plus")}New folder</button>`, "polished-dialog folder-picker-dialog");
  }
  function showEditor(id = null, destination = null) {
    const folder = model.folders().find(value => value.id === id);
    editing = { id, destination, owner: getUser()?.id };
    open(folder ? "Rename folder" : "New folder", `<form data-workspace-form="folder"><label class="field">Name<input name="name" maxlength="48" value="${esc(folder?.name || "")}" placeholder="e.g. Campus project" required autocomplete="off"></label><p class="form-error" data-folder-error role="alert" hidden></p><div class="dialog-actions"><button type="button" class="button button-secondary" data-workspace="${destination ? "folder-picker-back" : "close"}">Cancel</button><button type="submit" class="button button-primary">${folder ? "Save changes" : "Create folder"}</button></div></form>`, "polished-dialog folder-editor-dialog");
  }
  function setSelected(id) {
    selected = id;
    const url = new URL(location.href);
    if (id) url.searchParams.set("folder", id); else url.searchParams.delete("folder");
    window.history.replaceState(window.history.state, "", url);
  }
  async function choose(id, target = picker) {
    if (!target || target.owner !== getUser()?.id) return;
    const heading = dialog.firstElementChild;
    if (target.item) {
      if ((target.item.folderId || null) !== id) {
        if (!await model.move(target.item, id)) return;
        if (target.owner !== getUser()?.id) return;
        lastSeen.set(target.item.id, { ...target.item, folderId: id });
        if (id) { expanded.add(id); await model.loadGroup(id); rememberExpanded(); }
        notify(id ? `Moved to ${model.folders().find(value => value.id === id)?.name || "folder"}.` : "Moved to Ungrouped.");
      }
    } else setSelected(id);
    if (dialog.open && dialog.firstElementChild === heading) dialog.close();
    changed();
  }
  async function handleAction(button) {
    const action = button.dataset.workspace;
    if (!action.startsWith("folder-")) return false;
    if (!getUser() || operation) return true;
    const id = button.dataset.folderId || null;
    try {
      if (action === "folder-toggle") {
        if (expanded.has(id)) expanded.delete(id);
        else { expanded.add(id); if (!model.group(id).loaded) void model.loadGroup(id); }
        rememberExpanded(); changed();
      } else if (action === "folder-create") showEditor();
      else if (action === "folder-retry") await load();
      else if (action === "folder-load" || action === "folder-more") await model.loadGroup(id, action === "folder-more");
      else if (action === "folder-select") showPicker();
      else if (action === "folder-create-from-picker") showEditor(null, picker);
      else if (action === "folder-picker-back") showPicker(editing?.destination?.item || null);
      else if (action === "folder-pick") { operation = true; button.disabled = true; await choose(id); }
      else if (action === "folder-session-menu") {
        showPopover(button, `<button class="popover-item" data-workspace="folder-move" data-session-id="${esc(button.dataset.sessionId)}">${icon("folder")}Move to…</button>`);
      } else if (action === "folder-move") { const item = lookup(button.dataset.sessionId); if (item) showPicker(item); }
      else if (action === "folder-menu") showPopover(button, `<button class="popover-item" data-workspace="folder-new" data-folder-id="${id}">${icon("plus")}New debate</button><button class="popover-item" data-workspace="folder-rename" data-folder-id="${id}">${icon("edit")}Rename</button><button class="popover-item" data-workspace="folder-remove" data-folder-id="${id}">${icon("trash")}Remove folder</button>`);
      else if (action === "folder-new") { closePopover(); await navigate(`${appPath}?folder=${id}`); }
      else if (action === "folder-rename") showEditor(id);
      else if (action === "folder-remove") {
        const folder = model.folders().find(value => value.id === id);
        open("Remove folder?", `<p class="dialog-note">Conversations in <strong>${esc(folder?.name)}</strong> will move to Ungrouped.</p><div class="dialog-actions"><button class="button button-secondary" data-workspace="close">Cancel</button><button class="button button-primary" data-workspace="folder-confirm-remove" data-folder-id="${id}">Remove folder</button></div>`, "polished-dialog folder-editor-dialog");
      } else if (action === "folder-confirm-remove") {
        const heading = dialog.firstElementChild;
        operation = true; button.disabled = true;
        await model.request(`folders/${id}`, "DELETE");
        if (selected === id) setSelected(null);
        expanded.delete(id); await load(); rememberExpanded();
        if (dialog.open && dialog.firstElementChild === heading) dialog.close();
        changed(); notify("Folder removed. Your conversations are kept.");
      }
    } catch (error) { notify(error.message); if (button.isConnected) button.disabled = false; }
    finally { operation = false; }
    return true;
  }
  async function submit(form) {
    if (form.dataset.workspaceForm !== "folder") return false;
    if (operation || !editing || editing.owner !== getUser()?.id) return true;
    const name = form.elements.name.value.trim(), context = editing;
    if (!name) return true;
    const error = form.querySelector("[data-folder-error]"), submit = form.querySelector('[type="submit"]');
    operation = true; submit.disabled = true; error.hidden = true;
    try {
      const result = await model.request(`folders${context.id ? `/${context.id}` : ""}`, context.id ? "PATCH" : "POST", { name });
      if (context.owner !== getUser()?.id) return true;
      if (!context.id) expanded.add(result.folder.id);
      await load();
      rememberExpanded();
      if (!form.isConnected || !dialog.open || editing !== context) return true;
      if (context.destination) await choose(result.folder.id, context.destination);
      else { dialog.close(); changed(); }
    } catch (failure) { if (form.isConnected) { error.textContent = failure.message; error.hidden = false; submit.disabled = false; } }
    finally { operation = false; }
    return true;
  }
  return { render, chip, load, handleAction, submit, syncLocation: () => { selected = new URL(location.href).searchParams.get("folder"); }, observe: (saved, created) => {
      if (saved.access && saved.access !== "owner") return;
      lastSeen.set(saved.id, { id: saved.id, question: saved.title || saved.question, folderId: saved.folderId || null,
        started: saved.debateStarted, createdAt: saved.createdAt, archived: saved.archived });
      model.observe(saved, created);
    }, selectedId: () => model.valid(selected) ? selected : null,
    reset: () => { model.reset(); selected = null; expanded.clear(); lastSeen.clear(); picker = editing = null; },
    moveCurrent: () => { const state = getState(), item = lookup(state.sessionId); if (item) showPicker(item); } };
}
