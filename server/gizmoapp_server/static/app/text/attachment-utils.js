export const MAX_IMAGES = 6;
export const MAX_FILES = 6;
export const MAX_FILE_BYTES = 20 * 1024 * 1024;
export const IMAGE_EXTENSIONS = /\.(png|jpe?g|webp|gif)$/i;
export const FILE_EXTENSIONS = /\.(pdf|txt|md|csv|json|docx|xlsx|pptx)$/i;
export const attachmentIds = items => (items || []).map(item => item.id).join(',');
export function fileSize(size) {
  return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`;
}
export function rememberAttachments(storage, key, attachments, baseRevision) {
  if (!Number.isSafeInteger(baseRevision)) return;
  try { storage?.setItem(`${key}:attachments`, JSON.stringify({ attachments, baseRevision })); } catch { /* Optional recovery. */ }
}
export function recoverAttachments(storage, key, serverAttachments, revision) {
  try {
    const local = JSON.parse(storage?.getItem(`${key}:attachments`));
    if (local?.baseRevision === revision && Array.isArray(local.attachments) && local.attachments.length <= 12 && local.attachments.every(item => /^[a-f0-9]{32}$/.test(item?.id) && ['image', 'file'].includes(item.kind) && typeof item.name === 'string')) return local.attachments;
  } catch { /* Keep server state. */ }
  return serverAttachments || [];
}
export function acknowledgeAttachments(storage, key, attachments, revision) {
  try {
    const local = JSON.parse(storage?.getItem(`${key}:attachments`));
    if (!local || local.baseRevision > revision) return;
    if (attachmentIds(local.attachments) === attachmentIds(attachments)) storage.removeItem(`${key}:attachments`);
    else rememberAttachments(storage, key, local.attachments, revision);
  } catch { /* Saving must not depend on browser storage. */ }
}
