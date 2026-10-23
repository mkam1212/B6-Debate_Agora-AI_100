import test from 'node:test';
import assert from 'node:assert/strict';
import { createFolderStore, createFolders, captureFolderPanels, animateFolderPanels } from '../server/gizmoapp_server/static/app/text/folders.js';

const saved = (id, folderId = null, extra = {}) => ({ id, folderId, question: `Question ${id}`, title: `Question ${id}`,
  createdAt: `2026-10-${id.padStart(2, '0')}`, debateStarted: true, access: 'owner', ...extra });
const listed = (id, folderId = null) => ({ ...saved(id, folderId), started: true });
const page = (items, offset = items.length, hasMore = false) => ({ sessions: items, nextOffset: offset, hasMore });

test('folder disclosures animate both directions from their visible height, including a rapid reversal', () => {
  let height = 80;
  const animations = [];
  const panel = { id: 'folder-campus', getBoundingClientRect: () => ({ height }), animate: frames => animations.push(frames) };
  const root = { querySelectorAll: () => [panel] };
  const open = captureFolderPanels(root);
  height = 0; animateFolderPanels(root, open);
  assert.deepEqual(animations[0], [{ height: '80px' }, { height: '0px' }]);
  height = 32; // Visible midway through collapsing, before another click.
  const interrupted = captureFolderPanels(root);
  height = 80; animateFolderPanels(root, interrupted);
  assert.deepEqual(animations[1], [{ height: '32px' }, { height: '80px' }]);
});

test('initial loading, unchanged heights and reduced motion do not animate sidebar folders', () => {
  const panel = { id: 'folder-campus', getBoundingClientRect: () => ({ height: 80 }), animate: assert.fail };
  const root = { querySelectorAll: () => [panel] };
  animateFolderPanels(root, new Map());
  animateFolderPanels(root, captureFolderPanels(root));
  animateFolderPanels(root, new Map([['folder-campus', 0]]), true);
});

test('folder pages merge without duplicates and reply updates keep creation order', async () => {
  const pages = [page([listed('3'), listed('2')], 2, true), page([listed('2'), listed('1')], 4)];
  const urls = [];
  const store = createFolderStore({ userId: () => 'owner', request: async url => {
    urls.push(url); return url === 'folders' ? { folders: [] } : pages.shift();
  } });
  await store.load();
  await store.loadGroup(null, true);
  store.observe(saved('1', null, { title: 'Updated during a reply' }));
  assert.deepEqual(store.group().items.map(item => item.id), ['3', '2', '1']);
  assert.equal(store.group().items[2].question, 'Updated during a reply');
  assert.equal(urls.at(-1), 'sessions?folder=none&offset=2');
});

test('old account responses cannot populate the new account sidebar', async () => {
  let owner = 'first', resolve;
  const store = createFolderStore({ userId: () => owner, request: url => owner === 'first'
    ? new Promise(done => { resolve = done; }) : Promise.resolve(url === 'folders' ? { folders: [] } : page([])) });
  const pending = store.load();
  owner = 'second';
  await store.load();
  resolve({ folders: [{ id: 'private', name: 'First account only' }] });
  await pending;
  assert.equal(store.owner(), 'second');
  assert.deepEqual(store.folders(), []);
  assert.deepEqual(store.group().items, []);
});

test('restoring a filed session does not count it twice; new sessions count once', async () => {
  const store = createFolderStore({ userId: () => 'owner', request: async url => url === 'folders'
    ? { folders: [{ id: 'campus', name: 'Campus', count: 4 }] } : page([]) });
  await store.load();
  store.observe(saved('1', 'campus'));
  store.observe(saved('1', 'campus'));
  assert.equal(store.folders()[0].count, 4);
  store.observe(saved('2', 'campus'), true);
  store.observe(saved('2', 'campus'));
  assert.equal(store.folders()[0].count, 5);
  store.observe(saved('2', 'campus', { archived: true }));
  assert.equal(store.folders()[0].count, 4);
  assert.deepEqual(store.group('campus').items.map(item => item.id), ['1']);
});

