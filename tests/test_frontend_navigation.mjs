import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createAppNavigation, createViewPositions } from '../server/gizmoapp_server/static/app/text/navigation.js';
import { captureFolderPanels, animateFolderPanels } from '../server/gizmoapp_server/static/app/text/folders.js';

const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};
const tick = () => new Promise(done => setImmediate(done));
function harness(options = {}) {
  let location = 'https://agora.test/app/debate?session=original';
  const changes = [], busy = [];
  const history = {
    state: {},
    pushState: (_, __, url) => { location = new URL(url, location).href; changes.push(['push', location]); },
    replaceState: (_, __, url) => { location = new URL(url, location).href; changes.push(['replace', location]); },
  };
  const navigation = createAppNavigation({ appPath: '/app', location: () => location, history,
    prepare: async () => true, transition: async (_, context) => context.commit(),
    busy: value => busy.push(value), ...options });
  return { navigation, changes, busy, location: () => location, pop: url => { location = new URL(url, location).href; } };
}

test('current conversation clicks do not save, reload or transition; non-app links keep native navigation', async () => {
  const h = harness({ prepare: assert.fail, transition: assert.fail });
  assert.equal(await h.navigation.navigate(h.location()), true);
  assert.equal(h.navigation.navigate('https://elsewhere.test/app'), null);
  assert.equal(h.navigation.navigate('/'), null);
  assert.equal(h.navigation.navigate('/app/unknown'), null);
  assert.deepEqual(h.changes, []);
  assert.deepEqual(h.busy, []);
});

test('rapid conversation clicks wait for the draft save and load only the latest requested route', async () => {
  const save = deferred(), visited = [];
  let prepares = 0;
  const h = harness({ prepare: () => { prepares++; return save.promise; },
    transition: async (url, context) => { visited.push(url.search); return context.commit(); } });
  const first = h.navigation.navigate('/app/debate?session=first');
  const latest = h.navigation.navigate('/app/debate?session=latest');
  assert.deepEqual(h.changes, []);
  save.resolve(true);
  assert.equal(await first, true);
  assert.equal(await latest, true);
  assert.equal(prepares, 1);
  assert.deepEqual(visited, ['?session=latest']);
  assert.equal(h.changes.length, 1);
  assert.deepEqual(h.busy, [true, false]);
});

test('New debate can explicitly reset the current question without a duplicate history entry', async () => {
  let resets = 0;
  const h = harness({ transition: async (_, context) => { resets++; return context.commit(); } });
  await h.navigation.navigate('/app');
  await h.navigation.navigate('/app', { force: true });
  assert.equal(resets, 2);
  assert.deepEqual(h.changes.map(change => change[0]), ['push', 'replace']);
});

test('a slow old session response cannot replace the last conversation clicked', async () => {
  const response = deferred(), visited = [];
  const h = harness({ transition: async (url, context) => {
    visited.push(url.search);
    if (url.search.includes('slow')) await response.promise;
    if (!context.isCurrent()) return false;
    return context.commit();
  } });
  const first = h.navigation.navigate('/app/debate?session=slow');
  await tick();
  h.navigation.navigate('/app/debate?session=latest');
  response.resolve();
  await first;
  assert.deepEqual(visited, ['?session=slow', '?session=latest']);
  assert.deepEqual(h.changes, [['push', 'https://agora.test/app/debate?session=latest']]);
});

test('failed saving keeps the current view; a blocked Back restores its committed address', async () => {
  const h = harness({ prepare: async () => false, transition: assert.fail });
  assert.equal(await h.navigation.navigate('/app/debate?session=other'), false);
  assert.equal(h.changes.length, 0);
  h.pop('/app/debate?session=previous');
  assert.equal(await h.navigation.navigate(h.location(), { pop: true }), false);
  assert.deepEqual(h.changes, [['replace', 'https://agora.test/app/debate?session=original']]);
});

test('Back and Forward change the content without creating additional history entries', async () => {
  const visited = [];
  const h = harness({ transition: async (url, context) => { visited.push(url.search); return context.commit(); } });
  await h.navigation.navigate('/app/debate?session=next');
  h.pop('/app/debate?session=original');
  await h.navigation.navigate(h.location(), { pop: true });
  h.pop('/app/debate?session=next');
  await h.navigation.navigate(h.location(), { pop: true });
  assert.deepEqual(visited, ['?session=next', '?session=original', '?session=next']);
  assert.equal(h.changes.length, 1);
});

test('account sign-out cancels old responses without releasing a new navigation busy state', async () => {
  const oldResponse = deferred(), newResponse = deferred();
  const h = harness({ transition: async (url, context) => {
    await (url.search.includes('private') ? oldResponse.promise : newResponse.promise);
    return context.commit();
  } });
  const old = h.navigation.navigate('/app/debate?session=private');
  await tick();
  h.navigation.cancel();
  const next = h.navigation.navigate('/app?folder=public');
  await tick();
  oldResponse.resolve(); await old;
  assert.equal(h.busy.at(-1), true);
  assert.deepEqual(h.changes, []);
  newResponse.resolve(); await next;
  assert.equal(h.busy.at(-1), false);
  assert.equal(h.changes[0][1], 'https://agora.test/app?folder=public');
});

test('normalizing a route commits once, and a rendered unavailable conversation still finishes navigation', async () => {
  const h = harness({ transition: async (url, context) => {
    url.pathname = '/app/participants';
    assert.equal(context.commit(), true);
    assert.equal(context.commit(), true);
    return false;
  } });
  assert.equal(await h.navigation.navigate('/app/debate?session=not-started'), true);
  assert.deepEqual(h.changes, [['push', 'https://agora.test/app/participants?session=not-started']]);
});

