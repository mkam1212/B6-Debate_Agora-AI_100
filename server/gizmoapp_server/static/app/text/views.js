const esc = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#39;");

function hrefFor(path, basePath) {
  const route = String(path || "/");
  return `${basePath}${route.replace(/^\/+/, "")}`;
}

function routeLink(path, label, basePath, className = "", attrs = "") {
  return `<a class="${esc(className)}" href="${esc(hrefFor(path, basePath))}" data-route="${esc(path)}" ${attrs}>${label}</a>`;
}

function statusLabel(status) {
  const labels = {
    draft: "Draft",
    running: "In progress",
    needs_input: "Needs your input",
    completed: "Conclusion ready",
    failed: "Failed",
    stopped: "Stopped",
    interrupted: "Interrupted",
  };
  const value = labels[status] || "Draft";
  return `<span class="status-label" data-status="${esc(status || "draft")}">${esc(value)}</span>`;
}

function modelMessage(modelStatus) {
  if (modelStatus?.modelAvailable) {
    return "";
  }
  return `<div class="model-note warning-notice" role="status"><strong>Model unavailable</strong><p>Topics can be saved. Start the discussion after model access is restored.</p></div>`;
}

function feedback() {
  return `<p class="data-feedback error-notice" data-feedback role="alert" hidden></p>`;
}

function emptyMessage(basePath) {
  return `<div class="empty-state"><p>No topics yet.</p>${routeLink("/new", "New topic", basePath, "button button-primary")}</div>`;
}

function topicPath(discussion) {
  const tab = discussion.status === "draft"
    ? "question"
    : ["completed", "needs_input"].includes(discussion.status)
      ? "conclusion"
      : "discussion";
  return `/discussions/${encodeURIComponent(discussion.id)}/${tab}`;
}

export function renderWorkspace({ discussions, basePath }) {
  const rows = (discussions || []).map((discussion) => {
    const path = topicPath(discussion);
    const updated = discussion.updatedAt ? new Date(discussion.updatedAt) : null;
    const stamp = updated && !Number.isNaN(updated.valueOf())
      ? `Updated ${updated.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`
      : "Updated recently";
    const version = Number(discussion.currentVersion || 1) > 1 ? `Version ${esc(discussion.currentVersion)}` : "Version 1";
    return `<li>${routeLink(path, `<span class="topic-row-main"><strong class="topic-title">${esc(discussion.title)}</strong><span class="topic-row-meta">${statusLabel(discussion.status)}<span>${version}</span><span>${esc(stamp)}</span></span></span><span class="topic-row-arrow" aria-hidden="true">→</span>`, basePath, "topic-row", `aria-label="Open topic: ${esc(discussion.title)}"`)}</li>`;
  }).join("");

  return `<div class="reading-width topic-workspace"><h1>Topics</h1>${rows ? `<ul class="topic-list">${rows}</ul>` : emptyMessage(basePath)}</div>`;
}

