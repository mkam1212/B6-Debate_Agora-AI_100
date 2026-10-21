import test from 'node:test';
import assert from 'node:assert/strict';
import { rememberStartIntent, consumeStartIntent } from '../server/gizmoapp_server/static/app/text/start-flow.js';
const store = () => { const data = new Map(); return {getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)}; };
test('a home submission starts once, preserving the exact topic',()=>{const s=store();rememberStartIntent(s,'  A real decision? ',100);assert.equal(consumeStartIntent(s,'A real decision?',101),true);assert.equal(consumeStartIntent(s,'A real decision?',102),false);});
test('an arbitrary URL with no home submission cannot auto-run',()=>assert.equal(consumeStartIntent(store(),'A costly arbitrary topic',100),false));
test('a mismatched topic cannot use an existing intent',()=>{const s=store();rememberStartIntent(s,'Original',100);assert.equal(consumeStartIntent(s,'Different',101),false);assert.equal(consumeStartIntent(s,'Original',102),false);});
test('expired or future intents cannot auto-run',()=>{for(const now of [99,120101]){const s=store();rememberStartIntent(s,'Topic',100);assert.equal(consumeStartIntent(s,'Topic',now),false);}});
test('unavailable storage leaves manual start usable',()=>{const s={setItem(){throw Error();},getItem(){throw Error();},removeItem(){throw Error();}};assert.doesNotThrow(()=>rememberStartIntent(s,'Topic'));assert.equal(consumeStartIntent(s,'Topic'),false);});
