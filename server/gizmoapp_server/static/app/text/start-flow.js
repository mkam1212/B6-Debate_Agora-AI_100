const KEY = 'agora-start-intent-v1';

// A pasted URL may prefill a topic, but must not start spending replies on its own.
export function rememberStartIntent(storage, question, now = Date.now()) {
  try { storage.setItem(KEY, JSON.stringify({ question: question.trim(), at: now })); } catch { /* Prefill still works without browser storage. */ }
}

export function consumeStartIntent(storage, question, now = Date.now()) {
  try {
    const raw = storage.getItem(KEY);
    storage.removeItem(KEY);
    const value = JSON.parse(raw);
    return value?.question === question.trim() && Number.isFinite(value.at) && now >= value.at && now - value.at <= 120000;
  } catch { return false; }
}
