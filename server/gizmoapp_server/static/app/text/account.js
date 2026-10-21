import { esc, icon, avatar, avatarColorPicker, getAvatarColor, syncAvatarColorPreview } from "./ui.js?v=allowance-78";

export function createAccount({ basePath, open, dialog, notify, changed, beforeSignOut, afterSignOut, usageChanged }) {
  let user = null;
  let csrf = "";
  let available = false;
  let verification = null;
  let usage = null;
  let invitation = null;
  let authPromise = null;
  let resolveAuth = null;
  let settingsObserver = null;
  let settingsReadyFrame = 0;
  let inviteHistory = { period: "90", entries: [], nextOffset: 0, hasMore: false };
  const logo = new URL("./assets/debate-agora-wordmark.png", import.meta.url).href;
  const authBrand = `<div class="auth-brand" aria-hidden="true"><img src="${esc(logo)}" alt=""></div>`;
  const errorMarkup = '<p class="form-error" data-account-error role="alert" hidden></p>';
  const field = (label, name, type = "text", value = "", autocomplete = "off") => `<label class="field">${label}<input name="${name}" type="${type}" value="${esc(value)}" required autocomplete="${autocomplete}" ${type === "password" ? 'maxlength="128"' : type === "email" ? 'maxlength="254"' : 'maxlength="48"'}></label>`;
  const password = (label, name, autocomplete) => field(label, name, "password", "", autocomplete);
  const submit = label => `${errorMarkup}<div class="dialog-actions"><button class="button button-primary" type="submit">${label}</button></div>`;

  async function request(path, method = "GET", data) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(`${basePath}api/account/${path}`, {
        method, credentials: "same-origin", signal: controller.signal,
        headers: { Accept: "application/json", ...(data ? { "Content-Type": "application/json", "X-CSRF-Token": csrf } : {}) },
        ...(data ? { body: JSON.stringify(data) } : {}),
      });
      const result = await response.json();
      if (result.csrfToken) csrf = result.csrfToken;
      if (!response.ok) {
        if (response.status === 403 || response.status === 401) await load().catch(() => {});
        throw new Error(result.error || "Could not save your changes. Please try again.");
      }
      if (Object.hasOwn(result, "user")) {
        try { localStorage.setItem("agora-auth-change", String(Date.now()) + Math.random()); } catch {}
        user = result.user; if (user) await refreshUsage(); else usage = null; changed(); }
      return result;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("This is taking too long. Please try again.");
      throw error;
    } finally { clearTimeout(timeout); }
  }
  async function load() {
    const response = await fetch(`${basePath}api/account/session`, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error("Account settings are temporarily unavailable.");
    const result = await response.json();
    const previousId = user?.id;
    user = result.user;
    if (previousId && previousId !== user?.id) { usage = null; afterSignOut(); return; }
    csrf = result.csrfToken; usage = result.usage; invitation = result.invitation; available = true; changed();
  }
  const ready = load().catch(() => { available = false; });

  async function refreshUsage() {
    if (!user) return;
    try {
      const response = await fetch(`${basePath}api/account/usage`, { credentials: "same-origin", cache: "no-store" });
      if (response.ok) { usage = (await response.json()).usage; usageChanged?.(usage); changed(); }
      else if (response.status === 401) { user = null; usage = null; afterSignOut(); changed(); }
    } catch { /* Retain the last known balance during a brief connection failure. */ }
    return usage;
  }
  function finishAuth() {
    const resolve = resolveAuth;
    resolveAuth = null; authPromise = null;
    resolve?.(Boolean(user));
  }
  dialog.addEventListener("close", finishAuth);
  dialog.addEventListener("close", () => { settingsObserver?.disconnect(); cancelAnimationFrame(settingsReadyFrame); });
  async function ensureSignedIn() {
    await ready;
    if (!available) {
      try { await load(); } catch { notify("Login is temporarily unavailable. Please try again."); return false; }
    }
    if (user) return true;
    if (authPromise) return authPromise;
    authPromise = new Promise(resolve => { resolveAuth = resolve; });
    showAuth();
    return authPromise;
  }
  async function ensureReplyAvailable() {
    if (!await ensureSignedIn()) return false;
    if (usage?.remaining <= 0) {
      await refreshUsage();
      if (usage?.remaining <= 0) { await showUsage(); return false; }
    }
    return true;
  }

  function showAuth(mode = "sign-in") {
    verification = null;
    const bonus = invitation ? `<p class="auth-invitation">${esc(invitation.name)} invited you. Verify your account and you both get <strong>${invitation.reward} extra AI replies</strong>.</p>` : "";
    if (mode === "register") {
      open("A place for your ideas.", `${authBrand}${bonus}<form data-account-form="register">${field("Name", "name", "text", "", "nickname")}${avatarColorPicker({ name: "+" })}${field("Email", "email", "email", "", "email")}${password("Password", "password", "new-password")}<p class="field-help">At least 10 characters.</p>${submit("Create account")}</form><p class="auth-switch">Already have an account? <button data-account="sign-in">Log in</button></p>`, "account-dialog auth-dialog");
    } else if (mode === "reset") {
      open("Reset your password.", `${authBrand}<p class="auth-intro">We’ll email you a verification code.</p><form data-account-form="reset">${field("Email", "email", "email", "", "email")}${submit("Send verification code")}</form><p class="auth-switch"><button data-account="sign-in">Back to log in</button></p>`, "account-dialog auth-dialog");
    } else {
      open("Good to have you here.", `${authBrand}<form data-account-form="sign-in">${field("Email", "email", "email", "", "username")}${password("Password", "password", "current-password")}<button class="text-button forgot-password" type="button" data-account="reset">Forgot password?</button>${submit("Log in")}</form><p class="auth-switch">New to Debate Agora? <button data-account="register">Create an account</button></p>`, "account-dialog auth-dialog sign-in-dialog");
    }
  }
  async function showInvite() {
    if (!await ensureSignedIn()) return;
    await refreshUsage();
    if (!usage) { notify("Your invitation link is temporarily unavailable."); return; }
    open("Invite a friend to Debate Agora", `
      <div class="invite-hero" aria-hidden="true"><span class="invite-brand"><img src="${esc(logo)}" alt=""></span></div>
      <div class="invite-body">
        <p class="invite-description">You both get <strong>${usage.rewardPerInvite} AI replies</strong> when your friend joins and verifies their email.</p>
        <form data-account-form="invite">
          <div class="invite-email-field"><input name="emails" type="email" multiple required maxlength="1500" autocomplete="off" placeholder="Add emails" aria-label="Email addresses, separated by commas" title="Up to 5 email addresses, separated by commas"></div>
          ${errorMarkup}
          <div class="invite-actions"><button class="invite-track-button" type="button" data-account="invite-history">Track your invites</button><button class="invite-send-button" type="submit" disabled>Send</button></div>
        </form>
      </div>
    `, "account-dialog benefit-dialog invite-dialog");
  }
  function renderInviteHistory() {
    const date = timestamp => new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(new Date(timestamp * 1000));
    const row = entry => {
      const status = entry.status || "accepted";
      const timestamp = entry.joinedAt || entry.createdAt;
      const label = { accepted: "Accepted", pending: "Pending", sending: "Sending", failed: "Not sent" }[status] || "Pending";
      return `<li>${avatar(entry)}<span class="invite-person"><strong>${esc(entry.email || entry.name)}</strong><time datetime="${new Date(timestamp * 1000).toISOString()}">${date(timestamp)}</time></span><span class="invite-status invite-status-${esc(status)}">${label}${status === "accepted" ? `<small>+${entry.reward} replies</small>` : ""}</span></li>`;
    };
    open("Track your invites", `
      <button class="invite-history-back" type="button" data-account="invite">${icon("back")}<span>Go back</span></button>
      <label class="invite-period"><select data-invite-period aria-label="Invitation period">${[["30", "Past 30 days"], ["90", "Past 90 days"], ["all", "All time"]].map(([value, label]) => `<option value="${value}" ${inviteHistory.period === value ? "selected" : ""}>${label}</option>`).join("")}</select>${icon("down")}</label>
      ${inviteHistory.entries.length ? `<ul class="invite-history-list">${inviteHistory.entries.map(row).join("")}</ul>` : `<div class="invite-history-empty"><p>No invitations yet.</p></div>`}
      ${inviteHistory.hasMore ? '<button class="invite-history-more" type="button" data-account="invite-more">Show more</button>' : ""}
    `, "account-dialog benefit-dialog invite-history-dialog");
  }
  async function showInviteHistory(period = "90", more = false) {
    if (!await ensureSignedIn()) return;
    try {
      const result = await request(`invites?period=${encodeURIComponent(period)}&offset=${more ? inviteHistory.nextOffset : 0}`);
      inviteHistory = { period, entries: more ? [...inviteHistory.entries, ...result.invites] : result.invites, hasMore: result.hasMore, nextOffset: result.nextOffset };
      renderInviteHistory();
    } catch (error) { notify(error.message); }
  }
  async function showUsage() {
    if (!await ensureSignedIn()) return;
    await refreshUsage();
    if (!usage) { notify("Usage is temporarily unavailable."); return; }
    const total = usage.remaining + usage.used;
    const remainingPercent = total ? Math.max(0, Math.min(100, usage.remaining / total * 100)) : 0;
    open("Usage", `
      <div class="usage-hero">
        <div class="usage-balance"><strong>${usage.remaining}</strong><span>AI replies remaining</span></div>
      </div>
      <div class="usage-body">
        <div class="usage-overview">
          <div class="usage-allowance-heading"><span>Reply allowance</span><span><strong>${total}</strong> total</span></div>
          <div class="usage-meter" role="meter" aria-label="AI replies remaining" aria-valuemin="0" aria-valuemax="${Math.max(1, total)}" aria-valuenow="${usage.remaining}" aria-valuetext="${usage.remaining} of ${total} AI replies remaining, ${usage.used} used"><span style="width:${remainingPercent}%"></span></div>
          <div class="usage-legend"><span class="usage-key usage-key-remaining"><strong>${usage.remaining}</strong> remaining</span><span class="usage-key usage-key-used"><strong>${usage.used}</strong> used</span></div>
        </div>
        <div class="usage-footer"><p>${usage.rewardPerInvite} replies each.</p><button class="usage-invite" type="button" data-account="invite">Invite a friend</button></div>
        ${usage.remaining <= 0 ? `<div class="usage-request">${usage.creditRequest?.state === 'pending' ? '<p role="status">Extra replies requested. We’ll review your request.</p>' : '<button class="usage-request-link" type="button" data-account="credit-request">Request more replies</button>'}</div>` : ''}
        ${usage.remaining <= 0 ? '<button class="usage-continue" type="button" data-workspace="close">Keep reading the conversation</button>' : ''}
      </div>
    `, `account-dialog benefit-dialog usage-dialog${usage.remaining <= 0 ? " is-depleted" : ""}`);
  }
  async function showCreditRequests() {
    if (!user?.isAdmin) return;
    try {
      const result = await request("credit-requests");
      open("Reply requests", `<div class="credit-review-list">${result.requests.map(item => `
        <form class="credit-review-row" data-account-form="credit-review" data-request-id="${esc(item.id)}">
          ${avatar(item)}<div class="credit-review-person"><strong>${esc(item.name)}</strong><span>${new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric' }).format(item.createdAt * 1000)} · ${item.remaining} replies left</span></div>
          <div class="credit-review-actions"><label class="credit-review-amount"><input type="number" name="replies" value="50" min="1" max="1000" step="1" required inputmode="numeric" aria-label="Replies for ${esc(item.name)}"><span>replies</span></label><button type="submit" class="button button-primary">Approve</button><button type="button" class="dialog-text-button" data-account="credit-decline">Decline</button></div>
          <p class="credit-review-error" data-credit-error role="alert" hidden></p>
        </form>`).join('') || '<p class="empty-note">No pending requests.</p>'}</div>`, "polished-dialog credit-review-dialog");
    } catch (error) { notify(error.message); }
  }
  function showSettings(tab = "profile") {
    if (!user) { showAuth(); return; }
    if (!["profile", "email", "password"].includes(tab)) tab = "profile";
    verification = null;
    if (dialog.open && dialog.classList.contains("settings-dialog")) { selectSettingsTab(tab); return; }
    settingsObserver?.disconnect();
    cancelAnimationFrame(settingsReadyFrame);
    const panels = {
      profile: `<div class="settings-identity"><span data-avatar-preview>${avatar(user)}</span><div><strong>${esc(user.name)}</strong><span>${esc(user.email)}</span></div></div>${field("Name", "name", "text", user.name, "nickname")}${avatarColorPicker(user, 0, { preview: false })}`,
      email: `<p class="settings-current-email"><small>Current email</small><span>${esc(user.email)}</span></p>${field("New email", "email", "email", "", "email")}${password("Current password", "currentPassword", "current-password")}`,
      password: `${password("Current password", "currentPassword", "current-password")}${password("New password", "newPassword", "new-password").replace("required", 'required minlength="10" placeholder="At least 10 characters"')}${password("Confirm new password", "confirmPassword", "new-password")}`,
    };
    const labels = { profile: "Save changes", email: "Continue", password: "Update password" };
    open("Account", `<nav class="settings-tabs" role="tablist" aria-label="Account settings">${[["profile", "Profile"], ["email", "Email"], ["password", "Password"]].map(([key, label]) => `<button type="button" role="tab" id="settings-tab-${key}" aria-controls="settings-panel-${key}" aria-selected="${key === tab}" tabindex="${key === tab ? 0 : -1}" data-account="settings" data-tab="${key}">${label}</button>`).join("")}</nav><div class="settings-body">${Object.entries(panels).map(([key, body]) => `<form id="settings-panel-${key}" class="settings-panel${key === tab ? " is-active" : ""}" role="tabpanel" aria-labelledby="settings-tab-${key}" aria-hidden="${key !== tab}" ${key !== tab ? "inert" : ""} data-settings-panel="${key}" data-account-form="${key}"><div class="settings-fields">${body}${errorMarkup}</div><div class="dialog-actions settings-actions"><button class="dialog-primary" type="submit" disabled>${labels[key]}</button></div></form>`).join("")}</div>`, "account-dialog polished-dialog settings-dialog");
    selectSettingsTab(tab, true);
    settingsObserver = new ResizeObserver(() => measureSettings());
    dialog.querySelectorAll("[data-settings-panel], .settings-tabs").forEach(panel => settingsObserver.observe(panel));
  }
  function measureSettings(animate = false) {
    const body = dialog.querySelector(".settings-body");
    if (!body?.isConnected) { settingsObserver?.disconnect(); return; }
    const panel = body.querySelector(".is-active");
    if (!panel) return;
    if (!animate) body.classList.remove("is-motion-ready");
    body.style.height = `${Math.ceil(panel.getBoundingClientRect().height)}px`;
    if (!animate) {
      cancelAnimationFrame(settingsReadyFrame);
      settingsReadyFrame = requestAnimationFrame(() => body.classList.add("is-motion-ready"));
    }
  }
  function selectSettingsTab(tab, initial = false) {
    const tabs = [...dialog.querySelectorAll(".settings-tabs [data-tab]")];
    if (!tabs.length) return;
    const index = tabs.findIndex(button => button.dataset.tab === tab);
    dialog.querySelector(".settings-tabs").style.setProperty("--settings-tab-index", Math.max(0, index));
    tabs.forEach(button => {
      const active = button.dataset.tab === tab;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    dialog.querySelectorAll("[data-settings-panel]").forEach(panel => {
      const active = panel.dataset.settingsPanel === tab;
      panel.classList.toggle("is-active", active);
      panel.setAttribute("aria-hidden", String(!active));
      panel.inert = !active;
      if (active) syncSettingsForm(panel);
    });
    measureSettings(!initial);
  }
  function syncSettingsForm(form) {
    if (!user) return;
    const mode = form.dataset.accountForm;
    const confirm = form.elements.confirmPassword;
    if (confirm) confirm.setCustomValidity(confirm.value && confirm.value !== form.elements.newPassword.value ? "Passwords do not match." : "");
    const dirty = mode !== "profile" || form.elements.name.value.trim() !== user.name || form.elements.avatarColor.value !== getAvatarColor(user);
    form.querySelector('[type="submit"]').disabled = Boolean(form.dataset.busy) || !dirty || !form.checkValidity();
  }
  function showVerification() {
    const { purpose, email } = verification;
    open("Check your inbox.", `${authBrand}<p class="verification-note">Enter the code sent to <strong>${esc(email)}</strong>.</p><form data-account-form="verify"><label class="field">Verification code<input class="verification-code" name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" minlength="6" autocomplete="one-time-code" placeholder="000000" required></label>${purpose === "reset" ? `${password("New password", "newPassword", "new-password")}<p class="field-help">At least 10 characters.</p>` : ""}<p class="field-help">Code expires in 10 minutes.</p>${submit(purpose === "reset" ? "Reset password" : "Verify email")}</form><p class="auth-switch"><button data-account="${purpose === "email" ? "settings" : purpose}" data-tab="email">Use another email or request a new code</button></p>`, "account-dialog auth-dialog");
  }
  async function handleAction(button) {
    await ready;
    if (!available) {
      try { await load(); } catch { notify("Account settings are temporarily unavailable. Please try again."); return; }
    }
    const action = button.dataset.account;
    if (["sign-in", "register", "reset"].includes(action)) showAuth(action);
    else if (action === "invite") await showInvite();
    else if (action === "invite-history" || action === "invite-more") {
      button.disabled = true;
      try { await showInviteHistory(action === "invite-more" ? inviteHistory.period : "90", action === "invite-more"); }
      finally { button.disabled = false; }
    }
    else if (action === "usage") await showUsage();
    else if (action === "credit-request") {
      button.disabled = true;
      try { const result = await request("credit-request", "POST", {}); usage = result.usage; await showUsage(); }
      catch (error) { notify(error.message); button.disabled = false; }
    }
    else if (action === "credit-requests") await showCreditRequests();
    else if (action === "credit-decline") await reviewCreditRequest(button.closest('[data-account-form="credit-review"]'), true);
    else if (action === "settings") showSettings(button.dataset.tab);
    else if (action === "sign-out") {
      button.disabled = true;
      try {
        await load();
        if (!user) { afterSignOut(); return; }
        await beforeSignOut();
        await request("sign-out", "POST", {});
        dialog.close(); afterSignOut();
      } catch (error) { notify(error.message); }
      finally { button.disabled = false; }
    }
  }
  async function reviewCreditRequest(form, decline = false) {
    if (!form || form.dataset.busy) return;
    if (!decline && !form.reportValidity()) return;
    const replies = decline ? 50 : Number(form.elements.replies.value);
    if (!Number.isSafeInteger(replies) || replies < 1 || replies > 1000) return;
    form.dataset.busy = "true";
    form.setAttribute("aria-busy", "true");
    const controls = [...form.querySelectorAll("input, button")];
    controls.forEach(control => { control.disabled = true; });
    const error = form.querySelector("[data-credit-error]");
    error.hidden = true;
    try {
      const result = await request("credit-requests", "POST", { id: form.dataset.requestId, replies, decline });
      notify(result.ok ? decline ? "Request declined." : `${replies} replies added.` : "This request has already been reviewed.");
      if (form.isConnected) {
        const list = form.parentElement;
        form.remove();
        if (!list.querySelector(".credit-review-row")) list.innerHTML = '<p class="empty-note">No pending requests.</p>';
      }
      if (result.ok && !decline) void refreshUsage();
    } catch (failure) {
      if (form.isConnected) { error.textContent = failure.message || "Couldn’t review this request. Try again."; error.hidden = false; }
    } finally {
      delete form.dataset.busy;
      form.removeAttribute("aria-busy");
      controls.forEach(control => { control.disabled = false; });
    }
  }
  async function handleSubmit(form) {
    if (form.dataset.accountForm === "credit-review") return reviewCreditRequest(form);
    if (form.dataset.busy) return;
    if (form.matches("[data-settings-panel]") && !form.reportValidity()) return;
    form.dataset.busy = "true";
    const data = Object.fromEntries(new FormData(form));
    const mode = form.dataset.accountForm;
    if (mode === "password") delete data.confirmPassword;
    const button = form.querySelector('[type="submit"]');
    const error = form.querySelector("[data-account-error]");
    const oldLabel = button.textContent;
    button.disabled = true; button.textContent = "Please wait…"; error.hidden = true;
    if (form.matches("[data-settings-panel]")) dialog.querySelectorAll(".settings-tabs button").forEach(tab => { tab.disabled = true; });
    if (mode === "invite") form.elements.emails.readOnly = true;
    try {
      await ready;
      if (!available) await load();
      if (mode === "sign-in") { await request("sign-in", "POST", data); finishAuth(); dialog.close(); }
      else if (mode === "invite") {
        const result = await request("invites", "POST", data);
        if (form.isConnected) form.elements.emails.value = result.failed.join(", ");
        const registered = result.alreadyRegistered || [];
        if ((result.failed.length || registered.length) && form.isConnected) {
          error.textContent = [result.sent.length ? `${result.sent.length} sent.` : "",
            registered.length ? `Already registered: ${registered.join(", ")}.` : "",
            result.failed.length ? `Couldn’t send ${result.failed.length === 1 ? "this invitation" : "these invitations"}. Please try again.` : ""].filter(Boolean).join(" ");
          error.hidden = false;
        } else notify(result.sent.length ? (result.sent.length === 1 ? "Invitation sent." : `${result.sent.length} invitations sent.`) : "These invitations have already been sent.");
      }
      else if (["register", "email", "reset"].includes(mode)) {
        const purpose = mode === "email" ? "email" : mode;
        const result = await request("verification", "POST", { ...data, purpose });
        verification = { purpose, email: data.email, challengeId: result.challengeId };
        if (form.isConnected && dialog.open) showVerification();
      } else if (mode === "verify") {
        const purpose = verification?.purpose;
        await request("verify", "POST", { ...data, challengeId: verification?.challengeId });
        verification = null; finishAuth(); dialog.close();
        notify(purpose === "email" ? "Email updated." : purpose === "reset" ? "Password reset. You’re signed in." : "Account created. You’re signed in.");
      } else {
        await request(mode, "PATCH", data);
        dialog.close(); notify(mode === "password" ? "Password updated." : "Profile updated.");
      }
    } catch (failure) {
      if (form.isConnected) { error.textContent = failure.message || "Could not connect. Please try again."; error.hidden = false; }
    } finally {
      delete form.dataset.busy; button.disabled = false; button.textContent = oldLabel;
      if (form.isConnected && form.matches("[data-settings-panel]")) {
        dialog.querySelectorAll(".settings-tabs button").forEach(tab => { tab.disabled = false; });
        syncSettingsForm(form);
      }
      if (mode === "invite") { form.elements.emails.readOnly = false; syncInviteForm(form); }
    }
  }
  function syncInviteForm(form) {
    const email = form.querySelector('[name="emails"]');
    form.querySelector('[type="submit"]').disabled = Boolean(form.dataset.busy) || !email.value.trim() || !email.checkValidity();
  }
  dialog.addEventListener("input", event => {
    const invitationForm = event.target.closest('[data-account-form="invite"]');
    if (invitationForm) syncInviteForm(invitationForm);
    const form = event.target.closest('[data-account-form="register"], [data-account-form="profile"]');
    if (form && event.target.matches('[name="name"], [data-avatar-color-choice]')) syncAvatarColorPreview(form);
    const settingsForm = event.target.closest("[data-settings-panel]");
    if (settingsForm) syncSettingsForm(settingsForm);
  });
  dialog.addEventListener("keydown", event => {
    if (!event.target.matches('.settings-tabs [role="tab"]') || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...dialog.querySelectorAll('.settings-tabs [role="tab"]')];
    if (tabs.some(tab => tab.disabled)) return;
    event.preventDefault();
    const index = tabs.indexOf(event.target);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    selectSettingsTab(tabs[next].dataset.tab);
    tabs[next].focus({ preventScroll: true });
  });
  dialog.addEventListener("change", async event => {
    if (!event.target.matches("[data-invite-period]")) return;
    const select = event.target;
    select.disabled = true;
    try { await showInviteHistory(select.value); }
    finally { if (select.isConnected) { select.disabled = false; select.value = inviteHistory.period; } }
  });
  let lastFocusRefresh = 0;
  window.addEventListener("focus", () => { if (user && Date.now() - lastFocusRefresh > 10000) { lastFocusRefresh = Date.now(); void load().catch(() => {}); } });
  window.addEventListener("storage", event => { if (event.key === "agora-auth-change") void load().catch(() => {}); });
  window.addEventListener("pageshow", event => { if (event.persisted) void load().catch(() => {}); });
  return {
    user: () => user,
    usage: () => usage,
    ensureSignedIn, ensureReplyAvailable, refreshUsage, ready,
    menu: () => user
      ? `<div class="account-menu-header"><strong>${esc(user.name)}</strong><span>${esc(user.email)}</span></div><button class="popover-item" data-account="settings">${icon("settings")}Account settings</button>${user.isAdmin ? `<button class="popover-item" data-account="credit-requests">${icon("usage")}Reply requests</button>` : ''}<div class="popover-divider"></div><button class="popover-item" data-account="sign-out">${icon("logout")}Sign out</button>`
      : `<button class="popover-item" data-account="sign-in">${icon("user")}Sign in</button><button class="popover-item" data-account="register">${icon("plus")}Create account</button>`,
    handleAction, handleSubmit,
  };
}