export function renderNewPage({ modelStatus }) {
  const sources = [{ name: "", text: "" }];
  const srcMarkup = sources.map((source, index) => `<div class="source-editor" data-source-editor>
      <div class="field"><label for="source-name-${index}">Source name</label><input id="source-name-${index}" type="text" data-source-name maxlength="120" value="${esc(source.name)}" placeholder="For example, Project notes"></div>
      <div class="field"><label for="source-text-${index}">Pasted text</label><textarea id="source-text-${index}" data-source-text maxlength="12000" placeholder="Paste a passage or notes you want the discussion to reference">${esc(source.text)}</textarea></div>
      <input type="hidden" data-source-id value="${esc(source.id || "")}">
    </div>`).join("");

  return `<div class="reading-width">
    <div class="page-intro"><h1>New topic</h1></div>
    ${modelMessage(modelStatus)}
    <form class="form-stack" data-form="create-discussion">
      ${feedback()}
      <div class="field"><label for="new-question">Question</label><textarea class="large-input" id="new-question" name="question" required maxlength="4000" placeholder="What do you need to decide or understand?"></textarea></div>
      <div class="field"><label for="new-title">Topic title <span class="field-hint">Optional</span></label><input id="new-title" type="text" name="title" maxlength="120" placeholder="Short title"></div>
      <details class="disclosure">
        <summary>Optional details</summary>
        <div class="disclosure-content">
          <div class="field-grid">
            <div class="field"><label for="new-goal">Goal</label><textarea id="new-goal" name="goal" maxlength="6000" placeholder="What outcome matters most?"></textarea></div>
            <div class="field"><label for="new-constraints">Constraints</label><textarea id="new-constraints" name="constraints" maxlength="6000" placeholder="Limits, deadlines, budget, or requirements"></textarea></div>
            <div class="field"><label for="new-options">Options <span class="field-hint">One per line</span></label><textarea id="new-options" name="options" maxlength="2500" placeholder="Option one&#10;Option two"></textarea></div>
            <div class="field"><label for="new-view">Your current view</label><textarea id="new-view" name="currentView" maxlength="6000" placeholder="What do you think so far?"></textarea></div>
            <div class="field field-wide"><label for="new-context">Context</label><textarea id="new-context" name="context" maxlength="6000" placeholder="Background or facts to consider"></textarea></div>
          </div>
          <div class="field">
            <div class="section-heading"><label>Source excerpts <span class="field-hint">Optional · pasted text only</span></label><button class="button button-link" type="button" data-action="add-source">Add a source</button></div>
            <p class="field-hint">Citations can only use text you provide.</p>
            <div class="source-editors" data-source-editors>${srcMarkup}</div>
          </div>
        </div>
      </details>
      <div class="form-actions"><button class="button button-primary" type="submit">Start discussion</button></div>
    </form>
  </div>`;
}

function renderQuestionFields(question, sources, { disabled = false, idPrefix = "question" } = {}) {
  const disable = disabled ? "disabled" : "";
  const sourceRows = (sources?.length ? sources : [{ name: "", text: "", id: "" }]).map((source, index) => `<div class="source-editor" data-source-editor>
      <div class="field"><label for="${idPrefix}-source-name-${index}">Source name</label><input id="${idPrefix}-source-name-${index}" type="text" data-source-name maxlength="120" ${disable} value="${esc(source.name)}" placeholder="For example, Project notes"></div>
      <div class="field"><label for="${idPrefix}-source-text-${index}">Pasted text</label><textarea id="${idPrefix}-source-text-${index}" data-source-text maxlength="12000" ${disable} placeholder="Paste a passage or notes">${esc(source.text)}</textarea></div>
      <input type="hidden" data-source-id value="${esc(source.id || "")}">
    </div>`).join("");

  return `<div class="form-stack">
    <div class="field"><label for="${idPrefix}-title">Topic title</label><input id="${idPrefix}-title" name="title" type="text" maxlength="120" required ${disable} value="${esc(question.title)}"></div>
    <div class="field"><label for="${idPrefix}-question">Question</label><textarea class="large-input" id="${idPrefix}-question" name="question" maxlength="4000" required ${disable}>${esc(question.question)}</textarea></div>
    <details class="disclosure"><summary>Goals, constraints, and supporting context</summary><div class="disclosure-content"><div class="field-grid">
      <div class="field"><label for="${idPrefix}-goal">What matters most?</label><textarea id="${idPrefix}-goal" name="goal" maxlength="6000" ${disable}>${esc(question.goal)}</textarea></div>
      <div class="field"><label for="${idPrefix}-constraints">Constraints</label><textarea id="${idPrefix}-constraints" name="constraints" maxlength="6000" ${disable}>${esc(question.constraints)}</textarea></div>
      <div class="field"><label for="${idPrefix}-options">Options <span class="field-hint">One per line</span></label><textarea id="${idPrefix}-options" name="options" maxlength="2500" ${disable}>${esc((question.options || []).join("\n"))}</textarea></div>
      <div class="field"><label for="${idPrefix}-view">Your current view</label><textarea id="${idPrefix}-view" name="currentView" maxlength="6000" ${disable}>${esc(question.currentView)}</textarea></div>
      <div class="field field-wide"><label for="${idPrefix}-context">Context</label><textarea id="${idPrefix}-context" name="context" maxlength="6000" ${disable} placeholder="Background or facts to consider">${esc(question.context)}</textarea></div>
    </div><div class="field"><div class="section-heading"><span class="field-label">Sources</span>${disabled ? "" : `<button class="button button-link" type="button" data-action="add-source">Add a source</button>`}</div><p class="field-hint">Sources are supplied by you; citations are checked against the saved text.</p><div class="source-editors" data-source-editors>${sourceRows}</div></div></div></details>
  </div>`;
}

