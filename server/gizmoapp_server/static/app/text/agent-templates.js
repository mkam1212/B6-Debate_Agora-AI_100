// Starting points for the "Add agent" dialog. Each is a fallback profile for its role
// type; the dialog asks the server for one tailored to the debate's question first.
// The first three mirror the default agents (Avery, Jordan, Casey).
export const AGENT_TEMPLATES = [
  { id: "analyst", roleType: "Analyst", description: "Check the evidence and assumptions.", name: "Avery", identity: "Analyst", prompt: "Look for assumptions, evidence, and what would change the conclusion." },
  { id: "challenger", roleType: "Challenger", description: "Test the strongest argument.", name: "Jordan", identity: "Challenger", prompt: "Test the strongest claim. Name a practical trade-off or counterexample." },
  { id: "synthesizer", roleType: "Synthesizer", description: "Connect the strongest points.", name: "Casey", identity: "Synthesizer", prompt: "Connect the strongest points and name what remains unresolved." },
  { id: "builder", roleType: "Builder", description: "Make the next step practical.", name: "Riley", identity: "Builder", prompt: "Turn the discussion into practical next steps. Say what to try first, what it needs, and how to tell if it worked." },
  { id: "skeptic", roleType: "Skeptic", description: "Look for risks and blind spots.", name: "Morgan", identity: "Skeptic", prompt: "Look for risks and failure modes. Ask what could go wrong, who bears the cost, and what has not been checked." },
];

const ROLE_TYPES = new Set(AGENT_TEMPLATES.map(template => template.roleType));
const PROFILE_FIELDS = ["name", "identity", "prompt"];

// Prefer the template's own name; fall back to a numbered variant so a new agent
// never shares a name with an existing one (mentions resolve by name).
export function templateAgent(template, existingNames = []) {
  const taken = new Set(existingNames.map(name => String(name).trim().toLocaleLowerCase()));
  const base = template.name.trim().slice(0, 48);
  let name = base;
  for (let suffix = 2; taken.has(name.toLocaleLowerCase()); suffix += 1) {
    const ending = ` ${suffix}`;
    name = `${base.slice(0, 48 - ending.length).trimEnd()}${ending}`;
  }
  return { name, identity: template.identity, prompt: template.prompt, roleType: template.roleType };
}

// Accept a generated role only if it fits the agent editor's fields.
export function generatedAgent(role, expectedRoleType) {
  if (!role || typeof role !== "object") return null;
  const name = typeof role.name === "string" ? role.name.trim() : "";
  const identity = typeof role.identity === "string" ? role.identity.trim() : "";
  const prompt = typeof role.prompt === "string" ? role.prompt.trim() : "";
  if (!name || name.length > 48 || identity.length > 96 || !prompt || prompt.length > 1000 || !ROLE_TYPES.has(role.roleType)) return null;
  if (expectedRoleType && role.roleType !== expectedRoleType) return null;
  return { name, identity, prompt, roleType: role.roleType };
}

// Keep edits made during a request, and ignore results after this editor closes.
export function createAgentTemplateDraft({ generate, readFields, applyFields, existingNames, changed, roleType = "Analyst" }) {
  let disposed = false;
  const edited = new Set();
  let state = { busy: false, selectedId: "", roleType, status: "", description: "Choose an approach, or write your own." };
  return {
    get state() { return { ...state }; },
    edit(field) { if (state.busy && PROFILE_FIELDS.includes(field)) edited.add(field); },
    dispose() { disposed = true; },
    async select(template) {
      if (disposed || state.busy) return;
      const before = readFields();
      edited.clear();
      state = { busy: true, selectedId: template.id, roleType: template.roleType, status: "Generating…", description: template.description };
      changed({ ...state });
      let profile = null;
      try { profile = generatedAgent(await generate(template.roleType), template.roleType); } catch { /* A local starting point remains available. */ }
      if (disposed) return;
      const proposed = templateAgent(profile || template, existingNames());
      const current = readFields();
      const updates = {};
      for (const field of PROFILE_FIELDS) {
        if (!edited.has(field) && current[field] === before[field]) updates[field] = proposed[field];
      }
      applyFields(updates);
      state = { ...state, busy: false, status: "", description: profile ? template.description : "Starter template used. You can edit it." };
      changed({ ...state });
    },
  };
}
