// Starting points for the "Add agent" dialog. Each is a fallback profile for its role
// type; the dialog asks the server for one tailored to the debate's question first.
// The first three mirror the default agents (Avery, Jordan, Casey).
export const AGENT_TEMPLATES = [
  { id: "analyst", roleType: "Analyst", name: "Avery", identity: "Analyst", prompt: "Look for assumptions, evidence, and what would change the conclusion." },
  { id: "challenger", roleType: "Challenger", name: "Jordan", identity: "Challenger", prompt: "Test the strongest claim. Name a practical trade-off or counterexample." },
  { id: "synthesizer", roleType: "Synthesizer", name: "Casey", identity: "Synthesizer", prompt: "Connect the strongest points and name what remains unresolved." },
  { id: "builder", roleType: "Builder", name: "Riley", identity: "Builder", prompt: "Turn the discussion into practical next steps. Say what to try first, what it needs, and how to tell if it worked." },
  { id: "skeptic", roleType: "Skeptic", name: "Morgan", identity: "Skeptic", prompt: "Look for risks and failure modes. Ask what could go wrong, who bears the cost, and what has not been checked." },
];

// Prefer the template's own name; fall back to a numbered variant so a new agent
// never shares a name with an existing one (mentions resolve by name).
export function templateAgent(template, existingNames = []) {
  const taken = new Set(existingNames.map(name => String(name).trim().toLocaleLowerCase()));
  let name = template.name;
  for (let suffix = 2; taken.has(name.toLocaleLowerCase()); suffix += 1) name = `${template.name} ${suffix}`;
  return { name, identity: template.identity, prompt: template.prompt };
}

// Accept a generated role only if it fits the agent editor's fields.
export function generatedAgent(role) {
  if (!role || typeof role !== "object") return null;
  const name = typeof role.name === "string" ? role.name.trim() : "";
  const identity = typeof role.identity === "string" ? role.identity.trim() : "";
  const prompt = typeof role.prompt === "string" ? role.prompt.trim() : "";
  if (!name || name.length > 48 || identity.length > 96 || !prompt || prompt.length > 1000) return null;
  return { name, identity, prompt };
}
