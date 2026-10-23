import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { isDialogBackdropClick, mentionAtCaret, mentionedRole, insertRoleMention, withConversationStyle, updateRecentHistory, discussionTurnDelay } from '../server/gizmoapp_server/static/app/text/interaction-utils.js';

test('dialog padding stays open; only a click beyond its bounds counts as backdrop', () => {
  const dialog = { getBoundingClientRect: () => ({left:100,right:500,top:80,bottom:600}) };
  assert.equal(isDialogBackdropClick({target:dialog,clientX:115,clientY:95},dialog),false);
  assert.equal(isDialogBackdropClick({target:dialog,clientX:99,clientY:95},dialog),true);
  assert.equal(isDialogBackdropClick({target:{},clientX:99,clientY:95},dialog),false);
});
const roles = [{id:'one',name:'Alex'},{id:'two',name:'Alex Chen'},{id:'three',name:'Grace'}];
test('mentions resolve complete names, .ai suffixes and Chinese punctuation, without treating email as a mention', () => {
  assert.equal(mentionedRole('@Alex Chen, thoughts?',roles)?.id,'two');
  assert.equal(mentionedRole('Maybe @grace.ai，试试？',roles)?.id,'three');
  assert.equal(mentionedRole('Send it to alex@example.com',roles),null);
  assert.equal(mentionedRole('@Alexander, thoughts?',roles),null);
});
test('mention insertion replaces only the query and preserves text and caret position', () => {
  const text = 'What if @Gr first?';
  const mention = mentionAtCaret(text,11);
  assert.deepEqual(mention,{query:'Gr',range:[8,11]});
  const inserted=insertRoleMention(text,mention.range,'Grace');
  assert.equal(inserted.text,'What if @Grace first?');
  assert.equal(inserted.text.slice(0,inserted.caret),'What if @Grace');
  assert.equal(mentionAtCaret('alex@example.com',16),null);
});
test('conversation style replaces the old style once, preserving the role’s own instructions', () => {
  const concise=withConversationStyle('Check the budget.','Concise');
  const challenging=withConversationStyle(concise,'Challenging');
  assert.equal((challenging.match(/\[Conversation style:/g)||[]).length,1);
  assert.ok(challenging.startsWith('Check the budget.\n\n'));
  assert.ok(challenging.includes('specific claim'));
  assert.ok(!challenging.includes('single short sentence'));
});

test('recent conversations keep their position after being opened, edited or receiving replies', () => {
  const items=[{id:'newer',question:'Newer?',count:0},{id:'older',question:'Older?',count:1}];
  const updated=updateRecentHistory(items,{id:'older',question:'Edited?',count:3});
  assert.deepEqual(updated.map(item=>item.id),['newer','older']);
  assert.equal(updated[1].count,3);
  assert.equal(items[1].count,1);
  const added=updateRecentHistory(updated,{id:'newest',question:'Newest?'},2);
  assert.deepEqual(added.map(item=>item.id),['newest','newer']);
});

test('turn pacing leaves reading time, scales with English or Chinese text and stays bounded', () => {
  assert.equal(discussionTurnDelay(),1800);
  assert.ok(discussionTurnDelay('A short reply.')>1800);
  assert.ok(discussionTurnDelay('word '.repeat(25))>discussionTurnDelay('word '.repeat(5)));
  assert.ok(discussionTurnDelay('这需要先确认交通服务频率是否会受到影响。')>2000);
  assert.equal(discussionTurnDelay('word '.repeat(200)),4200);
});

const source=readFileSync(new URL('../server/gizmoapp_server/static/app/text/debate.js',import.meta.url),'utf8');
function loadFunction(start,end,context) {
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end))+`;globalThis.run=${/function (\w+)/.exec(start)[1]};`,context);
  return context.run;
}
function baseState(extra={}) {
  return {stage:'participants',paused:true,question:'A real question?',roles:[{id:'one',name:'Alex'}],messages:[],draftAttachments:[],pendingUserMessages:[],unsavedMessageIds:new Set(),composeDraft:'',sessionId:'session',saveTimer:null,discussionLoopVersion:0,...extra};
}
test('starting from setup clears a stale pause and starts the first reply immediately', async () => {
  const state=baseState(); const calls=[];
  const context={state,workspace:{ensureReplyAvailable:async()=>true},showReplyError:assert.fail,render:()=>{},window:{history:{pushState:()=>{}}},stageUrl:()=>'/debate',nextReplyForSavedConversation:()=>({roleId:null,initial:true}),ensureDiscussionLoop:reply=>calls.push(reply)};
  await loadFunction('async function startDebate()','async function submitChatMessage()',context)();
  assert.equal(state.paused,false); assert.equal(state.debateStarted,true); assert.equal(state.stage,'debate'); assert.equal(calls.length,1);
});
test('sending from a paused conversation saves and queues the message, then resumes automatically', async () => {
  const state=baseState({stage:'debate',composeDraft:'Try one route first.'}); const calls=[];
  const context={state,MAX_CHAT_MESSAGES:100,workspace:{ensureReplyAvailable:async()=>true,uploading:()=>false},roleForChatMessage:()=>null,newChatMessageId:()=> 'new-message',markSummaryDirty:()=>{},rememberComposeDraft:()=>{},render:()=>{},saveSessionNow:async()=>true,ensureDiscussionLoop:(...args)=>calls.push(args)};
  await loadFunction('async function submitChatMessage()','function retryRoleReply()',context)();
  assert.equal(state.paused,false); assert.equal(state.pendingUserMessages.length,1); assert.equal(state.pendingUserMessages[0].body,'Try one route first.'); assert.equal(state.composeDraft,''); assert.equal(calls[0][1],true);
});
test('unselected streaming placeholders never borrow the first agent’s identity', () => {
  const state=baseState({streamingReply:{roleId:'',body:''}});
  const run=loadFunction('function renderStreamingReply()','function renderPendingChatMessage(',{state});
  const markup=run(); assert.ok(markup.includes('Preparing a reply')); assert.ok(!markup.includes('Alex'));
});
test('changing conversation style during a reply queues it without changing the active role prompt', () => {
  const state=baseState({replyPending:true,roleEditVersion:4,roles:[{id:'one',name:'Alex',prompt:'Check the budget.'}]});
  const target={value:'Challenging',matches:selector=>selector==='[data-conversation-style]'};
  const context={state,CONVERSATION_STYLES:{Challenging:'Test the claim.'},withConversationStyle,workspace:{notify:()=>{}},render:assert.fail,scheduleSessionSave:assert.fail};
  loadFunction('function handleChange(event)','function addRole()',context)({target});
  assert.equal(state.pendingConversationStyle,'Challenging');
  assert.equal(state.roleEditVersion,4);
  assert.equal(state.roles[0].prompt,'Check the budget.');
});
test('saving after a brief outage continues the queued conversation without another Resume click', async () => {
  const state=baseState({stage:'debate',debateStarted:true,paused:false,replyBlockedBySave:true,replyError:true,replyRetryTarget:{roleId:'one'}});
  const calls=[];
  const run=loadFunction('async function retrySavingConversation()','function updateRoleSummary(',{state,saveSessionNow:async()=>true,render:()=>{},ensureDiscussionLoop:(...args)=>calls.push(args)});
  await run();
  assert.equal(state.replyError,false);
  assert.equal(calls[0][0].roleId,'one');
  assert.equal(calls[0][1],true);
});

