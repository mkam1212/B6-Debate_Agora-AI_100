function runtimeTimeout() {
  return window.GizmoAppRuntime?.readConfig().requestTimeoutMs || 15000;
}


export async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs || runtimeTimeout();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      throw new Error(`Server returned ${response.status} without a JSON response.`);
    }
    const payload = await response.json();
    if (!response.ok) {
      const message = payload.errors?.join("; ") || `Request failed with ${response.status}`;
      const error = new Error(payload.requestId ? `${message} (request ${payload.requestId})` : message);
      error.status = response.status;
      error.diagnostics = payload.diagnostics;
      throw error;
    }
    return payload;
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds.`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}


export async function requestEventStream(url, options = {}, onEvent) {
  const controller = new AbortController();
  const { timeoutMs = runtimeTimeout(), signal, ...fetchOptions } = options;
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  let reader;
  let streamFinished = false;
  try {
    const response = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        Accept: "text/event-stream",
        ...(options.headers || {}),
      },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const message = payload?.errors?.join("; ") || `Request failed with ${response.status}`;
      const error = new Error(payload?.requestId ? `${message} (request ${payload.requestId})` : message);
      error.status = response.status;
      error.diagnostics = payload?.diagnostics;
      throw error;
    }
    if (!(response.headers.get("content-type") || "").includes("text/event-stream") || !response.body) {
      throw new Error(`Server returned ${response.status} without an event stream.`);
    }

    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const dispatch = async (frame) => {
      const data = frame.split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart())
        .join("\n");
      if (!data) return;
      let event;
      try {
        event = JSON.parse(data);
      } catch {
        throw new Error("The server sent an invalid event-stream message.");
      }
      await onEvent(event);
    };

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
        .replace(/\r\n/g, "\n").replace(/\r/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        await dispatch(frame);
        boundary = buffer.indexOf("\n\n");
      }
      if (done) {
        if (buffer.trim()) await dispatch(buffer);
        streamFinished = true;
        return;
      }
    }
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds.`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", cancel);
    if (reader && !streamFinished) await reader.cancel().catch(() => {});
    reader?.releaseLock();
  }
}


export function fetchBootstrap(apiBase) {
  return requestJson(`${apiBase}/bootstrap`);
}


export function fetchCapabilities(apiBase) {
  return requestJson(`${apiBase}/capabilities`);
}


export function searchRecords(apiBase, query) {
  const params = new URLSearchParams({ q: query });
  return requestJson(`${apiBase}/search?${params.toString()}`);
}
