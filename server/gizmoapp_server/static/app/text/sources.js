// A source identity is supplied by the server after an actual tool retrieval.
const esc = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');

export function trustedCitations(items = []) {
  if (!Array.isArray(items)) return [];
  const candidates = items.slice(0, 8);
  const valid = candidates.every(item => {
    if (!/^web-[a-f0-9]{20}$/.test(item?.sourceId || '') || !Number.isSafeInteger(item.fetchedAt)) return false;
    try {
      const url = new URL(item.url);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password;
    } catch { return false; }
  });
  return valid ? candidates : [];
}

export function renderCitedText(text, citations = []) {
  const sources = trustedCitations(citations);
  return esc(text).replace(/\[(\d{1,2})\]/g, (match, index) => {
    const source = sources[Number(index) - 1];
    return source ? `<a class="message-citation" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer" aria-label="Source ${index}: ${esc(source.title)}" title="${esc(source.title)}">${index}</a>` : match;
  });
}

export function streamingText(text) {
  // Hosted citation syntax becomes a trusted, clickable reference on completion.
  return text.replace(/\ue200[^\ue201]*\ue201/g, '').replace(/\ue200[^\ue201]*$/g, '')
    .replace(/\[[^\]]*\]\(https?:\/\/[^)]*\)/g, '').replace(/\[S:web-[^\]]*\]?/g, '')
    .replace(/\[[^\]]*(?:\]\([^)]*)?$/g, '');
}
export function sourceFormat(value) {
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) return "";
    const path = decodeURIComponent(url.pathname);
    return path.match(/\.(pdf|docx?|xlsx?|pptx?|csv|txt|rtf|odt)$/i)?.[1].toUpperCase() || "";
  } catch { return ""; }
}

export function collectSources(messages = [], roles = []) {
  const sources = new Map();
  for (const message of messages) {
    if (message.kind !== "participant" || !Array.isArray(message.citations)) continue;
    for (const citation of trustedCitations(message.citations)) {
      if (typeof citation?.url !== "string") continue;
      let url;
      try { url = new URL(citation.url); } catch { continue; }
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) continue;
      url.hash = "";
      const role = roles.find(item => item.id === message.roleId);
      const author = role?.name || "Former agent";
      if (sources.has(url.href)) { sources.get(url.href).authors.add(author); continue; }
      sources.set(url.href, {
        url: url.href, domain: url.hostname.replace(/^www\./, ""),
        format: sourceFormat(url.href),
        title: typeof citation.title === "string" ? citation.title.slice(0, 240) : url.hostname,
        excerpt: typeof citation.excerpt === "string" ? citation.excerpt.slice(0, 600) : "",
        authors: new Set([author]),
      });
    }
  }
  return [...sources.values()];
}
