import test from 'node:test';
import assert from 'node:assert/strict';
import { rememberDraft, recoverDraft, acknowledgeDraft } from '../server/gizmoapp_server/static/app/text/drafts.js';
function storage() {
  const map = new Map();
  return { getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, value), removeItem: key => map.delete(key) };
}
test('an immediate reload recovers an unsent excerpt against the same revision', () => {
  const tab = storage();
  rememberDraft(tab, 'session-a', '[Source]\nAn unsent excerpt.', 7);
  assert.equal(recoverDraft(tab, 'session-a', '', 7), '[Source]\nAn unsent excerpt.');
});
test('a newer server revision is not overwritten by an old local draft', () => {
  const tab = storage();
  rememberDraft(tab, 'session-a', 'Older local text', 7);
  assert.equal(recoverDraft(tab, 'session-a', 'Newer saved text', 8), 'Newer saved text');
});
test('acknowledging an older save preserves typing that happened while it was in flight', () => {
  const tab = storage();
  rememberDraft(tab, 'session-a', 'More recent typing', 7);
  acknowledgeDraft(tab, 'session-a', 'Earlier snapshot', 8);
  assert.equal(recoverDraft(tab, 'session-a', 'Earlier snapshot', 8), 'More recent typing');
  acknowledgeDraft(tab, 'session-a', 'More recent typing', 9);
  assert.equal(tab.getItem('session-a'), null);
});
test('sending clears the cached draft without resurrecting a sent message', () => {
  const tab = storage();
  rememberDraft(tab, 'session-a', 'Send this', 7);
  rememberDraft(tab, 'session-a', '', 7);
  acknowledgeDraft(tab, 'session-a', '', 8);
  assert.equal(recoverDraft(tab, 'session-a', '', 8), '');
});
test('malformed and unavailable storage cannot stop the composer', () => {
  const tab = storage(); tab.setItem('session-a', '{bad json');
  assert.equal(recoverDraft(tab, 'session-a', 'Server text', 7), 'Server text');
  assert.doesNotThrow(() => rememberDraft(null, 'session-a', 'Draft', 7));
  assert.doesNotThrow(() => acknowledgeDraft(null, 'session-a', '', 8));
});