test('Enter selects the sole matching agent without sending, including a stale highlight index', () => {
  const state=baseState({mentionOpen:true,mentionActiveIndex:5});
  const calls=[];
  const context={state,matchingMentionRoles:()=>[{id:'grace'}],insertMention:id=>calls.push(id),submitChatMessage:assert.fail};
  const run=loadFunction('function handleComposerKeydown(event)','async function prepareNavigation()',context);
  run({key:'Enter',target:{matches:selector=>selector==='[data-chat-input]'},preventDefault:()=>calls.push('prevent')});
  assert.deepEqual(calls,['prevent','grace']);
});

test('editing an agent does not turn on manual pause', async () => {
  const state=baseState({stage:'debate',groupBuilt:true,debateStarted:true,paused:false});
  const calls=[];
  const run=loadFunction('async function editAgents(','async function saveAgent(',{state,workspace:{showAgentEditor:(...args)=>calls.push(args)}});
  await run('one');
  assert.equal(state.paused,false);
  assert.equal(calls[0][0],'one');
});

test('the setup add button opens the shared editor without inserting a blank participant', () => {
  const state = baseState({ roles: [{ id: 'one', name: 'Grace' }] });
  const calls = [];
  const run = loadFunction('function addRole()', 'function removeSelectedRole()', { editAgents: (...args) => calls.push(args) });
  run();
  assert.equal(calls[0][1], true);
  assert.equal(state.roles.length, 1);
});

