export function isDialogBackdropClick(event, dialog) {
  if (event.target !== dialog) return false;
  const bounds = dialog.getBoundingClientRect();
  return event.clientX < bounds.left || event.clientX > bounds.right
    || event.clientY < bounds.top || event.clientY > bounds.bottom;
}

export function mentionAtCaret(text, caret) {
  const before = text.slice(0, caret);
  const match = /(?:^|\s)@([^\s@]*)$/u.exec(before);
  return match ? { query: match[1], range: [before.length - match[1].length - 1, caret] } : null;
}

export function mentionedRole(text, roles) {
  // A normal email address is not an agent mention. Match complete names, including spaces.
  const mentions = [...text.matchAll(/(?:^|\s)@/gu)];
  for (const match of mentions) {
    const after = text.slice(match.index + match[0].length);
    for (const role of [...roles].sort((a, b) => b.name.trim().length - a.name.trim().length)) {
      const name = role.name.trim();
      if (!name || after.slice(0, name.length).toLocaleLowerCase() !== name.toLocaleLowerCase()) continue;
      const suffix = after.slice(name.length).replace(/^\.ai\b/iu, '');
      if (!suffix || /^[\s,.!?;:，。！？；：]/u.test(suffix)) return role;
    }
  }
  return null;
}

export function insertRoleMention(text, range, name) {
  const [start, end] = range || [text.length, text.length];
  const before = text.slice(0, start);
  const after = text.slice(end);
  const prefix = before && !/\s$/u.test(before) ? ' ' : '';
  const spacer = /^\s/u.test(after) ? '' : ' ';
  return { text: `${before}${prefix}@${name}${spacer}${after}`, caret: before.length + prefix.length + name.length + 1 + spacer.length };
}

export function updateRecentHistory(items, item, limit = 40) {
  // Only a new conversation gets a new position. Reading or replying never reorders it.
  return (items.some(entry => entry.id === item.id)
    ? items.map(entry => entry.id === item.id ? { ...entry, ...item } : entry)
    : [item, ...items]).slice(0, limit);
}

export function discussionTurnDelay(message = '') {
  const words = message.trim().split(/\s+/u).filter(Boolean).length;
  const characters = (message.match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/gu) || []).length;
  return Math.min(4200, 1800 + Math.max(words * 55, characters * 55));
}

export const CONVERSATION_STYLES = {
  Balanced: 'Use one or two short sentences to respond directly to a specific point. Weigh the strongest trade-off without a recap.',
  Exploratory: 'Offer one different possibility or useful what-if in one or two short sentences. Build on the last speaker’s actual point.',
  Challenging: 'Test the last speaker’s specific claim with one concrete objection, counterexample, or evidence question. Be direct and respectful, in one or two short sentences.',
  Concise: 'Make one concrete point in a single short sentence. Skip introductions, lists, recaps, and follow-up questions unless necessary.',
};
export function withConversationStyle(prompt, style) {
  const original = prompt.replace(/\n\n\[Conversation style: \w+\][\s\S]*$/u, '');
  return Object.hasOwn(CONVERSATION_STYLES, style)
    ? `${original}\n\n[Conversation style: ${style}] ${CONVERSATION_STYLES[style]}` : original;
}