function renderQuestionSummary(question) {
  const fields = [
    ["What matters most?", question.goal],
    ["Constraints", question.constraints],
    ["Options", (question.options || []).join("\n")],
    ["Context", question.context],
    ["Your current view", question.currentView],
  ].filter(([, value]) => value && String(value).trim());
  return `<dl class="question-summary">${fields.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>`;
}

function renderSources(sources) {
  if (!sources?.length) return `<p class="quiet">No source excerpts were added.</p>`;
  return sources.map((source) => `<article class="source-card" id="source-${esc(source.id)}"><h3>${esc(source.name)}</h3><span class="source-id">Source ID: ${esc(source.id)} · User-provided</span><p class="source-text">${esc(source.text)}</p></article>`).join("");
}

function renderAssumptions(framing) {
  if (!framing) return "";
  const assumptions = framing.assumptions || [];
  const questions = framing.clarifyingQuestions || [];
  return `<section class="content-section"><h2>Assumptions for this version</h2>${assumptions.length ? `<ul class="reason-list">${assumptions.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>` : `<p class="quiet">No additional assumptions were recorded.</p>`}${questions.length && framing.enoughToProceed === false ? `<div class="notice warning-notice"><strong>Questions that could change the analysis</strong><ol>${questions.map((item) => `<li>${esc(item)}</li>`).join("")}</ol></div>` : ""}</section>`;
}

function renderPerspectiveList(perspectives) {
  return `<ul class="perspective-list">${(perspectives || []).map((perspective) => `<li><strong>${esc(perspective.name)}</strong><p>${esc(perspective.focus)}</p></li>`).join("")}</ul>`;
}

function runNotice(discussion) {
  const run = discussion.run || {};
  if (run.state === "running") {
    return `<div class="run-status" role="status" aria-live="polite"><strong><span class="progress-dot" aria-hidden="true"></span>${esc(run.currentStep || "Discussion in progress")}</strong><p>Completed responses are saved as they arrive. You can stop the run at any time.</p></div>`;
  }
  if (run.state === "needs_input") {
    return `<div class="notice warning-notice"><strong>A few details could change the analysis.</strong><p>Answer the questions in the Question tab, or continue with the assumptions shown there.</p></div>`;
  }
  if (["failed", "interrupted"].includes(run.state)) {
    const failedStep = run.lastFailedStep ? ` during ${esc(run.lastFailedStep)}` : "";
    return `<div class="error-notice"><strong>Stopped${failedStep}.</strong><p>Saved responses are intact. ${esc(run.error || "Retry to continue from the last saved step.")}</p></div>`;
  }
  if (run.state === "stopped") {
    return `<div class="notice warning-notice"><strong>This discussion stopped before a conclusion was ready.</strong><p>Completed responses are saved. You can retry from the last saved step.</p></div>`;
  }
  return "";
}

function discussionHeader(discussion, tab, { basePath, apiBase, pendingDelete }) {
  const isSample = discussion.mode === "sample";
  const versionQuery = `?version=${encodeURIComponent(discussion.version || 1)}`;
  const tabPath = (name) => `/discussions/${encodeURIComponent(discussion.id)}/${name}${versionQuery}`;
  const actions = isSample ? `<a class="button button-link" href="${esc(`${apiBase}/debate/examples/sample/export?format=md&full=1`)}">Download sample Markdown</a>` : `<a class="button button-link" href="${esc(`${apiBase}/debate/discussions/${encodeURIComponent(discussion.id)}/export?version=${discussion.version || 1}&format=md`)}">Export Markdown</a>${discussion.isCurrentVersion === false ? routeLink(`/discussions/${encodeURIComponent(discussion.id)}/question?version=${discussion.currentVersion}`, "View current version", basePath, "button button-link") : `<button class="button button-link button-danger" type="button" data-action="ask-delete" data-id="${esc(discussion.id)}">Delete discussion</button>`}`;
  const deletePrompt = !isSample && pendingDelete === discussion.id ? `<div class="pending-delete" role="group" aria-label="Confirm deletion"><p>Delete this discussion and all saved versions?</p><div class="inline-actions"><button class="button button-danger" type="button" data-action="delete-discussion" data-id="${esc(discussion.id)}">Delete permanently</button><button class="button button-link" type="button" data-action="cancel-delete">Keep discussion</button></div></div>` : "";
  const tabs = [
    ["question", "Question"],
    ["discussion", "Discussion"],
    ["conclusion", "Conclusion"],
  ].map(([name, label]) => routeLink(tabPath(name), esc(label), basePath, "", `aria-current="${tab === name ? "page" : "false"}"`)).join("");
  return `<div class="discussion-title-row"><div><p class="eyebrow">${isSample ? `<span class="sample-label">Sample discussion — prewritten example</span>` : `Discussion · Version ${esc(discussion.version || 1)}`}</p><h1>${esc(discussion.title)}</h1><div class="discussion-meta">${statusLabel(discussion.status)}${discussion.isCurrentVersion === false ? `<span>Saved version ${esc(discussion.version)} · Current version ${esc(discussion.currentVersion)}</span>` : ""}</div></div><div class="page-actions">${actions}</div></div>${deletePrompt}<nav class="subnav" aria-label="Discussion sections">${tabs}</nav>`;
}

function questionTab(discussion, basePath) {
  const isEditableDraft = discussion.status === "draft" && discussion.run?.state === "idle";
  const isCurrent = discussion.isCurrentVersion !== false;
  if (isEditableDraft && discussion.mode !== "sample") {
    return `<div class="tab-content"><section class="content-section"><h2>Question</h2>
      <form class="form-stack" data-form="save-question" data-id="${esc(discussion.id)}" data-version="${esc(discussion.version)}">${feedback()}${renderQuestionFields(discussion.question, discussion.sources, { idPrefix: "draft" })}<div class="form-actions"><button class="button button-secondary" type="submit" name="submitAction" value="save">Save draft</button><button class="button button-primary" type="submit" name="submitAction" value="start">Start discussion</button></div></form>
    </section></div>`;
  }

  const run = discussion.run || {};
  const startActions = !discussion.mode || discussion.mode !== "sample" ? `<div class="button-row">${run.state === "running" ? `<button class="button button-secondary" type="button" data-action="cancel-run" data-id="${esc(discussion.id)}">Stop discussion</button>` : ""}${["failed", "stopped", "interrupted"].includes(run.state) && isCurrent ? `<button class="button button-secondary" type="button" data-action="retry-run" data-id="${esc(discussion.id)}">Retry from saved responses</button>` : ""}${run.state === "needs_input" ? `<a class="button button-secondary" href="${esc(hrefFor(`/discussions/${encodeURIComponent(discussion.id)}/conclusion?version=${discussion.version}`, basePath))}" data-route="/discussions/${esc(discussion.id)}/conclusion?version=${esc(discussion.version)}">Review questions</a>` : ""}</div>` : "";
  return `<div class="tab-content"><section class="content-section"><h2>Question</h2><h3>${esc(discussion.question.question)}</h3>${renderQuestionSummary(discussion.question)}</section><section class="content-section"><h2>Angles</h2>${renderPerspectiveList(discussion.perspectives)}</section>${renderAssumptions(discussion.framing)}<section class="content-section"><h2>Sources</h2>${renderSources(discussion.sources)}</section>${runNotice(discussion)}${startActions}${!isCurrent ? `<p class="quiet">Earlier version · The recommendation reflects the conditions saved here.</p>` : ""}</div>`;
}

function citationMarkup(citations, sources) {
  if (!citations?.length) return "";
  const sourceMap = new Map((sources || []).map((source) => [source.id, source]));
  return `<div class="citation-list" aria-label="Source citations">${citations.map((citation) => {
    const source = sourceMap.get(citation.sourceId);
    if (!source || typeof citation.quote !== "string" || !source.text.includes(citation.quote)) {
      return `<span class="quiet">Needs verification · unverified source reference was hidden</span>`;
    }
    return `<a class="text-link" href="#source-${esc(source.id)}">${esc(source.name)}: “${esc(citation.quote)}”</a>`;
  }).join("")}</div>`;
}

function contributionMarkup(item, roleNames, sources) {
  const revision = item.stage === "challenge" && ["revised", "withdrawn"].includes(item.revisionStatus) ? `<p class="revision-note">${item.revisionStatus === "revised" ? "Revised view" : "Withdrawn view"}: ${esc(item.revisionReason)}</p>` : "";
  const responseLinks = item.respondsTo?.length ? `<div class="response-links"><span class="quiet">Responds to</span>${item.respondsTo.map((id) => `<a class="text-link" href="#${esc(id)}">${esc(roleNames.get(id) || "View the related point")}</a>`).join("")}</div>` : "";
  const reason = item.reasons?.[0] ? `<p class="contribution-detail">${esc(item.reasons[0])}</p>` : "";
  const notes = [
    ...(item.assumptions?.length ? [`Assumes: ${item.assumptions[0]}`] : []),
    ...(item.uncertainties?.length ? [`Unclear: ${item.uncertainties[0]}`] : []),
  ];
  const noteMarkup = notes.length ? `<p class="contribution-note">${notes.map(esc).join(" · ")}</p>` : "";
  return `<article class="contribution" id="${esc(item.id)}"><div class="contribution-header"><strong>${esc(roleNames.get(item.roleId) || "Perspective")}</strong><span>${item.stage === "initial" ? "First take" : "Reply"}</span></div><p class="contribution-claim">${esc(item.claim)}</p>${reason}${noteMarkup}${revision}${responseLinks}${citationMarkup(item.citations, sources)}</article>`;
}

function discussionTab(discussion, basePath) {
  const contributions = discussion.contributions || [];
  const initial = contributions.filter((item) => item.stage === "initial");
  const challenges = contributions.filter((item) => item.stage === "challenge");
  const roles = new Map((discussion.perspectives || []).map((role) => [role.id, role.name]));
  const argumentRoles = new Map(initial.map((item) => [item.id, roles.get(item.roleId)]));
  const roleNames = new Map([...roles, ...argumentRoles]);
  const sections = [
    ["stage-initial", "First takes", initial],
    ["stage-challenges", "Replies", challenges],
  ];
  const hasAny = initial.length || challenges.length;
  const stageIndex = hasAny ? `<ul class="stage-index">${sections.filter(([, , items]) => items.length).map(([id, title]) => `<li><a href="#${id}">${esc(title)}</a></li>`).join("")}</ul>` : "";
  const stageMarkup = sections.map(([id, title, items]) => items.length ? `<section class="stage-section" id="${id}"><h2>${esc(title)}</h2>${items.map((item) => contributionMarkup(item, roleNames, discussion.sources)).join("")}</section>` : "").join("");
  const empty = !hasAny && discussion.run?.state === "idle" ? `<div class="empty-state"><h2>The discussion has not started.</h2><p>Review the question and perspectives, then start when you are ready.</p>${routeLink(`/discussions/${encodeURIComponent(discussion.id)}/question?version=${discussion.version}`, "Review question", basePath, "button button-secondary")}</div>` : !hasAny && discussion.run?.state === "needs_input" ? `<div class="empty-state"><h2>Waiting for your input</h2><p>Review the clarifying questions on the Question tab.</p></div>` : !hasAny ? `<div class="empty-state"><p>This discussion stopped before any responses were saved.</p></div>` : "";
  const sources = discussion.sources?.length ? `<details class="content-section source-reference-list"><summary>View supplied sources (${discussion.sources.length})</summary>${renderSources(discussion.sources)}</details>` : "";
  const actions = discussion.run?.state === "running" ? `<button class="button button-secondary" type="button" data-action="cancel-run" data-id="${esc(discussion.id)}">Stop discussion</button>` : ["failed", "stopped", "interrupted"].includes(discussion.run?.state) && discussion.isCurrentVersion !== false ? `<button class="button button-secondary" type="button" data-action="retry-run" data-id="${esc(discussion.id)}">Retry from saved responses</button>` : "";
  return `<div class="tab-content">${runNotice(discussion)}${discussion.mode === "sample" ? `<p class="quiet">This example is prewritten. It does not stream or call a model.</p>` : ""}${actions ? `<div class="action-row">${actions}</div>` : ""}${empty}${stageIndex}${stageMarkup}${sources}</div>`;
}

function simpleSection(title, values) {
  if (!values?.length) return "";
  return `<section class="content-section"><h2>${esc(title)}</h2><ul class="reason-list">${values.map((value) => `<li>${esc(value)}</li>`).join("")}</ul></section>`;
}

function changeConditionsForm(discussion) {
  return `<details class="change-conditions"><summary>Change conditions and start a new version</summary><div class="disclosure-content"><p class="quiet">The current version remains saved. A new version gets its own analysis and conclusion.</p><form class="form-stack" data-form="new-version" data-id="${esc(discussion.id)}" data-version="${esc(discussion.version)}">${feedback()}${renderQuestionFields(discussion.question, discussion.sources, { idPrefix: "version" })}<div class="form-actions"><button class="button button-primary" type="submit">Create version and start</button></div></form></div></details>`;
}

function conclusionTab(discussion, { apiBase, basePath }) {
  const synthesis = discussion.synthesis;
  const question = discussion.question || {};
  const currentCompleted = discussion.status === "completed" && discussion.isCurrentVersion !== false;
  if (!synthesis) {
    const framingQuestions = discussion.framing?.clarifyingQuestions || [];
    const clarificationForm = discussion.run?.state === "needs_input" && discussion.isCurrentVersion !== false ? `<section class="content-section"><h2>Answer or continue with assumptions</h2><form class="inline-form" data-form="clarify" data-id="${esc(discussion.id)}" data-version="${esc(discussion.version)}">${feedback()}<div class="field"><label for="clarification-text">Your answers <span class="field-hint">Optional · saved in a new version</span></label><textarea id="clarification-text" name="text" maxlength="6000" placeholder="Add the missing details that matter to this question"></textarea></div><div class="form-actions"><button class="button button-primary" type="submit">Create a version with my answers</button><button class="button button-secondary" type="button" data-action="continue-assumptions" data-id="${esc(discussion.id)}">Continue with listed assumptions</button></div></form></section>${framingQuestions.length ? `<section class="content-section"><h2>Questions to consider</h2><ol>${framingQuestions.map((item) => `<li>${esc(item)}</li>`).join("")}</ol></section>` : ""}` : "";
    const startAction = discussion.run?.state === "running" ? `<button class="button button-secondary" type="button" data-action="cancel-run" data-id="${esc(discussion.id)}">Stop discussion</button>` : ["failed", "stopped", "interrupted"].includes(discussion.run?.state) && discussion.isCurrentVersion !== false ? `<button class="button button-secondary" type="button" data-action="retry-run" data-id="${esc(discussion.id)}">Retry from saved responses</button>` : "";
    return `<div class="tab-content"><section class="content-section"><h2>Recommendation</h2>${runNotice(discussion)}<p class="quiet">A recommendation appears only after the synthesis stage completes.</p>${startAction ? `<div class="button-row">${startAction}</div>` : ""}</section>${clarificationForm}<section class="content-section"><h2>Question</h2><p>${esc(question.question)}</p></section></div>`;
  }

  const isDirect = synthesis.kind === "direct_answer";
  const conditions = synthesis.conditions?.length ? `<p class="recommendation-conditions"><strong>Applies if:</strong> ${synthesis.conditions.map(esc).join("; ")}</p>` : "";
  const comparison = synthesis.optionComparison?.length ? `<section class="content-section"><h2>Option comparison</h2><div class="comparison-wrap"><table class="comparison-table"><thead><tr><th>Option</th><th>Assessment</th><th>Listed constraints</th></tr></thead><tbody>${synthesis.optionComparison.map((item) => `<tr><th scope="row">${esc(item.option)}</th><td>${esc(item.assessment)}</td><td>${item.meetsConstraints ? "Meets" : "Does not meet"}</td></tr>`).join("")}</tbody></table></div></section>` : "";
  const objection = synthesis.strongestObjection ? `<section class="content-section"><h2>Strongest objection</h2><p>${esc(synthesis.strongestObjection)}</p></section>` : "";
  const citations = citationMarkup(synthesis.citations, discussion.sources);
  const followup = currentCompleted && discussion.mode !== "sample" ? `<section class="followup-section"><h2>Ask a follow-up</h2><p class="quiet">Ask about a specific point or add a new angle. The response becomes a separate version.</p><form class="inline-form" data-form="followup" data-id="${esc(discussion.id)}" data-version="${esc(discussion.version)}">${feedback()}<div class="field"><label for="followup-text">What would you like to ask?</label><textarea id="followup-text" name="text" required maxlength="1500" placeholder="For example: How would the recommendation change if time mattered most?"></textarea></div><button class="button button-primary" type="submit">Discuss this follow-up</button></form></section>` : "";
  const reflection = currentCompleted && discussion.mode !== "sample" ? `<section class="reflection-section"><h2>What changed your view?</h2><p class="quiet">Optional personal note. It is saved with this version and not sent to the model.</p><form class="inline-form" data-form="reflection" data-id="${esc(discussion.id)}" data-version="${esc(discussion.version)}">${feedback()}<div class="field"><label for="reflection-text">What changed your view?</label><textarea id="reflection-text" name="reflection" maxlength="6000">${esc(discussion.reflection)}</textarea></div><button class="button button-secondary" type="submit">Save note</button></form></section>` : "";
  const downloads = discussion.mode === "sample"
    ? `<a class="button button-link" href="${esc(`${apiBase}/debate/examples/sample/export?format=txt`)}">Plain text</a><a class="button button-link" href="${esc(`${apiBase}/debate/examples/sample/export?format=md&full=1`)}">Full Markdown</a>`
    : `<a class="button button-link" href="${esc(`${apiBase}/debate/discussions/${encodeURIComponent(discussion.id)}/export?version=${discussion.version}&format=txt`)}">Plain text</a><a class="button button-link" href="${esc(`${apiBase}/debate/discussions/${encodeURIComponent(discussion.id)}/export?version=${discussion.version}&format=md&full=1`)}">Full Markdown</a>`;
  return `<div class="tab-content">
    <section class="content-section"><p class="eyebrow">${isDirect ? "Direct answer" : "Recommendation"}</p><p class="recommendation">${esc(synthesis.recommendation)}</p>${conditions}<div class="button-row" style="margin-top:1rem"><button class="button button-secondary" type="button" data-action="copy-recommendation" data-id="${esc(discussion.id)}">Copy recommendation</button>${downloads}</div><p class="copy-status" data-copy-status aria-live="polite"></p>${citations}</section>
    ${simpleSection("Why", synthesis.reasons)}
    ${simpleSection("Key trade-offs", synthesis.tradeoffs)}
    ${comparison}
    ${objection}
    ${simpleSection("Alternatives", synthesis.alternatives)}
    ${simpleSection("Key disagreements", synthesis.keyDisagreements)}
    ${simpleSection("Open questions", synthesis.openQuestions)}
    ${simpleSection("Needs verification", synthesis.needsVerification)}
    ${simpleSection("Next steps", synthesis.nextSteps)}
    ${discussion.sources?.length ? `<details class="content-section"><summary>View supplied sources (${discussion.sources.length})</summary>${renderSources(discussion.sources)}</details>` : ""}
    ${followup}${reflection}
    ${discussion.mode !== "sample" ? changeConditionsForm(discussion) : ""}
    ${discussion.mode === "sample" ? `<p class="sample-banner">Prewritten sample · No live model call was made for this discussion.</p>` : ""}
  </div>`;
}

export function renderDiscussionPage(discussion, options) {
  const { tab, basePath, apiBase, pendingDelete } = options;
  const selectedTab = ["question", "discussion", "conclusion"].includes(tab) ? tab : "question";
  const body = selectedTab === "question" ? questionTab(discussion, basePath) : selectedTab === "discussion" ? discussionTab(discussion, basePath) : conclusionTab(discussion, { apiBase, basePath });
  return `<div class="discussion-page">${discussionHeader(discussion, selectedTab, { basePath, apiBase, pendingDelete })}${discussion.mode === "sample" ? `<p class="sample-banner">Sample discussion — prewritten example. This page does not stream or call a model.</p>` : ""}${body}</div>`;
}

export function renderRouteError(message, basePath) {
  return `<div class="reading-width route-error"><p class="eyebrow">Something went wrong</p><h1>This page could not be loaded.</h1><p class="error-notice" role="alert">${esc(message)}</p><div class="button-row">${routeLink("/workspace", "Return to topics", basePath, "button button-primary")}</div></div>`;
}

export function renderNotFound(basePath) {
  return `<div class="reading-width route-error"><p class="eyebrow">Not found</p><h1>That page is unavailable.</h1><div class="button-row">${routeLink("/workspace", "Return to topics", basePath, "button button-primary")}</div></div>`;
}

export function collectQuestionForm(form) {
  const data = new FormData(form);
  const question = {
    title: String(data.get("title") || "").trim(),
    question: String(data.get("question") || "").trim(),
    goal: String(data.get("goal") || "").trim(),
    constraints: String(data.get("constraints") || "").trim(),
    options: String(data.get("options") || "").split("\n").map((item) => item.trim()).filter(Boolean),
    context: String(data.get("context") || "").trim(),
    currentView: String(data.get("currentView") || "").trim(),
    sources: [...form.querySelectorAll("[data-source-editor]")].map((editor) => ({
      id: editor.querySelector("[data-source-id]")?.value || "",
      name: editor.querySelector("[data-source-name]")?.value.trim() || "",
      text: editor.querySelector("[data-source-text]")?.value.trim() || "",
    })).filter((source) => source.name || source.text),
  };
  return question;
}

export function copyableRecommendation(discussion) {
  const synthesis = discussion.synthesis || {};
  const lines = [
    discussion.mode === "sample" ? "Sample discussion — prewritten example" : discussion.title,
    "",
    `Question: ${discussion.question?.question || ""}`,
    "",
    synthesis.kind === "direct_answer" ? "Direct answer" : "Recommendation",
    synthesis.recommendation || "",
  ];
  if (synthesis.conditions?.length) lines.push("", "Applies if:", ...synthesis.conditions.map((item) => `- ${item}`));
  if (synthesis.reasons?.length) lines.push("", "Why:", ...synthesis.reasons.map((item) => `- ${item}`));
  if (synthesis.tradeoffs?.length) lines.push("", "Key trade-offs:", ...synthesis.tradeoffs.map((item) => `- ${item}`));
  if (synthesis.strongestObjection) lines.push("", "Strongest objection:", synthesis.strongestObjection);
  if (synthesis.openQuestions?.length) lines.push("", "Open questions:", ...synthesis.openQuestions.map((item) => `- ${item}`));
  return lines.join("\n");
}

export { hrefFor };
