import { esc } from "./ui.js?v=dialogs-68";

const headings = /^(what changed|the key trade-off|evidence & unknowns|where they agree|areas? of agreement|common ground|consensus|main views|main perspectives|key perspectives|perspectives|the disagreement|disagreements?|open questions?|unresolved questions?|a useful next step|next[- ]step suggestions?|next steps?|core question|key takeaways)\s*(?:[:：]\s*(.*))?$/i;

export function summarySections(value) {
  const sections = [];
  let current = { title: "", lines: [] };
  for (const raw of String(value || "").replaceAll("\r\n", "\n").split("\n")) {
    const clean = raw.trim().replace(/^(?:#{1,6}\s+|\d+[.)]\s+)/, "").replaceAll("**", "");
    const heading = clean.match(headings);
    const markdownHeading = raw.trim().match(/^#{1,6}\s+(.+?)\s*:?$/);
    if (heading || markdownHeading) {
      if (current.title || current.lines.some(line => line.trim())) sections.push(current);
      current = { title: heading ? heading[1] : markdownHeading[1], lines: heading?.[2] ? [heading[2]] : [] };
    } else current.lines.push(raw);
  }
  if (current.title || current.lines.some(line => line.trim())) sections.push(current);
  return sections;
}

const inline = value => esc(value).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
export function renderSummaryCopy(value) {
  return summarySections(value).map(section => {
    const paragraphs = section.lines.join("\n").trim().split(/\n\s*\n/).filter(Boolean).map(paragraph => {
      const lines = paragraph.split("\n");
      return lines.every(line => /^\s*[-*]\s+/.test(line))
        ? `<ul>${lines.map(line => `<li>${inline(line.replace(/^\s*[-*]\s+/, ""))}</li>`).join("")}</ul>`
        : `<p>${inline(paragraph).replaceAll("\n", "<br>")}</p>`;
    }).join("");
    return `<section>${section.title ? `<h3>${esc(section.title)}</h3>` : ""}${paragraphs}</section>`;
  }).join("");
}
