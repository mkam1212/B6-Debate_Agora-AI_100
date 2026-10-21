"""Bounded discussion rounds, derived from saved dialogue rather than browser timers."""

from __future__ import annotations

from typing import Any


def round_context(session: dict[str, Any]) -> dict[str, Any]:
    messages = [m for m in session.get("messages", []) if isinstance(m, dict)
                and not any(m.get(k) is True for k in ("isMock", "is_mock", "mock"))
                and m.get("source") != "mock" and m.get("origin") != "mock"]
    start = 0
    for index, message in enumerate(messages):
        if message.get("kind") == "user" or message.get("roundEnd") is True:
            start = index + 1
    turns = [m for m in messages[start:] if m.get("kind") == "participant"]
    participants = session.get("participants", [])
    closing_turns = min(4, max(1, len(participants)))
    limit = min(12, max(6, len(participants) * 3))
    closing_at = limit - closing_turns
    count = len(turns)
    has_prior_reply = any(m.get("kind") == "participant" for m in messages[:start])
    phase = "closing" if count >= closing_at else "opening" if count == 0 and not has_prior_reply else "debate"
    return {
        "phase": phase,
        "replyNumber": count + 1,
        "replyLimit": limit,
        "finalReply": count + 1 >= limit,
        "closingSpeakerIds": [m.get("roleId") for m in turns[closing_at:]],
        "continuedRound": start > 0,
        "turns": turns,
    }


def round_guidance(session: dict[str, Any]) -> str:
    context = round_context(session)
    if context["phase"] == "closing":
        return (
            "This round is closing. Respond to the strongest objection to YOUR actual earlier position. "
            "Say plainly what you now accept, would change, or still reject, and give the one reason that matters. "
            "A partial concession is welcome; honest unresolved disagreement is welcome. Do not invent a past stance, "
            "agreement, evidence or a winner. Do not add a new policy, research task, checklist or side issue. "
            "The app will summarize after the closing replies; do not write the group summary yourself."
        )
    if context["phase"] == "opening":
        return "Take a clear, concrete position on the question, with one everyday example. Leave a claim the others can test."
    return (
        "Stay on the central disagreement in the user's question. Pick one specific claim another speaker made: "
        "challenge its consequence or assumption with a concrete example, or answer an objection to your own view. "
        "Address that person by name when useful. Defend a position, rather than adding another independent tip. "
        "Do not drift into designing studies, collecting more data, contracts or checklists unless that is the actual "
        "disagreement. When an objection is persuasive, acknowledge the precise change; do not agree automatically."
    )
