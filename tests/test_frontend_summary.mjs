import test from 'node:test';
import assert from 'node:assert/strict';
import { summarySections, renderSummaryCopy } from '../server/gizmoapp_server/static/app/text/summary-view.js';

test('summary sections preserve the saved content and do not invent agreement', () => {
  const sections = summarySections('Main views: Nina favors lower fares.\nDisagreement: Jack prioritizes frequency.\nOpen question: Who pays?\nNext-step suggestion: Try a limited pilot.');
  assert.deepEqual(sections.map(s => s.title), ['Main views', 'Disagreement', 'Open question', 'Next-step suggestion']);
  assert.equal(sections[0].lines.join(''), 'Nina favors lower fares.');
  assert.equal(sections[2].lines.join(''), 'Who pays?');
  assert.equal(summarySections('An unstructured but valid summary.')[0].title, '');
});

test('summary presentation supports headings, paragraphs and bullet lists', () => {
  const markup = renderSummaryCopy('## The disagreement\n**Reliability** matters.\n\n- Protect frequency\n- Measure access');
  assert.match(markup, /<h3>The disagreement<\/h3>/);
  assert.match(markup, /<strong>Reliability<\/strong>/);
  assert.match(markup, /<ul><li>Protect frequency<\/li><li>Measure access<\/li><\/ul>/);
});

test('model text and headings cannot introduce executable markup', () => {
  const markup = renderSummaryCopy('## <img src=x onerror=alert(1)>\n<script>alert(1)</script>\n**<svg onload=alert(2)>**');
  assert.doesNotMatch(markup, /<(?:img|script|svg)\b/);
  assert.match(markup, /&lt;img/);
  assert.match(markup, /&lt;script/);
});
