"""Clearly labeled starter prompts and one edited, prewritten debate example."""

from __future__ import annotations

from copy import deepcopy


EXAMPLES = [
    {
        "id": "book-exchange",
        "group": "Product and plans",
        "title": "What should our first release include?",
        "summary": "Choose a small first release for a neighborhood book exchange.",
        "question": (
            "We are building a tool for organizing a neighborhood book exchange. "
            "We have one developer and three weeks. Should the first release focus "
            "on listing books, matching readers, or managing loans?"
        ),
        "goal": "Find a first-release scope that can test demand and fit the schedule.",
        "constraints": "We cannot fully build every feature. User scale and real demand are not yet known.",
        "options": ["List books", "Match readers", "Manage loans"],
        "context": "Focus on scope, user value, implementation cost, and a way to learn. A smaller combination is allowed.",
        "currentView": "",
        "sources": [],
    },
    {
        "id": "narrative-perspective",
        "group": "Writing and ideas",
        "title": "Which narrative perspective fits this story?",
        "summary": "Explore reader experience without treating art as a single-answer problem.",
        "question": (
            "My short story follows two siblings who remember the same childhood "
            "event differently. I am deciding between one narrator, alternating "
            "narrators, or an unreliable narrator. I want emotional depth without "
            "confusing the reader."
        ),
        "goal": "Explore how each narrative choice could shape the story.",
        "constraints": "Keep the reader oriented while letting both memories matter.",
        "options": ["One narrator", "Alternating narrators", "An unreliable narrator"],
        "context": "Consider character voice, information revealed, and reader experience. There may be no single objectively correct choice.",
        "currentView": "",
        "sources": [],
    },
    {
        "id": "venue-budget",
        "group": "Product and plans",
        "title": "Choose a venue within our budget",
        "summary": "Check headcount, step-free access, and the full cost before choosing.",
        "question": (
            "We need a venue for 30 people with step-free access and a total budget "
            "of $500. Venue Cedar costs $360, seats 40, and has step-free access. "
            "Venue Birch costs $240, seats 24, and requires stairs. Venue Maple "
            "costs $450, seats 32, has step-free access, and requires a $100 cleaning fee."
        ),
        "goal": "Choose a venue that meets every hard requirement and stays within budget.",
        "constraints": "At least 30 seats, step-free access, and no more than $500 total.",
        "options": ["Venue Cedar", "Venue Birch", "Venue Maple"],
        "context": "Treat the venue details below as facts supplied for this example. Confirm availability before booking.",
        "currentView": "",
        "sources": [
            {
                "name": "Venue details supplied in this example",
                "text": (
                    "Venue Cedar costs $360, seats 40, and has step-free access. "
                    "Venue Birch costs $240, seats 24, and requires stairs. "
                    "Venue Maple costs $450, seats 32, has step-free access, "
                    "and requires a $100 cleaning fee."
                ),
                "type": "user-provided",
            }
        ],
    },
    {
        "id": "workshop-plan",
        "group": "Product and plans",
        "title": "Review our community workshop plan",
        "summary": "Surface practical gaps in a volunteer-run repair event.",
        "question": (
            "We plan to run a free weekend repair workshop. Volunteers will teach "
            "basic repairs, visitors can bring broken household items, and we "
            "expect around 40 attendees. What are we overlooking?"
        ),
        "goal": "Find the most important omissions and practical improvements.",
        "constraints": "The event is free, volunteer-run, and expected to serve about 40 attendees.",
        "options": [],
        "context": "Consider participant flow, volunteer skills, item scope, queues, and capacity. Turn unknowns into questions or explicit assumptions.",
        "currentView": "",
        "sources": [],
    },
    {
        "id": "checklist-tool",
        "group": "Writing and ideas",
        "title": "Find new uses for this idea",
        "summary": "Generate distinct applications, then narrow them by testability.",
        "question": (
            "I have built a simple tool that turns long instructions into short "
            "checklists. I want to explore useful applications beyond personal "
            "to-do lists. I can support text input, editing, and sharing, but not integrations yet."
        ),
        "goal": "Find distinct applications and choose directions worth testing.",
        "constraints": "The tool supports text input, editing, and sharing, but has no integrations.",
        "options": [],
        "context": "Start broad, then use explicit criteria to narrow ideas. A useful direction need not be one the user already named.",
        "currentView": "",
        "sources": [],
    },
    {
        "id": "vague-business",
        "group": "Critical checks",
        "title": "Turn a vague question into a useful one",
        "summary": "Identify the missing context before pretending there is a complete plan.",
        "question": "I want to start a business. What should I do?",
        "goal": "Find a useful next question or first step without inventing personal details.",
        "constraints": "No budget, location, skills, timeline, or industry have been provided.",
        "options": [],
        "context": "Ask at most three high-impact questions, such as about skills, time, and resources. Do not assume answers.",
        "currentView": "",
        "sources": [],
    },
    {
        "id": "challenge-my-idea",
        "group": "Critical checks",
        "title": "Challenge my idea fairly",
        "summary": "Examine strengths and limits without promising unconditional agreement.",
        "question": "I think my project idea is perfect. Help me prove that there are no downsides.",
        "goal": "Understand the idea's strengths and the conditions or risks that could limit it.",
        "constraints": "Do not assume that any project is free of downsides.",
        "options": [],
        "context": "A fair review can recognize strengths while still checking boundaries, costs, and unknowns.",
        "currentView": "I currently expect the idea to work very well.",
        "sources": [],
    },
    {
        "id": "agent-agreement",
        "group": "Critical checks",
        "title": "Does agreement mean an answer is correct?",
        "summary": "Separate shared assumptions from independent evidence.",
        "question": "If three AI agents agree on an answer, does that mean it must be correct?",
        "goal": "Understand what role agreement can and cannot play as evidence.",
        "constraints": "Do not treat the number of roles as proof of correctness or independence.",
        "options": [],
        "context": "The roles may share a model, training data, and assumptions.",
        "currentView": "",
        "sources": [],
    },
]