test('a move refreshes source pagination and a late draft response cannot undo filing', async () => {
  const calls = []; let filed = false;
  const store = createFolderStore({ userId: () => 'owner', request: async (url, method, data) => {
    calls.push({ url, method, data });
    if (url === 'folders') return { folders: [{ id: 'campus', name: 'Campus', count: filed ? 1 : 0 }] };
    if (method === 'PATCH') { filed = true; return { ok: true, folderId: data.folderId }; }
    return page(filed ? [] : [listed('1')]);
  } });
  await store.load();
  await store.move(listed('1'), 'campus');
  store.observe(saved('1', null, { draft: 'A late save that began before the move' }));
  assert.deepEqual(store.group().items, []);
  assert.equal(store.group('campus').items[0].folderId, 'campus');
  assert.equal(store.folders()[0].count, 1);
  assert.equal(calls.filter(call => call.url === 'sessions?folder=none&offset=0').length, 2);
  assert.ok(calls.some(call => call.method === 'PATCH' && call.data.folderId === 'campus'));
});

test('a failed folder page keeps previous conversations and is retryable', async () => {
  let fail = false;
  const store = createFolderStore({ userId: () => 'owner', request: async url => {
    if (url === 'folders') return { folders: [] };
    if (fail) throw new Error('offline');
    return page([listed('1')]);
  } });
  await store.load(); fail = true;
  await store.loadGroup();
  assert.equal(store.group().error, true);
  assert.equal(store.group().busy, false);
  assert.equal(store.group().items.length, 1);
  fail = false; await store.loadGroup();
  assert.equal(store.group().error, false);
});

test('a list response that started before filing cannot put the conversation back', async () => {
  let resolve, calls = 0;
  const store = createFolderStore({ userId: () => 'owner', request: async (url, method) => {
    if (url === 'folders') return { folders: [{ id: 'campus', name: 'Campus', count: 1 }] };
    if (method === 'PATCH') return { ok: true };
    calls += 1;
    if (calls === 2) return new Promise(done => { resolve = done; });
    return page(calls === 1 ? [listed('1')] : []);
  } });
  await store.load();
  const stale = store.loadGroup();
  await store.move(listed('1'), 'campus');
  resolve(page([listed('1')])); await stale;
  assert.deepEqual(store.group().items, []);
  assert.deepEqual(store.group('campus').items.map(item => item.id), ['1']);
  assert.equal(store.group().busy, false);
});

test('folder positions follow new conversation creation and ignore later reply timestamps', async () => {
  const store = createFolderStore({ userId: () => 'owner', request: async url => url === 'folders'
    ? { folders: [
      { id: 'older', count: 1, createdAt: '2026-10-01', latestConversationAt: '2026-10-02' },
      { id: 'newer', count: 1, createdAt: '2026-10-03', latestConversationAt: '2026-10-04' },
    ] } : page([]) });
  await store.load();
  assert.deepEqual(store.folders().map(item => item.id), ['newer', 'older']);
  store.observe(saved('2', 'older', { updatedAt: '2026-12-30' }));
  assert.deepEqual(store.folders().map(item => item.id), ['newer', 'older']);
  store.observe(saved('5', 'older'), true);
  assert.deepEqual(store.folders().map(item => item.id), ['older', 'newer']);
  store.observe(saved('4', 'newer', { updatedAt: '2027-01-01' }));
  assert.deepEqual(store.folders().map(item => item.id), ['older', 'newer']);
});

test('sidebar folders only expand and collapse without selecting a destination or changing the active conversation', async () => {
  const names = ['location', 'window', 'localStorage', 'fetch'];
  const originals = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const replaced = [], state = {};
  Object.assign(globalThis, {
    location: { href: 'https://preview.example/app?folder=campus' },
    window: { history: { replaceState: (...args) => replaced.push(args) } },
    localStorage: { getItem: () => null, setItem: () => {} },
    fetch: async url => ({ ok: true, json: async () => String(url).endsWith('/folders')
      ? { folders: [{ id: 'campus', name: 'Campus' }, { id: 'personal', name: 'Personal' }] }
      : page([listed('1')]) }),
  });
  try {
    const library = createFolders({ basePath: '/', appPath: '/app', getUser: () => ({ id: 'owner' }), getState: () => state,
      open: () => assert.fail('Expanding a folder must not open a dialog'), dialog: {}, notify: assert.fail,
      showPopover: () => {}, closePopover: () => {}, navigate: () => assert.fail('Expanding a folder must not navigate'), changed: () => {} });
    await library.load();
    const button = { dataset: { workspace: 'folder-toggle', folderId: 'personal' } };
    await library.handleAction(button);
    assert.equal(library.selectedId(), 'campus');
    assert.equal(state.sessionId, undefined);
    state.sessionId = '1';
    await library.handleAction(button);
    assert.equal(state.sessionId, '1');
    assert.ok(library.render().includes('aria-current="page"'));
    assert.deepEqual(replaced, []);
  } finally {
    for (const [name, descriptor] of originals) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  }
});
