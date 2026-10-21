// Keep unsent text in this tab until the server acknowledges that exact draft.
export function rememberDraft(storage, key, draft, baseRevision) {
  if (typeof draft !== "string" || draft.length > 2000 || !Number.isSafeInteger(baseRevision)) return;
  try { storage.setItem(key, JSON.stringify({ draft, baseRevision })); } catch { /* Storage is optional. */ }
}
export function recoverDraft(storage, key, serverDraft, serverRevision) {
  try {
    const local = JSON.parse(storage.getItem(key));
    if (typeof local?.draft === "string" && local.draft.length <= 2000 && local.baseRevision === serverRevision) return local.draft;
  } catch { /* Use the saved server draft when storage is unavailable or invalid. */ }
  return serverDraft;
}
export function acknowledgeDraft(storage, key, savedDraft, savedRevision) {
  try {
    const local = JSON.parse(storage.getItem(key));
    if (!local || !Number.isSafeInteger(local.baseRevision) || local.baseRevision > savedRevision) return;
    if (local.draft === savedDraft) storage.removeItem(key);
    else rememberDraft(storage, key, local.draft, savedRevision);
  } catch { /* Saving a session must never depend on browser storage. */ }
}