test('reading positions stay separate by conversation and account, are copied, bounded and cleared', () => {
  const cache = createViewPositions(2), position = { messageTop: 250, followLatest: false };
  cache.save('owner:first:debate', position);
  position.messageTop = 0;
  cache.save('owner:second:debate', { messageTop: 900, followLatest: true });
  assert.equal(cache.get('owner:first:debate').messageTop, 250);
  const saved = cache.get('owner:first:debate'); saved.messageTop = 30;
  assert.equal(cache.get('owner:first:debate').messageTop, 250);
  assert.equal(cache.get('other:first:debate'), null);
  cache.save('owner:third:debate', { messageTop: 0 });
  assert.equal(cache.get('owner:first:debate'), null);
  cache.clear();
  assert.equal(cache.get('owner:second:debate'), null);
});

const source = readFileSync(new URL('../server/gizmoapp_server/static/app/text/debate.js', import.meta.url), 'utf8');
test('an old allowance check cannot submit a draft into a conversation opened while it was pending', async () => {
  const allowance = deferred();
  const state = { sessionId: 'first', navigationVersion: 0, stage: 'debate', readOnly: false };
  const context = { state, workspace: { ensureReplyAvailable: () => allowance.promise } };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('async function submitChatMessage()'), source.indexOf('function retryRoleReply()')) + ';globalThis.run=submitChatMessage;', context);
  const submission = context.run();
  state.navigationVersion++; state.sessionId = 'second';
  allowance.resolve(true);
  assert.equal(await submission, false);
  assert.equal(state.sessionId, 'second');
});

test('rendering another conversation and returning restores its reading position; new replies respect reading intent', () => {
  const cache = createViewPositions();
  let list = { scrollTop: 240, scrollHeight: 2000, clientHeight: 500 };
  let roles = { scrollTop: 70, scrollLeft: 30, children: [], querySelector: () => null };
  const state = { sessionId: 'first', stage: 'debate', selectedRoleId: 'one', roles: [] };
  const appView = {
    querySelector: selector => selector === '.message-list' ? list : selector === '.role-list' ? roles : null,
    querySelectorAll: () => [],
    set innerHTML(_) {
      list = { scrollTop: 0, scrollHeight: 2500, clientHeight: 500 };
      roles = { scrollTop: 0, scrollLeft: 0, children: [], querySelector: () => null };
    },
  };
  const context = { state, viewPositions: cache, renderedViewKey: 'owner:first:debate', appView,
    viewKey: () => `owner:${state.sessionId}:debate`, workspace: { renderSidebar: () => {} },
    stageNavigation: {}, document: { body: { dataset: {} }, activeElement: {}, querySelector: () => null },
    location: { pathname: '/app/debate', search: '' }, appNavigation: { sync: () => {} },
    MESSAGE_LIST_BOTTOM_THRESHOLD_PX: 80, renderDebate: () => '', renderSaveStatus: () => '',
    renderStepper: () => '', renderDock: () => '', resizeComposer: () => {}, resizeQuestionInput: () => {}, renderSummaryDialog: () => {} };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function rememberViewPosition()'), source.indexOf('function setNavigationBusy('))
    + source.slice(source.indexOf('function isMessageListNearBottom('), source.indexOf('function syncRoleGenerationControls(')), context);
  context.rememberViewPosition();
  state.sessionId = 'second'; context.render();
  assert.equal(list.scrollTop, 2500); // A new conversation initially follows its latest reply.
  list.scrollTop = 900; context.rememberViewPosition();
  state.sessionId = 'first'; context.render();
  assert.equal(list.scrollTop, 240);
  assert.equal(roles.scrollTop, 70);
  assert.equal(roles.scrollLeft, 30);
  context.render();
  assert.equal(list.scrollTop, 240); // A reply/update must not interrupt reading older messages.
  list.scrollTop = 2000; context.render();
  assert.equal(list.scrollTop, 2500); // Reading at the bottom follows new messages.
});

test('reply updates leave an unchanged sidebar DOM intact; a changed selection retains sidebar scroll', () => {
  const workspaceSource = readFileSync(new URL('../server/gizmoapp_server/static/app/text/workspace.js', import.meta.url), 'utf8');
  const state = { sessionId: 'first' }, library = { scrollTop: 180 };
  let writes = 0;
  const sidebar = { contains: () => false, querySelector: () => library, querySelectorAll: () => [], set innerHTML(_) { writes++; library.scrollTop = 0; } };
  const user = { id: 'owner', name: 'Fixture' };
  const context = { sidebar, sidebarMarkup: '', document: { activeElement: null }, getState: () => state,
    currentProfile: () => user, account: { user: () => user, usage: () => ({ remaining: 50 }) },
    historyUserId: 'owner', historyTask: null, folders: { render: () => state.sessionId },
    captureFolderPanels, animateFolderPanels, window: { matchMedia: () => ({ matches: false }) },
    esc: String, icon: () => '', avatar: () => '', basePath: '/', LOGO_URL: '/logo.png' };
  vm.createContext(context);
  vm.runInContext(workspaceSource.slice(workspaceSource.indexOf('  function renderSidebar()'), workspaceSource.indexOf('  function open(')), context);
  context.renderSidebar();
  state.messages = [{ body: 'A streamed reply completed.' }];
  context.renderSidebar();
  assert.equal(writes, 1);
  assert.equal(library.scrollTop, 180);
  state.sessionId = 'second'; context.renderSidebar();
  assert.equal(writes, 2);
  assert.equal(library.scrollTop, 180);
});