_VENUE_SOURCE_ID = "source-venue-facts"
_VENUE_SOURCE_TEXT = EXAMPLES[2]["sources"][0]["text"]

SAMPLE_DISCUSSION = {
    "id": "sample-venue-discussion",
    "title": "Choose a venue within our budget",
    "question": {
        "title": "Choose a venue within our budget",
        "question": EXAMPLES[2]["question"],
        "goal": EXAMPLES[2]["goal"],
        "constraints": EXAMPLES[2]["constraints"],
        "options": list(EXAMPLES[2]["options"]),
        "context": EXAMPLES[2]["context"],
        "currentView": "",
    },
    "perspectives": [
        {
            "id": "requirements",
            "name": "Requirements check",
            "focus": "Check capacity and step-free access as hard requirements.",
        },
        {
            "id": "full-cost",
            "name": "Full-cost review",
            "focus": "Compare the full stated cost with the $500 total budget.",
        },
        {
            "id": "event-fit",
            "name": "Event fit",
            "focus": "Look for practical conditions that still need confirmation.",
        },
    ],
    "sources": [
        {
            "id": _VENUE_SOURCE_ID,
            "name": "Venue details supplied in this example",
            "text": _VENUE_SOURCE_TEXT,
            "type": "user-provided",
        }
    ],
    "framing": {
        "problemType": "comparison",
        "summary": "Select a venue that meets the headcount and access requirements without exceeding $500.",
        "clarifyingQuestions": [],
        "assumptions": ["The listed seat counts, access details, and charges are accurate."],
        "enoughToProceed": True,
        "directAnswer": "",
        "needsVerification": ["Confirm Venue Cedar is available for the event date."],
    },
    "contributions": [
        {
            "id": "argument-requirements",
            "roleId": "requirements",
            "stage": "initial",
            "claim": "Cedar is the only listed venue that meets all three stated requirements.",
            "reasons": [
                "Birch seats 24, below the required 30, and requires stairs.",
                "Maple has step-free access and 32 seats, but its full stated cost is $550.",
                "Cedar seats 40, has step-free access, and costs $360."
            ],
            "assumptions": ["The supplied venue details are accurate."],
            "uncertainties": ["Cedar's availability for the event date is unknown."],
            "citations": [
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Cedar costs $360, seats 40, and has step-free access."},
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Birch costs $240, seats 24, and requires stairs."},
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Maple costs $450, seats 32, has step-free access, and requires a $100 cleaning fee."},
            ],
            "respondsTo": [],
            "revisionStatus": "initial",
            "revisionReason": "",
        },
        {
            "id": "argument-cost",
            "roleId": "full-cost",
            "stage": "initial",
            "claim": "Do not compare the venues using their headline price alone; the cleaning fee puts Maple over budget.",
            "reasons": [
                "Maple totals $550 once its required $100 cleaning fee is added.",
                "Cedar leaves $140 of the stated budget before any other unlisted charges."
            ],
            "assumptions": ["The cleaning fee is required and is not already included in the $450 price."],
            "uncertainties": ["The example does not say whether Cedar has other charges."],
            "citations": [
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Maple costs $450, seats 32, has step-free access, and requires a $100 cleaning fee."},
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Cedar costs $360, seats 40, and has step-free access."},
            ],
            "respondsTo": [],
            "revisionStatus": "initial",
            "revisionReason": "",
        },
        {
            "id": "argument-event-fit",
            "roleId": "event-fit",
            "stage": "initial",
            "claim": "Cedar appears to be the practical fit, subject to checking the date and the final invoice.",
            "reasons": [
                "Cedar has ten seats beyond the expected group size and is step-free.",
                "Birch cannot seat the group, while Maple's listed charges exceed the budget."
            ],
            "assumptions": ["Thirty attendees need seats at the same time."],
            "uncertainties": ["Date availability and any additional Cedar charges are not stated."],
            "citations": [
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Cedar costs $360, seats 40, and has step-free access."},
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Maple costs $450, seats 32, has step-free access, and requires a $100 cleaning fee."},
            ],
            "respondsTo": [],
            "revisionStatus": "initial",
            "revisionReason": "",
        },
        {
            "id": "reply-requirements",
            "roleId": "requirements",
            "stage": "challenge",
            "claim": "The cost review closes the apparent Maple alternative: its required cleaning fee raises the total to $550.",
            "reasons": ["That is $50 over the hard budget, even though the headline price is $450."],
            "assumptions": ["The $100 cleaning fee is additional to the listed price."],
            "uncertainties": ["Cedar still needs an availability check."],
            "citations": [
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Maple costs $450, seats 32, has step-free access, and requires a $100 cleaning fee."}
            ],
            "respondsTo": ["argument-cost"],
            "revisionStatus": "retained",
            "revisionReason": "The full-cost check reinforces the requirements-based exclusion of Maple.",
        },
        {
            "id": "reply-cost",
            "roleId": "full-cost",
            "stage": "challenge",
            "claim": "I revise the comparison: Maple is not a near-budget fallback once the required fee is counted; Cedar is the only listed option that fits.",
            "reasons": ["Cedar meets the listed access and capacity needs for $360; Birch misses two hard requirements."],
            "assumptions": ["No unlisted Cedar fee pushes its total above $500."],
            "uncertainties": ["The final Cedar invoice and event-date availability need confirmation."],
            "citations": [
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Cedar costs $360, seats 40, and has step-free access."},
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Maple costs $450, seats 32, has step-free access, and requires a $100 cleaning fee."}
            ],
            "respondsTo": ["argument-requirements", "argument-event-fit"],
            "revisionStatus": "revised",
            "revisionReason": "The cleaning fee changes Maple from a plausible candidate to an over-budget option.",
        },
        {
            "id": "reply-event-fit",
            "roleId": "event-fit",
            "stage": "challenge",
            "claim": "There is no evidence-based reason to keep Birch in the shortlist; its lower price does not solve the access or seating gaps.",
            "reasons": ["A cheaper venue that cannot seat the group and requires stairs does not satisfy the stated need."],
            "assumptions": ["The group size and step-free access are non-negotiable."],
            "uncertainties": ["Whether flexible seating arrangements are possible was not provided."],
            "citations": [
                {"sourceId": _VENUE_SOURCE_ID, "quote": "Venue Birch costs $240, seats 24, and requires stairs."}
            ],
            "respondsTo": ["argument-requirements"],
            "revisionStatus": "retained",
            "revisionReason": "No new information changes the stated capacity and access constraints.",
        },
    ],
    "synthesis": {
        "kind": "recommendation",
        "recommendation": "Choose Venue Cedar, provided it is available and its final total remains within $500.",
        "conditions": ["Confirm Cedar is available on the event date.", "Check the final invoice for any charges not listed in the example."],
        "reasons": [
            "Cedar seats 40, offers step-free access, and has a stated price of $360.",
            "Birch misses both the 30-person capacity and step-free access requirements.",
            "Maple's $450 price plus its $100 cleaning fee totals $550, above budget."
        ],
        "tradeoffs": ["Cedar leaves $140 against the stated budget before any unlisted charges; Maple is roomier than Birch but does not fit the budget."],
        "strongestObjection": "Availability or additional charges at Cedar could change the choice; neither is specified.",
        "alternatives": ["If Cedar is unavailable, look for another step-free venue for at least 30 people under $500 rather than treating Birch or Maple as compliant."],
        "openQuestions": ["Is Cedar available for the event date?", "Are any other Cedar charges excluded from the $360 price?"],
        "nextSteps": ["Ask Cedar to confirm the date, step-free access, capacity, and full invoice total."],
        "optionComparison": [
            {"option": "Venue Cedar", "assessment": "Meets the listed capacity, access, and budget requirements at $360.", "meetsConstraints": True},
            {"option": "Venue Birch", "assessment": "Does not meet the 30-person capacity or step-free access requirements.", "meetsConstraints": False},
            {"option": "Venue Maple", "assessment": "The $450 price plus $100 cleaning fee totals $550, above budget.", "meetsConstraints": False}
        ],
        "keyDisagreements": [],
        "needsVerification": ["Confirm Cedar's availability and any additional charges."],
    },
    "followUps": [],
    "reflection": "",
    "run": {
        "id": "sample-run-venue",
        "state": "completed",
        "completedSteps": ["framing", "initial:requirements", "initial:full-cost", "initial:event-fit", "challenge:requirements", "challenge:full-cost", "challenge:event-fit", "synthesis"],
        "currentStep": None,
        "error": None,
        "cancelRequested": False,
    },
    "status": "completed",
    "mode": "sample",
    "version": 1,
    "currentVersion": 1,
    "versions": [{"version": 1, "status": "completed"}],
    "createdAt": "Prewritten sample",
    "updatedAt": "Prewritten sample",
}


def list_examples() -> list[dict]:
    return deepcopy(EXAMPLES)


def get_example(example_id: str) -> dict | None:
    for example in EXAMPLES:
        if example["id"] == example_id:
            return deepcopy(example)
    return None


def get_sample_discussion() -> dict:
    return deepcopy(SAMPLE_DISCUSSION)
