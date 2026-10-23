import test from 'node:test';
import assert from 'node:assert/strict';
import { AGENT_TEMPLATES, templateAgent, generatedAgent, createAgentTemplateDraft } from '../server/gizmoapp_server/static/app/text/agent-templates.js';

test('templates cover the five role types and fit the agent editor limits', () => {
  assert.deepEqual(AGENT_TEMPLATES.map(item => item.identity), ['Analyst', 'Challenger', 'Synthesizer', 'Builder', 'Skeptic']);
  assert.equal(new Set(AGENT_TEMPLATES.map(item => item.id)).size, AGENT_TEMPLATES.length);
  for (const { name, identity, prompt } of AGENT_TEMPLATES) {
    assert.ok(name && name.length <= 48);
    assert.ok(identity.length <= 96);
    assert.ok(prompt && prompt.length <= 1000);
  }
});

test('a template keeps its name unless an agent already uses it', () => {
  const [analyst] = AGENT_TEMPLATES;
  assert.equal(templateAgent(analyst, ['Jordan']).name, 'Avery');
  assert.equal(templateAgent(analyst, ['avery']).name, 'Avery 2');
  assert.equal(templateAgent(analyst, ['Avery', ' AVERY 2 ']).name, 'Avery 3');
  assert.equal(templateAgent(analyst).prompt, analyst.prompt);
});

test('generated agents are accepted only when they fit the editor fields', async () => {
  assert.deepEqual(generatedAgent({ id: 'x', name: ' Priya ', identity: 'Transit rider', prompt: ' Weighs fares against service. ', roleType: 'Analyst' }),
    { name: 'Priya', identity: 'Transit rider', prompt: 'Weighs fares against service.', roleType: 'Analyst' });
  assert.equal(generatedAgent(null), null);
  assert.equal(generatedAgent({ name: '', identity: 'a', prompt: 'b' }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 'a', prompt: '' }), null);
  assert.equal(generatedAgent({ name: 'A'.repeat(49), identity: 'a', prompt: 'b' }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 'a'.repeat(97), prompt: 'b' }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 'a', prompt: 'b'.repeat(1001) }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 7, prompt: 'b', roleType: 'Analyst' }).identity, '');
  assert.equal(generatedAgent({ name: 'A', identity: 'a', prompt: 'b', roleType: 'Wizard' }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 'a', prompt: 'b', roleType: 'Analyst' }, 'Skeptic'), null);
});
test('every template names a supported role type', () => {
  assert.deepEqual(AGENT_TEMPLATES.map(item => item.roleType).sort(), ['Analyst', 'Builder', 'Challenger', 'Skeptic', 'Synthesizer']);
});

test('duplicate generated names stay within the editor limit when numbered', () => {
  const name = 'A'.repeat(48);
  const profile = templateAgent({ name, identity: 'Planner', prompt: 'Check service.', roleType: 'Builder' }, [name, `${'A'.repeat(46)} 2`]);
  assert.equal(profile.name.length, 48);
  assert.ok(profile.name.endsWith(' 3'));
  assert.equal(profile.roleType, 'Builder');
});

const skeptic = AGENT_TEMPLATES.find(template => template.roleType === 'Skeptic');
function draftFixture(generate) {
  const fields = { name: '', identity: '', prompt: '' };
  const changes = [];
  const draft = createAgentTemplateDraft({ generate, readFields: () => ({ ...fields }), applyFields: values => Object.assign(fields, values), existingNames: () => ['Priya'], changed: state => changes.push(state) });
  return { draft, fields, changes };
}
const generated = { name: 'Priya', identity: 'Transit rider', prompt: 'Check service gaps.', roleType: 'Skeptic' };

test('a delayed result preserves typing, including editing a field back to its original value', async () => {
  let finish;
  const { draft, fields } = draftFixture(() => new Promise(resolve => { finish = resolve; }));
  const pending = draft.select(skeptic);
  assert.equal(draft.state.busy, true);
  fields.name = 'My name'; draft.edit('name');
  fields.prompt = 'temporary'; draft.edit('prompt'); fields.prompt = '';
  finish(generated); await pending;
  assert.equal(fields.name, 'My name');
  assert.equal(fields.prompt, '');
  assert.equal(fields.identity, 'Transit rider');
  assert.equal(draft.state.roleType, 'Skeptic');
  assert.equal(draft.state.busy, false);
});

test('an untouched generated name is made unique and its approach survives', async () => {
  const { draft, fields } = draftFixture(async () => generated);
  await draft.select(skeptic);
  assert.equal(fields.name, 'Priya 2');
  assert.equal(fields.prompt, generated.prompt);
  assert.equal(draft.state.roleType, 'Skeptic');
});

test('a busy draft ignores repeated template clicks instead of making more model calls', async () => {
  let finish; let calls = 0;
  const { draft } = draftFixture(() => { calls += 1; return new Promise(resolve => { finish = resolve; }); });
  const pending = draft.select(skeptic);
  await draft.select(AGENT_TEMPLATES[0]);
  assert.equal(calls, 1);
  assert.equal(draft.state.selectedId, skeptic.id);
  finish(generated); await pending;
});

test('closing an editor ignores its late result without changing another draft', async () => {
  let finish;
  const old = draftFixture(() => new Promise(resolve => { finish = resolve; }));
  const pending = old.draft.select(skeptic);
  old.draft.dispose();
  const fresh = draftFixture(async () => ({ ...generated, roleType: 'Analyst' }));
  await fresh.draft.select(AGENT_TEMPLATES[0]);
  finish(generated); await pending;
  assert.deepEqual(old.fields, { name: '', identity: '', prompt: '' });
  assert.equal(old.changes.length, 1);
  assert.equal(fresh.draft.state.roleType, 'Analyst');
  assert.equal(fresh.fields.name, 'Priya 2');
});

test('generation failure uses the selected local template and preserves concurrent edits', async () => {
  let fail;
  const { draft, fields } = draftFixture(() => new Promise((_resolve, reject) => { fail = reject; }));
  const pending = draft.select(skeptic);
  fields.identity = 'My perspective'; draft.edit('identity');
  fail(new Error('offline')); await pending;
  assert.equal(fields.name, skeptic.name);
  assert.equal(fields.identity, 'My perspective');
  assert.equal(fields.prompt, skeptic.prompt);
  assert.equal(draft.state.roleType, 'Skeptic');
  assert.match(draft.state.description, /Starter template used/);
  assert.equal(draft.state.busy, false);
});
