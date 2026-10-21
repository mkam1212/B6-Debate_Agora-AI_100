import test from 'node:test';
import assert from 'node:assert/strict';
import { rememberAttachments, recoverAttachments, acknowledgeAttachments } from '../server/gizmoapp_server/static/app/text/attachment-utils.js';
const item = { id: 'a'.repeat(32), kind: 'image', name: 'photo.png', mime: 'image/png', size: 100 };
const storage = () => { const map = new Map(); return { getItem: k => map.get(k) ?? null, setItem: (k,v) => map.set(k,v), removeItem: k => map.delete(k) }; };
test('a just-uploaded attachment survives an immediate refresh without altering text', () => {
  const s = storage(); rememberAttachments(s, 'chat', [item], 4);
  assert.deepEqual(recoverAttachments(s, 'chat', [], 4), [item]);
  assert.deepEqual(recoverAttachments(s, 'chat', [], 5), []);
});
test('an old save cannot clear a newer uploaded attachment', () => {
  const s = storage(); rememberAttachments(s, 'chat', [item], 4);
  acknowledgeAttachments(s, 'chat', [], 5);
  assert.deepEqual(recoverAttachments(s, 'chat', [], 5), [item]);
  acknowledgeAttachments(s, 'chat', [item], 6);
  assert.equal(s.getItem('chat:attachments'), null);
});
test('removing or sending an attachment does not resurrect it from an old save', () => {
  const s = storage(); rememberAttachments(s, 'chat', [], 4);
  acknowledgeAttachments(s, 'chat', [item], 5);
  assert.deepEqual(recoverAttachments(s, 'chat', [item], 5), []);
  acknowledgeAttachments(s, 'chat', [], 6);
  assert.equal(s.getItem('chat:attachments'), null);
});
