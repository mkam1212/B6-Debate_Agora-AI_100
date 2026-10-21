import test from 'node:test';
import assert from 'node:assert/strict';
import { AVATAR_COLORS, avatar, avatarColorPicker, avatarStyle, getAvatarColor, randomAvatarColor } from '../server/gizmoapp_server/static/app/text/ui.js';

test('saved colors follow the person when agent order changes', () => {
  const person = { id: 'agent-one', name: 'Maya', avatarColor: '#2473BD' };
  assert.equal(avatar(person, 0), avatar(person, 7));
  assert.match(avatar(person), /color:#2473bd/);
});

test('earlier selections use the refreshed palette without adding a ninth choice', () => {
  const person = { name: 'Maya', avatarColor: '#8260A4' };
  assert.equal(getAvatarColor(person), '#8b5cf6');
  assert.equal((avatarColorPicker(person).match(/type="radio"/g) || []).length, 8);
  assert.equal(randomAvatarColor(['#4667A5', ...AVATAR_COLORS.slice(2).map(([, color]) => color)]), AVATAR_COLORS[1][1]);
});

test('invalid stored styles cannot inject avatar markup or CSS', () => {
  const html = avatar({ name: '<script>', avatarColor: '#fff;display:none' });
  assert.ok(!html.includes('display:none'));
  assert.ok(!html.includes('<script>'));
  assert.match(avatarStyle({ avatarColor: null }), /^background:rgb\(\d+,\d+,\d+\);color:#[0-9a-f]{6}$/);
});

test('initial colors use available choices and remain valid when all colors are in use', () => {
  const colors = AVATAR_COLORS.map(([, color]) => color);
  assert.equal(randomAvatarColor(colors.slice(1)), colors[0]);
  assert.ok(colors.includes(randomAvatarColor(colors)));
});
