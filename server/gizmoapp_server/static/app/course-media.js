// Media requests are short; the central job survives between browser polls.
// apiBase must come from the page's injected config. No credential enters JS.
const sleep = (ms, signal) => new Promise((resolve, reject) => {
  if (signal?.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
  const done = () => { signal?.removeEventListener("abort", abort); resolve(); };
  const timer = setTimeout(done, ms);
  const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
  signal?.addEventListener("abort", abort, { once: true });
});

export async function generateMedia(apiBase, operation, payload, {signal, onProgress = () => {}, timeoutMs = 600000} = {}) {
  if (!["image", "speech"].includes(operation)) throw new Error("Unknown media operation");
  const deadline = Date.now() + Math.min(600000, Math.max(1000, timeoutMs));
  let path = operation;
  let body = payload;
  while (Date.now() < deadline) {
    signal?.throwIfAborted();
    let response;
    try {
      // A stalled individual request cannot consume the entire job deadline.
      const controller = new AbortController();
      const abort = () => controller.abort();
      signal?.addEventListener("abort", abort, {once: true});
      const timer = setTimeout(abort, Math.min(35000, deadline - Date.now()));
      try {
        response = await fetch(`${apiBase}/course-media/${path}`, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body), signal: controller.signal,
        });
        if (response.status === 202) {
          const progress = await response.json();
          if (typeof progress.pollTicket !== "string") throw new Error("Invalid media progress response");
          body = {pollTicket: progress.pollTicket};
          path = "poll";
          onProgress(progress.status);
        } else if (response.ok) {
          return await response.blob();
        } else {
          // Only retry reads of an already accepted job; never duplicate a submission.
          if (path !== "poll" || ![503, 504].includes(response.status)) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.errors?.[0] || "Media generation failed");
          }
        }
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener("abort", abort);
      }
    } catch (error) {
      if (signal?.aborted || path !== "poll" || !(error instanceof TypeError || error.name === "AbortError")) throw error;
      onProgress("reconnecting");
    }
    await sleep(2000, signal);
  }
  throw new Error("Media is taking longer than expected. No duplicate request was submitted.");
}

export async function narrate(apiBase, text, {onChunk, onProgress = () => {}, signal, ...voiceOptions} = {}) {
  if (typeof text !== "string" || !text.trim() || text.length > 16000) throw new Error("Narration must contain 1–16000 characters");
  if (typeof onChunk !== "function") throw new Error("Provide onChunk to play or save each audio chunk");
  // Keep each request short enough for prompt playback; never concatenate MP3/WAV containers.
  const chunks = [];
  let rest = text.trim();
  while (rest) {
    let end = Math.min(500, rest.length);
    if (end < rest.length) {
      const space = rest.lastIndexOf(" ", end);
      if (space > 250) end = space;
    }
    chunks.push(rest.slice(0, end));
    rest = rest.slice(end).trimStart();
  }
  for (let index = 0; index < chunks.length; index++) {
    const blob = await generateMedia(apiBase, "speech", {text: chunks[index], ...voiceOptions}, {
      signal, onProgress: status => onProgress({status, index, total: chunks.length}),
    });
    // Callback enqueues playback immediately, while the next chunk starts generating.
    await onChunk(blob, index, chunks.length);
    onProgress({status: "chunk-ready", index, total: chunks.length});
  }
}
