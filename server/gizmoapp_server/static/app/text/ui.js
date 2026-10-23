export const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

// Lucide 0.468.0 — ISC. Full license in vendor/LUCIDE-LICENSE.
const paths = {
  "inbox": '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"/>',
  "folder": '<path d="M20 20H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2Z"/>',
  "folder-open": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.9 2.6l-1.6 5.4a2 2 0 0 1-1.92 1.4H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v2"/>',
  "globe": '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20"/>',
  "external": '<path d="M7 7h10v10M7 17 17 7"/>',
  "gift": '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-8M12 8v13"/><path d="M12 8H7.5A2.5 2.5 0 1 1 10 5.5L12 8Zm0 0h4.5A2.5 2.5 0 1 0 14 5.5L12 8Z"/>',
  "calendar": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M16 3v4M8 3v4M3 11h18m-13 5 2 2 5-5"/>',
  "brush": '<path d="m14.1 6.1 3.8 3.8M9.1 15.1 20 4.2a1.5 1.5 0 0 0-2.2-2.1L6.9 13"/><path d="M8.8 14.2a3.5 3.5 0 0 1 0 5C7.2 20.8 4.5 21 2 21c1.5-1.5 1-3.5 1.8-5a3.5 3.5 0 0 1 5-1.8Z"/>',
  "tram": '<rect x="5" y="5" width="14" height="14" rx="4"/><path d="M5 12h14M12 5v7M8 2h8M12 2v3M8 19l-2 3m10-3 2 3M8 16h.01M16 16h.01"/>',
  "rocket": '<path d="M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2M12 15l-3-3a22 22 0 0 1 4-6c3.7-3.7 8-3 8-3s.7 4.3-3 8a22 22 0 0 1-6 4ZM9 12H4s.6-3 2-4c1.5-1 4.5 0 4.5 0M12 15v5s3-.6 4-2c1-1.5 0-4.5 0-4.5"/><circle cx="16" cy="8" r="1"/>',
  "login": '<path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><path d="m10 17 5-5-5-5M15 12H3"/>',
  "usage": '<rect x="3.5" y="11" width="3" height="9" rx="1"/><rect x="10.5" y="4" width="3" height="16" rx="1"/><rect x="17.5" y="8" width="3" height="12" rx="1"/>',
  "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
  "agent-customize": '<circle cx="9" cy="7" r="3"/><path d="M3 20v-2a5 5 0 0 1 5-5h2a5 5 0 0 1 3.6 1.5"/><path d="m14 18 6-6 2 2-6 6-3 1 1-3Z"/>',
  "user": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  "search": "<circle cx=\"11\" cy=\"11\" r=\"8\" />\n  <path d=\"m21 21-4.3-4.3\" />",
  "compass": "<path d=\"m16.24 7.76-1.804 5.411a2 2 0 0 1-1.265 1.265L7.76 16.24l1.804-5.411a2 2 0 0 1 1.265-1.265z\" />\n  <circle cx=\"12\" cy=\"12\" r=\"10\" />",
  "chat": "<path d=\"M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z\" />",
  "plus": "<path d=\"M5 12h14\" />\n  <path d=\"M12 5v14\" />",
  "pause": "<rect x=\"14\" y=\"4\" width=\"4\" height=\"16\" rx=\"1\" />\n  <rect x=\"6\" y=\"4\" width=\"4\" height=\"16\" rx=\"1\" />",
  "play": "<polygon points=\"6 3 20 12 6 21 6 3\" />",
  "down": "<path d=\"m6 9 6 6 6-6\" />",
  "right": "<path d=\"m9 18 6-6-6-6\" />",
  "back": "<path d=\"m12 19-7-7 7-7\" />\n  <path d=\"M19 12H5\" />",
  "share": "<path d=\"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4\" />\n  <polyline points=\"17 8 12 3 7 8\" />\n  <line x1=\"12\" x2=\"12\" y1=\"3\" y2=\"15\" />",
  "paperclip": "<path d=\"M13.234 20.252 21 12.3\" />\n  <path d=\"m16 6-8.414 8.586a2 2 0 0 0 0 2.828 2 2 0 0 0 2.828 0l8.414-8.586a4 4 0 0 0 0-5.656 4 4 0 0 0-5.656 0l-8.415 8.585a6 6 0 1 0 8.486 8.486\" />",
  "document": "<path d=\"M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z\" />\n  <path d=\"M14 2v4a2 2 0 0 0 2 2h4\" />\n  <path d=\"M10 9H8\" />\n  <path d=\"M16 13H8\" />\n  <path d=\"M16 17H8\" />",
  "send": "<path d=\"m5 12 7-7 7 7\" />\n  <path d=\"M12 19V5\" />",
  "more": "<circle cx=\"12\" cy=\"12\" r=\"1\" />\n  <circle cx=\"19\" cy=\"12\" r=\"1\" />\n  <circle cx=\"5\" cy=\"12\" r=\"1\" />",
  "settings": "<line x1=\"21\" x2=\"14\" y1=\"4\" y2=\"4\" />\n  <line x1=\"10\" x2=\"3\" y1=\"4\" y2=\"4\" />\n  <line x1=\"21\" x2=\"12\" y1=\"12\" y2=\"12\" />\n  <line x1=\"8\" x2=\"3\" y1=\"12\" y2=\"12\" />\n  <line x1=\"21\" x2=\"16\" y1=\"20\" y2=\"20\" />\n  <line x1=\"12\" x2=\"3\" y1=\"20\" y2=\"20\" />\n  <line x1=\"14\" x2=\"14\" y1=\"2\" y2=\"6\" />\n  <line x1=\"8\" x2=\"8\" y1=\"10\" y2=\"14\" />\n  <line x1=\"16\" x2=\"16\" y1=\"18\" y2=\"22\" />",
  "close": "<path d=\"M18 6 6 18\" />\n  <path d=\"m6 6 12 12\" />",
  "menu": "<line x1=\"4\" x2=\"20\" y1=\"12\" y2=\"12\" />\n  <line x1=\"4\" x2=\"20\" y1=\"6\" y2=\"6\" />\n  <line x1=\"4\" x2=\"20\" y1=\"18\" y2=\"18\" />",
  "panel": "<rect width=\"18\" height=\"18\" x=\"3\" y=\"3\" rx=\"2\" />\n  <path d=\"M15 3v18\" />",
  "check": "<path d=\"M20 6 9 17l-5-5\" />",
  "link": "<path d=\"M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71\" />\n  <path d=\"M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71\" />",
  "balance": "<line x1=\"21\" x2=\"14\" y1=\"4\" y2=\"4\" />\n  <line x1=\"10\" x2=\"3\" y1=\"4\" y2=\"4\" />\n  <line x1=\"21\" x2=\"12\" y1=\"12\" y2=\"12\" />\n  <line x1=\"8\" x2=\"3\" y1=\"12\" y2=\"12\" />\n  <line x1=\"21\" x2=\"16\" y1=\"20\" y2=\"20\" />\n  <line x1=\"12\" x2=\"3\" y1=\"20\" y2=\"20\" />\n  <line x1=\"14\" x2=\"14\" y1=\"2\" y2=\"6\" />\n  <line x1=\"8\" x2=\"8\" y1=\"10\" y2=\"14\" />\n  <line x1=\"16\" x2=\"16\" y1=\"18\" y2=\"22\" />",
  "at": "<circle cx=\"12\" cy=\"12\" r=\"4\" />\n  <path d=\"M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8\" />",
  "copy": "<rect width=\"14\" height=\"14\" x=\"8\" y=\"8\" rx=\"2\" ry=\"2\" />\n  <path d=\"M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2\" />",
  "download": "<path d=\"M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4\" />\n  <polyline points=\"7 10 12 15 17 10\" />\n  <line x1=\"12\" x2=\"12\" y1=\"15\" y2=\"3\" />",
  "edit": "<path d=\"M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7\" />\n  <path d=\"M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z\" />",
  "trash": "<path d=\"M3 6h18\" />\n  <path d=\"M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6\" />\n  <path d=\"M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2\" />\n  <line x1=\"10\" x2=\"10\" y1=\"11\" y2=\"17\" />\n  <line x1=\"14\" x2=\"14\" y1=\"11\" y2=\"17\" />",
  "users": "<path d=\"M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2\" />\n  <circle cx=\"9\" cy=\"7\" r=\"4\" />\n  <path d=\"M22 21v-2a4 4 0 0 0-3-3.87\" />\n  <path d=\"M16 3.13a4 4 0 0 1 0 7.75\" />"
};
export function icon(name, className = "") {
  return `<svg class="ui-icon ${esc(className)}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${paths[name] || paths.chat}</svg>`;
}
export function initials(name) {
  return Array.from(String(name || "?").replace(/\.ai$/i, "").trim())[0]?.toLocaleUpperCase() || "?";
}
export function agentName(role) {
  return `${String(role?.name || "Agent").replace(/\.ai$/i, "")}.ai`;
}
export const AVATAR_COLORS = [
  ["Blue", "#2563eb"], ["Emerald", "#059669"],
  ["Violet", "#8b5cf6"], ["Pink", "#db3985"],
  ["Gold", "#e6a21a"], ["Cyan", "#0789a4"],
  ["Indigo", "#5d5fe9"], ["Coral", "#df5e42"],
];
// Keep earlier selections in the same color family without adding extra swatches.
const previousAvatarColors = Object.fromEntries([
  "#4667a5", "#64764e", "#8260a4", "#a15a74",
  "#957134", "#347b73", "#64738a", "#a96648",
].map((color, index) => [color, AVATAR_COLORS[index][1]]));
export const isAvatarColor = value => typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value);
const currentAvatarColor = color => previousAvatarColors[color.toLowerCase()] || color.toLowerCase();
export function getAvatarColor(person, index = 0) {
  return isAvatarColor(person?.avatarColor) ? currentAvatarColor(person.avatarColor) : AVATAR_COLORS[((index % AVATAR_COLORS.length) + AVATAR_COLORS.length) % AVATAR_COLORS.length][1];
}
export function randomAvatarColor(used = []) {
  const usedColors = new Set(used.filter(isAvatarColor).map(currentAvatarColor));
  const available = AVATAR_COLORS.filter(([, value]) => !usedColors.has(value));
  const choices = available.length ? available : AVATAR_COLORS;
  return choices[Math.floor(Math.random() * choices.length)][1];
}
export function avatarStyle(person, index = 0) {
  const color = getAvatarColor(person, index);
  const rgb = [1, 3, 5].map(offset => Number.parseInt(color.slice(offset, offset + 2), 16));
  const tint = rgb.map(channel => Math.round(channel * .16 + 255 * .84)).join(",");
  return `background:rgb(${tint});color:${color}`;
}
export function avatar(role, index = 0, extra = "") {
  return `<span class="initial-avatar ${esc(extra)}" style="${avatarStyle(role, index)}" ${role?.id ? `data-role-avatar="${esc(role.id)}"` : ""} aria-hidden="true">${esc(initials(role?.name))}</span>`;
}
export function avatarColorPicker(person, index = 0, { preview = true, disabled = false } = {}) {
  const selected = getAvatarColor(person, index);
  const colors = AVATAR_COLORS.some(([, value]) => value === selected) ? AVATAR_COLORS : [...AVATAR_COLORS, ["Custom", selected]];
  return `<fieldset class="avatar-color-picker"><legend>Avatar color</legend><div class="avatar-color-row">${preview ? `<span class="avatar-color-preview" data-avatar-preview>${avatar(person, index)}</span>` : ""}<div class="avatar-color-options">${colors.map(([name, color]) => `<label class="avatar-color-swatch" title="${name}"><input type="radio" name="avatarColor" data-avatar-color-choice value="${color}" aria-label="${name}" ${color === selected ? "checked" : ""} ${disabled ? "disabled" : ""}><span style="--swatch-color:${color}" aria-hidden="true">${icon("check")}</span></label>`).join("")}</div></div></fieldset>`;
}
export function syncAvatarColorPreview(form) {
  if (!form) return;
  const name = form.querySelector('[name="name"]')?.value || "+";
  const avatarColor = form.querySelector('[name="avatarColor"]:checked')?.value;
  for (const preview of form.querySelectorAll("[data-avatar-preview]")) preview.innerHTML = avatar({ name, avatarColor });
}
