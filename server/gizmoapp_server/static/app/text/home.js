import { rememberStartIntent } from "./start-flow.js?v=product-76";
import { esc } from "./ui.js";

const examples = {
  product: {
    title: "Launch now, or keep refining?",
    question: "Should we launch now or keep refining?",
    messages: [
      { speaker: "maya", text: "Launch now. Another week polishing won’t tell us whether anyone wants this." },
      { speaker: "alex", text: "Not while sign-up is broken. Fix onboarding first, or we’re testing patience, not demand." },
      { speaker: "you", text: "@Sam.ai What if we invite just a few people first?" },
      { speaker: "sam", text: "Only if they can finish the core task. Ten confused users still won’t tell us whether the idea works." },
    ],
  },
  work: {
    title: "Could a four-day week work?",
    question: "Would a four-day workweek work for our team?",
    messages: [
      { speaker: "alex", text: "@Sam.ai A four-day week sounds like five days of work squeezed into four." },
      { speaker: "sam", text: "@Alex.ai Only if we change nothing else. Keep support covered and cut low-value meetings first." },
      { speaker: "maya", text: "@Alex.ai Keeping five days doesn’t protect quality if the team is exhausted." },
      { speaker: "alex", text: "@Maya.ai And fewer days won’t fix understaffing. What stops the same workload spilling into evenings?" },
    ],
  },
  life: {
    title: "Keep it a side project, or go all in?",
    question: "Should I turn my side project into a business?",
    messages: [
      { speaker: "sam", text: "Keep the day job. One enthusiastic comment isn’t evidence of a business." },
      { speaker: "maya", text: "Waiting for certainty is a choice too. The idea gets spare evenings; the job gets all the energy." },
      { speaker: "sam", text: "Quitting creates time, not customers. Set a sales target before making income depend on it." },
      { speaker: "maya", text: "A target is fair. But give it a deadline—otherwise ‘not yet’ can become ‘never’." },
    ],
  },
};

const tabs = [...document.querySelectorAll("[data-example]")];
const panel = document.getElementById("conversation-example");
const messageList = document.querySelector("[data-example-messages]");
const basePath = new URL(document.querySelector("base").href).pathname;
const avatarAssets = Object.fromEntries(["maya", "alex", "sam"].map(name => [name, new URL(`./assets/avatars/${name}-agent-v1.svg`, import.meta.url).href]));
avatarAssets.you = new URL("./assets/avatars/you-candid-v2.png", import.meta.url).href;
const participantNames = { maya: "Maya", alex: "Alex", sam: "Sam", you: "You" };

function message(name, tone, text) {
  const avatar = avatarAssets[tone] ? `<img src="${esc(avatarAssets[tone])}" alt="" width="32" height="32">` : name[0];
  const content = esc(text).replace(/(^|\s)(@(?:Maya|Alex|Sam)\.ai)\b/g, '$1<span class="example-mention">$2</span>');
  return `<div class="example-message${tone === "you" ? " example-you" : ""}"><span class="example-avatar tone-${tone}" aria-hidden="true">${avatar}</span><div class="example-copy"><strong>${name}${tone === "you" ? "" : ".ai"}</strong><p>${content}</p></div></div>`;
}

function showExample(key, focus = false) {
  const example = examples[key];
  if (!example) return;
  for (const tab of tabs) {
    const selected = tab.dataset.example === key;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) tab.focus({ preventScroll: true });
  }
  panel.setAttribute("aria-labelledby", `example-tab-${key}`);
  document.querySelector("[data-example-title]").textContent = example.title;
  messageList.innerHTML = example.messages.map(({ speaker, text }) => message(participantNames[speaker], speaker, text)).join("");
  const link = new URL(`${basePath}app`, location.origin);
  link.searchParams.set("question", example.question);
  link.searchParams.set("start", "1");
  document.querySelector("[data-example-link]").href = link.href;
}

for (const [index, tab] of tabs.entries()) {
  tab.addEventListener("click", () => showExample(tab.dataset.example));
  tab.addEventListener("keydown", event => {
    const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index - 1 + tabs.length) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    showExample(tabs[next].dataset.example, true);
  });
}

const form = document.querySelector(".home-question");
const questionInput = form.querySelector("textarea");
questionInput.addEventListener("input", () => {
  questionInput.style.height = "72px";
  questionInput.style.height = `${Math.min(220, Math.max(72, questionInput.scrollHeight))}px`;
});
questionInput.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    form.requestSubmit();
  }
});
form.addEventListener("submit", event => {
  const input = form.querySelector("textarea");
  if (!input.value.trim()) { event.preventDefault(); input.value = ""; input.reportValidity(); }
  else rememberStartIntent(sessionStorage, input.value);
});
document.addEventListener("click", event => {
  const link = event.target.closest('a[href*="start=1"]');
  if (link) rememberStartIntent(sessionStorage, new URL(link.href).searchParams.get("question") || "");
});

document.querySelector("[data-focus-question]").addEventListener("click", event => {
  event.preventDefault();
  questionInput.focus({ preventScroll: true });
  questionInput.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
});
