# B6-Debate_Agora-AI_100
# Debate Agora

Debate Agora lets users ask questions and watch AI participants discuss them from different viewpoints, challenge each other's ideas, and suggest possible answers.

**Course:** AI 100 | **Team:** Guolin, Matthew, Oliver |

## The Problem

When people ask an AI about a question or project idea, a single response can leave out important perspectives. Without someone to question the answer, it's hard to spot missing assumptions, consider other options, or judge whether the advice fits your situation. Debate Agora gives users several AI participants with different roles who discuss the question, so problems and alternatives surface before time is spent on a plan.

## Who It's For

- **Students** developing a course project or exploring a topic
- **Individuals** working on their own projects who want feedback from different viewpoints
- **Small teams** comparing plans and trade-offs

No coding or AI knowledge is needed. Users should be able to use a basic web app, describe their question in writing, and decide which suggestions fit their situation.

## How It Works

1. **Ask a question.** Enter your question and, optionally, background, goals, or constraints (e.g. "IPO or exit, unhealthy child").
2. **Review participants.** The app suggests AI roles suited to your question (such as a developer or a potential user). Confirm, edit, add, or remove them.
3. **Watch the debate.** Each participant gives an opening response with reasons, then they question and respond to each other's ideas.
4. **Join in.** Ask follow-ups, add details, correct mistakes, or `@mention` a participant to reply.
5. **Summarize.** Click **Summarize** for the options, their pros and cons, and remaining disagreements.
6. **Pause / Resume.** Stop and restart the discussion at any time.

## Interface

The app has three screens, matching the tabs at the top of the interface:

| Screen | Purpose |
|---|---|
| **01-Q: Question** | Enter your question and optional context. The button is disabled while the question is empty. |
| **02-P: Participants** | Edit each participant's avatar, name, role type, and prompt. Name and prompt cannot be blank. |
| **03-D: Debate** | Read the discussion, send follow-ups, pause/resume, and summarize. |

## Inputs and Outputs

**User inputs:** question (required), background/context (optional), confirmed list of roles, follow-up messages (with optional `@mention`), Pause/Resume, Summarize.

**Outputs:** a list of suggested roles, ordered discussion messages with each speaker's role shown, and a short summary of options, pros and cons, and disagreements.

## Technology

- Web app prototype
- LLM service accessed through an API, used to suggest roles, generate replies, and write summaries
- Each participant's replies are guided by the app's discussion instructions, the user-confirmed roles, and earlier messages in the current discussion

## Limitations and Responsible Use

- Participants are **AI simulations**, not real people. They do not replace feedback from actual teammates, users, or experts.
- The model may give incorrect information. Replies are meant to point out assumptions and claims that need checking, so verify important claims yourself.
- Similar role descriptions can lead to repetitive replies. Edit the roles to get more distinct viewpoints.
- Missing context can cause repeated or inconsistent replies. Restate important details if needed.
- If an API call fails, the existing discussion is kept and you can retry.
- If you change a shared plan based on the discussion, classmates, colleagues, or end users may be affected, so check with them.