test('a new agent retains its selected approach and shared viewers cannot add agents', async () => {
  const state = baseState({ groupBuilt: true, nextRoleId: 2, roleEditVersion: 0, sessionId: '' });
  const values = { name: 'Priya', identity: 'Transit rider', prompt: 'Check service gaps.', avatarColor: '#246cf6', roleType: 'Skeptic' };
  const run = loadFunction('async function saveAgent(', 'async function generateAgent(', { state, MAX_PARTICIPANTS: 16, ROLE_TYPES: ['Analyst', 'Skeptic'], isAvatarColor: () => true, initials: name => name[0], invalidateRoleGeneration: () => {}, render: () => {} });
  assert.equal(await run('', values), true);
  assert.equal(state.roles.at(-1).roleType, 'Skeptic');
  state.readOnly = true;
  const count = state.roles.length;
  assert.equal(await run('', values), false);
  assert.equal(state.roles.length, count);
});

test('new agent generation includes existing perspectives without forwarding unrelated state', async () => {
  const state = baseState({ roles: [{ id: 'one', name: 'Grace', identity: 'Policy analyst', prompt: 'Check evidence.', privateValue: 'excluded' }] });
  let payload;
  const run = loadFunction('async function generateAgent(', 'async function removeAgent(', { state, MAX_RECENT_GENERATED_NAMES: 12, ROLE_GENERATION_TIMEOUT_MS: 60000, singleRoleGenerationApi: () => '/generate-one', jsonOptions: (_method, data) => data, requestJson: async (_url, data) => { payload = data; return { role: { name: 'Priya' } }; } });
  await run('Skeptic');
  assert.equal(payload.roleType, 'Skeptic');
  assert.deepEqual(JSON.parse(JSON.stringify(payload.existingParticipants)), [{ name: 'Grace', identity: 'Policy analyst', prompt: 'Check evidence.' }]);
});

test('typing while an agent replies leaves the discussion running and retains the draft', () => {
  const state=baseState({stage:'debate',replyPending:true,paused:false});
  const input={value:'What if we test one route?',selectionStart:26,matches:selector=>selector==='[data-chat-input]'};
  const context={state,appView:{querySelector:()=>null},workspace:{uploading:()=>false},updateComposeDraft:value=>state.composeDraft=value,resizeComposer:()=>{},mentionAtCaret,syncMentionMenu:()=>{},scheduleSessionSave:()=>{}};
  loadFunction('function handleInput(event)','function handleChange(event)',context)({target:input});
  assert.equal(state.paused,false);
  assert.equal(state.replyPending,true);
  assert.equal(state.composeDraft,input.value);
});

test('an agent editor defers the next turn until it closes and then continues without Resume', async () => {
  const state=baseState({paused:false});
  const calls=[];
  let closeEditor;
  const editorClosed=new Promise(resolve=>closeEditor=resolve);
  const run=loadFunction('async function runDiscussionLoop(','function ensureDiscussionLoop(',{
    state,discussionLoopIsCurrent:()=>true,
    workspace:{waitForAgentEditor:()=>editorClosed},
    requestRoleReply:async roleId=>{calls.push(roleId);return false;},
  });
  const task=run(1,{roleId:'one'});
  assert.equal(calls.length,0);
  assert.equal(state.paused,false);
  closeEditor();await task;
  assert.deepEqual(calls,['one']);
  assert.equal(state.paused,false);
});
