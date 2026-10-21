import { esc, icon } from './ui.js';
import { MAX_IMAGES, MAX_FILES, MAX_FILE_BYTES, IMAGE_EXTENSIONS, FILE_EXTENSIONS, fileSize } from './attachment-utils.js';

export function createAttachments({ basePath, getState, changed, notify, open, dialog }) {
  const uploading = new Map();
  const path = (item, operation) => {
    const query = new URLSearchParams();
    if (getState().readOnly) { query.set('session', getState().sessionId); const share = new URLSearchParams(location.search).get('share'); if (share) query.set('share', share); }
    return `${basePath}api/debate/attachments/${encodeURIComponent(item.id)}/${operation}${query.size ? `?${query}` : ''}`;
  };
  const imageInput = document.createElement('input');
  const fileInput = document.createElement('input');
  imageInput.type = fileInput.type = 'file';
  imageInput.multiple = fileInput.multiple = true;
  imageInput.hidden = fileInput.hidden = true;
  imageInput.accept = '.png,.jpg,.jpeg,.webp,.gif';
  fileInput.accept = '.pdf,.txt,.md,.csv,.json,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.webp,.gif';
  imageInput.id = 'image-file-input'; fileInput.id = 'attachment-file-input';
  document.body.append(imageInput, fileInput);
  let csrfPromise = null;
  async function csrfToken() {
    if (!csrfPromise) csrfPromise = fetch(`${basePath}api/account/session`, { credentials: 'same-origin', cache: 'no-store' })
      .then(async response => { if (!response.ok) throw new Error('Could not start this upload.'); return (await response.json()).csrfToken; })
      .catch(error => { csrfPromise = null; throw error; });
    return csrfPromise;
  }
  function render(items = [], editable = false) {
    const all = editable ? [...items, ...uploading.values()] : items;
    if (!all.length) return '';
    return `<div class="attachment-tray${editable ? ' draft-attachments' : ' message-attachments'}" aria-label="Attachments">${all.map(item => {
      const pending = Boolean(item.pending);
      const preview = item.kind === 'image';
      return `<div class="attachment-item ${preview ? 'image-attachment' : 'file-attachment'}${pending ? ' is-uploading' : ''}">
        <button type="button" class="attachment-preview" data-attachment-action="preview" data-attachment-id="${esc(item.id)}" aria-label="Preview ${esc(item.name)}" title="${esc(item.name)}" ${pending ? 'disabled' : ''}>
          ${preview ? `<img src="${esc(pending ? item.objectUrl : path(item, 'preview'))}" alt="${esc(item.name)}">` : `<span class="attachment-file-icon">${icon('document')}</span><span class="attachment-file-copy"><strong>${esc(item.name)}</strong><span class="attachment-file-meta">${pending ? 'Uploading…' : `<span class="attachment-file-type">${esc(item.name.split('.').at(-1).toUpperCase())}</span><span class="attachment-meta-separator" aria-hidden="true">·</span><span>${fileSize(item.size)}</span>`}</span></span>`}
          ${pending && preview ? '<span class="attachment-upload-indicator" aria-label="Uploading"></span>' : ''}
        </button>
        ${editable ? `<button type="button" class="attachment-remove" data-attachment-action="remove" data-attachment-id="${esc(item.id)}" aria-label="Remove ${esc(item.name)}" title="Remove">${icon('close')}</button>` : ''}
      </div>`;
    }).join('')}</div>`;
  }
  async function discard(id) {
    try {
      let response = await fetch(`${basePath}api/debate/attachments/${encodeURIComponent(id)}`, {
        method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': await csrfToken() },
      });
      if (response.status === 403) { csrfPromise = null; await fetch(`${basePath}api/debate/attachments/${encodeURIComponent(id)}`, {
        method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': await csrfToken() },
      }); }
    } catch { /* Abandoned uploads are also collected on the server after the grace period. */ }
  }
  async function upload(item, file) {
    const controller = new AbortController(); item.controller = controller;
    const timeout = setTimeout(() => controller.abort(), 120000);
    const startingSession = getState().sessionId;
    try {
      const data = new FormData(); data.append('file', file);
      let response = await fetch(`${basePath}api/debate/attachments`, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-CSRF-Token': await csrfToken() }, signal: controller.signal });
      if (response.status === 403) {
        csrfPromise = null;
        response = await fetch(`${basePath}api/debate/attachments`, { method: 'POST', body: data, credentials: 'same-origin', headers: { 'X-CSRF-Token': await csrfToken() }, signal: controller.signal });
      }
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.errors?.join(' ') || payload.error || 'Upload failed. Try again.');
      if (uploading.has(item.id) && getState().sessionId === startingSession) getState().draftAttachments.push(payload.attachment);
      else void discard(payload.attachment.id);
    } catch (error) {
      if (uploading.has(item.id)) notify(error.name === 'AbortError' ? `“${file.name}” took too long to upload. Try again.` : `Couldn’t upload “${file.name}”. Try again.`);
    } finally {
      clearTimeout(timeout); uploading.delete(item.id);
      if (item.objectUrl) URL.revokeObjectURL(item.objectUrl);
      changed();
    }
  }
  async function addFiles(files) {
    if (getState().readOnly) return;
    if (!['question', 'debate'].includes(getState().stage) || getState().roleGenerationPending) { notify('Finish the current step before uploading.'); return; }
    const accepted = [];
    for (const file of files) {
      const kind = IMAGE_EXTENSIONS.test(file.name) ? 'image' : FILE_EXTENSIONS.test(file.name) ? 'file' : '';
      if (!kind) { notify(`${file.name}: this file type is not supported.`); continue; }
      if (!file.size) { notify(`“${file.name}” is empty. Choose another file.`); continue; }
      if (file.size > MAX_FILE_BYTES) { notify(`“${file.name}” is too large. Choose a file under 20 MB.`); continue; }
      const current = [...getState().draftAttachments, ...uploading.values()];
      const limit = kind === 'image' ? MAX_IMAGES : MAX_FILES;
      if (current.filter(item => item.kind === kind).length >= limit) { notify(`Up to ${limit} ${kind === 'image' ? 'images' : 'documents'} per message.`); continue; }
      const item = { id: `upload-${crypto.randomUUID()}`, name: file.name, kind, size: file.size, pending: true, ...(kind === 'image' ? { objectUrl: URL.createObjectURL(file) } : {}) };
      uploading.set(item.id, item); accepted.push([item, file]);
    }
    if (!accepted.length) return;
    changed();
    // Reserve all slots first, then upload sequentially to keep ordering and memory predictable.
    for (const [item, file] of accepted) if (uploading.has(item.id)) await upload(item, file);
  }
  for (const input of [imageInput, fileInput]) input.addEventListener('change', () => { const files = [...input.files]; input.value = ''; void addFiles(files); });
  function find(id) {
    const state = getState();
    return [...state.draftAttachments, ...state.messages.flatMap(item => item.attachments || []), ...state.pendingUserMessages.flatMap(item => item.attachments || [])].find(item => item.id === id);
  }
  async function preview(item) {
    const download = `<a class="button button-secondary" href="${esc(path(item, 'content'))}" download>${icon('download')}Download</a>`;
    open(item.name, `${item.kind === 'image' ? `<img class="attachment-full-image" src="${esc(path(item, 'preview'))}" alt="${esc(item.name)}">` : `<div class="attachment-document-preview" data-attachment-preview="${esc(item.id)}"><p class="dialog-note">Loading preview…</p></div>`}<div class="attachment-preview-footer"><span>${fileSize(item.size)}</span>${download}</div>`, item.kind === 'image' ? 'image-preview-dialog' : 'file-preview-dialog');
    if (item.kind === 'image') return;
    try {
      const response = await fetch(path(item, 'preview'));
      if (!response.ok) throw new Error();
      const payload = await response.json();
      const target = dialog.querySelector(`[data-attachment-preview="${item.id}"]`);
      if (target) target.innerHTML = payload.text ? `<pre>${esc(payload.text)}</pre>` : `<p class="dialog-note">${payload.status === 'encrypted' ? 'This file is password protected.' : 'A text preview isn’t available for this file.'} Download the original to view it.</p>`;
    } catch { const target = dialog.querySelector(`[data-attachment-preview="${item.id}"]`); if (target) target.innerHTML = '<p class="dialog-note">Could not load this preview. You can download the original.</p>'; }
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-attachment-action]');
    if (!button || button.disabled) return;
    event.preventDefault();
    const action = button.dataset.attachmentAction;
    if (action === 'images') imageInput.click();
    else if (action === 'files') fileInput.click();
    else if (action === 'remove') {
      const id = button.dataset.attachmentId;
      const pending = uploading.get(id);
      if (pending) { uploading.delete(id); pending.controller?.abort(); if (pending.objectUrl) URL.revokeObjectURL(pending.objectUrl); }
      else getState().draftAttachments = getState().draftAttachments.filter(item => item.id !== id);
      const saved = await changed(true);
      if (!pending && saved === true) void discard(id);
    } else if (action === 'preview') { const item = find(button.dataset.attachmentId); if (item) void preview(item); }
  });
  document.addEventListener('paste', event => {
    if (!event.target.closest('.composer-form, .question-input-shell') || !event.clipboardData?.files.length) return;
    event.preventDefault(); void addFiles([...event.clipboardData.files]);
  });
  for (const name of ['dragover', 'dragleave', 'drop']) document.addEventListener(name, event => {
    const form = event.target.closest('.composer-form, .question-input-shell');
    if (!form || !event.dataTransfer?.types.includes('Files')) return;
    event.preventDefault();
    if (name === 'dragover') form.classList.add('is-dragging-files');
    else if (name === 'drop') { form.classList.remove('is-dragging-files'); void addFiles([...event.dataTransfer.files]); }
    else if (!form.contains(event.relatedTarget)) form.classList.remove('is-dragging-files');
  });
  return { render, busy: () => uploading.size > 0 };
}
