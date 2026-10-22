import test from 'node:test';
import assert from 'node:assert/strict';
import { AGENT_TEMPLATES, templateAgent } from '../server/gizmoapp_server/static/app/text/agent-templates.js';

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
  const { generatedAgent } = await import('../server/gizmoapp_server/static/app/text/agent-templates.js');
  assert.deepEqual(generatedAgent({ id: 'x', name: ' Priya ', identity: 'Transit rider', prompt: ' Weighs fares against service. ', roleType: 'Analyst' }),
    { name: 'Priya', identity: 'Transit rider', prompt: 'Weighs fares against service.' });
  assert.equal(generatedAgent(null), null);
  assert.equal(generatedAgent({ name: '', identity: 'a', prompt: 'b' }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 'a', prompt: '' }), null);
  assert.equal(generatedAgent({ name: 'A'.repeat(49), identity: 'a', prompt: 'b' }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 'a'.repeat(97), prompt: 'b' }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 'a', prompt: 'b'.repeat(1001) }), null);
  assert.equal(generatedAgent({ name: 'A', identity: 7, prompt: 'b' }).identity, '');
});
test('every template names a supported role type', () => {
  assert.deepEqual(AGENT_TEMPLATES.map(item => item.roleType).sort(), ['Analyst', 'Builder', 'Challenger', 'Skeptic', 'Synthesizer']);
});
