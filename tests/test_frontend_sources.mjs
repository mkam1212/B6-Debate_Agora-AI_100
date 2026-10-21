import test from 'node:test';
import assert from 'node:assert/strict';
import { collectSources, sourceFormat, trustedCitations, renderCitedText, streamingText } from '../server/gizmoapp_server/static/app/text/sources.js';

test('web sources are deduplicated and retain all citing agents', () => {
  const roles = [{ id: 'a', name: 'Maya' }, { id: 'b', name: 'Alex' }];
  const messages = [
    { kind: 'participant', roleId: 'a', citations: [{ sourceId:'web-'+'a'.repeat(20), fetchedAt:100, url: 'https://example.com/report#one', title: 'Report', excerpt: 'Evidence' }] },
    { kind: 'participant', roleId: 'b', citations: [{ sourceId:'web-'+'a'.repeat(20), fetchedAt:100, url: 'https://example.com/report#two' }] },
    { kind: 'user', citations: [{ url: 'https://other.example/' }] },
  ];
  const sources = collectSources(messages, roles);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].title, 'Report');
  assert.deepEqual([...sources[0].authors], ['Maya', 'Alex']);
});

test('unsafe links and ordinary conversation text do not become web citations', () => {
  const messages = [{ kind: 'participant', body: 'https://example.com is a guess.', citations: [
    { url: 'javascript:alert(1)' }, { url: 'data:text/html,test' }, { url: 'https://user:pass@example.com' }, { url: 'not a URL' },
  ] }];
  assert.equal(collectSources(messages).length, 0);
});

test('source icons distinguish direct documents from webpages using the URL path', () => {
  assert.equal(sourceFormat('https://example.org/report.PDF?download=1#page=2'), 'PDF');
  assert.equal(sourceFormat('https://example.org/report%2epdf'), 'PDF');
  assert.equal(sourceFormat('https://example.org/search?q=report.pdf'), '');
  assert.equal(sourceFormat('https://example.org/report.pdf/article'), '');
  assert.equal(sourceFormat('javascript:report.pdf'), '');
  assert.equal(sourceFormat('not a URL'), '');
});


test('only server-retrieved citations become clickable sources and markup stays inert', () => {
  assert.deepEqual(trustedCitations([{url:'https://invented.example/'}]),[]);
  const sources=[{sourceId:'web-'+'a'.repeat(20),fetchedAt:100,url:'https://example.org/docs',title:'<img onerror=alert(1)>'}];
  const html=renderCitedText('<script>bad</script> Claim.[1]',sources);
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('class="message-citation"'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(!renderCitedText('Claim.[1]',[]).includes('<a'));
  assert.equal(collectSources([{kind:'participant',citations:[{url:'https://invented.example/'}]}]).length,0);
});

test('provider citation tokens stay hidden while text streams', () => {
  assert.equal(streamingText('A claim. citeturn0search0'),'A claim. ');
  assert.equal(streamingText('A claim. citeturn0'),'A claim. ');
  assert.equal(streamingText('A claim. [Docs](https://example.org/docs)'),'A claim. ');
  assert.equal(streamingText('A claim. [Docs](https://example.org'),'A claim. ');
});
