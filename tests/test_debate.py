from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from functools import wraps
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

from server.gizmoapp_server import create_app, debate_chat, debate_runner, llm
from server.gizmoapp_server.debate_language import DEFAULT_LANGUAGE_INSTRUCTION
from server.gizmoapp_server.debate_roles import MAX_AVATAR_SEED, SYSTEM_PROMPT, _validated_roles
from server.gizmoapp_server.debate_runner import _verified_citations
from tests.auth_helpers import authenticated_client


def make_app(url_prefix: str = ""):
    temp_dir = tempfile.TemporaryDirectory()
    db_path = Path(temp_dir.name) / "agent-debate.sqlite3"
    app = create_app(
        {
            "TESTING": True,
            "DB_PATH": db_path,
            "URL_PREFIX": url_prefix,
            "SECRET_KEY": "debate-test-secret",
            "AUTO_MIGRATE": True,
            "ENABLED_FEATURES": frozenset(),
            "ACCOUNT_STARTER_REPLIES": 10000,
        },
        shell_variant="text",
    )
    return temp_dir, app


def question_payload(question: str = "Should we build the listing or loan flow first?"):
    return {
        "title": "Choose a first release",
        "question": question,
        "goal": "Learn what people need within three weeks.",
        "constraints": "One developer; no more than three weeks.",
        "options": ["Listings", "Loans"],
        "context": "Demand has not been checked yet.",
        "currentView": "I suspect a smaller scope is safer.",
        "sources": [{"name": "Planning note", "text": "We have one developer and three weeks."}],
    }


def chat_session_payload():
    return {
        "question": "Should we open the workshop to beginners?",
        "participants": [
            {
                "id": "role-1",
                "avatar": "🔎",
                "name": "Avery",
                "prompt": "Check the evidence and assumptions.",
                "roleType": "Analyst",
            },
            {
                "id": "role-2",
                "avatar": "⚖️",
                "name": "Jordan",
                "prompt": "Test the strongest claim.",
                "roleType": "Challenger",
            },
        ],
        "messages": [
            {"kind": "participant", "roleId": "role-1", "letter": "A", "body": "Start with a short pilot."},
            {"kind": "user", "body": "What would the pilot need to measure?"},
        ],
        "draft": "Ask two instructors first",
        "debateStarted": True,
        "paused": True,
    }


def reply_context_from_request(messages):
    """Reconstruct source assertions from the actual role-separated SDK transcript."""
    metadata_marker = "\n\nInternal source and speaker directory (JSON; context only):\n"
    system = messages[0]["content"]
    system_rules, _, role_json = system.partition(metadata_marker)
    role_metadata = json.loads(role_json) if role_json else {}
    turns = []
    coordination_task = None
    for message, source in zip(messages[1:], role_metadata.get("turnSources", [])):
        source = dict(source)
        if source.get("sourceType") == "coordinator_advisory_task":
            _, _, task_json = message["content"].partition("\n")
            coordination_task = json.loads(task_json)
            continue
        source["content"] = message["content"]
        source["role"] = message["role"]
        if source.get("sourceType") == "user_question_and_message":
            source["sourceType"] = "user_message"
            source["openingQuestionSourceId"] = "opening-question:0"
        turns.append(source)
    users = [turn for turn in turns if turn["speakerType"] == "user"]
    advisers = [turn for turn in turns if turn["speakerType"] == "simulated_adviser"]
    question_turn = next(
        (turn for turn in turns if turn.get("sourceType") == "user_question"),
        next((turn for turn in turns if turn.get("openingQuestionSourceId") == "opening-question:0"), None),
    )
    latest_user = users[-1]["content"] if users else None
    latest_message = turns[-1] if turns else None
    latest_adviser = next((turn for turn in reversed(turns) if turn["speakerType"] != "user"), None)
    assigned = role_metadata.get("currentSpeaker", {})
    identity_directory = {
        "questionAskerId": "user",
        "simulatedAdvisers": role_metadata.get("simulatedAdvisers", []),
        "userTeamMembershipRecords": [],
    }
    return {
        "systemRules": system_rules,
        "question": question_turn["content"] if question_turn else None,
        "questionSource": (
            {
                "sourceId": "opening-question:0",
                "sequence": 0,
                "sourceType": "user_question",
                "speakerId": "user",
                "content": question_turn["content"],
            }
            if question_turn
            else None
        ),
        "identityDirectory": identity_directory,
        "assignedAdvisor": assigned,
        "turnGuidance": (
            {
                "source": "coordinator_suggestion_not_user_content",
                "intent": coordination_task.get("intent"),
                "focus": coordination_task.get("focus"),
                "replyToMessageId": coordination_task.get("replyToMessageId"),
                "referencedMessage": coordination_task.get("referencedMessage"),
                "isUserAuthored": coordination_task.get("isUserAuthored"),
                "status": coordination_task.get("status"),
            }
            if coordination_task
            else None
        ),
        "conversation": turns,
        "sourceDirectory": role_metadata.get("turnSources", []),
        "userAuthoredMessages": [{"speakerId": "user", "content": turn["content"]} for turn in users],
        "simulatedAdviserTurns": [
            {"speakerId": turn["speakerId"], "speaker": turn["speaker"], "content": turn["content"]}
            for turn in advisers
        ],
        "latestMessage": latest_message,
        "latestAdvisorMessage": latest_adviser,
        "assignedAdvisorPreviousReplies": [
            turn for turn in advisers if turn["speakerId"] == assigned.get("advisorId")
        ][-3:],
        "unansweredAdvisorQuestions": [],
        "latestUserMessage": latest_user if latest_message and latest_message["speakerType"] == "user" else None,
        "currentUserGoal": latest_user,
        "modelRequestText": json.dumps(messages, ensure_ascii=False),
    }


def coordinator_decision(
    participant_id: str,
    focus: str,
    intent: str = "respond",
) -> str:
    return json.dumps(
        {
            "participantId": participant_id,
            "focus": focus,
            "intent": intent,
        }
    )


def coordinator_context_from_request(messages):
    return json.loads(messages[1]["content"])


def changed_goal_session_payload():
    payload = chat_session_payload()
    payload["question"] = "Should I apply to Stanford or Tsinghua for graduate school?"
    payload["participants"] = [
        {
            "id": "role-1",
            "avatar": "🔎",
            "name": "Lan Ting",
            "prompt": "Compare Stanford and Tsinghua graduate admissions and school selection.",
            "roleType": "Analyst",
        },
        {
            "id": "role-2",
            "avatar": "⚖️",
            "name": "Career Lens",
            "prompt": "Assess finance careers, entry-level employment, and direct work after a bachelor's degree.",
            "roleType": "Analyst",
        },
        {
            "id": "role-3",
            "avatar": "🧭",
            "name": "Goal Tracker",
            "prompt": "Focus on changed user goals, when a graduate-school application question stops applying, and whether direct employment changes the relevant decision.",
            "roleType": "Analyst",
        },
    ]
    payload["messages"] = [
        {"kind": "user", "body": payload["question"]},
        {
            "kind": "participant",
            "roleId": "role-1",
            "letter": "A",
            "body": "Are you applying to those graduate programs?",
            "waitingForUser": True,
        },
        {
            "kind": "user",
            "body": "I changed my goal: I will not attend graduate school. I want direct finance employment after my bachelor's.",
        },
    ]
    return payload


def summary_session_payload(*, real_replies: int = 3):
    payload = chat_session_payload()
    payload["question"] = "How should the group test one campus second-hand trade?"
    payload["participants"][0].update(
        identity="student organizing campus resale trades",
        prompt="Focus on handoffs and trust; local participation is unknown.",
    )
    payload["participants"][1].update(
        identity="student who buys and sells second-hand goods",
        prompt="Consider buyer confidence and listing friction; do not claim local user evidence.",
    )
    payload["messages"] = [
        {"kind": "user", "id": "summary-opening", "body": payload["question"]},
        {
            "kind": "participant",
            "id": "summary-role-1-a",
            "roleId": "role-1",
            "letter": "A",
            "body": "A short handoff test could reveal where coordination gets difficult.",
        },
        {"kind": "user", "id": "summary-goal", "body": "I want to test one trade before deciding what to build."},
        {
            "kind": "participant",
            "id": "summary-role-2-b",
            "roleId": "role-2",
            "letter": "B",
            "body": "Buyers may need clear pickup details to feel comfortable.",
        },
    ]
    if real_replies >= 3:
        payload["messages"].append(
            {
                "kind": "participant",
                "id": "summary-role-1-c",
                "roleId": "role-1",
                "letter": "A",
                "body": "A shared note is one possible way to record whether a handoff completed.",
            }
        )
    return payload


def append_mock_session_message(app, session_id: str, body: str = "SUMMARY_MOCK_SENTINEL"):
    connection = sqlite3.connect(app.config["DB_PATH"])
    try:
        row = connection.execute(
            "SELECT messages_json FROM debate_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        messages = json.loads(row[0])
        messages.append(
            {
                "kind": "participant",
                "roleId": "role-2",
                "letter": "B",
                "body": body,
                "isMock": True,
            }
        )
        connection.execute(
            "UPDATE debate_sessions SET messages_json = ? WHERE id = ?",
            (json.dumps(messages), session_id),
        )
        connection.commit()
    finally:
        connection.close()


def marked_reply(content: str, *, waiting_for_user: bool = False) -> str:
    marker = "true" if waiting_for_user else "false"
    return f"WAITING_FOR_USER: {marker}\n{content}"


def with_legacy_control_protocol(test):
    @wraps(test)
    def wrapped(self, *args, **kwargs):
        with patch.object(debate_chat, "_AUTO_CLARIFICATION_WAIT_ENABLED", True):
            return test(self, *args, **kwargs)

    return wrapped


def completion_metadata(content: str) -> dict[str, object]:
    return {
        "finish_reason": "stop",
        "output_chars": len(content),
        "token_parameter": "max_tokens",
        "reasoning_hints": "reasoning_effort",
        "failure": None,
        "api_key": "diag-test-secret-key",
        "authorization": "Bearer diag-test-secret-key",
        "raw_output": content,
        "conversation": "diag-test-private-conversation",
    }


def observed_stream_failure(error, *, finish_reason, output_chars, failure):
    def stream(_messages, *, max_tokens, on_completion=None):
        if on_completion is not None:
            on_completion(
                {
                    "finish_reason": finish_reason,
                    "output_chars": output_chars,
                    "token_parameter": "max_tokens",
                    "reasoning_hints": "reasoning_effort",
                    "failure": failure,
                    "api_key": "diag-test-secret-key",
                    "authorization": "Bearer diag-test-secret-key",
                    "internal_url": "https://private.invalid/endpoint",
                }
            )
        raise error

    return stream


def observed_chat_replies(*contents: str):
    responses = iter(contents)

    def chat(_messages, *, max_tokens, on_completion=None):
        content = next(responses)
        if on_completion is not None:
            on_completion(completion_metadata(content))
        return content

    return chat


def observed_stream_reply(*chunks: str):
    def stream_chat(_messages, *, max_tokens, on_completion=None):
        yield from chunks
        if on_completion is not None:
            on_completion(completion_metadata("".join(chunks)))

    return stream_chat


def generated_roles_response():
    return json.dumps(
        {
            "roles": [
                {
                    "name": "Maya",
                    "identity": "parent who visits the library with young children",
                    "roleType": "Analyst",
                    "prompt": "As a simulated library visitor, Maya notices travel time, accessible hours, and whether the schedule works for families. She can surface practical access needs but does not know local demand without asking; she speaks in concrete examples and brief questions.",
                    "avatarSeed": 101,
                },
                {
                    "name": "Devon",
                    "identity": "library staff member coordinating weekend shifts",
                    "roleType": "Challenger",
                    "prompt": "From a simulated shift-coordinator viewpoint, Devon weighs staffing hours, fair rota coverage, and fatigue. Devon can map schedule trade-offs but does not know this library's rota; the voice is direct and careful about unknowns.",
                    "avatarSeed": 202,
                },
                {
                    "name": "Leila",
                    "identity": "local resident considering the library's weekend programs",
                    "roleType": "Builder",
                    "prompt": "Leila represents a simulated service-planning viewpoint, focusing on incremental cost, opening-hour demand, and service value. She can suggest a small test but lacks attendance data; she speaks cautiously and asks what evidence is available.",
                    "avatarSeed": 303,
                },
            ]
        }
    )


def fake_framing(_context):
    return {
        "problemType": "comparison",
        "summary": "Choose a small feature that tests demand.",
        "enoughToProceed": True,
        "clarifyingQuestions": [],
        "assumptions": ["The team can speak with a few local readers."],
        "directAnswer": "",
        "directAnswerReasons": [],
        "needsVerification": [],
        "citations": [],
    }


def fake_initial(_context, perspective):
    return {
        "claim": f"Start with a small test from {perspective['name']}’s perspective.",
        "reasons": [perspective["focus"]],
        "assumptions": [],
        "uncertainties": ["Local demand is not yet known."],
        "citations": [],
    }


def fake_challenge(_context, perspective, initial_views):
    other = next(item for item in initial_views if item["roleId"] != perspective["id"])
    return {
        "claim": f"Keep the small test, while checking {other['id']}’s concern.",
        "reasons": ["A short test can reveal whether the scope solves a real need."],
        "assumptions": [],
        "uncertainties": [],
        "respondsTo": [other["id"]],
        "revisionStatus": "revised",
        "revisionReason": "The other perspective surfaced a useful constraint.",
        "citations": [],
    }


def fake_synthesis(context, _framing, _perspectives, contributions):
    assert any(item["stage"] == "challenge" for item in contributions)
    return {
        "kind": "recommendation",
        "recommendation": f"Test a small release based on this question: {context['question']}",
        "conditions": ["Talk with readers before expanding scope."],
        "reasons": ["The team has three weeks and demand is still uncertain."],
        "tradeoffs": ["A narrow test postpones some useful features."],
        "strongestObjection": "The test may not represent future demand.",
        "alternatives": ["Add the second feature after the first test."],
        "openQuestions": ["How many readers can be reached this week?"],
        "nextSteps": ["Ask five readers to try a listing."],
        "optionComparison": [],
        "keyDisagreements": [],
        "needsVerification": [],
        "citations": [],
    }


class AgentDebateApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir, self.app = make_app()
        self.client = authenticated_client(self.app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pages_load_directly_and_assets_are_available(self):
        for path in (
            "/app",
            "/app/participants",
            "/app/debate",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                page = response.get_data(as_text=True)
                self.assertIn("<title>Debate Agora</title>", page)
                self.assertNotIn("Mock mode", page)
                self.assertIn('id="stage-navigation"', page)
                self.assertIn("/app/text/debate.js", page)
                self.assertRegex(page, r'type="module" src="/app/text/debate\.js(?:\?[^\"]*)?"')
        for path in ("/app/text/debate.js", "/app/text/debate.css", "/app/text/ui.js", "/app/text/workspace.js", "/app/text/drafts.js", "/app/text/assets/debate-agora-wordmark.png"):
            response = self.client.get(path)
            try:
                self.assertEqual(response.status_code, 200)
                if path == "/app/text/debate.js":
                    script = response.get_data(as_text=True)
                    self.assertIn('data-role-field="identity"', script)
                    self.assertIn('maxlength="96"', script)
                    self.assertIn('function roleIdentityLabel(role)', script)
                    self.assertIn('"Participant"', script)
                    identity_label = script.split("function roleIdentityLabel(role)", 1)[1].split("\n}", 1)[0]
                    self.assertIn("role?.identity", identity_label)
                    self.assertNotIn("roleType", identity_label)
            finally:
                response.close()

    def test_source_archive_download_contains_source_and_excludes_runtime_data(self):
        response = self.client.get("/api/debate/source.zip")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn("attachment; filename=\"debate-agora-source.zip\"", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        with ZipFile(BytesIO(response.data)) as archive:
            self.assertIsNone(archive.testzip())
            names = set(archive.namelist())
        self.assertIn("debate-agora/server/gizmoapp_server/debate_api.py", names)
        self.assertIn("debate-agora/server/gizmoapp_server/static/app/text/debate.js", names)
        self.assertIn("debate-agora/server/gizmoapp_server/static/app/text/assets/debate-agora-wordmark.png", names)
        self.assertIn("debate-agora/server/gizmoapp_server/static/app/text/vendor/LUCIDE-LICENSE", names)
        self.assertIn("debate-agora/server/gizmoapp_server/static/app/text/drafts.js", names)
        self.assertIn("debate-agora/server/gizmoapp_server/templates/index_text.html", names)
        self.assertIn("debate-agora/tests/test_debate.py", names)
        self.assertIn("debate-agora/server/requirements.txt", names)
        self.assertFalse(any(".env" in name or "/var/" in name or ".sqlite" in name for name in names))
        self.assertFalse(any("/__pycache__/" in name or "/.git/" in name or "/.venv/" in name for name in names))

    def test_examples_are_distinct_and_sample_is_clearly_prewritten(self):
        examples = self.client.get("/api/debate/examples").get_json()["examples"]
        self.assertEqual(len(examples), 8)
        self.assertEqual(len({example["question"] for example in examples}), 8)
        self.assertIn("Venue Maple", examples[2]["question"])

        sample = self.client.get("/api/debate/examples/sample").get_json()
        discussion = sample["discussion"]
        self.assertTrue(sample["sample"])
        self.assertEqual(discussion["mode"], "sample")
        self.assertEqual(discussion["status"], "completed")
        self.assertIn("Cedar", discussion["synthesis"]["recommendation"])
        self.assertFalse(discussion["synthesis"]["optionComparison"][1]["meetsConstraints"])
        self.assertFalse(discussion["synthesis"]["optionComparison"][2]["meetsConstraints"])
        exported = self.client.get("/api/debate/examples/sample/export?format=md&full=1").get_data(as_text=True)
        self.assertIn("Sample discussion — prewritten example", exported)
        self.assertIn("$550", exported)
        self.assertIn("I revise the comparison", exported)

    def test_create_edit_reload_and_versions_are_saved_in_sqlite(self):
        created = self.client.post("/api/debate/discussions", json=question_payload())
        self.assertEqual(created.status_code, 201)
        discussion = created.get_json()["discussion"]
        self.assertEqual(discussion["status"], "draft")
        self.assertEqual(discussion["mode"], "live")
        self.assertEqual(len(discussion["perspectives"]), 3)
        source_id = discussion["sources"][0]["id"]
        self.assertTrue(source_id.startswith("source-"))

        edited_payload = question_payload("Should the first test focus on listings?")
        edited_payload["sources"][0]["id"] = source_id
        edited_payload["perspectives"] = discussion["perspectives"]
        edited = self.client.put(f"/api/debate/discussions/{discussion['id']}", json=edited_payload)
        self.assertEqual(edited.status_code, 200)
        reloaded = self.client.get(f"/api/debate/discussions/{discussion['id']}").get_json()["discussion"]
        self.assertEqual(reloaded["question"]["question"], "Should the first test focus on listings?")
        self.assertEqual(reloaded["sources"][0]["id"], source_id)

        changed = question_payload("Should we test a reader match first?")
        version = self.client.post(
            f"/api/debate/discussions/{discussion['id']}/versions",
            json={**changed, "baseVersion": 1, "perspectives": discussion["perspectives"]},
        )
        self.assertEqual(version.status_code, 201)
        latest = version.get_json()["discussion"]
        self.assertEqual(latest["version"], 2)
        self.assertEqual(latest["question"]["question"], "Should we test a reader match first?")
        old = self.client.get(f"/api/debate/discussions/{discussion['id']}?version=1").get_json()["discussion"]
        self.assertEqual(old["question"]["question"], "Should the first test focus on listings?")
        self.assertEqual(old["isCurrentVersion"], False)

    def test_missing_model_fails_closed_without_fabricating_responses(self):
        created = self.client.post("/api/debate/discussions", json=question_payload()).get_json()["discussion"]
        with patch("server.gizmoapp_server.debate_runner.llm.is_configured", return_value=False):
            response = self.client.post(f"/api/debate/discussions/{created['id']}/run")
        self.assertEqual(response.status_code, 410)
        self.assertIn("retired", response.get_json()["errors"][0])
        saved = self.client.get(f"/api/debate/discussions/{created['id']}").get_json()["discussion"]
        self.assertEqual(saved["contributions"], [])
        self.assertIsNone(saved["synthesis"])
        self.assertEqual(saved["run"]["state"], "idle")

    def test_role_generation_returns_three_valid_roles_with_server_ids_and_prefix(self):
        temp_dir, app = make_app("/agent-debate")
        question = "校园旧书交换如何进行一次小规模测试？"
        try:
            client = authenticated_client(app)
            with (
                patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
                patch(
                    "server.gizmoapp_server.debate_roles.llm.chat",
                    return_value=generated_roles_response(),
                ) as model_chat,
            ):
                response = client.post(
                    "/agent-debate/api/debate/roles/generate",
                    json={"question": question, "avoidNames": ["Maya", "Avery", "阿杰"]},
                )

            self.assertEqual(response.status_code, 200)
            roles = response.get_json()["roles"]
            self.assertEqual(len(roles), 3)
            self.assertTrue(
                all(set(role) == {"id", "name", "identity", "roleType", "prompt", "avatarSeed"} for role in roles)
            )
            self.assertEqual(len({role["id"] for role in roles}), 3)
            self.assertTrue(all(len(role["id"]) == 32 for role in roles))
            self.assertEqual(len({role["roleType"] for role in roles}), 3)
            self.assertTrue(all(role["identity"].strip() for role in roles))
            self.assertTrue(all(role["identity"] != role["roleType"] for role in roles))
            self.assertTrue(all(isinstance(role["avatarSeed"], int) for role in roles))
            model_input = json.loads(model_chat.call_args.args[0][1]["content"].split("\n", 1)[1])
            self.assertEqual(model_input, {"discussionQuestion": question, "avoidNames": ["Maya", "Avery", "阿杰"]})
            generation_instructions = model_chat.call_args.args[0][0]["content"]
            self.assertEqual(generation_instructions, SYSTEM_PROMPT)
            self.assertIn(DEFAULT_LANGUAGE_INSTRUCTION, generation_instructions)
            self.assertIn("write each identity and prompt in plain, familiar English", generation_instructions)
            self.assertIn("varied personal names written in Latin script and familiar to English readers", generation_instructions)
            self.assertNotIn("primary language of discussionQuestion", generation_instructions)
            self.assertIn("Treat optional avoidNames as recent names to avoid", generation_instructions)
            self.assertIn("Names must be distinct after trimming and casefolding", generation_instructions)
            self.assertIn("A personal name and identity are separate fields", generation_instructions)
            self.assertIn("brief, concrete current role/status label for a simulated participant", generation_instructions)
            self.assertIn("relevant to the broad discussion subject", generation_instructions)
            self.assertIn("not a default set", generation_instructions)
            self.assertIn("thinking method, or abstract action-style label", generation_instructions)
            self.assertIn("Never copy a roleType into identity", generation_instructions)
            self.assertIn("not a sentence, past event, achievement, or biography", generation_instructions)
            self.assertIn("Do not invent or claim past accomplishments, personal experience", generation_instructions)
            self.assertIn("General knowledge may inform reasoning but must not be presented as lived experience", generation_instructions)
            self.assertIn("The discussionQuestion is case context, not a persona assignment", generation_instructions)
            self.assertIn("project ownership, team membership", generation_instructions)
            self.assertIn("stable priorities", generation_instructions)
            self.assertNotIn("Ground concerns in the question", generation_instructions)
            self.assertNotIn("why its viewpoint matters to", generation_instructions)
            self.assertIn("Describe stable priorities", generation_instructions)
            self.assertIn("knowledge boundaries", generation_instructions)
            self.assertIn("substantively different areas of attention", generation_instructions)
            self.assertIn("Do not prescribe an answer", generation_instructions)
            self.assertIn("Define how the role examines a question, not a favored answer", generation_instructions)
            self.assertIn("never assign a lasting preference for one tool, platform, or solution", generation_instructions)
            self.assertIn("assume a participant supports or opposes any option", generation_instructions)
            self.assertIn("Leave judgment open to the question's evidence and later discussion", generation_instructions)
            self.assertIn("roleType must not define a participant's focus, stance, personality, or reply behavior", generation_instructions)
            self.assertNotIn("as embodied by", generation_instructions)
            self.assertNotIn("These examples are not a fixed set", generation_instructions)
            self.assertEqual([role["name"] for role in roles], ["Maya", "Devon", "Leila"])
            self.assertIn("does not know local demand without asking", roles[0]["prompt"])
            self.assertLessEqual(model_chat.call_args.kwargs["max_tokens"], 900)
            self.assertEqual(client.post("/api/debate/roles/generate", json={"question": question}).status_code, 404)
        finally:
            temp_dir.cleanup()

    def test_legacy_analysis_model_request_defaults_to_english_for_chinese_question(self):
        context = {"question": "校园旧书交换如何开始？", "currentView": "先做小规模测试。"}
        messages = debate_runner._messages(context, "Compare one useful first step.")
        self.assertIn(DEFAULT_LANGUAGE_INSTRUCTION, messages[0]["content"])
        self.assertNotIn("Use the language of the user's question", messages[0]["content"])
        self.assertIn(json.dumps(context, ensure_ascii=False, separators=(",", ":")), messages[1]["content"])

    def test_single_role_generation_returns_one_role_of_the_requested_type(self):
        temp_dir, app = make_app("/agent-debate")
        question = "Should public transit be free?"
        role = {
            "name": "Priya",
            "identity": "Daily transit rider",
            "roleType": "Skeptic",
            "prompt": "Looks for service gaps that fares alone would not fix. Asks who is left out and what evidence is missing.",
            "avatarSeed": 5,
        }
        try:
            client = authenticated_client(app)
            with (
                patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
                patch(
                    "server.gizmoapp_server.debate_roles.llm.chat",
                    return_value=json.dumps({"roles": [role]}),
                ) as model_chat,
            ):
                response = client.post(
                    "/agent-debate/api/debate/roles/generate-one",
                    json={"question": question, "roleType": "Skeptic", "avoidNames": ["Avery"]},
                )
                invalid = client.post(
                    "/agent-debate/api/debate/roles/generate-one",
                    json={"question": question, "roleType": "Wizard"},
                )
                wrong_type = client.post(
                    "/agent-debate/api/debate/roles/generate-one",
                    json={"question": question, "roleType": "Builder"},
                )

            self.assertEqual(response.status_code, 200)
            generated = response.get_json()["role"]
            self.assertEqual(set(generated), {"id", "name", "identity", "roleType", "prompt", "avatarSeed"})
            self.assertEqual(len(generated["id"]), 32)
            self.assertEqual(generated["roleType"], "Skeptic")
            first_input = json.loads(model_chat.call_args_list[0].args[0][1]["content"].split("\n", 1)[1])
            self.assertEqual(first_input, {"discussionQuestion": question, "roleType": "Skeptic", "avoidNames": ["Avery"]})
            self.assertEqual(invalid.status_code, 400)
            # The model answered with a Skeptic for a Builder request, twice (initial attempt and repair).
            self.assertEqual(wrong_type.status_code, 502)
        finally:
            temp_dir.cleanup()

    def test_role_generation_repairs_missing_identity_and_returns_concrete_person_fields(self):
        malformed_payload = json.loads(generated_roles_response())
        del malformed_payload["roles"][1]["identity"]
        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
                patch(
                    "server.gizmoapp_server.debate_roles.llm.chat",
                    side_effect=[json.dumps(malformed_payload), generated_roles_response()],
            ) as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={
                    "question": "How should students choose a first domestic finance job?",
                    "avoidNames": ["Recent Name", "Another Name"],
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model_chat.call_count, 2)
        roles = response.get_json()["roles"]
        self.assertTrue(all(role["identity"].strip() for role in roles))
        first_request_data = json.loads(model_chat.call_args_list[0].args[0][1]["content"].split("\n", 1)[1])
        self.assertEqual(
            first_request_data,
            {"discussionQuestion": "How should students choose a first domestic finance job?", "avoidNames": ["Recent Name", "Another Name"]},
        )
        repair_messages = model_chat.call_args_list[1].args[0]
        self.assertEqual(repair_messages[0]["content"], SYSTEM_PROMPT)
        self.assertIn("following every system instruction", repair_messages[1]["content"])
        self.assertIn("Regenerate any identity or prompt that contains a case-specific fact", repair_messages[1]["content"])
        self.assertIn("Keep discussionQuestion unchanged", repair_messages[1]["content"])
        self.assertIn(json.dumps(first_request_data, ensure_ascii=False), repair_messages[1]["content"])
        self.assertIn("identity", repair_messages[1]["content"])
        self.assertNotIn("preserve the previous response", repair_messages[1]["content"])

    def test_role_generation_isolates_case_quantities_through_real_sdk_repair_requests(self):
        questions = [
            ("本人每周10小时，朋友时间未知，一位想花两个月完整开发", "每周只有十小时开发时间，两周开发周期"),
            ("本人每周4小时，朋友时间未知，一位想花六周完整开发", "每周四小时，六周内完成开发"),
        ]
        clean_roles = [
            {
                "name": "林岚",
                "identity": "软件工程师",
                "roleType": "Analyst",
                "prompt": "长期关注系统可靠性与维护性，先厘清技术边界再比较方案。善于指出依赖与不确定性，不把一般知识当作实测结论。表达简洁并说明判断依据。",
                "avatarSeed": 11,
            },
            {
                "name": "许宁",
                "identity": "用户研究者",
                "roleType": "Challenger",
                "prompt": "长期关注真实需求与证据质量，优先追问使用者及如何验证价值。擅长设计中立的验证思路，但不预设答案。表达好奇并追问依据。",
                "avatarSeed": 22,
            },
            {
                "name": "周禾",
                "identity": "服务设计者",
                "roleType": "Builder",
                "prompt": "长期关注服务可及性与潜在副作用，检查方案对不同人群的影响。善于梳理可能的受益与风险，也会标明知识边界。表达谨慎并区分事实和假设。",
                "avatarSeed": 33,
            },
        ]
        sdk_create = Mock()
        sdk_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=sdk_create))
        )
        inert_environment = {
            "GIZMO_LLM_API_KEY": "test-placeholder",
            "GIZMO_LLM_BASE_URL": "https://mock.invalid/v1",
            "GIZMO_LLM_MODEL": "mock-model",
        }

        with (
            patch.dict("os.environ", inert_environment, clear=True),
            patch.object(llm, "OpenAI", Mock(return_value=sdk_client)),
            patch.object(llm, "_client", None),
            patch.object(llm, "_client_signature", None),
        ):
            for question, leaked_details in questions:
                with self.subTest(question=question):
                    bad_roles = json.loads(json.dumps(clean_roles, ensure_ascii=False))
                    bad_roles[0]["prompt"] = f"作为核心开发人员，{leaked_details}。"
                    bad_response = json.dumps({"roles": bad_roles}, ensure_ascii=False)
                    clean_response = json.dumps({"roles": clean_roles}, ensure_ascii=False)
                    sdk_create.reset_mock()
                    sdk_create.side_effect = [
                        SimpleNamespace(
                            choices=[SimpleNamespace(message=SimpleNamespace(content=bad_response), finish_reason="stop")]
                        ),
                        SimpleNamespace(
                            choices=[SimpleNamespace(message=SimpleNamespace(content=clean_response), finish_reason="stop")]
                        ),
                    ]

                    response = self.client.post(
                        "/api/debate/roles/generate",
                        json={"question": question, "avoidNames": ["近期姓名"]},
                    )

                    self.assertEqual(response.status_code, 200)
                    generated = response.get_json()["roles"]
                    self.assertEqual(len({role["prompt"] for role in generated}), 3)
                    self.assertEqual(sdk_create.call_count, 2)
                    initial_messages = sdk_create.call_args_list[0].kwargs["messages"]
                    repair_messages = sdk_create.call_args_list[1].kwargs["messages"]
                    self.assertEqual([message["role"] for message in initial_messages], ["system", "user"])
                    self.assertEqual(initial_messages[0]["content"], SYSTEM_PROMPT)
                    initial_data = json.loads(initial_messages[1]["content"].split("\n", 1)[1])
                    self.assertEqual(initial_data, {"discussionQuestion": question, "avoidNames": ["近期姓名"]})
                    self.assertEqual(repair_messages[0]["role"], "system")
                    self.assertEqual(repair_messages[0]["content"], SYSTEM_PROMPT)
                    repair_content = repair_messages[1]["content"]
                    self.assertIn(json.dumps(initial_data, ensure_ascii=False), repair_content)
                    self.assertIn("Regenerate any identity or prompt that contains a case-specific fact", repair_content)
                    self.assertIn("Keep discussionQuestion unchanged", repair_content)
                    self.assertIn(json.dumps(bad_response, ensure_ascii=False), repair_content)

    def test_role_generation_validates_optional_avoid_names(self):
        invalid_lists = [
            [f"Name {index}" for index in range(13)],
            ["Maya", 7],
            ["M" * 49],
            ["   "],
        ]
        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
            patch("server.gizmoapp_server.debate_roles.llm.chat") as model_chat,
        ):
            for names in invalid_lists:
                with self.subTest(names=names):
                    response = self.client.post(
                        "/api/debate/roles/generate",
                        json={"question": "How do students choose a first job?", "avoidNames": names},
                    )
                    self.assertEqual(response.status_code, 400)
        model_chat.assert_not_called()

    def test_same_group_names_are_distinct_after_trim_and_casefold(self):
        payload = json.loads(generated_roles_response())
        payload["roles"][0]["name"] = " Maya "
        payload["roles"][1]["name"] = "mAyA"
        with self.assertRaisesRegex(ValueError, "role names must be distinct"):
            _validated_roles(json.dumps(payload))

    def test_role_generation_reports_missing_ai_configuration_without_fallback(self):
        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=False),
            patch("server.gizmoapp_server.debate_roles.llm.chat") as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={"question": "Should we open the workshop to beginners?"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.get_json()["errors"][0])
        self.assertIn("No roles were generated", response.get_json()["errors"][0])
        model_chat.assert_not_called()

    def test_role_generation_repairs_invalid_format_only_once(self):
        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
            patch(
                "server.gizmoapp_server.debate_roles.llm.chat",
                side_effect=[json.dumps({"roles": []}), generated_roles_response()],
            ) as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={"question": "Should we open the workshop to beginners?"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["roles"]), 3)
        self.assertEqual(model_chat.call_count, 2)
        self.assertIn("Correct the previous response", model_chat.call_args_list[1].args[0][1]["content"])

    def test_role_generation_stops_after_one_failed_format_repair(self):
        malformed = json.dumps({"roles": []})
        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
            patch(
                "server.gizmoapp_server.debate_roles.llm.chat",
                side_effect=[malformed, malformed],
            ) as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={"question": "Should we open the workshop to beginners?"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertIn("unusable role format", response.get_json()["errors"][0])
        diagnostics = response.get_json()["diagnostics"]
        self.assertEqual(diagnostics["reason"], "invalid_role_count")
        self.assertEqual(diagnostics["rejections"][0]["actualCount"], 0)
        self.assertEqual(diagnostics["rejections"][1]["expectedCount"], 3)
        self.assertEqual(model_chat.call_count, 2)

    def test_role_generation_diagnostic_identifies_enum_and_field_without_model_text(self):
        malformed_roles = json.loads(generated_roles_response())
        malformed_roles["roles"][1]["roleType"] = "PRIVATE_UNSUPPORTED_ENUM"
        malformed = json.dumps(malformed_roles)

        def model_response(_messages, *, max_tokens, on_completion=None):
            if on_completion is not None:
                on_completion(
                    {
                        "finish_reason": "stop",
                        "output_chars": len(malformed),
                        "token_parameter": "max_tokens",
                        "reasoning_hints": "none",
                        "failure": None,
                    }
                )
            return malformed

        with (
            self.assertLogs("server.gizmoapp_server.debate_roles", level="WARNING") as captured,
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
            patch("server.gizmoapp_server.debate_roles.llm.chat", side_effect=model_response) as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={"question": "How should a campus book exchange begin?"},
            )

        self.assertEqual(response.status_code, 502)
        diagnostics = response.get_json()["diagnostics"]
        self.assertEqual(diagnostics["reason"], "invalid_enum")
        self.assertEqual(diagnostics["field"], "roleType")
        self.assertEqual(diagnostics["finishReason"], "stop")
        self.assertEqual(diagnostics["outputChars"], len(malformed))
        self.assertEqual(len(diagnostics["rejections"]), 2)
        self.assertTrue(all(item["reason"] == "invalid_enum" for item in diagnostics["rejections"]))
        self.assertEqual(diagnostics["rejections"][1]["allowedValues"], sorted({
            "Analyst", "Challenger", "Builder", "Skeptic", "Synthesizer"
        }))
        self.assertNotIn("PRIVATE_UNSUPPORTED_ENUM", json.dumps(response.get_json()))
        self.assertTrue(any("ROLE_GENERATION_DIAGNOSTIC" in message for message in captured.output))
        self.assertNotIn("PRIVATE_UNSUPPORTED_ENUM", "\n".join(captured.output))
        self.assertEqual(model_chat.call_count, 2)

    def test_role_generation_diagnostic_identifies_field_type_without_model_value(self):
        malformed_roles = json.loads(generated_roles_response())
        malformed_roles["roles"][0]["avatarSeed"] = {"PRIVATE": "VALUE"}
        malformed = json.dumps(malformed_roles)
        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
            patch(
                "server.gizmoapp_server.debate_roles.llm.chat",
                side_effect=[malformed, malformed],
            ) as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={"question": "How should a campus book exchange begin?"},
            )

        self.assertEqual(response.status_code, 502)
        diagnostics = response.get_json()["diagnostics"]
        self.assertEqual(diagnostics["reason"], "invalid_field_type")
        self.assertEqual(diagnostics["field"], "avatarSeed")
        self.assertEqual(diagnostics["expectedType"], "integer")
        self.assertEqual(diagnostics["actualType"], "object")
        self.assertNotIn("PRIVATE", json.dumps(response.get_json()))
        self.assertNotIn("VALUE", json.dumps(response.get_json()))
        self.assertEqual(model_chat.call_count, 2)

    def test_role_generation_error_surfaces_truncation_completion_metadata(self):
        def truncated_response(_messages, *, max_tokens, on_completion=None):
            if on_completion is not None:
                on_completion(
                    {
                        "finish_reason": "length",
                        "output_chars": 871,
                        "token_parameter": "max_tokens",
                        "reasoning_hints": "reasoning_effort",
                        "failure": "truncated",
                    }
                )
            raise llm.CourseLLMTruncatedError("The course model reached its reply token limit.")

        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
            patch("server.gizmoapp_server.debate_roles.llm.chat", side_effect=truncated_response) as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={"question": "How should a campus book exchange begin?"},
            )

        self.assertEqual(response.status_code, 503)
        diagnostics = response.get_json()["diagnostics"]
        self.assertEqual(diagnostics["reason"], "model_truncated")
        self.assertEqual(diagnostics["attempt"], 1)
        self.assertEqual(diagnostics["finishReason"], "length")
        self.assertEqual(diagnostics["outputChars"], 871)
        self.assertEqual(diagnostics["completionFailure"], "truncated")
        self.assertEqual(model_chat.call_count, 1)

    def test_role_generation_invalid_json_returns_safe_parser_diagnostic(self):
        malformed = "PRIVATE_JSON_SENTINEL { roles: ["
        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
            patch(
                "server.gizmoapp_server.debate_roles.llm.chat",
                side_effect=[malformed, malformed],
            ) as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={"question": "How should a campus book exchange begin?"},
            )

        self.assertEqual(response.status_code, 502)
        diagnostics = response.get_json()["diagnostics"]
        self.assertEqual(diagnostics["reason"], "invalid_json")
        self.assertEqual(diagnostics["outputChars"], len(malformed))
        self.assertIsInstance(diagnostics["jsonPosition"], int)
        self.assertNotIn("PRIVATE_JSON_SENTINEL", json.dumps(response.get_json()))
        self.assertEqual(model_chat.call_count, 2)

    def test_role_generation_accepts_workweek_topic_label_but_rejects_duration(self):
        roles = json.loads(generated_roles_response())
        roles["roles"][0]["prompt"] = (
            "Assess staffing coverage around a four-day workweek as a topic without assuming the team has adopted it. "
            "Consider support continuity and workload tradeoffs."
        )
        response_text = json.dumps(roles)
        with (
            patch("server.gizmoapp_server.debate_roles.llm.is_configured", return_value=True),
            patch(
                "server.gizmoapp_server.debate_roles.llm.chat",
                return_value=response_text,
            ) as model_chat,
        ):
            response = self.client.post(
                "/api/debate/roles/generate",
                json={
                    "question": (
                        "Should our team move to a four-day workweek? We are a small software team, "
                        "and customer support must remain available Monday through Friday."
                    )
                },
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertIn("four-day workweek", response.get_json()["roles"][0]["prompt"])
        model_chat.assert_called_once()

        roles["roles"][0]["prompt"] = "Recommend a four-week trial before making a longer-term decision."
        with self.assertRaises(ValueError) as raised:
            _validated_roles(json.dumps(roles))
        self.assertEqual(raised.exception.diagnostics["reason"], "time_bound_quantity")
        self.assertEqual(raised.exception.diagnostics["field"], "prompt")

    def test_full_run_saves_independent_views_challenges_and_conditioned_synthesis(self):
        created = self.client.post("/api/debate/discussions", json=question_payload()).get_json()["discussion"]
        run_path = f"/api/debate/discussions/{created['id']}/run"
        with (
            patch("server.gizmoapp_server.debate_runner.llm.is_configured", return_value=True),
            patch("server.gizmoapp_server.debate_runner._request_framing", side_effect=fake_framing),
            patch("server.gizmoapp_server.debate_runner._request_initial", side_effect=fake_initial),
            patch("server.gizmoapp_server.debate_runner._request_challenge", side_effect=fake_challenge),
            patch("server.gizmoapp_server.debate_runner._request_synthesis", side_effect=fake_synthesis),
        ):
            # The earlier runner remains a storage/unit compatibility test; its public
            # generation routes are retired so they cannot bypass live allowances.
            with self.app.app_context():
                from server.gizmoapp_server.db import get_db
                started = debate_runner.start_run(self.app, created['id'], created['version'], get_db())
            self.assertIn(started['status'], {'running', 'completed'})
            deadline = time.monotonic() + 4
            saved = None
            while time.monotonic() < deadline:
                saved = self.client.get(f"/api/debate/discussions/{created['id']}").get_json()["discussion"]
                if saved["run"]["state"] != "running":
                    break
                time.sleep(0.01)

        self.assertIsNotNone(saved)
        self.assertEqual(saved["run"]["state"], "completed")
        self.assertEqual(len([item for item in saved["contributions"] if item["stage"] == "initial"]), 3)
        self.assertEqual(len([item for item in saved["contributions"] if item["stage"] == "challenge"]), 3)
        self.assertTrue(all(item["respondsTo"] for item in saved["contributions"] if item["stage"] == "challenge"))
        self.assertIn("Should we build the listing or loan flow first?", saved["synthesis"]["recommendation"])
        self.assertEqual(saved["run"]["completedSteps"][-1], "synthesis")

    def test_unverified_citations_are_filtered_and_duplicate_source_ids_are_rejected(self):
        context = {"sources": [{"id": "source-valid", "text": "The stated budget is $500."}]}
        self.assertEqual(
            _verified_citations(
                {"citations": [{"sourceId": "source-valid", "quote": "The stated budget is $500."}]}, context
            ),
            [{"sourceId": "source-valid", "quote": "The stated budget is $500."}],
        )
        self.assertEqual(
            _verified_citations(
                {"citations": [{"sourceId": "source-missing", "quote": "The stated budget is $500."}]}, context
            ),
            [],
        )
        payload = question_payload()
        payload["sources"] = [
            {"name": "First", "text": "First source text", "id": "source-duplicate"},
            {"name": "Second", "text": "Second source text", "id": "source-duplicate"},
        ]
        result = self.client.post("/api/debate/discussions", json=payload)
        self.assertEqual(result.status_code, 400)
        self.assertIn("source IDs must be unique", result.get_json()["errors"][0])

    def test_chat_session_saves_restores_and_updates_full_state(self):
        payload = chat_session_payload()
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        saved = created.get_json()["session"]
        self.assertEqual(len(saved["id"]), 32)
        self.assertEqual(saved["question"], payload["question"])
        self.assertEqual(saved["participants"], payload["participants"])
        self.assertEqual(saved["messages"], payload["messages"])
        self.assertEqual(saved["draft"], payload["draft"])
        self.assertEqual(saved["revision"], 1)
        self.assertTrue(saved["debateStarted"])
        self.assertTrue(saved["paused"])

        restored = self.client.get(f"/api/debate/sessions/{saved['id']}")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.get_json()["session"], saved)

        payload["question"] = "Should we run two beginner sessions?"
        payload["messages"].append({"kind": "user", "body": "Let's compare the staffing cost."})
        payload["draft"] = ""
        payload["paused"] = False
        payload["expectedRevision"] = saved["revision"]
        updated = self.client.put(f"/api/debate/sessions/{saved['id']}", json=payload)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["session"]["revision"], 2)
        restored_again = self.client.get(f"/api/debate/sessions/{saved['id']}")
        self.assertEqual(restored_again.status_code, 200)
        final_session = restored_again.get_json()["session"]
        self.assertEqual(final_session["question"], payload["question"])
        self.assertEqual(final_session["messages"], payload["messages"])
        self.assertEqual(final_session["draft"], "")
        self.assertEqual(final_session["revision"], 2)
        self.assertFalse(final_session["paused"])

    def test_chat_session_preserves_web_citations_separately_from_user_context(self):
        payload = chat_session_payload()
        citations = [{"url": "https://example.org/report", "title": "Transit report", "excerpt": "Ridership increased."}]
        payload["messages"][0]["citations"] = citations
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session_id = created.json["session"]["id"]
        restored = self.client.get(f"/api/debate/sessions/{session_id}").json["session"]
        self.assertEqual(restored["messages"][0]["citations"], citations)
        self.assertNotIn("citations", restored["messages"][1])

    def test_chat_session_rejects_unsafe_citation_urls(self):
        for url in ("javascript:alert(1)", "data:text/html,hello", "https://user:password@example.org", "https://example.org/\nfoo"):
            with self.subTest(url=url):
                payload = chat_session_payload()
                payload["messages"][0]["citations"] = [{"url": url}]
                self.assertEqual(self.client.post("/api/debate/sessions", json=payload).status_code, 400)

    def test_identity_saves_restores_edits_and_reaches_model_context(self):
        payload = chat_session_payload()
        payload["participants"][0]["identity"] = "student developer building a campus marketplace"
        payload["participants"][0]["prompt"] = "Notice implementation limits; ask what data is missing."
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        saved = created.get_json()["session"]
        self.assertEqual(saved["participants"][0]["identity"], payload["participants"][0]["identity"])

        restored = self.client.get(f"/api/debate/sessions/{saved['id']}").get_json()["session"]
        self.assertEqual(restored["participants"][0]["identity"], payload["participants"][0]["identity"])
        restored["participants"][0]["identity"] = "student who buys and sells second-hand goods"
        restored["expectedRevision"] = restored["revision"]
        updated = self.client.put(f"/api/debate/sessions/{saved['id']}", json=restored)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(
            updated.get_json()["session"]["participants"][0]["prompt"],
            "Notice implementation limits; ask what data is missing.",
        )

        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            return_value="Ask a few campus sellers how they handle pickups.",
        ) as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{saved['id']}/reply", json={"participantId": "role-1"}
            )
        self.assertEqual(response.status_code, 200)
        context = reply_context_from_request(model_chat.call_args_list[0].args[0])
        self.assertEqual(context["assignedAdvisor"]["identity"], "student who buys and sells second-hand goods")
        self.assertEqual(context["assignedAdvisor"]["savedPrompt"], "Notice implementation limits; ask what data is missing.")
        self.assertEqual(
            context["identityDirectory"]["simulatedAdvisers"][0]["identity"],
            "student who buys and sells second-hand goods",
        )

    def test_each_saved_identity_and_prompt_reaches_its_selected_role_request(self):
        payload = chat_session_payload()
        payload["participants"].append(
            {
                "id": "role-3",
                "avatar": "🧭",
                "name": "Ruo",
                "prompt": "Track how exchange organizers handle trust and handoffs.",
                "roleType": "Builder",
            }
        )
        profiles = [
            ("student developer for a campus resale marketplace", "Focus on listing flow, implementation boundaries, and maintenance."),
            ("student who buys and sells second-hand goods", "Attend to trust, search friction, and practical exchange preferences."),
            ("student organizing peer-to-peer trades", "Consider safe handoffs, coordination, and unresolved transaction risks."),
        ]
        for role, (identity, prompt) in zip(payload["participants"], profiles):
            role["identity"] = identity
            role["prompt"] = prompt
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]

        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            side_effect=["A useful next contribution."] * 3,
        ) as model_chat:
            for role, (identity, prompt) in zip(payload["participants"], profiles):
                response = self.client.post(
                    f"/api/debate/sessions/{session_id}/reply", json={"participantId": role["id"]}
                )
                self.assertEqual(response.status_code, 200)
                context = reply_context_from_request(model_chat.call_args.args[0])
                self.assertEqual(context["assignedAdvisor"]["identity"], identity)
                self.assertEqual(context["assignedAdvisor"]["savedPrompt"], prompt)
                self.assertEqual(context["assignedAdvisor"]["roleType"], role["roleType"])
                self.assertIsNone(context["turnGuidance"])
        self.assertEqual(model_chat.call_count, 3)

    def test_chinese_saved_roles_do_not_override_coordinator_or_reply_default_english(self):
        payload = chat_session_payload()
        payload["question"] = "校园旧书交换怎样开始？"
        payload["participants"][0].update(
            name="陈诺", identity="校园旧书交换志愿者", prompt="关注书籍品相、交接和同学信任。"
        )
        payload["participants"][1].update(
            name="赵阳", identity="寻找二手教材的学生", prompt="关注价格、包邮和取书便利。"
        )
        payload["messages"] = [
            {"kind": "user", "id": "opening-cn", "body": payload["question"]},
            {
                "kind": "participant",
                "id": "old-cn-reply",
                "roleId": "role-1",
                "letter": "A",
                "body": "可以先核对书本品相，再约定交接地点。",
            },
            {"kind": "user", "id": "latest-cn-user", "body": "我想先测试一笔，不确定同学是否愿意到校取书。"},
        ]
        payload["paused"] = False
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]

        focus = "Compare one low-effort pickup option and identify an unknown to test."
        reply = "A staffed campus pickup point could be compared with a direct handoff."
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            side_effect=[coordinator_decision("role-2", focus), reply],
        ) as model_chat:
            response = self.client.post(f"/api/debate/sessions/{session_id}/reply", json={})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["content"], reply)
        self.assertEqual(model_chat.call_count, 2)
        coordinator_request = model_chat.call_args_list[0].args[0]
        reply_request = model_chat.call_args_list[1].args[0]
        self.assertIn(DEFAULT_LANGUAGE_INSTRUCTION, coordinator_request[0]["content"])
        self.assertIn(DEFAULT_LANGUAGE_INSTRUCTION, reply_request[0]["content"])
        coordinator_context = coordinator_context_from_request(coordinator_request)
        self.assertEqual(coordinator_context["candidates"][0]["identity"], "校园旧书交换志愿者")
        self.assertEqual(coordinator_context["candidates"][0]["prompt"], "关注书籍品相、交接和同学信任。")
        role_context = reply_context_from_request(reply_request)
        self.assertEqual(role_context["assignedAdvisor"]["identity"], "寻找二手教材的学生")
        self.assertEqual(role_context["assignedAdvisor"]["savedPrompt"], "关注价格、包邮和取书便利。")

    def test_legacy_participants_without_identity_remain_unassigned(self):
        payload = chat_session_payload()
        self.assertTrue(all("identity" not in role for role in payload["participants"]))
        created = self.client.post("/api/debate/sessions", json=payload)
        session = created.get_json()["session"]
        self.assertTrue(all("identity" not in role for role in session["participants"]))
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            return_value="The saved viewpoint can still contribute without a declared identity.",
        ) as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{session['id']}/reply", json={"participantId": "role-1"}
            )
        self.assertEqual(response.status_code, 200)
        context = reply_context_from_request(model_chat.call_args_list[0].args[0])
        self.assertEqual(context["assignedAdvisor"]["identity"], "")
        self.assertIn("Use this participant's identity and saved prompt as a viewpoint", model_chat.call_args_list[0].args[0][0]["content"])

    def test_summary_requires_three_saved_real_replies_not_users_or_mocks(self):
        payload = summary_session_payload(real_replies=2)
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]
        append_mock_session_message(self.app, session_id)

        with patch("server.gizmoapp_server.debate_api.llm.chat") as model_chat:
            response = self.client.post(f"/api/debate/sessions/{session_id}/summary", json={})

        self.assertEqual(response.status_code, 409)
        self.assertIn("3 saved participant replies", response.get_json()["errors"][0])
        model_chat.assert_not_called()

    def test_summary_uses_saved_snapshot_once_and_does_not_write_chat_history(self):
        payload = summary_session_payload(real_replies=3)
        payload["question"] = "校园二手交易的第一轮小测试应该怎么做？"
        payload["participants"][0].update(name="周文", identity="校园二手书店主", prompt="Private saved prompt one.")
        payload["participants"][1].update(name="苏青", identity="计算机系大三学生", prompt="Private saved prompt two.")
        payload["messages"][0]["body"] = payload["question"]
        payload["messages"][1]["body"] = "可以先找一个同学完成一次交接。"
        payload["messages"][2]["body"] = "我先只想试一笔交易，再看要不要扩大。"
        payload["messages"][3]["body"] = "买家可能还需要清楚的取货信息。"
        payload["messages"][4]["body"] = "共享文档可以记录交接情况，作为一个选项。"
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]
        append_mock_session_message(self.app, session_id)
        before = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        summary_text = "Main views: Zhou Wen noted handoff details; Su Qing noted pickup information.\nDisagreement: No option is confirmed.\nOpen question: What buyers need in a first trade.\nNext-step suggestion: Consider recording one handoff."

        with patch("server.gizmoapp_server.debate_api.llm.chat", return_value=summary_text) as model_chat:
            response = self.client.post(f"/api/debate/sessions/{session_id}/summary", json={})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["summary"], summary_text)
        self.assertEqual(response.get_json()["sourceRevision"], before["revision"])
        self.assertEqual(response.get_json()["summarizedMessageCount"], 5)
        model_chat.assert_called_once()
        self.assertEqual(model_chat.call_args.kwargs["max_tokens"], 650)
        request = model_chat.call_args.args[0]
        self.assertEqual([message["role"] for message in request], ["system", "user"])
        self.assertNotIn("WAITING_FOR_USER", request[0]["content"])
        context = json.loads(request[1]["content"])
        self.assertEqual(context["question"], payload["question"])
        self.assertEqual(context["sourceRevision"], before["revision"])
        self.assertEqual(
            context["participants"],
            [
                {"id": role["id"], "name": role["name"], "identity": role["identity"]}
                for role in before["participants"]
            ],
        )
        self.assertEqual([role["name"] for role in context["participants"]], ["周文", "苏青"])
        self.assertNotIn("roleType", json.dumps(context, ensure_ascii=False))
        self.assertNotIn("Private saved prompt", json.dumps(context, ensure_ascii=False))
        self.assertIn(DEFAULT_LANGUAGE_INSTRUCTION, request[0]["content"])
        self.assertIn("four concise sections in the selected output language", request[0]["content"])
        self.assertIn("do not transliterate names to pinyin", request[0]["content"])
        self.assertIn("100–180 English words by default", request[0]["content"])
        self.assertIn("not a cutoff", request[0]["content"])
        self.assertIn("not Markdown asterisk emphasis", request[0]["content"])
        self.assertIn("rather than restating the opening question in full", request[0]["content"])
        self.assertEqual(
            [turn["content"] for turn in context["conversation"]],
            [message["body"] for message in before["messages"] if not message.get("isMock")],
        )
        self.assertNotIn("SUMMARY_MOCK_SENTINEL", json.dumps(context, ensure_ascii=False))

        after = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["messages"], before["messages"])

    def test_summary_model_failures_are_explicit_and_never_modify_history(self):
        created = self.client.post("/api/debate/sessions", json=summary_session_payload()).get_json()
        session_id = created["session"]["id"]
        failures = (
            llm.CourseLLMError("The course model is temporarily unavailable."),
            llm.CourseLLMTruncatedError("The course model reached its reply token limit."),
            "",
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                before = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
                with patch(
                    "server.gizmoapp_server.debate_api.llm.chat",
                    return_value=failure if isinstance(failure, str) else None,
                    side_effect=None if isinstance(failure, str) else failure,
                ) as model_chat:
                    response = self.client.post(f"/api/debate/sessions/{session_id}/summary", json={})
                self.assertEqual(response.status_code, 503)
                model_chat.assert_called_once()
                after = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
                self.assertEqual(after["revision"], before["revision"])
                self.assertEqual(after["messages"], before["messages"])

    def test_pending_user_message_survives_pause_inflight_completion_refresh_and_resume(self):
        payload = chat_session_payload()
        payload["messages"] = payload["messages"][:1]
        payload["pendingUserMessages"] = []
        payload["paused"] = False
        saved = self.client.post("/api/debate/sessions", json=payload).get_json()["session"]
        session_id = saved["id"]

        def put_snapshot(current, **changes):
            snapshot = {
                key: current[key]
                for key in (
                    "question", "participants", "messages", "pendingUserMessages",
                    "draft", "debateStarted", "paused",
                )
            }
            snapshot.update(changes)
            snapshot["expectedRevision"] = current["revision"]
            response = self.client.put(f"/api/debate/sessions/{session_id}", json=snapshot)
            self.assertEqual(response.status_code, 200, response.get_json())
            return response.get_json()["session"]

        queued = {"id": "msg-send-pause-1", "body": "@Jordan I only have ten hours.", "participantId": "role-2"}
        with patch("server.gizmoapp_server.debate_chat.llm.chat") as model_chat:
            saved = put_snapshot(saved, pendingUserMessages=[queued], draft="")
            self.assertEqual(saved["pendingUserMessages"], [queued])
            saved = put_snapshot(saved, paused=True)
            self.assertTrue(saved["paused"])

            # The already-running reply can finish; it must not consume or duplicate the queued user turn.
            completed_reply = {
                "kind": "participant", "roleId": "role-2", "letter": "B",
                "body": "The current role reply has completed.",
            }
            saved = put_snapshot(saved, messages=[*saved["messages"], completed_reply])
            refreshed = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            self.assertTrue(refreshed["paused"])
            self.assertEqual(refreshed["pendingUserMessages"], [queued])
            self.assertEqual(refreshed["messages"][-1]["body"], completed_reply["body"])

            # Saving the Resume state does not itself call the model or drop the @ target.
            resumed = put_snapshot(refreshed, paused=False)
            self.assertFalse(resumed["paused"])
            self.assertEqual(resumed["pendingUserMessages"], [queued])
            model_chat.assert_not_called()

            # The serialized consume transition moves the same ID into messages exactly once.
            consumed = put_snapshot(
                resumed,
                messages=[*resumed["messages"], {
                    "kind": "user", "id": queued["id"], "participantId": queued["participantId"],
                    "body": queued["body"],
                }],
                pendingUserMessages=[],
            )
            repeated = put_snapshot(consumed)
            matching = [message for message in repeated["messages"] if message.get("id") == queued["id"]]
            self.assertEqual(len(matching), 1)
            self.assertEqual(matching[0]["participantId"], "role-2")
            self.assertIn("@Jordan", matching[0]["body"])
            self.assertEqual(repeated["pendingUserMessages"], [])
            model_chat.assert_not_called()

    def test_pending_user_message_conflict_and_invalid_save_preserve_saved_snapshot(self):
        payload = chat_session_payload()
        payload["pendingUserMessages"] = []
        saved = self.client.post("/api/debate/sessions", json=payload).get_json()["session"]
        session_id = saved["id"]
        queued = {"id": "msg-conflict-1", "body": "@Jordan Preserve this edit.", "participantId": "role-2"}
        latest = {
            key: saved[key]
            for key in (
                "question", "participants", "messages", "pendingUserMessages",
                "draft", "debateStarted", "paused",
            )
        }
        latest.update({"pendingUserMessages": [queued], "paused": True, "expectedRevision": saved["revision"]})
        first = self.client.put(f"/api/debate/sessions/{session_id}", json=latest)
        self.assertEqual(first.status_code, 200)
        current = first.get_json()["session"]

        stale = {**latest, "pendingUserMessages": [], "expectedRevision": saved["revision"]}
        conflict = self.client.put(f"/api/debate/sessions/{session_id}", json=stale)
        self.assertEqual(conflict.status_code, 409)
        invalid = {**latest, "pendingUserMessages": [queued, queued], "expectedRevision": current["revision"]}
        rejected = self.client.put(f"/api/debate/sessions/{session_id}", json=invalid)
        self.assertEqual(rejected.status_code, 400)

        refreshed = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        self.assertTrue(refreshed["paused"])
        self.assertEqual(refreshed["pendingUserMessages"], [queued])
        self.assertEqual(refreshed["revision"], current["revision"])

    def test_user_time_constraint_context_keeps_subject_and_scope_explicit(self):
        payload = chat_session_payload()
        payload["question"] = "How should I balance work and other commitments?"
        user_statement = "I can spend at most ten hours each week; my two friends' availability is unknown."
        payload["messages"] = [{"kind": "user", "body": user_statement}]
        created = self.client.post("/api/debate/sessions", json=payload)
        session_id = created.get_json()["session"]["id"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            return_value="The role should consider the user's own available time.",
        ) as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply", json={"participantId": "role-1"}
            )
        self.assertEqual(response.status_code, 200)
        model_system_prompt = model_chat.call_args_list[0].args[0][0]["content"]
        model_context = reply_context_from_request(model_chat.call_args_list[0].args[0])
        self.assertEqual(model_context["latestUserMessage"], user_statement)
        self.assertEqual(model_context["userAuthoredMessages"], [
            {"speakerId": "user", "content": payload["question"]},
            {"speakerId": "user", "content": user_statement},
        ])
        self.assertEqual(model_context["simulatedAdviserTurns"], [])
        self.assertIn("Preserve each user's original words, source, order, and every stated number, unit, time period, scope, and uncertainty", model_system_prompt)
        self.assertIn("If information or a condition is missing or disputed, say what remains unknown", model_system_prompt)
        self.assertIn("not part of the user's team", model_system_prompt)
        self.assertNotIn("userAvailabilityEstimate", model_context["modelRequestText"])

    def test_chat_role_reply_uses_saved_role_and_ignores_explicit_mock_messages(self):
        payload = chat_session_payload()
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        saved = created.get_json()["session"]
        session_id = saved["id"]
        payload["participants"][1]["prompt"] = "Use the latest saved challenge prompt."
        payload["expectedRevision"] = saved["revision"]
        updated = self.client.put(f"/api/debate/sessions/{session_id}", json=payload)
        self.assertEqual(updated.status_code, 200)

        connection = sqlite3.connect(self.app.config["DB_PATH"])
        try:
            row = connection.execute(
                "SELECT messages_json FROM debate_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            messages = json.loads(row[0])
            messages.append(
                {
                    "kind": "participant",
                    "roleId": "role-2",
                    "letter": "B",
                    "body": "Marked mock must not enter the prompt.",
                    "isMock": True,
                }
            )
            connection.execute(
                "UPDATE debate_sessions SET messages_json = ? WHERE id = ?",
                (json.dumps(messages), session_id),
            )
            connection.commit()
        finally:
            connection.close()

        before = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            return_value="Test the attendance plan before widening access.",
        ) as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-2"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {k: v for k, v in response.get_json().items() if k not in {'message', 'revision'}},
            {
                "speakerId": "role-2",
                "selectionMode": "explicit",
                "fallbackReason": "none",
                "content": "Test the attendance plan before widening access.",
                "roundComplete": False,
                "remaining": 9999,
                "waitingForUser": False,
                "replyMode": "plain_text_v1",
            },
        )
        model_chat.assert_called_once()
        messages_sent = model_chat.call_args_list[0].args[0]
        self.assertEqual(model_chat.call_args_list[0].kwargs["max_tokens"], 320)
        context = reply_context_from_request(messages_sent)
        self.assertEqual(context["question"], "Should we open the workshop to beginners?")
        self.assertEqual(
            context["assignedAdvisor"],
            {
                "advisorId": "role-2",
                "displayName": "Jordan",
                "identity": "",
                "roleType": "Challenger",
                "savedPrompt": "Use the latest saved challenge prompt.",
            },
        )
        self.assertEqual(len(context["conversation"]), 3)
        self.assertEqual(context["latestUserMessage"], "What would the pilot need to measure?")
        self.assertNotIn("Marked mock must not enter the prompt.", json.dumps(messages_sent, ensure_ascii=False))

        after = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual(after["messages"][:-1], before["messages"])
        self.assertEqual(after["messages"][-1], response.get_json()['message'])
        self.assertEqual(after["participants"][1]["prompt"], "Use the latest saved challenge prompt.")

    def test_saved_multi_turn_user_facts_reach_json_and_sse_sdk_requests(self):
        user_statement = "每周四小时，朋友未知，六周是方案不是承诺"
        updated_user_statement = "更新：我现在每周八小时；朋友时间仍未知，六周仍是方案，不是承诺。"
        adviser_claims = [
            "全队总共只有四小时，完整功能肯定做不出来。",
            "微信群无法沉淀数据，不能用于验证。",
            "可以先用小范围试点，观察成员是否愿意留下反馈。",
        ]
        payload = chat_session_payload()
        payload["question"] = f"我想用微信群试验一个点子；{user_statement}"
        payload["messages"] = [{"kind": "user", "body": user_statement}]
        payload["participants"].append(
            {
                "id": "role-3",
                "avatar": "🧭",
                "name": "Ruo",
                "prompt": "Suggest a small evidence-based next step.",
                "roleType": "Builder",
            }
        )
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201, created.get_json())
        saved = created.get_json()["session"]
        session_id = saved["id"]

        def save_messages(current, messages):
            snapshot = {
                key: current[key]
                for key in (
                    "question", "participants", "pendingUserMessages", "draft",
                    "debateStarted", "paused",
                )
            }
            snapshot["messages"] = messages
            snapshot["expectedRevision"] = current["revision"]
            response = self.client.put(f"/api/debate/sessions/{session_id}", json=snapshot)
            self.assertEqual(response.status_code, 200, response.get_json())
            return response.get_json()["session"]

        for role_id, claim in zip(("role-1", "role-2", "role-3"), adviser_claims):
            saved = save_messages(
                saved,
                [
                    *saved["messages"],
                    {"kind": "participant", "roleId": role_id, "letter": "A", "body": claim},
                ],
            )
        self.assertEqual(len(saved["messages"]), 4)

        def sdk_response(content, *, streaming):
            if streaming:
                return iter(
                    [
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content=content),
                                    finish_reason=None,
                                )
                            ]
                        ),
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content=None),
                                    finish_reason="stop",
                                )
                            ]
                        ),
                    ]
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content),
                        finish_reason="stop",
                    )
                ]
            )

        sdk_create = Mock(
            side_effect=[
                sdk_response("先挑一个具体反馈指标做小范围测试。", streaming=False),
                sdk_response("先确认群成员是否能留下可复查的反馈记录。", streaming=True),
            ]
        )
        sdk_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=sdk_create))
        )
        with patch.object(llm, "_get_client", return_value=sdk_client), patch.object(
            llm, "model_name", return_value="test-model"
        ):
            json_response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply", json={"participantId": "role-1"}
            )
            self.assertEqual(json_response.status_code, 200, json_response.get_json())

            saved = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            saved = save_messages(
                saved,
                [*saved["messages"], {"kind": "user", "body": updated_user_statement}],
            )
            sse_response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-2"},
                headers={"Accept": "text/event-stream"},
            )
            sse_body = sse_response.get_data(as_text=True)

        self.assertEqual(sse_response.status_code, 200)
        events = [
            json.loads(line[len("data: "):])
            for line in sse_body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(events[0]["type"], "start")
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["replyMode"], "plain_text_v1")
        self.assertEqual(len(sdk_create.call_args_list), 2)

        json_call, sse_call = [call.kwargs for call in sdk_create.call_args_list]
        self.assertNotIn("stream", json_call)
        self.assertIs(sse_call["stream"], True)
        json_messages = json_call["messages"]
        sse_messages = sse_call["messages"]
        self.assertEqual(json_messages[0]["role"], "system")
        rules_marker = "\n\nInternal source and speaker directory (JSON; context only):\n"
        self.assertEqual(json_messages[0]["content"].split(rules_marker, 1)[0], sse_messages[0]["content"].split(rules_marker, 1)[0])
        self.assertIn("If information or a condition is missing or disputed, say what remains unknown", json_messages[0]["content"])
        self.assertIn("Preserve each user's original words, source, order, and every stated number", json_messages[0]["content"])

        def request_context(messages):
            return reply_context_from_request(messages)

        json_context = request_context(json_messages)
        self.assertEqual(json_context["question"], payload["question"])
        self.assertEqual(json_context["userAuthoredMessages"], [
            {"speakerId": "user", "content": payload["question"]},
            {"speakerId": "user", "content": user_statement},
        ])
        self.assertEqual(json_context["currentUserGoal"], user_statement)
        self.assertNotIn("userAvailabilityEstimate", json_context["modelRequestText"])
        self.assertNotIn("userTeamMembershipRecords", json_context["modelRequestText"])
        self.assertEqual(
            [turn["content"] for turn in json_context["simulatedAdviserTurns"]], adviser_claims
        )
        self.assertEqual(
            [turn["speakerId"] for turn in json_context["simulatedAdviserTurns"]],
            ["role-1", "role-2", "role-3"],
        )
        user_fact_fields = json.dumps(
            {
                key: json_context[key]
                for key in (
                    "userAuthoredMessages", "latestUserMessage", "currentUserGoal",
                    "identityDirectory",
                )
            },
            ensure_ascii=False,
        )
        for claim in adviser_claims:
            self.assertNotIn(claim, user_fact_fields)

        sse_context = request_context(sse_messages)
        self.assertEqual(
            sse_context["userAuthoredMessages"],
            [
                {"speakerId": "user", "content": payload["question"]},
                {"speakerId": "user", "content": user_statement},
                {"speakerId": "user", "content": updated_user_statement},
            ],
        )
        self.assertEqual(sse_context["latestUserMessage"], updated_user_statement)
        self.assertEqual(sse_context["currentUserGoal"], updated_user_statement)
        self.assertEqual(
            [turn["content"] for turn in sse_context["simulatedAdviserTurns"]], adviser_claims + [json_response.json['content']]
        )
        sse_user_fact_fields = json.dumps(
            {
                key: sse_context[key]
                for key in (
                    "userAuthoredMessages", "latestUserMessage", "currentUserGoal",
                    "identityDirectory",
                )
            },
            ensure_ascii=False,
        )
        for claim in adviser_claims:
            self.assertNotIn(claim, sse_user_fact_fields)

    def test_adviser_proposal_is_not_promoted_to_user_confirmation_in_json_and_sse(self):
        question = "朋友的可用时间未知，我怎么安排一个小规模试验？"
        original_user_message = "朋友的可用时间未知；还没有确认分工。"
        no_agreement_message = "朋友还没答应。"
        agreed_not_started_message = "双方已同意分工，但还没开始。"
        proposal = "我建议朋友负责初期拉人维护，你先观察。"
        false_confirmation = "分工明确了，这周重点就是朋友拉人、你观察。"
        payload = chat_session_payload()
        payload["question"] = question
        payload["messages"] = [
            {"kind": "user", "id": "user-original", "body": original_user_message},
        ]
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201, created.get_json())
        saved = created.get_json()["session"]
        session_id = saved["id"]

        def save_messages(current, additions):
            snapshot = {
                key: current[key]
                for key in (
                    "question", "participants", "messages", "pendingUserMessages", "draft",
                    "debateStarted", "paused",
                )
            }
            snapshot["messages"] = [*snapshot["messages"], *additions]
            snapshot["expectedRevision"] = current["revision"]
            response = self.client.put(f"/api/debate/sessions/{session_id}", json=snapshot)
            self.assertEqual(response.status_code, 200, response.get_json())
            return response.get_json()["session"]

        saved = save_messages(
            saved,
            [
                {"kind": "participant", "roleId": "role-1", "letter": "A", "body": proposal},
                {"kind": "participant", "roleId": "role-2", "letter": "B", "body": false_confirmation},
            ],
        )

        def sdk_response(content, *, streaming):
            if streaming:
                return iter(
                    [
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content=content),
                                    finish_reason=None,
                                )
                            ]
                        ),
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content=None),
                                    finish_reason="stop",
                                )
                            ]
                        ),
                    ]
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content),
                        finish_reason="stop",
                    )
                ]
            )

        sdk_create = Mock(
            side_effect=[
                sdk_response("先核实朋友是否愿意参与，再讨论具体分工。", streaming=False),
                sdk_response("先等朋友明确回复，再把可能的分工写成计划。", streaming=True),
                sdk_response("既然双方同意但还没开始，可以先约定首次招募时间。", streaming=False),
            ]
        )
        sdk_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=sdk_create))
        )
        with patch.object(llm, "_get_client", return_value=sdk_client), patch.object(
            llm, "model_name", return_value="test-model"
        ):
            first = self.client.post(
                f"/api/debate/sessions/{session_id}/reply", json={"participantId": "role-1"}
            )
            self.assertEqual(first.status_code, 200, first.get_json())
            saved = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            saved = save_messages(
                saved,
                [{"kind": "participant", "roleId": "role-1", "letter": "A", "body": first.get_json()["content"]}],
            )
            saved = save_messages(
                saved,
                [{"kind": "user", "id": "user-no-agreement", "body": no_agreement_message}],
            )
            second = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-2"},
                headers={"Accept": "text/event-stream"},
            )
            self.assertEqual(second.status_code, 200)
            second_events = [
                json.loads(line[len("data: "):])
                for line in second.get_data(as_text=True).splitlines()
                if line.startswith("data: ")
            ]
            self.assertEqual(second_events[-1]["type"], "done")
            saved = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            saved = save_messages(
                saved,
                [{"kind": "participant", "roleId": "role-2", "letter": "B", "body": "先等朋友明确回复，再把可能的分工写成计划。"}],
            )
            saved = save_messages(
                saved,
                [{"kind": "user", "id": "user-agreed-not-started", "body": agreed_not_started_message}],
            )
            third = self.client.post(
                f"/api/debate/sessions/{session_id}/reply", json={"participantId": "role-1"}
            )
            self.assertEqual(third.status_code, 200, third.get_json())

        self.assertEqual(len(sdk_create.call_args_list), 3)
        calls = [call.kwargs for call in sdk_create.call_args_list]
        first_sdk, second_sdk, third_sdk = calls
        self.assertNotIn("stream", first_sdk)
        self.assertIs(second_sdk["stream"], True)
        self.assertNotIn("stream", third_sdk)

        def context_from(call):
            request_messages = call["messages"]
            return request_messages[0]["content"], reply_context_from_request(request_messages)

        first_prompt, first_context = context_from(first_sdk)
        second_prompt, second_context = context_from(second_sdk)
        third_prompt, third_context = context_from(third_sdk)
        rules_marker = "\n\nInternal source and speaker directory (JSON; context only):\n"
        self.assertEqual(first_prompt.split(rules_marker, 1)[0], second_prompt.split(rules_marker, 1)[0])
        self.assertEqual(first_prompt.split(rules_marker, 1)[0], third_prompt.split(rules_marker, 1)[0])
        self.assertIn("original question and user-authored messages are the only sources for user/project facts", first_prompt)
        self.assertIn("Every assistant/participant message—including your prior replies and coordinator guidance—is that speaker's unconfirmed proposal or claim", first_prompt)
        self.assertIn("Preserve each user's original words, source, order, and every stated number", first_prompt)

        question_source = {
            "sourceId": "opening-question:0",
            "sequence": 0,
            "sourceType": "user_question",
            "speakerId": "user",
            "content": question,
        }
        for context in (first_context, second_context, third_context):
            self.assertEqual(context["question"], question)
            self.assertEqual(context["questionSource"], question_source)
            self.assertNotIn("userAvailabilityEstimate", context["modelRequestText"])

        first_turns = first_context["conversation"]
        self.assertEqual(
            [(turn["sourceId"], turn["sequence"], turn["speakerType"]) for turn in first_turns],
            [
                ("opening-question:0", 0, "user"),
                ("saved-message:user-original", 1, "user"),
                ("legacy-message-0002", 2, "simulated_adviser"),
                ("legacy-message-0003", 3, "simulated_adviser"),
            ],
        )
        self.assertEqual(first_turns[0]["content"], question)
        self.assertEqual(first_turns[1]["content"], original_user_message)
        self.assertEqual(first_turns[2]["speakerId"], "role-1")
        self.assertEqual(first_turns[2]["content"], proposal)
        self.assertEqual(first_turns[2]["notUserConfirmation"], True)
        self.assertEqual(first_turns[2]["executionStatus"], "not_established_by_this_adviser_message")
        self.assertEqual(first_turns[3]["speakerId"], "role-2")
        self.assertEqual(first_turns[3]["content"], false_confirmation)
        self.assertEqual(first_turns[3]["notUserConfirmation"], True)
        self.assertEqual(first_turns[3]["executionStatus"], "not_established_by_this_adviser_message")
        self.assertIsNone(first_context["latestUserMessage"])
        self.assertEqual(first_context["currentUserGoal"], original_user_message)
        self.assertEqual(first_context["userAuthoredMessages"], [
            {"speakerId": "user", "content": question},
            {"speakerId": "user", "content": original_user_message},
        ])
        self.assertEqual(first_context["simulatedAdviserTurns"][1]["content"], false_confirmation)
        self.assertNotIn(false_confirmation, json.dumps(first_context["userAuthoredMessages"], ensure_ascii=False))
        self.assertEqual(
            [(turn["sourceId"], turn["sequence"]) for turn in second_context["conversation"][:3]],
            [(turn["sourceId"], turn["sequence"]) for turn in first_turns[:3]],
        )

        self.assertEqual(second_context["latestUserMessage"], no_agreement_message)
        self.assertEqual(second_context["currentUserGoal"], no_agreement_message)
        self.assertEqual(second_context["conversation"][-1]["sourceId"], "saved-message:user-no-agreement")
        self.assertEqual(second_context["conversation"][-1]["sequence"], 6)
        self.assertEqual(second_context["conversation"][-1]["content"], no_agreement_message)
        self.assertEqual(
            second_context["userAuthoredMessages"][-1],
            {"speakerId": "user", "content": no_agreement_message},
        )
        self.assertEqual(second_context["simulatedAdviserTurns"][1]["content"], false_confirmation)
        self.assertNotIn(false_confirmation, json.dumps(second_context["userAuthoredMessages"], ensure_ascii=False))

        self.assertEqual(third_context["latestUserMessage"], agreed_not_started_message)
        self.assertEqual(third_context["currentUserGoal"], agreed_not_started_message)
        self.assertEqual(third_context["conversation"][-1]["sourceId"], "saved-message:user-agreed-not-started")
        self.assertEqual(third_context["conversation"][-1]["sequence"], 9)
        self.assertEqual(third_context["conversation"][-1]["content"], agreed_not_started_message)
        self.assertNotIn("executionStatus", third_context["conversation"][-1])
        self.assertNotIn("userConfirmedAssignments", third_context)
        self.assertEqual(third_context["simulatedAdviserTurns"][1]["content"], false_confirmation)
        self.assertNotIn(false_confirmation, json.dumps(third_context["userAuthoredMessages"], ensure_ascii=False))

    def test_actual_sdk_turns_keep_three_adviser_replies_and_latest_user_clarification_separate(self):
        question = "我负责开发，每周4小时，两个朋友时间未知，6周只是朋友的方案。"
        clarification = "朋友还没答应分工，两人的可用时间仍然未知。每周四小时只指我本人。"
        history = [
            {"kind": "user", "id": "opening", "body": question},
            {"kind": "participant", "roleId": "role-1", "letter": "A", "body": "六周排期对每人每周仅四小时来说不可行。"},
            {"kind": "participant", "roleId": "role-2", "letter": "B", "body": "先确认朋友是否愿意参与，再拆解任务。"},
            {"kind": "participant", "roleId": "role-3", "letter": "C", "body": "可以把六周先保留为待讨论的方案。"},
            {"kind": "user", "id": "latest-clarification", "body": clarification},
        ]
        payload = chat_session_payload()
        payload["question"] = question
        payload["participants"].append(
            {"id": "role-3", "avatar": "🧭", "name": "Ruo", "prompt": "Offer a practical next step.", "roleType": "Builder"}
        )
        payload["messages"] = history
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201, created.get_json())
        session_id = created.get_json()["session"]["id"]

        def completion(content, *, stream=False):
            if stream:
                events = [
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=None)]),
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")]),
                ]
                return iter(events)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")])

        sdk_create = Mock(side_effect=[
            completion("把开发范围列成可估算的几个小项，再评估六周方案。"),
            completion("朋友是否愿意参与仍需直接确认；这不代表已经同意。", stream=True),
        ])
        sdk_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=sdk_create)))
        with patch.object(llm, "_get_client", return_value=sdk_client), patch.object(llm, "model_name", return_value="test-model"):
            json_response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply", json={"participantId": "role-1"}
            )
            self.assertEqual(json_response.status_code, 200, json_response.get_json())
            sse_session_id = self.client.post("/api/debate/sessions", json=payload).json["session"]["id"]
            sse_response = self.client.post(
                f"/api/debate/sessions/{sse_session_id}/reply",
                json={"participantId": "role-2"},
                headers={"Accept": "text/event-stream"},
            )
            sse_body = sse_response.get_data(as_text=True)
        sse_events = [
            json.loads(line[len("data: "):])
            for line in sse_body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(sse_events[-1]["type"], "done")
        self.assertEqual(len(sdk_create.call_args_list), 2)
        json_request, sse_request = [call.kwargs for call in sdk_create.call_args_list]
        self.assertNotIn("stream", json_request)
        self.assertIs(sse_request["stream"], True)

        expected_current_speakers = ("role-1", "role-2")
        for request, current_speaker_id in zip(
            (json_request["messages"], sse_request["messages"]), expected_current_speakers
        ):
            self.assertEqual([message["role"] for message in request], ["system", "user", "assistant", "assistant", "assistant", "user"])
            context = reply_context_from_request(request)
            self.assertEqual([turn["content"] for turn in context["conversation"]], [
                question,
                history[1]["body"],
                history[2]["body"],
                history[3]["body"],
                clarification,
            ])
            self.assertEqual(context["conversation"][-1]["speakerType"], "user")
            self.assertEqual(context["conversation"][-1]["sourceId"], "saved-message:latest-clarification")
            self.assertTrue(all(turn["speakerType"] == "simulated_adviser" for turn in context["conversation"][1:4]))
            user_turn_bodies = [turn["content"] for turn in context["conversation"] if turn["speakerType"] == "user"]
            adviser_turn_bodies = [turn["content"] for turn in context["conversation"] if turn["speakerType"] == "simulated_adviser"]
            self.assertEqual(user_turn_bodies, [question, clarification])
            self.assertEqual(adviser_turn_bodies, [item["body"] for item in history[1:4]])
            self.assertEqual(context["assignedAdvisor"]["advisorId"], current_speaker_id)
            selected_role = next(role for role in payload["participants"] if role["id"] == current_speaker_id)
            self.assertEqual(context["assignedAdvisor"]["displayName"], selected_role["name"])
            assistant_messages = [message for message in request[1:] if message["role"] == "assistant"]
            self.assertEqual([message["content"] for message in assistant_messages], [item["body"] for item in history[1:4]])
            self.assertTrue(all("sourceId" not in message["content"] for message in assistant_messages))
            self.assertTrue(all("Simulated adviser statement; not user confirmation" not in message["content"] for message in assistant_messages))
            self.assertEqual(
                [(turn["sourceId"], turn["sequence"], turn["sourceType"]) for turn in context["conversation"]],
                [
                    ("saved-message:opening", 1, "user_message"),
                    ("legacy-message-0002", 2, "simulated_adviser_statement"),
                    ("legacy-message-0003", 3, "simulated_adviser_statement"),
                    ("legacy-message-0004", 4, "simulated_adviser_statement"),
                    ("saved-message:latest-clarification", 5, "user_message"),
                ],
            )
            self.assertEqual(
                [(turn["sourceId"], turn["sequence"], turn["sourceType"]) for turn in context["sourceDirectory"]],
                [
                    ("saved-message:opening", 1, "user_question_and_message"),
                    ("legacy-message-0002", 2, "simulated_adviser_statement"),
                    ("legacy-message-0003", 3, "simulated_adviser_statement"),
                    ("legacy-message-0004", 4, "simulated_adviser_statement"),
                    ("saved-message:latest-clarification", 5, "user_message"),
                ],
            )
            self.assertTrue(all(turn.get("notUserConfirmation") is True for turn in context["sourceDirectory"][1:4]))
            self.assertNotIn("Saved discussion context (JSON data)", json.dumps(request, ensure_ascii=False))
            self.assertNotIn("currentUserGoal", json.dumps(request, ensure_ascii=False))
            self.assertNotIn("userAvailabilityEstimate", json.dumps(request, ensure_ascii=False))

    def test_exact_duplicate_text_returns_normally_once_for_json_and_sse_and_saves_once(self):
        repeated = "朋友确认时间后再细化分工。"
        prior = "朋友确认时间后再细化分工！"
        clarification = "朋友还没答应，时间未知。"

        def make_session():
            payload = chat_session_payload()
            payload["messages"] = [
                {"kind": "user", "id": "question", "body": payload["question"]},
                {"kind": "user", "id": "clarification", "body": clarification},
                {"kind": "participant", "roleId": "role-1", "letter": "A", "body": repeated},
                {"kind": "participant", "roleId": "role-2", "letter": "B", "body": prior},
            ]
            created = self.client.post("/api/debate/sessions", json=payload)
            self.assertEqual(created.status_code, 201)
            return created.get_json()["session"]["id"]

        json_session_id = make_session()
        before_json = self.client.get(f"/api/debate/sessions/{json_session_id}").get_json()["session"]
        with patch("server.gizmoapp_server.debate_chat.llm.chat", return_value=repeated) as model_chat, patch(
            "server.gizmoapp_server.debate_chat._find_duplicate_reply", side_effect=AssertionError("duplicate check called")
        ), patch(
            "server.gizmoapp_server.debate_chat._correct_plain_reply", side_effect=AssertionError("rewrite called")
        ), patch(
            "server.gizmoapp_server.debate_chat._fact_review_plain_reply", side_effect=AssertionError("review called")
        ), patch(
            "server.gizmoapp_server.debate_chat._reject_duplicate_reply", side_effect=AssertionError("duplicate rejection called")
        ):
            response = self.client.post(
                f"/api/debate/sessions/{json_session_id}/reply", json={"participantId": "role-1"}
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["content"], repeated)
        self.assertEqual(response.get_json()["speakerId"], "role-1")
        model_chat.assert_called_once()

        sse_session_id = make_session()
        before_sse = self.client.get(f"/api/debate/sessions/{sse_session_id}").get_json()["session"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.stream_chat",
            return_value=iter([repeated[:12], repeated[12:]]),
        ) as model_stream, patch(
            "server.gizmoapp_server.debate_chat.llm.chat", side_effect=AssertionError("extra call")
        ) as model_chat, patch(
            "server.gizmoapp_server.debate_chat._find_duplicate_reply", side_effect=AssertionError("duplicate check called")
        ), patch(
            "server.gizmoapp_server.debate_chat._correct_plain_reply", side_effect=AssertionError("rewrite called")
        ), patch(
            "server.gizmoapp_server.debate_chat._fact_review_plain_reply", side_effect=AssertionError("review called")
        ), patch(
            "server.gizmoapp_server.debate_chat._reject_duplicate_reply", side_effect=AssertionError("duplicate rejection called")
        ):
            sse_response = self.client.post(
                f"/api/debate/sessions/{sse_session_id}/reply",
                json={"participantId": "role-1"},
                headers={"Accept": "text/event-stream"},
            )
            sse_body = sse_response.get_data(as_text=True)
        sse_events = [
            json.loads(line[len("data: "):])
            for line in sse_body.splitlines()
                if line.startswith("data: ")
        ]
        self.assertEqual(sse_events[0]["type"], "start")
        self.assertEqual(sse_events[-1]["type"], "done")
        self.assertEqual("".join(event["text"] for event in sse_events if event["type"] == "delta"), repeated)
        model_stream.assert_called_once()
        model_chat.assert_not_called()

        before_revision = before_json["revision"]
        before_json["messages"].append(
            {"kind": "participant", "roleId": "role-1", "letter": "A", "body": repeated}
        )
        before_json["expectedRevision"] = before_revision
        saved = self.client.put(f"/api/debate/sessions/{json_session_id}", json=before_json)
        self.assertEqual(saved.status_code, 200, saved.get_json())
        saved_session = saved.get_json()["session"]
        self.assertEqual(saved_session["revision"], before_revision + 2)
        self.assertEqual(sum(message.get("body") == repeated for message in saved_session["messages"]), 2)
        self.assertEqual(before_sse["messages"], self.client.get(f"/api/debate/sessions/{sse_session_id}").get_json()["session"]["messages"][:-1])

    def test_conservative_near_duplicate_match_accepts_new_conditions_and_changed_conclusions(self):
        original = "这周拉个群，找5个同学试发和撮合一笔物品交易，看能否跑通流程并收集反馈。"
        near_rephrases = [
            "可以，这周拉个群，找5个同学试发和撮合一笔交易，跑通流程并收集反馈。",
            "这周拉个群，找5个同学试发和撮合一笔交易，看能否跑通流程并收集反馈。",
        ]
        session = {"messages": [{"kind": "participant", "roleId": "role-1", "body": original}]}
        for candidate in near_rephrases:
            with self.subTest(candidate=candidate):
                match = debate_chat._find_duplicate_reply(session, candidate)
                self.assertIsNotNone(match)
                self.assertEqual(match[0]["roleId"], "role-1")
                self.assertFalse(match[1])

        meaningfully_new = (
            "这周拉个群，找5个同学试发和撮合一笔交易；先征得参与者同意并明确物品信息如何保护，"
            "再用能否完成一笔交易作为检验标准。"
        )
        changed_conclusion = "这周先别建群，我认为撮合交易不能验证真实需求，应该先访谈潜在用户。"
        shared_topic_only = "这周找5个同学。"
        for candidate in (meaningfully_new, changed_conclusion, shared_topic_only):
            with self.subTest(candidate=candidate):
                self.assertIsNone(debate_chat._find_duplicate_reply(session, candidate))

    def test_near_duplicate_text_is_returned_once_for_json_and_sse(self):
        original = "这周拉个群，找5个同学试发和撮合一笔物品交易，看能否跑通流程并收集反馈。"
        near_duplicate = "这周拉个群，找5个同学试发和撮合一笔交易，看能否跑通流程并收集反馈。"

        def make_session():
            payload = chat_session_payload()
            payload["question"] = "我每周只有四小时，朋友能投入的时间未知，微信群只是候选。"
            payload["messages"] = [
                {"kind": "user", "id": "question", "body": payload["question"]},
                {"kind": "participant", "id": "seed-reply", "roleId": "role-1", "letter": "A", "body": original},
            ]
            created = self.client.post("/api/debate/sessions", json=payload)
            self.assertEqual(created.status_code, 201)
            return created.get_json()["session"]["id"]

        json_session_id = make_session()
        with patch("server.gizmoapp_server.debate_chat.llm.chat", return_value=near_duplicate) as json_model, patch(
            "server.gizmoapp_server.debate_chat._find_duplicate_reply", side_effect=AssertionError("duplicate check called")
        ), patch(
            "server.gizmoapp_server.debate_chat._correct_plain_reply", side_effect=AssertionError("rewrite called")
        ), patch(
            "server.gizmoapp_server.debate_chat._fact_review_plain_reply", side_effect=AssertionError("review called")
        ), patch(
            "server.gizmoapp_server.debate_chat._reject_duplicate_reply", side_effect=AssertionError("duplicate rejection called")
        ):
            json_response = self.client.post(
                f"/api/debate/sessions/{json_session_id}/reply", json={"participantId": "role-2"}
            )
        self.assertEqual(json_response.status_code, 200, json_response.get_json())
        self.assertEqual(json_response.get_json()["speakerId"], "role-2")
        self.assertEqual(json_response.get_json()["content"], near_duplicate)
        json_model.assert_called_once()

        sse_session_id = make_session()
        with patch(
            "server.gizmoapp_server.debate_chat.llm.stream_chat", return_value=iter([near_duplicate[:25], near_duplicate[25:]])
        ) as stream_model, patch(
            "server.gizmoapp_server.debate_chat.llm.chat", side_effect=AssertionError("extra call")
        ) as extra_model, patch(
            "server.gizmoapp_server.debate_chat._find_duplicate_reply", side_effect=AssertionError("duplicate check called")
        ), patch(
            "server.gizmoapp_server.debate_chat._correct_plain_reply", side_effect=AssertionError("rewrite called")
        ), patch(
            "server.gizmoapp_server.debate_chat._fact_review_plain_reply", side_effect=AssertionError("review called")
        ), patch(
            "server.gizmoapp_server.debate_chat._reject_duplicate_reply", side_effect=AssertionError("duplicate rejection called")
        ):
            sse_response = self.client.post(
                f"/api/debate/sessions/{sse_session_id}/reply",
                json={"participantId": "role-2"},
                headers={"Accept": "text/event-stream"},
            )
            sse_body = sse_response.get_data(as_text=True)
        events = [
            json.loads(line[len("data: "):])
            for line in sse_body.splitlines()
                if line.startswith("data: ")
        ]
        self.assertEqual("".join(event["text"] for event in events if event["type"] == "delta"), near_duplicate)
        self.assertEqual(events[0]["speakerId"], "role-2")
        self.assertEqual(events[-1]["type"], "done")
        stream_model.assert_called_once()
        extra_model.assert_not_called()

    def test_plain_sse_transport_and_truncation_failures_do_not_retry_or_publish(self):
        failures = (
            llm.CourseLLMError("The course model is temporarily unavailable."),
            llm.CourseLLMTruncatedError("The course model reached its reply token limit."),
        )
        for failure in failures:
            with self.subTest(failure=str(failure)):
                created = self.client.post("/api/debate/sessions", json=chat_session_payload())
                session_id = created.get_json()["session"]["id"]
                with patch(
                    "server.gizmoapp_server.debate_chat.llm.stream_chat", side_effect=failure
                ) as stream_model, patch("server.gizmoapp_server.debate_chat.llm.chat") as extra_model:
                    response = self.client.post(
                        f"/api/debate/sessions/{session_id}/reply",
                        json={"participantId": "role-1"},
                        headers={"Accept": "text/event-stream"},
                    )
                    body = response.get_data(as_text=True)
                self.assertIn('"type": "error"', body)
                stream_model.assert_called_once()
                extra_model.assert_not_called()

    def test_plain_reply_blank_and_generation_failures_do_not_retry_or_change_history(self):
        failures = (
            "  \n",
            llm.CourseLLMError("The course model is temporarily unavailable."),
            llm.CourseLLMTruncatedError("The course model reached its reply token limit."),
        )

        def make_session():
            payload = chat_session_payload()
            payload["question"] = "我每周只有四小时；朋友的时间未知，微信群只是候选。"
            payload["messages"] = [{"kind": "user", "body": payload["question"]}]
            created = self.client.post("/api/debate/sessions", json=payload)
            return created.get_json()["session"]["id"]

        for failure in failures:
            with self.subTest(transport="json", failure=type(failure).__name__):
                session_id = make_session()
                before = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
                with patch("server.gizmoapp_server.debate_chat.llm.chat", side_effect=[failure]) as model_chat:
                    response = self.client.post(
                        f"/api/debate/sessions/{session_id}/reply", json={"participantId": "role-1"}
                    )
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("content", response.get_json())
                model_chat.assert_called_once()
                after = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
                self.assertEqual(after["messages"], before["messages"])
                self.assertEqual(after["revision"], before["revision"])

            with self.subTest(transport="sse", failure=type(failure).__name__):
                session_id = make_session()
                before = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
                stream_failure = failure if isinstance(failure, Exception) else iter([failure])
                with patch(
                    "server.gizmoapp_server.debate_chat.llm.stream_chat",
                    side_effect=stream_failure if isinstance(stream_failure, Exception) else None,
                    return_value=stream_failure if not isinstance(stream_failure, Exception) else None,
                ) as stream_model, patch(
                    "server.gizmoapp_server.debate_chat.llm.chat", side_effect=AssertionError("extra call")
                ) as extra_model:
                    response = self.client.post(
                        f"/api/debate/sessions/{session_id}/reply",
                        json={"participantId": "role-1"},
                        headers={"Accept": "text/event-stream"},
                    )
                    body = response.get_data(as_text=True)
                events = [
                    json.loads(line[len("data: "):])
                    for line in body.splitlines()
                    if line.startswith("data: ")
                ]
                self.assertEqual([event["type"] for event in events], ["start", "error"])
                stream_model.assert_called_once()
                extra_model.assert_not_called()
                after = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
                self.assertEqual(after["messages"], before["messages"])
                self.assertEqual(after["revision"], before["revision"])

    def test_distinct_json_and_sse_replies_are_returned_and_saved_once(self):
        payload = chat_session_payload()
        created = self.client.post("/api/debate/sessions", json=payload)
        session_id = created.get_json()["session"]["id"]
        json_content = "先把测试范围缩到一个可观察指标。"
        sse_content = "再核对参与者是否愿意留下反馈。"

        def save_once(content, role_id, letter):
            session = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            self.assertEqual(session["messages"][-1]["body"], content)
            self.assertEqual(session["messages"][-1]["roleId"], role_id)
            return session

        with patch("server.gizmoapp_server.debate_chat.llm.chat", return_value=json_content) as model_chat:
            json_response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply", json={"participantId": "role-1"}
            )
        self.assertEqual(json_response.status_code, 200)
        self.assertEqual(json_response.get_json()["content"], json_content)
        model_chat.assert_called_once()
        save_once(json_response.get_json()["content"], "role-1", "A")

        with patch("server.gizmoapp_server.debate_chat.llm.stream_chat", return_value=iter([sse_content])) as model_stream, patch(
            "server.gizmoapp_server.debate_chat.llm.chat", side_effect=AssertionError("extra call")
        ) as extra_model:
            sse_response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-2"},
                headers={"Accept": "text/event-stream"},
            )
            sse_body = sse_response.get_data(as_text=True)
        events = [
            json.loads(line[len("data: "):])
            for line in sse_body.splitlines()
            if line.startswith("data: ")
        ]
        deltas = [event["text"] for event in events if event["type"] == "delta"]
        self.assertEqual(deltas, [sse_content])
        self.assertEqual(events[-1]["type"], "done")
        model_stream.assert_called_once()
        extra_model.assert_not_called()
        saved = save_once("".join(deltas), "role-2", "B")
        bodies = [message["body"] for message in saved["messages"] if message["kind"] == "participant"]
        self.assertEqual(bodies.count(json_content), 1)
        self.assertEqual(bodies.count(sse_content), 1)

    def test_reply_context_separates_user_facts_from_participant_preferences(self):
        payload = chat_session_payload()
        payload["question"] = (
            "I can spend at most ten hours each week; my two friends' availability is unknown. How should we test this?"
        )
        payload["participants"] = [
            {**payload["participants"][0], "name": "Lin"},
            {**payload["participants"][1], "name": "Jie"},
            {
                "id": "role-3",
                "avatar": "🧩",
                "name": "Ruo",
                "prompt": "Look for a useful next step.",
                "roleType": "Builder",
            },
        ]
        payload["messages"] = [
            {"kind": "user", "body": payload["question"]},
            {"kind": "participant", "roleId": "role-1", "letter": "A", "body": "Development takes ten hours each week, and I will handle all coordination."},
        ]
        payload["participants"][1]["prompt"] = "Use the saved user-experience viewpoint, but do not claim team membership."
        created = self.client.post("/api/debate/sessions", json=payload)
        session_id = created.get_json()["session"]["id"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            return_value="We could first compare options without assigning work to your friends.",
        ) as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-2"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["speakerId"], "role-2")
        self.assertEqual(response.get_json()["content"], "We could first compare options without assigning work to your friends.")
        context = reply_context_from_request(model_chat.call_args_list[0].args[0])
        self.assertEqual(
            context["userAuthoredMessages"],
            [{"speakerId": "user", "content": payload["question"]}],
        )
        self.assertEqual(context["question"], payload["question"])
        self.assertEqual(
            {key: context["conversation"][0][key] for key in ("speakerType", "speakerId", "speaker", "content")},
            {"speakerType": "user", "speakerId": "user", "speaker": "User", "content": payload["question"]},
        )
        self.assertEqual(context["conversation"][1]["speakerId"], "role-1")
        self.assertEqual(context["conversation"][1]["speakerType"], "simulated_adviser")
        self.assertEqual(context["conversation"][1]["speaker"], "Lin")
        self.assertEqual(context["conversation"][1]["content"], payload["messages"][1]["body"])
        self.assertEqual(
            context["simulatedAdviserTurns"],
            [{"speakerId": "role-1", "speaker": "Lin", "content": payload["messages"][1]["body"]}],
        )
        self.assertNotIn(payload["messages"][1]["body"], json.dumps(context["userAuthoredMessages"]))
        self.assertEqual(context["assignedAdvisor"]["savedPrompt"], payload["participants"][1]["prompt"])
        self.assertEqual(context["assignedAdvisor"]["advisorId"], "role-2")
        self.assertEqual(context["assignedAdvisor"]["displayName"], "Jie")
        self.assertEqual(context["identityDirectory"], {
            "questionAskerId": "user",
            "simulatedAdvisers": [
                {"advisorId": "role-1", "displayName": "Lin", "identity": ""},
                {"advisorId": "role-2", "displayName": "Jie", "identity": ""},
                {"advisorId": "role-3", "displayName": "Ruo", "identity": ""},
            ],
            "userTeamMembershipRecords": [],
        })
        self.assertEqual(context["assignedAdvisor"]["identity"], "")
        self.assertNotIn("userAvailabilityEstimate", context["modelRequestText"])
        prompt = model_chat.call_args_list[0].args[0][0]["content"]
        self.assertIn("original question and user-authored messages are the only sources for user/project facts", prompt)
        self.assertIn("Every assistant/participant message—including your prior replies and coordinator guidance—is that speaker's unconfirmed proposal or claim", prompt)
        self.assertIn("Preserve each user's original words, source, order, and every stated number", prompt)
        self.assertIn("Add one small, useful contribution", prompt)
        self.assertIn("If information or a condition is missing or disputed, say what remains unknown", prompt)
        self.assertIn("Use this participant's identity and saved prompt as a viewpoint", prompt)
        self.assertIn("at most ten hours each week", json.dumps(context["userAuthoredMessages"], ensure_ascii=False))
        self.assertNotIn(payload["messages"][1]["body"], json.dumps(context["userAuthoredMessages"], ensure_ascii=False))

    def test_automatic_replies_follow_changed_goal_and_pass_it_to_each_relevant_role(self):
        payload = changed_goal_session_payload()
        changed_goal = (
            "I changed my goal: I will not attend graduate school. I want domestic finance work after my bachelor's."
        )
        old_role_opinion = "Graduate school is the only sensible path; direct domestic work is a bad idea."
        payload["messages"].insert(
            -1,
            {"kind": "participant", "roleId": "role-1", "letter": "A", "body": old_role_opinion},
        )
        payload["messages"][-1]["body"] = changed_goal
        created = self.client.post("/api/debate/sessions", json=payload)
        session_id = created.get_json()["session"]["id"]
        first_reply = "Check one domestic entry-level posting for required skills and location before choosing a next step."
        second_reply = "Compare a second posting against that checklist to see which requirement repeats."
        replies = [first_reply, second_reply]
        role_replies = iter(replies)
        coordinator_inputs = []

        def model_turn(messages, **kwargs):
            if "You coordinate one next turn" in messages[0]["content"]:
                context = coordinator_context_from_request(messages)
                coordinator_inputs.append(context)
                user_turns = [turn for turn in context["recentConversation"] if turn["speakerType"] == "user"]
                if context["recentConversation"][-1]["speakerType"] == "simulated_adviser":
                    return coordinator_decision("role-2", "Compare relevant finance-entry options.")
                if "will not attend graduate school" in user_turns[-1]["content"]:
                    return coordinator_decision("role-3", "Set aside graduate-school comparisons.")
                return coordinator_decision("role-1", "Compare graduate-school choices.")
            return next(role_replies)

        def save_reply(reply):
            session = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            self.assertEqual(session["messages"][-1], reply["message"])

        with patch("server.gizmoapp_server.debate_chat.llm.chat", side_effect=model_turn) as model_chat:
            first = self.client.post(f"/api/debate/sessions/{session_id}/reply", json={})
            self.assertEqual(first.status_code, 200)
            first_reply = first.get_json()
            self.assertEqual(first_reply["speakerId"], "role-3")
            self.assertNotEqual(first_reply["speakerId"], "role-1")
            save_reply(first_reply)

            second = self.client.post(f"/api/debate/sessions/{session_id}/reply", json={})
            self.assertEqual(second.status_code, 200)
            second_reply = second.get_json()
            self.assertEqual(second_reply["speakerId"], "role-2")
            self.assertNotEqual(second_reply["speakerId"], first_reply["speakerId"])

        self.assertEqual(model_chat.call_count, 4)
        self.assertEqual(len(coordinator_inputs), 2)
        coordinator_rules = model_chat.call_args_list[0].args[0][0]["content"]
        self.assertIn("short, actionable response task", coordinator_rules)
        self.assertIn("not a topic label, summary, quote, complete sentence answer", coordinator_rules)
        self.assertIn("keep the exchange on it", coordinator_rules)
        self.assertIn("address a concrete claim or objection", coordinator_rules)
        self.assertIn("Do not manufacture conflict, facts, prior positions, consensus or a winner", coordinator_rules)
        self.assertIn("Only user-authored entries establish user facts and goals", coordinator_rules)
        role_rules = model_chat.call_args_list[1].args[0][0]["content"]
        self.assertIn("unverified suggestion, not a user-authored turn", role_rules)
        self.assertIn("Do not independently answer the opening question again", role_rules)
        self.assertIn("respond to a specific thing somebody said", role_rules)
        self.assertIn("this participant's identity and saved prompt as a viewpoint", role_rules)
        self.assertIn("never invent a necessary gate", role_rules)
        self.assertEqual(coordinator_inputs[0]["question"], payload["question"])
        self.assertEqual(coordinator_inputs[0]["recentConversation"][-1]["speakerType"], "user")
        self.assertEqual(coordinator_inputs[1]["recentConversation"][-1]["speakerType"], "simulated_adviser")
        self.assertEqual(coordinator_inputs[0]["candidates"][1]["prompt"], payload["participants"][1]["prompt"])
        contexts = [
            reply_context_from_request(model_chat.call_args_list[index].args[0])
            for index in (1, 3)
        ]
        self.assertEqual([context["assignedAdvisor"]["advisorId"] for context in contexts], ["role-3", "role-2"])
        self.assertTrue(all(context["currentUserGoal"] == changed_goal for context in contexts))
        self.assertEqual(contexts[0]["latestUserMessage"], changed_goal)
        self.assertIsNone(contexts[1]["latestUserMessage"])
        self.assertEqual(contexts[1]["latestAdvisorMessage"]["speakerId"], "role-3")
        self.assertEqual(contexts[1]["latestMessage"]["speakerId"], "role-3")
        self.assertEqual(contexts[0]["userAuthoredMessages"][-1]["content"], changed_goal)
        self.assertEqual(contexts[0]["turnGuidance"]["focus"], "Set aside graduate-school comparisons.")
        self.assertEqual(contexts[1]["turnGuidance"]["focus"], "Compare relevant finance-entry options.")
        self.assertNotIn(
            contexts[0]["turnGuidance"]["focus"],
            json.dumps(contexts[0]["userAuthoredMessages"], ensure_ascii=False),
        )
        self.assertNotIn(old_role_opinion, json.dumps(contexts[0]["userAuthoredMessages"], ensure_ascii=False))
        self.assertEqual(contexts[0]["simulatedAdviserTurns"][1]["content"], old_role_opinion)
        saved_old_opinion = next(
            turn for turn in contexts[0]["conversation"] if turn["content"] == old_role_opinion
        )
        self.assertEqual(saved_old_opinion["speakerType"], "simulated_adviser")
        self.assertEqual(saved_old_opinion["speakerId"], "role-1")
        self.assertEqual(contexts[1]["currentUserGoal"], changed_goal)
        self.assertEqual(contexts[1]["userAuthoredMessages"][-1]["content"], changed_goal)
        self.assertEqual(contexts[1]["simulatedAdviserTurns"][1]["content"], old_role_opinion)
        self.assertEqual(contexts[1]["simulatedAdviserTurns"][-1]["speakerId"], "role-3")
        self.assertEqual(len(contexts[1]["conversation"]), 5)
        self.assertEqual(contexts[1]["conversation"][-1]["speakerId"], "role-3")
        system_prompt = model_chat.call_args_list[3].args[0][0]["content"]
        self.assertIn("An unanswered user turn takes priority", system_prompt)
        self.assertIn("Add one small, useful contribution that is distinct", system_prompt)

    def test_saved_multi_turn_proposals_reach_coordinator_and_role_focus_and_explicit_at_bypasses(self):
        payload = chat_session_payload()
        question = "How should students test a campus second-hand marketplace?"
        role_one_identity = "student organizing campus resale trades"
        role_one_prompt = "Consider handoffs, seller coordination, and trust at pickup."
        role_two_identity = "student who buys and sells second-hand goods"
        role_two_prompt = "Attend to buyer confidence, search friction, and convenient exchanges."
        payload["question"] = question
        payload["participants"][0].update(identity=role_one_identity, prompt=role_one_prompt)
        payload["participants"][1].update(identity=role_two_identity, prompt=role_two_prompt)
        payload["messages"] = [{"kind": "user", "id": "opening", "body": question}]
        payload["paused"] = False
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]

        saved = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        latest_proposal = "Our data retention policy requires keeping complete transaction records."
        role_two_previous = "We need complete transaction details to satisfy our data integrity requirements."
        latest_user = "I changed my goal: I want to test one campus resale trade. I personally have four hours each week; friends' availability is unknown."
        saved["messages"].extend(
            [
                {"kind": "participant", "id": "proposal-one", "roleId": "role-1", "letter": "A", "body": latest_proposal},
                {"kind": "participant", "id": "reply-two", "roleId": "role-2", "letter": "B", "body": role_two_previous},
                {"kind": "user", "id": "latest-user", "body": latest_user},
            ]
        )
        saved["expectedRevision"] = saved["revision"]
        persisted = self.client.put(f"/api/debate/sessions/{session_id}", json=saved)
        self.assertEqual(persisted.status_code, 200)

        focus = "As a buyer, treat the earlier retention claim as unconfirmed and identify one relevant evidence gap."
        role_reply = "The retention point is only a proposal; first identify which transaction details would help resolve a buyer dispute."
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            side_effect=[coordinator_decision("role-2", focus), role_reply],
        ) as model_chat:
            automatic = self.client.post(f"/api/debate/sessions/{session_id}/reply", json={})
        self.assertEqual(automatic.status_code, 200, automatic.get_json())
        self.assertEqual(automatic.get_json()["speakerId"], "role-2")
        self.assertEqual(automatic.get_json()["selectionMode"], "coordinator")
        self.assertEqual(model_chat.call_count, 2)

        coordinator_request = model_chat.call_args_list[0].args[0]
        coordinator_context = coordinator_context_from_request(coordinator_request)
        self.assertNotIn("replyToMessageId", coordinator_context)
        self.assertEqual(
            [(turn["speakerType"], turn.get("speakerId"), turn["content"]) for turn in coordinator_context["recentConversation"]],
            [
                ("user", "user", question),
                ("simulated_adviser", "role-1", latest_proposal),
                ("simulated_adviser", "role-2", role_two_previous),
                ("user", "user", latest_user),
            ],
        )
        self.assertEqual(coordinator_context["questionSource"], {
            "sourceId": "opening-question:0", "sequence": 0, "speakerId": "user"
        })
        self.assertEqual(
            [(turn["sourceId"], turn["sequence"]) for turn in coordinator_context["recentConversation"]],
            [
                ("saved-message:opening", 1),
                ("saved-message:proposal-one", 2),
                ("saved-message:reply-two", 3),
                ("saved-message:latest-user", 4),
            ],
        )
        self.assertEqual(coordinator_context["recentConversation"][1]["speaker"], "Avery")
        self.assertEqual(coordinator_context["recentConversation"][2]["speaker"], "Jordan")
        self.assertEqual(coordinator_context["recentConversation"][1]["identity"], role_one_identity)
        self.assertEqual(coordinator_context["recentConversation"][2]["identity"], role_two_identity)
        candidate = next(item for item in coordinator_context["candidates"] if item["participantId"] == "role-2")
        self.assertEqual(candidate["identity"], role_two_identity)
        self.assertEqual(candidate["prompt"], role_two_prompt)

        role_context = reply_context_from_request(model_chat.call_args_list[1].args[0])
        self.assertEqual(role_context["assignedAdvisor"]["identity"], role_two_identity)
        self.assertEqual(role_context["assignedAdvisor"]["savedPrompt"], role_two_prompt)
        self.assertEqual(role_context["assignedAdvisorPreviousReplies"][-1]["content"], role_two_previous)
        self.assertEqual(role_context["latestAdvisorMessage"]["speakerId"], "role-2")
        self.assertEqual(role_context["latestAdvisorMessage"]["content"], role_two_previous)
        self.assertEqual(role_context["turnGuidance"]["focus"], focus)
        self.assertEqual(role_context["turnGuidance"]["replyToMessageId"], "latest-user")
        self.assertEqual(role_context["turnGuidance"]["referencedMessage"]["speakerType"], "user")
        self.assertEqual(role_context["turnGuidance"]["referencedMessage"]["speakerId"], "user")
        self.assertEqual(role_context["turnGuidance"]["referencedMessage"]["content"], latest_user)
        self.assertEqual(role_context["currentUserGoal"], latest_user)
        self.assertEqual(role_context["userAuthoredMessages"][-1]["content"], latest_user)
        self.assertEqual(
            [turn["content"] for turn in role_context["userAuthoredMessages"]], [question, latest_user]
        )
        retained_claim = next(turn for turn in role_context["conversation"] if turn["content"] == latest_proposal)
        self.assertEqual(retained_claim["speakerType"], "simulated_adviser")
        self.assertEqual(retained_claim["speakerId"], "role-1")
        self.assertEqual(retained_claim["speaker"], "Avery")
        self.assertTrue(retained_claim["notUserConfirmation"])
        self.assertNotIn(latest_proposal, json.dumps(role_context["userAuthoredMessages"], ensure_ascii=False))
        self.assertNotIn(role_two_previous, json.dumps(role_context["userAuthoredMessages"], ensure_ascii=False))
        self.assertNotIn("currentUserGoal", model_chat.call_args_list[1].args[0][0]["content"])
        self.assertIn("never a user fact, agreed decision, shared policy, or team requirement", model_chat.call_args_list[1].args[0][0]["content"])
        self.assertNotIn(focus, json.dumps(role_context["userAuthoredMessages"], ensure_ascii=False))

        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat", return_value="One distinct buyer-focused reply."
        ) as explicit_call:
            explicit = self.client.post(
                f"/api/debate/sessions/{session_id}/reply", json={"participantId": "role-2"}
            )
        self.assertEqual(explicit.status_code, 200)
        self.assertEqual(explicit.get_json()["selectionMode"], "explicit")
        explicit_call.assert_called_once()
        explicit_context = reply_context_from_request(explicit_call.call_args.args[0])
        self.assertEqual(explicit_context["assignedAdvisor"]["identity"], role_two_identity)
        self.assertEqual(explicit_context["assignedAdvisor"]["savedPrompt"], role_two_prompt)
        self.assertEqual(explicit_context["assignedAdvisorPreviousReplies"][-2]["content"], role_two_previous)
        self.assertEqual(explicit_context["assignedAdvisorPreviousReplies"][-1]["content"], automatic.get_json()['content'])
        self.assertIsNone(explicit_context["turnGuidance"])
        self.assertNotIn("You coordinate one next turn", explicit_call.call_args.args[0][0]["content"])

    def test_explicit_participant_id_overrides_automatic_role_selection(self):
        payload = changed_goal_session_payload()
        payload["messages"].append({"kind": "user", "body": "@Lan Ting, can you reconsider the old comparison?"})
        created = self.client.post("/api/debate/sessions", json=payload)
        session_id = created.get_json()["session"]["id"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            return_value="The old graduate comparison no longer fits.",
        ) as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["speakerId"], "role-1")
        model_chat.assert_called_once()
        context = reply_context_from_request(model_chat.call_args_list[0].args[0])
        self.assertEqual(context["assignedAdvisor"]["displayName"], "Lan Ting")
        self.assertIsNone(context["turnGuidance"])
        self.assertEqual(context["latestUserMessage"], "@Lan Ting, can you reconsider the old comparison?")

    def test_answered_clarifications_yield_to_relevant_roles_and_keep_new_goal_in_later_context(self):
        payload = changed_goal_session_payload()
        payload["question"] = "Should I apply to Stanford or Tsinghua for graduate school?"
        payload["participants"][0]["prompt"] = "Compare Stanford and Tsinghua graduate admissions and application choices."
        payload["participants"][1]["prompt"] = "Focus on direct finance employment after a bachelor's, entry-level roles, and the user's stated career priorities."
        payload["participants"][2]["prompt"] = "Track a changed decision and set aside graduate-school comparisons when the user no longer plans to study further."
        changed_goal = "I will not attend graduate school. I want domestic finance employment after my bachelor's."
        payload["messages"] = [{"kind": "user", "body": payload["question"]}]
        for question, answer in (
            ("Are you deciding where to apply?", "I was considering applications before."),
            ("Which field interests you?", "Finance."),
            ("Would you study abroad?", "I have not decided."),
            ("Are you choosing between graduate offers?", changed_goal),
        ):
            payload["messages"].extend(
                [
                    {
                        "kind": "participant",
                        "roleId": "role-1",
                        "letter": "A",
                        "body": question,
                        "waitingForUser": True,
                    },
                    {"kind": "user", "body": answer},
                ]
            )
        created = self.client.post("/api/debate/sessions", json=payload)
        session_id = created.get_json()["session"]["id"]

        def save_reply(reply):
            session = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            self.assertEqual(session["messages"][-1], reply["message"])

        reply_text = "The graduate-school comparison no longer fits; direct finance work is the decision now."
        decisions = iter([
            ("role-2", "Consider direct finance work.", "respond"),
            ("role-3", "Track the goal change.", "clarify"),
        ])

        def model_turn(messages, **kwargs):
            if "You coordinate one next turn" in messages[0]["content"]:
                context = coordinator_context_from_request(messages)
                participant_id, focus, intent = next(decisions)
                return coordinator_decision(participant_id, focus, intent)
            return reply_text

        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat", side_effect=model_turn
        ) as model_chat:
            first = self.client.post(f"/api/debate/sessions/{session_id}/reply", json={}).get_json()
            self.assertIn(first["speakerId"], {"role-2", "role-3"})
            self.assertNotEqual(first["speakerId"], "role-1")
            save_reply(first)

            before_second = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            second_response = self.client.post(f"/api/debate/sessions/{session_id}/reply", json={})
            self.assertEqual(second_response.status_code, 200, second_response.get_json())
            self.assertEqual(second_response.get_json()["content"], reply_text)
            self.assertIn(second_response.get_json()["speakerId"], {"role-2", "role-3"})
            save_reply(second_response.get_json())
            after_second = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            self.assertEqual(after_second["revision"], before_second["revision"] + 1)
            self.assertEqual(sum(message.get("body") == reply_text for message in after_second["messages"]), 2)

        contexts = [
            reply_context_from_request(model_chat.call_args_list[index].args[0])
            for index in (1, 3)
        ]
        self.assertEqual(model_chat.call_count, 4)
        self.assertEqual(len(contexts), 2)
        self.assertTrue(all(context["currentUserGoal"] == changed_goal for context in contexts))
        self.assertTrue(all(context["question"] == payload["question"] for context in contexts))
        self.assertEqual(contexts[1]["latestAdvisorMessage"]["content"], reply_text)
        self.assertEqual(contexts[0]["turnGuidance"]["focus"], "Consider direct finance work.")
        self.assertEqual(contexts[1]["turnGuidance"]["intent"], "clarify")
        system_prompt = model_chat.call_args_list[3].args[0][0]["content"]
        self.assertIn("Never include your own name", system_prompt)

    def test_automatic_sse_reply_announces_the_context_selected_participant(self):
        payload = changed_goal_session_payload()
        payload["messages"][-1]["id"] = "latest-user-message"
        created = self.client.post("/api/debate/sessions", json=payload)
        session_id = created.get_json()["session"]["id"]
        with patch(
            "server.gizmoapp_server.debate_api.stream_role_reply",
            return_value=iter([
                {"type": "mode", "replyMode": "plain_text_v1"},
                {"type": "metadata", "waitingForUser": False},
                {"type": "delta", "text": "The updated goal changes the decision."},
            ]),
        ) as stream_reply, patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            return_value=coordinator_decision("role-3", "Set aside the graduate-school question."),
        ) as coordinator_call:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={},
                headers={"Accept": "text/event-stream"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            'data: {"type": "start", "speakerId": "role-3", "selectionMode": "coordinator", "fallbackReason": "none", "replyMode": "plain_text_v1"}\n\n',
            response.get_data(as_text=True),
        )
        coordinator_call.assert_called_once()
        self.assertEqual(stream_reply.call_args.kwargs["coordination_focus"], "Set aside the graduate-school question.")
        self.assertEqual(stream_reply.call_args.kwargs["coordination_reply_to_message_id"], "latest-user-message")

    def test_coordinator_failures_and_invalid_ids_fall_back_once_without_leaking_output(self):
        created = self.client.post("/api/debate/sessions", json=changed_goal_session_payload())
        session_id = created.get_json()["session"]["id"]
        cases = [
            (llm.CourseLLMError("provider token PRIVATE_MODEL_TOKEN"), "model_error", "PRIVATE_MODEL_TOKEN"),
            ("not JSON PRIVATE_COORDINATOR_TEXT", "invalid_json", "PRIVATE_COORDINATOR_TEXT"),
            (
                coordinator_decision("not-in-session", "PRIVATE_UNKNOWN_ROLE_OUTPUT"),
                "unknown_participant_id",
                "PRIVATE_UNKNOWN_ROLE_OUTPUT",
            ),
        ]
        for coordinator_output, reason, private_text in cases:
            session_id = self.client.post("/api/debate/sessions", json=changed_goal_session_payload()).json["session"]["id"]
            with self.subTest(reason=reason), self.assertLogs(
                "server.gizmoapp_server.debate_chat", level="WARNING"
            ) as captured, patch(
                "server.gizmoapp_server.debate_chat.llm.chat",
                side_effect=[coordinator_output, "Fallback reply"],
            ) as model_chat, patch(
                "server.gizmoapp_server.debate_chat._fact_review_plain_reply",
                side_effect=AssertionError("quality review must not run"),
            ), patch(
                "server.gizmoapp_server.debate_chat._correct_plain_reply",
                side_effect=AssertionError("rewrite must not run"),
            ), patch(
                "server.gizmoapp_server.debate_chat._reject_duplicate_reply",
                side_effect=AssertionError("duplicate rejection must not run"),
            ):
                response = self.client.post(f"/api/debate/sessions/{session_id}/reply", json={})
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertEqual(response.get_json()["content"], "Fallback reply")
            self.assertEqual(response.get_json()["speakerId"], "role-3")
            self.assertEqual(response.get_json()["selectionMode"], "fallback")
            self.assertEqual(response.get_json()["fallbackReason"], reason)
            self.assertEqual(model_chat.call_count, 2)
            self.assertIn("You coordinate one next turn", model_chat.call_args_list[0].args[0][0]["content"])
            self.assertNotIn("You coordinate one next turn", model_chat.call_args_list[1].args[0][0]["content"])
            self.assertIn(f"reason={reason}", "".join(captured.output))
            self.assertNotIn(private_text, "".join(captured.output))

    def test_opening_coordinator_task_is_null_cited_and_only_in_advisory_user_context(self):
        payload = chat_session_payload()
        payload["messages"] = [{"kind": "user", "id": "opening", "body": payload["question"]}]
        payload["paused"] = False
        created = self.client.post("/api/debate/sessions", json=payload)
        session_id = created.get_json()["session"]["id"]
        focus = "The team must use automation for each trade."
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            side_effect=[coordinator_decision("role-1", focus), "A short pilot may clarify the handoff."],
        ) as model_chat:
            response = self.client.post(f"/api/debate/sessions/{session_id}/reply", json={})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["replyMode"], "plain_text_v1")
        self.assertEqual(model_chat.call_count, 2)
        role_request = model_chat.call_args_list[1].args[0]
        system_content = role_request[0]["content"]
        role_context = reply_context_from_request(role_request)
        self.assertNotIn(focus, system_content)
        self.assertNotIn(focus, json.dumps(role_context["userAuthoredMessages"], ensure_ascii=False))
        self.assertIsNone(role_context["turnGuidance"]["replyToMessageId"])
        self.assertIsNone(role_context["turnGuidance"]["referencedMessage"])
        self.assertFalse(role_context["turnGuidance"]["isUserAuthored"])
        task_messages = [message for message in role_request[1:] if "COORDINATOR_ADVISORY_TASK" in message["content"]]
        self.assertEqual(len(task_messages), 1)
        self.assertEqual(task_messages[0]["role"], "user")
        self.assertIn(focus, task_messages[0]["content"])

    def test_selection_observability_reports_safe_metadata_for_json_and_sse_branches(self):
        for transport in ("json", "sse"):
            for branch in ("coordinator", "fallback", "explicit"):
                with self.subTest(transport=transport, branch=branch):
                    created = self.client.post("/api/debate/sessions", json=chat_session_payload())
                    session_id = created.get_json()["session"]["id"]
                    model_calls = []

                    def model_chat(messages, **kwargs):
                        model_calls.append(messages)
                        if branch == "coordinator":
                            if "You coordinate one next turn" in messages[0]["content"]:
                                return coordinator_decision("role-2", "PRIVATE_FOCUS_SENTINEL")
                            return "Independent reply"
                        if branch == "fallback":
                            if "You coordinate one next turn" in messages[0]["content"]:
                                return "PRIVATE_COORDINATOR_RAW_SENTINEL"
                            return "Independent reply"
                        return "Independent reply"

                    stream_events = iter([
                        {"type": "mode", "replyMode": "plain_text_v1"},
                        {"type": "metadata", "waitingForUser": False},
                        {"type": "delta", "text": "Independent reply"},
                    ])
                    with patch(
                        "server.gizmoapp_server.debate_chat.llm.chat", side_effect=model_chat
                    ) as model_call, patch(
                        "server.gizmoapp_server.debate_api.stream_role_reply",
                        return_value=stream_events,
                    ) as stream_call:
                        payload = {"participantId": "role-1"} if branch == "explicit" else {}
                        headers = {"Accept": "text/event-stream"} if transport == "sse" else {}
                        response = self.client.post(
                            f"/api/debate/sessions/{session_id}/reply", json=payload, headers=headers
                        )

                    expected_mode = {
                        "coordinator": "coordinator",
                        "fallback": "fallback",
                        "explicit": "explicit",
                    }[branch]
                    expected_reason = "invalid_json" if branch == "fallback" else "none"
                    if transport == "json":
                        self.assertEqual(response.status_code, 200, response.get_json())
                        body = response.get_json()
                        self.assertEqual(body["selectionMode"], expected_mode)
                        self.assertEqual(body["fallbackReason"], expected_reason)
                        if branch == "explicit":
                            self.assertEqual(body["speakerId"], "role-1")
                        elif branch == "coordinator":
                            self.assertEqual(body["speakerId"], "role-2")
                        else:
                            self.assertIn(body["speakerId"], {"role-1", "role-2"})
                        self.assertEqual(body["content"], "Independent reply")
                        self.assertNotIn("PRIVATE_FOCUS_SENTINEL", response.get_data(as_text=True))
                        self.assertNotIn("PRIVATE_COORDINATOR_RAW_SENTINEL", response.get_data(as_text=True))
                        self.assertEqual(model_call.call_count, 1 if branch == "explicit" else 2)
                    else:
                        self.assertEqual(response.status_code, 200)
                        events = [
                            json.loads(line[6:])
                            for line in response.get_data(as_text=True).splitlines()
                            if line.startswith("data: ")
                        ]
                        for event_type in ("start", "done"):
                            event = next(item for item in events if item["type"] == event_type)
                            self.assertEqual(event["selectionMode"], expected_mode)
                            self.assertEqual(event["fallbackReason"], expected_reason)
                            self.assertIn(event["speakerId"], {"role-1", "role-2"})
                        start = next(item for item in events if item["type"] == "start")
                        done = next(item for item in events if item["type"] == "done")
                        self.assertEqual(start["speakerId"], done["speakerId"])
                        if branch == "coordinator":
                            self.assertEqual(start["speakerId"], "role-2")
                        elif branch == "explicit":
                            self.assertEqual(start["speakerId"], "role-1")
                        self.assertNotIn("PRIVATE_FOCUS_SENTINEL", response.get_data(as_text=True))
                        self.assertNotIn("PRIVATE_COORDINATOR_RAW_SENTINEL", response.get_data(as_text=True))
                        self.assertEqual(model_call.call_count, 0 if branch == "explicit" else 1)
                        stream_call.assert_called_once()

    @with_legacy_control_protocol
    def test_waiting_marker_is_validated_and_saved_with_the_participant_message(self):
        payload = chat_session_payload()
        payload["messages"][0]["waitingForUser"] = True
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]
        restored = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        self.assertTrue(restored["messages"][0]["waitingForUser"])

        failed_replies = (
            "WAITING_FOR_USER: maybe\nPRIVATE_MODEL_BODY_ONE",
            "WAITING_FOR_USER: perhaps\nPRIVATE_MODEL_BODY_TWO",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            diagnostics_path = Path(temp_dir) / "diagnostics.jsonl"
            with patch.object(debate_chat, "_REPLY_DIAGNOSTIC_PATH", diagnostics_path), patch(
                "server.gizmoapp_server.debate_chat.llm.chat",
                side_effect=observed_chat_replies(*failed_replies),
            ) as model_chat:
                response = self.client.post(
                    f"/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-2"},
                )

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertIn("after one repair", payload["errors"][0])
        self.assertEqual(model_chat.call_count, 2)
        diagnostics = payload["diagnostics"]
        self.assertEqual(
            set(diagnostics),
            {"version", "status", "requestId", "attempts"},
        )
        self.assertEqual(diagnostics["version"], "debate_reply_diag_v2")
        self.assertEqual(diagnostics["status"], "failed")
        self.assertEqual(diagnostics["requestId"], payload["requestId"])
        self.assertEqual(
            diagnostics["attempts"],
            [
                {
                    "attempt": 1,
                    "finishReason": "stop",
                    "outputChars": len(failed_replies[0]),
                    "category": "invalid_boolean",
                    "structure": {
                        "markerPrefixFound": True,
                        "booleanValueParsed": False,
                        "newlineSeen": True,
                        "bodyAfterMarker": True,
                    },
                    "parseFailure": "invalid_boolean",
                },
                {
                    "attempt": 2,
                    "finishReason": "stop",
                    "outputChars": len(failed_replies[1]),
                    "category": "invalid_boolean",
                    "structure": {
                        "markerPrefixFound": True,
                        "booleanValueParsed": False,
                        "newlineSeen": True,
                        "bodyAfterMarker": True,
                    },
                    "parseFailure": "invalid_boolean",
                },
            ],
        )
        error_text = response.get_data(as_text=True)
        for private_value in (
            "PRIVATE_MODEL_BODY_ONE",
            "PRIVATE_MODEL_BODY_TWO",
            "WAITING_FOR_USER: maybe",
            "diag-test-secret-key",
            "diag-test-private-conversation",
            "Authorization",
            "private.invalid",
        ):
            self.assertNotIn(private_value, error_text)

    @with_legacy_control_protocol
    def test_json_reply_diagnostics_distinguish_incomplete_marker_and_empty_body(self):
        malformed_replies = (
            ("missing_marker", "No control line\nPRIVATE_MODEL_BODY"),
            ("missing_marker", "Plain response at EOF"),
            ("incomplete_marker", "WAITING_FOR_USER"),
            ("incomplete_marker", "WAITING_FOR_USER: tr"),
            ("invalid_boolean", "WAITING_FOR_USER: maybe\nPRIVATE_MODEL_BODY"),
            ("empty_body", "WAITING_FOR_USER: true"),
            ("empty_body", "WAITING_FOR_USER: false\n   "),
        )
        for expected_category, malformed_reply in malformed_replies:
            with self.subTest(category=expected_category), tempfile.TemporaryDirectory() as temp_dir:
                created = self.client.post("/api/debate/sessions", json=chat_session_payload())
                session_id = created.get_json()["session"]["id"]
                diagnostics_path = Path(temp_dir) / "diagnostics.jsonl"
                with patch.object(debate_chat, "_REPLY_DIAGNOSTIC_PATH", diagnostics_path), patch(
                    "server.gizmoapp_server.debate_chat.llm.chat",
                    side_effect=observed_chat_replies(malformed_reply, malformed_reply),
                ) as model_chat:
                    response = self.client.post(
                        f"/api/debate/sessions/{session_id}/reply",
                        json={"participantId": "role-1"},
                )
                self.assertEqual(response.status_code, 503)
                self.assertEqual(model_chat.call_count, 2)
                attempts = response.get_json()["diagnostics"]["attempts"]
                self.assertEqual([item["category"] for item in attempts], [expected_category] * 2)
                self.assertEqual([item["parseFailure"] for item in attempts], [expected_category] * 2)
                self.assertEqual(attempts[0]["structure"], attempts[1]["structure"])
                error_text = response.get_data(as_text=True)
                self.assertNotIn(malformed_reply, error_text)
                self.assertNotIn("diag-test-secret-key", error_text)

    @with_legacy_control_protocol
    def test_nonstream_parser_accepts_a_complete_inline_marker_and_body_at_eof(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        inline_reply = "WAITING_FOR_USER: false Should we ask the group?"
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            side_effect=observed_chat_replies(inline_reply),
        ) as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "Should we ask the group?")
        self.assertFalse(response.get_json()["waitingForUser"])
        model_chat.assert_called_once()

    @with_legacy_control_protocol
    def test_complete_malformed_nonstream_reply_is_repaired_and_saved_once(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        failed_reply = "not a control line\nDiscard this first body."
        repaired_reply = marked_reply("Keep this answer once.")
        with tempfile.TemporaryDirectory() as temp_dir:
            diagnostics_path = Path(temp_dir) / "diagnostics.jsonl"
            with patch.object(debate_chat, "_REPLY_DIAGNOSTIC_PATH", diagnostics_path), patch(
                "server.gizmoapp_server.debate_chat.llm.chat",
                side_effect=observed_chat_replies(failed_reply, repaired_reply),
            ) as model_chat:
                response = self.client.post(
                    f"/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-2"},
                )
            diagnostics = [
                json.loads(line) for line in diagnostics_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "Keep this answer once.")
        self.assertEqual(model_chat.call_count, 2)
        repair_prompt = json.dumps(model_chat.call_args_list[1].args[0])
        self.assertIn("omitted the required `WAITING_FOR_USER` control line", repair_prompt)
        self.assertNotIn("Discard this first body.", repair_prompt)
        self.assertEqual([item["attempt"] for item in diagnostics], [1, 2])
        self.assertEqual([item["request_mode"] for item in diagnostics], ["completion", "completion"])
        self.assertEqual([item["completion_reason"] for item in diagnostics], ["stop", "stop"])
        self.assertEqual([item["output_chars"] for item in diagnostics], [len(failed_reply), len(repaired_reply)])
        self.assertEqual(diagnostics[0]["control_line_excerpt"], "not a control line")
        self.assertEqual(diagnostics[0]["parse_rejection_reason"], "missing_marker")
        self.assertIsNone(diagnostics[1]["parse_rejection_reason"])
        self.assertFalse(diagnostics[1]["failed_output_included_in_repair"])
        diagnostic_text = json.dumps(diagnostics)
        self.assertNotIn("Discard this first body.", diagnostic_text)
        saved = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        saved_replies = [message["body"] for message in saved["messages"] if message["kind"] == "participant"]
        self.assertEqual(saved_replies.count("Keep this answer once."), 1)
        self.assertNotIn("Discard this first body.", saved_replies)

    def test_participant_question_does_not_implicitly_request_user_wait(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            return_value="Jordan, would a smaller pilot answer that?",
        ):
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["content"], "Jordan, would a smaller pilot answer that?")
        self.assertFalse(response.get_json()["waitingForUser"])
        self.assertEqual(response.get_json()["replyMode"], "plain_text_v1")

    def test_plain_text_role_reply_accepts_sse_and_emits_false_wait_state(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        generated_text = "A streamed participant reply."
        with patch(
            "server.gizmoapp_server.debate_chat.llm.stream_chat",
            return_value=iter(["A streamed ", "participant reply."]),
        ) as model_stream, patch(
            "server.gizmoapp_server.debate_chat.llm.chat", side_effect=AssertionError("extra call")
        ) as extra_model:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
                headers={"Accept": "text/event-stream"},
            )
            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/event-stream")
        self.assertIn(
            'data: {"type": "start", "speakerId": "role-1", "selectionMode": "explicit", "fallbackReason": "none", "replyMode": "plain_text_v1"}\n\n',
            body,
        )
        chunks = [json.loads(line[6:])["text"] for line in body.splitlines() if line.startswith("data: ") and json.loads(line[6:])["type"] == "delta"]
        self.assertGreater(len(chunks), 1)
        self.assertEqual("".join(chunks), generated_text)
        done = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")][-1]
        self.assertEqual(done["type"], "done")
        self.assertFalse(done["waitingForUser"])
        self.assertEqual(done["message"]["body"], generated_text)
        self.assertEqual(done["remaining"], 9999)
        self.assertNotIn("WAITING_FOR_USER", body)
        model_stream.assert_called_once()
        extra_model.assert_not_called()
        context = reply_context_from_request(model_stream.call_args.args[0])
        self.assertEqual(context["identityDirectory"]["questionAskerId"], "user")
        self.assertEqual(
            {item["advisorId"] for item in context["identityDirectory"]["simulatedAdvisers"]},
            {role["id"] for role in chat_session_payload()["participants"]},
        )
        self.assertNotIn("userTeamMembershipRecords", context["modelRequestText"])
        self.assertEqual(
            context["userAuthoredMessages"],
            [
                {"speakerId": "user", "content": "Should we open the workshop to beginners?"},
                {"speakerId": "user", "content": "What would the pilot need to measure?"},
            ],
        )
        self.assertEqual(
            context["simulatedAdviserTurns"],
            [{"speakerId": "role-1", "speaker": "Avery", "content": "Start with a short pilot."}],
        )
        self.assertNotIn("userAvailabilityEstimate", context["modelRequestText"])
        self.assertEqual(context["assignedAdvisor"]["advisorId"], "role-1")

    def test_plain_text_stream_rejects_a_whitespace_only_reply(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.stream_chat",
            return_value=iter(["  ", "\n"]),
        ) as model_stream:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
                headers={"Accept": "text/event-stream"},
            )
            events = [
                json.loads(line[len("data: "):])
                for line in response.get_data(as_text=True).splitlines()
                if line.startswith("data: ")
            ]
        self.assertEqual(response.status_code, 200)
        error_event = next(event for event in events if event["type"] == "error")
        self.assertIn("empty participant reply", error_event["message"])
        self.assertEqual(error_event["replyMode"], "plain_text_v1")
        self.assertFalse(any(event["type"] == "done" for event in events))
        model_stream.assert_called_once()

    @with_legacy_control_protocol
    def test_stream_parser_accepts_a_complete_inline_marker_and_body_at_eof(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        reply = "WAITING_FOR_USER: true Which date works?"
        with patch(
            "server.gizmoapp_server.debate_chat.llm.stream_chat",
            side_effect=lambda messages, *, max_tokens, on_completion=None: observed_stream_reply(reply)(
                messages, max_tokens=max_tokens, on_completion=on_completion
            ),
        ) as model_stream:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
                headers={"Accept": "text/event-stream"},
            )
            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('data: {"type": "delta", "text": "Which date works?"}\n\n', body)
        self.assertEqual(body.count('"text": "Which date works?"'), 1)
        self.assertIn('"type": "done", "speakerId": "role-1", "selectionMode": "explicit", "fallbackReason": "none", "waitingForUser": true', body)
        self.assertNotIn("WAITING_FOR_USER", body)
        model_stream.assert_called_once()

    @with_legacy_control_protocol
    def test_stream_parser_repairs_incomplete_marker_only_after_end_and_emits_body_once(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        attempts = [
            iter(["WAITING_FOR_USER: tr"]),
            iter(["WAITING_FOR_USER: fal", "se\nRecovered after incomplete marker."]),
        ]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.stream_chat", side_effect=attempts
        ) as model_stream:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
                headers={"Accept": "text/event-stream"},
            )
            body = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("WAITING_FOR_USER", body)
        self.assertEqual(body.count('"text": "Recovered after incomplete marker."'), 1)
        self.assertIn('"type": "done", "speakerId": "role-1", "selectionMode": "explicit", "fallbackReason": "none", "waitingForUser": false', body)
        self.assertEqual(model_stream.call_count, 2)
        repair_prompt = json.dumps(model_stream.call_args_list[1].args[0])
        self.assertIn("ended before a complete `WAITING_FOR_USER` boolean value", repair_prompt)
        self.assertIn("This does not imply true or false", repair_prompt)
        self.assertNotIn('WAITING_FOR_USER: tr"', repair_prompt)

    @with_legacy_control_protocol
    def test_stream_parser_reports_complete_marker_at_eof_as_empty_body_not_incomplete(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        stream_replies = [
            observed_stream_reply("WAITING_FOR_USER: true"),
            observed_stream_reply("WAITING_FOR_USER: false"),
        ]

        def stream_side_effect(messages, *, max_tokens, on_completion=None):
            return stream_replies.pop(0)(messages, max_tokens=max_tokens, on_completion=on_completion)

        with tempfile.TemporaryDirectory() as temp_dir:
            diagnostics_path = Path(temp_dir) / "diagnostics.jsonl"
            with patch.object(debate_chat, "_REPLY_DIAGNOSTIC_PATH", diagnostics_path), patch(
                "server.gizmoapp_server.debate_chat.llm.stream_chat",
                side_effect=stream_side_effect,
            ) as model_stream:
                response = self.client.post(
                    f"/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-1"},
                    headers={"Accept": "text/event-stream"},
                )
                body = response.get_data(as_text=True)

        events = [
            json.loads(line[len("data: "):])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        error_event = next(event for event in events if event["type"] == "error")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(model_stream.call_count, 2)
        self.assertEqual(
            [attempt["category"] for attempt in error_event["diagnostics"]["attempts"]],
            ["empty_body", "empty_body"],
        )
        self.assertEqual(
            [attempt["parseFailure"] for attempt in error_event["diagnostics"]["attempts"]],
            ["empty_body", "empty_body"],
        )
        self.assertEqual(
            error_event["diagnostics"]["attempts"][0]["structure"],
            {
                "markerPrefixFound": True,
                "booleanValueParsed": True,
                "newlineSeen": False,
                "bodyAfterMarker": False,
            },
        )
        self.assertNotIn("WAITING_FOR_USER", body)

    @with_legacy_control_protocol
    def test_eof_control_marker_categories_match_for_streaming_and_nonstreaming(self):
        malformed_replies = (
            ("missing_marker", "No control marker"),
            ("incomplete_marker", "WAITING_FOR"),
            ("incomplete_marker", "WAITING_FOR_USER: tr"),
            ("invalid_boolean", "WAITING_FOR_USER: trueish Body"),
            ("empty_body", "WAITING_FOR_USER: false"),
        )

        for streaming in (False, True):
            for expected_category, malformed_reply in malformed_replies:
                with self.subTest(streaming=streaming, reply=malformed_reply), tempfile.TemporaryDirectory() as temp_dir:
                    created = self.client.post("/api/debate/sessions", json=chat_session_payload())
                    session_id = created.get_json()["session"]["id"]
                    diagnostics_path = Path(temp_dir) / "diagnostics.jsonl"

                    def stream_side_effect(messages, *, max_tokens, on_completion=None):
                        return observed_stream_reply(malformed_reply)(
                            messages,
                            max_tokens=max_tokens,
                            on_completion=on_completion,
                        )

                    with patch.object(debate_chat, "_REPLY_DIAGNOSTIC_PATH", diagnostics_path), patch(
                        "server.gizmoapp_server.debate_chat.llm.chat",
                        side_effect=observed_chat_replies(malformed_reply, malformed_reply),
                    ), patch(
                        "server.gizmoapp_server.debate_chat.llm.stream_chat",
                        side_effect=stream_side_effect,
                    ):
                        response = self.client.post(
                            f"/api/debate/sessions/{session_id}/reply",
                            json={"participantId": "role-1"},
                            headers={"Accept": "text/event-stream"} if streaming else {},
                        )
                        response_body = response.get_data(as_text=True) if streaming else ""

                    if streaming:
                        events = [
                            json.loads(line[len("data: "):])
                            for line in response_body.splitlines()
                            if line.startswith("data: ")
                        ]
                        diagnostics = next(event for event in events if event["type"] == "error")["diagnostics"]
                    else:
                        diagnostics = response.get_json()["diagnostics"]
                    self.assertEqual(
                        [attempt["category"] for attempt in diagnostics["attempts"]],
                        [expected_category, expected_category],
                    )
                    self.assertEqual(
                        [attempt["parseFailure"] for attempt in diagnostics["attempts"]],
                        [expected_category, expected_category],
                    )

    def test_plain_text_reply_uses_sdk_without_wait_control_or_repair(self):
        plain_reply = "A short participant reply without control metadata."
        def sdk_response(content, *, streaming):
            if streaming:
                return iter(
                    [
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content=content),
                                    finish_reason=None,
                                )
                            ]
                        ),
                        SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    delta=SimpleNamespace(content=None),
                                    finish_reason="stop",
                                )
                            ]
                        ),
                    ]
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=content),
                        finish_reason="stop",
                    )
                ]
            )

        for streaming in (False, True):
            created = self.client.post("/api/debate/sessions", json=chat_session_payload())
            session_id = created.get_json()["session"]["id"]
            client = Mock()
            client.chat.completions.create.side_effect = [
                sdk_response(plain_reply, streaming=streaming),
            ]

            with patch.object(llm, "_get_client", return_value=client), patch.object(
                llm, "model_name", return_value="test-model"
            ), patch.object(
                debate_chat,
                "_parse_complete_reply",
                side_effect=AssertionError("plain-text replies must not parse control markers"),
            ), patch.object(
                debate_chat,
                "_format_repair_messages",
                side_effect=AssertionError("plain-text replies must not build format repairs"),
            ), patch.object(
                debate_chat,
                "_stream_role_reply_attempt",
                side_effect=AssertionError("plain-text streams must not enter the control parser"),
            ):
                response = self.client.post(
                    f"/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-1"},
                    headers={"Accept": "text/event-stream"} if streaming else {},
                )
                response_body = response.get_data(as_text=True) if streaming else ""

            calls = client.chat.completions.create.call_args_list
            self.assertEqual(len(calls), 1)
            messages = calls[0].kwargs["messages"]
            self.assertEqual(messages[0]["role"], "system")
            self.assertIn("Return only the concise plain-text reply", messages[0]["content"])
            self.assertNotIn("WAITING_FOR_USER", messages[0]["content"])
            self.assertFalse(debate_chat._AUTO_CLARIFICATION_WAIT_ENABLED)

            if streaming:
                self.assertIn(f'"text": "{plain_reply}"', response_body)
                self.assertIn('"waitingForUser": false', response_body)
                events = [
                    json.loads(line[len("data: "):])
                    for line in response_body.splitlines()
                    if line.startswith("data: ")
                ]
                self.assertEqual(events[0]["type"], "start")
                self.assertEqual(events[0]["replyMode"], "plain_text_v1")
                self.assertEqual(events[-1]["type"], "done")
                self.assertEqual(events[-1]["replyMode"], "plain_text_v1")
            else:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["content"], plain_reply)
                self.assertFalse(response.get_json()["waitingForUser"])
                self.assertEqual(response.get_json()["replyMode"], "plain_text_v1")

    @with_legacy_control_protocol
    def test_stream_parser_discards_failed_attempt_body_and_stops_after_one_failed_repair(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        created_session = created.get_json()["session"]
        session_id = created_session["id"]
        stream_replies = [
            observed_stream_reply("bad marker\nDiscarded first body."),
            observed_stream_reply("also bad\nDiscarded second body."),
        ]

        def stream_side_effect(messages, *, max_tokens, on_completion=None):
            return stream_replies.pop(0)(messages, max_tokens=max_tokens, on_completion=on_completion)

        with tempfile.TemporaryDirectory() as temp_dir:
            diagnostics_path = Path(temp_dir) / "diagnostics.jsonl"
            with patch.object(debate_chat, "_REPLY_DIAGNOSTIC_PATH", diagnostics_path), patch(
                "server.gizmoapp_server.debate_chat.llm.stream_chat", side_effect=stream_side_effect
            ) as model_stream:
                response = self.client.post(
                    f"/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-1"},
                    headers={"Accept": "text/event-stream"},
                )
                body = response.get_data(as_text=True)
            diagnostics = [
                json.loads(line) for line in diagnostics_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Discarded first body.", body)
        self.assertNotIn("Discarded second body.", body)
        self.assertNotIn('"type": "delta"', body)
        self.assertIn("after one repair", body)
        self.assertEqual(model_stream.call_count, 2)
        events = [
            json.loads(line[len("data: "):])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        error_event = next(event for event in events if event["type"] == "error")
        public_diagnostics = error_event["diagnostics"]
        self.assertEqual(
            set(public_diagnostics),
            {"version", "status", "requestId", "attempts"},
        )
        self.assertEqual(public_diagnostics["version"], "debate_reply_diag_v2")
        self.assertEqual(public_diagnostics["status"], "failed")
        self.assertEqual(public_diagnostics["requestId"], response.headers["X-Request-ID"])
        self.assertEqual(public_diagnostics["requestId"], public_diagnostics["requestId"].lower())
        self.assertEqual(len(public_diagnostics["requestId"]), 16)
        int(public_diagnostics["requestId"], 16)
        self.assertEqual(
            public_diagnostics["attempts"],
            [
                {
                    "attempt": 1,
                    "finishReason": "stop",
                    "outputChars": len("bad marker\nDiscarded first body."),
                    "category": "missing_marker",
                    "structure": {
                        "markerPrefixFound": False,
                        "booleanValueParsed": False,
                        "newlineSeen": True,
                        "bodyAfterMarker": False,
                    },
                    "parseFailure": "missing_marker",
                },
                {
                    "attempt": 2,
                    "finishReason": "stop",
                    "outputChars": len("also bad\nDiscarded second body."),
                    "category": "missing_marker",
                    "structure": {
                        "markerPrefixFound": False,
                        "booleanValueParsed": False,
                        "newlineSeen": True,
                        "bodyAfterMarker": False,
                    },
                    "parseFailure": "missing_marker",
                },
            ],
        )
        repair_prompt = json.dumps(model_stream.call_args_list[1].args[0])
        self.assertIn("omitted the required `WAITING_FOR_USER` control line", repair_prompt)
        self.assertNotIn("Discarded first body.", repair_prompt)
        self.assertEqual([item["attempt"] for item in diagnostics], [1, 2])
        self.assertEqual([item["completion_reason"] for item in diagnostics], ["stop", "stop"])
        self.assertEqual(
            [item["output_chars"] for item in diagnostics],
            [len("bad marker\nDiscarded first body."), len("also bad\nDiscarded second body.")],
        )
        self.assertEqual(
            [item["parse_rejection_reason"] for item in diagnostics],
            ["missing_marker", "missing_marker"],
        )
        self.assertEqual([item["control_line_excerpt"] for item in diagnostics], ["bad marker", "also bad"])
        self.assertTrue(all(item["request_mode"] == "stream" for item in diagnostics))
        self.assertFalse(diagnostics[1]["failed_output_included_in_repair"])
        diagnostic_text = json.dumps(diagnostics)
        self.assertNotIn("Discarded first body.", diagnostic_text)
        self.assertNotIn("Discarded second body.", diagnostic_text)
        for private_value in (
            "Discarded first body.",
            "Discarded second body.",
            "bad marker",
            "also bad",
            "WAITING_FOR_USER",
            "diag-test-secret-key",
            "diag-test-private-conversation",
            "Authorization",
        ):
            self.assertNotIn(private_value, body)
        restored = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        self.assertEqual(restored["messages"], created_session["messages"])

    @with_legacy_control_protocol
    def test_stream_parser_emits_complete_false_marker_and_body(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.stream_chat",
            return_value=iter(["WAITING_FOR_USER: false\nThis turn can continue."]),
        ):
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
                headers={"Accept": "text/event-stream"},
            )
            body = response.get_data(as_text=True)

        self.assertIn('"text": "This turn can continue."', body)
        self.assertIn('"type": "done", "speakerId": "role-1", "selectionMode": "explicit", "fallbackReason": "none", "waitingForUser": false', body)

    def test_stream_truncation_and_transport_failures_are_not_format_retried(self):
        failures = (
            (
                llm.CourseLLMTruncatedError("The course model reached its reply token limit."),
                "length",
                27,
                "truncated",
                "truncated",
            ),
            (
                llm.CourseLLMError("The course model is temporarily unavailable."),
                None,
                0,
                "transport_error",
                "transport_failure",
            ),
        )
        for failure, finish_reason, output_chars, observer_failure, expected_category in failures:
            with self.subTest(error=type(failure).__name__), tempfile.TemporaryDirectory() as temp_dir:
                created = self.client.post("/api/debate/sessions", json=chat_session_payload())
                session_id = created.get_json()["session"]["id"]
                diagnostics_path = Path(temp_dir) / "diagnostics.jsonl"
                with patch.object(debate_chat, "_REPLY_DIAGNOSTIC_PATH", diagnostics_path), patch(
                    "server.gizmoapp_server.debate_chat.llm.stream_chat",
                    side_effect=observed_stream_failure(
                        failure,
                        finish_reason=finish_reason,
                        output_chars=output_chars,
                        failure=observer_failure,
                    ),
                ) as model_stream:
                    response = self.client.post(
                        f"/api/debate/sessions/{session_id}/reply",
                        json={"participantId": "role-1"},
                        headers={"Accept": "text/event-stream"},
                    )
                    body = response.get_data(as_text=True)
                self.assertIn('"type": "error"', body)
                model_stream.assert_called_once()
                events = [
                    json.loads(line[len("data: "):])
                    for line in body.splitlines()
                    if line.startswith("data: ")
                ]
                error_event = next(event for event in events if event["type"] == "error")
                self.assertIn(str(failure), error_event["message"])
                self.assertNotIn("diagnostics", error_event)
                self.assertNotIn("WAITING_FOR_USER", model_stream.call_args.args[0][0]["content"])
                self.assertFalse(diagnostics_path.exists())
                model_stream.assert_called_once()
                self.assertNotIn("diag-test-secret-key", body)
                self.assertNotIn("Authorization", body)
                self.assertNotIn("private.invalid", body)

    def test_chat_role_reply_sse_respects_url_prefix(self):
        temp_dir, app = make_app("/agent-debate")
        try:
            client = authenticated_client(app)
            created = client.post("/agent-debate/api/debate/sessions", json=chat_session_payload())
            session_id = created.get_json()["session"]["id"]
            with patch(
                "server.gizmoapp_server.debate_api.stream_role_reply",
                return_value=iter([
                    {"type": "mode", "replyMode": "plain_text_v1"},
                    {"type": "metadata", "waitingForUser": False},
                    {"type": "delta", "text": "Streamed under the prefix."},
                ]),
            ):
                response = client.post(
                    f"/agent-debate/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-1"},
                    headers={"Accept": "text/event-stream"},
                )
                body = response.get_data(as_text=True)
            self.assertEqual(response.status_code, 200)
            self.assertIn('"text": "Streamed under the prefix."', body)
            self.assertEqual(
                client.post(
                    f"/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-1"},
                    headers={"Accept": "text/event-stream"},
                ).status_code,
                404,
            )
        finally:
            temp_dir.cleanup()

    def test_later_role_replies_receive_the_saved_multi_turn_discussion(self):
        payload = chat_session_payload()
        payload["question"] = "What are the downsides of your recommendation?"
        payload["participants"][0]["name"] = "Maya"
        builder_identity = (
            "A simulated builder viewpoint: attends to implementation hours, scope, and maintenance. "
            "Can identify what the team must build but does not know the team's codebase; speaks in practical, brief steps."
        )
        user_identity = (
            "A simulated user viewpoint: attends to finding an item, trust, and a smooth exchange. "
            "Can notice friction in the flow but cannot know local user behavior without evidence; speaks plainly and asks concrete questions."
        )
        payload["participants"][0]["prompt"] = builder_identity
        payload["participants"][1]["name"] = "Devon"
        payload["participants"][1]["roleType"] = "Skeptic"
        payload["participants"][1]["prompt"] = user_identity
        payload["messages"] = [{"kind": "user", "body": payload["question"]}]
        payload["draft"] = ""
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session_id = created.get_json()["session"]["id"]

        def persist_reply(reply, letter):
            session = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            self.assertEqual(session["messages"][-1], reply["message"])

        def reply_as(participant_id):
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": participant_id},
            )
            self.assertEqual(response.status_code, 200)
            return response.get_json()

        def persist_user_answer(body):
            session = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
            session["messages"].append({"kind": "user", "body": body})
            session["expectedRevision"] = session["revision"]
            saved = self.client.put(f"/api/debate/sessions/{session_id}", json=session)
            self.assertEqual(saved.status_code, 200)

        mock_reply_contents = [
            "Which recommendation would you like me to assess?",
            "Finance identifies a field, but not a location or role.",
            "The remaining decision should use the user's stated priorities.",
        ]
        mock_replies = mock_reply_contents
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat", side_effect=mock_replies
        ) as model_chat:
            first_reply = reply_as("role-1")
            self.assertFalse(first_reply["waitingForUser"])
            persist_reply(first_reply, "A")
            persist_user_answer("finance")
            second_reply = reply_as("role-2")
            persist_reply(second_reply, "B")
            third_reply = reply_as("role-1")

        self.assertEqual(third_reply["speakerId"], "role-1")
        self.assertEqual(model_chat.call_count, 3)
        first_context = reply_context_from_request(model_chat.call_args_list[0].args[0])
        second_context = reply_context_from_request(model_chat.call_args_list[1].args[0])
        third_context = reply_context_from_request(model_chat.call_args_list[2].args[0])

        self.assertEqual(first_context["latestUserMessage"], payload["question"])
        self.assertEqual(first_context["latestAdvisorMessage"], None)
        self.assertEqual(first_context["assignedAdvisor"]["savedPrompt"], builder_identity)
        self.assertEqual(second_context["assignedAdvisor"]["savedPrompt"], user_identity)
        self.assertEqual(third_context["assignedAdvisor"]["savedPrompt"], builder_identity)
        self.assertEqual(second_context["latestUserMessage"], "finance")
        self.assertEqual(
            {
                key: second_context["conversation"][1][key]
                for key in ("speakerType", "speakerId", "speaker", "content")
            },
            {
                "speakerType": "simulated_adviser",
                "speakerId": "role-1",
                "speaker": "Maya",
                "content": mock_reply_contents[0],
            },
        )
        self.assertEqual(second_context["unansweredAdvisorQuestions"], [])
        self.assertEqual(
            [(message["speaker"], message["content"]) for message in third_context["conversation"]],
            [
                ("User", payload["question"]),
                ("Maya", mock_reply_contents[0]),
                ("User", "finance"),
                ("Devon", mock_reply_contents[1]),
            ],
        )
        self.assertEqual(third_context["latestAdvisorMessage"]["speaker"], "Devon")
        self.assertEqual(
            [(message["speakerId"], message["speaker"]) for message in third_context["conversation"]],
            [("user", "User"), ("role-1", "Maya"), ("user", "User"), ("role-2", "Devon")],
        )
        self.assertEqual(
            third_context["assignedAdvisorPreviousReplies"][0]["content"], mock_reply_contents[0]
        )
        self.assertEqual(third_context["unansweredAdvisorQuestions"], [])
        saved_messages = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]["messages"]
        saved_first_reply = next(message for message in saved_messages if message.get("body") == mock_reply_contents[0])
        self.assertNotIn("waitingForUser", saved_first_reply)
        system_prompt = model_chat.call_args_list[2].args[0][0]["content"]
        self.assertIn("An unanswered user turn takes priority", system_prompt)
        self.assertIn("Use this participant's identity and saved prompt as a viewpoint", system_prompt)
        self.assertIn("original question and user-authored messages are the only sources for user/project facts", system_prompt)
        self.assertIn("Every assistant/participant message—including your prior replies and coordinator guidance—is that speaker's unconfirmed proposal or claim", system_prompt)
        self.assertIn("Preserve each user's original words, source, order, and every stated number", system_prompt)
        self.assertIn("Return only the concise plain-text reply", system_prompt)
        self.assertNotIn("WAITING_FOR_USER", system_prompt)
        self.assertIn("Add one small, useful contribution", system_prompt)
        self.assertIn("Never include your own name", system_prompt)
        self.assertIn(DEFAULT_LANGUAGE_INSTRUCTION, system_prompt)
        self.assertIn("15–35 English words", system_prompt)

    def test_chat_role_reply_rejects_unknown_participant_without_model_call(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        with patch("server.gizmoapp_server.debate_chat.llm.chat") as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-missing"},
            )
        self.assertEqual(response.status_code, 404)
        self.assertIn("participantId is not part", response.get_json()["errors"][0])
        model_chat.assert_not_called()

    def test_chat_role_reply_reports_model_failure_without_fallback(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        session_id = created.get_json()["session"]["id"]
        with patch(
            "server.gizmoapp_server.debate_chat.llm.chat",
            side_effect=llm.CourseLLMError("Model credentials are not configured."),
        ) as model_chat:
            response = self.client.post(
                f"/api/debate/sessions/{session_id}/reply",
                json={"participantId": "role-1"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Model credentials are not configured", response.get_json()["errors"][0])
        self.assertEqual(response.get_json()["replyMode"], "plain_text_v1")
        model_chat.assert_called_once()

    def test_plain_text_reply_preserves_empty_network_and_truncation_errors(self):
        failures = (
            llm.CourseLLMError("The course model returned an empty response. Please try again."),
            llm.CourseLLMError("The course model is temporarily unavailable. Please try again."),
            llm.CourseLLMTruncatedError("The course model reached its reply token limit. Please retry."),
        )
        for failure in failures:
            with self.subTest(failure=str(failure)):
                created = self.client.post("/api/debate/sessions", json=chat_session_payload())
                session_id = created.get_json()["session"]["id"]
                with patch("server.gizmoapp_server.debate_chat.llm.chat", side_effect=failure) as model_chat:
                    response = self.client.post(
                        f"/api/debate/sessions/{session_id}/reply",
                        json={"participantId": "role-1"},
                    )
                self.assertEqual(response.status_code, 503)
                self.assertIn(str(failure), response.get_json()["errors"][0])
                self.assertNotIn("content", response.get_json())
                self.assertEqual(response.get_json()["replyMode"], "plain_text_v1")
                model_chat.assert_called_once()

    def test_chat_role_reply_route_respects_url_prefix(self):
        temp_dir, app = make_app("/agent-debate")
        try:
            client = authenticated_client(app)
            created = client.post("/agent-debate/api/debate/sessions", json=chat_session_payload())
            session_id = created.get_json()["session"]["id"]
            with patch(
                "server.gizmoapp_server.debate_chat.llm.chat",
                return_value="Check one assumption first.",
            ):
                response = client.post(
                    f"/agent-debate/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-1"},
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["speakerId"], "role-1")
            self.assertEqual(
                client.post(
                    f"/api/debate/sessions/{session_id}/reply",
                    json={"participantId": "role-1"},
                ).status_code,
                404,
            )
        finally:
            temp_dir.cleanup()

    def test_chat_session_round_trips_avatar_seed_and_accepts_legacy_roles(self):
        payload = chat_session_payload()
        payload["participants"][0]["avatarSeed"] = MAX_AVATAR_SEED
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        saved = created.get_json()["session"]
        self.assertEqual(saved["participants"][0]["avatarSeed"], MAX_AVATAR_SEED)
        self.assertNotIn("avatarSeed", saved["participants"][1])

        restored = self.client.get(f"/api/debate/sessions/{saved['id']}")
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.get_json()["session"]["participants"], saved["participants"])

        payload["participants"][0]["avatarSeed"] = 0
        payload["expectedRevision"] = saved["revision"]
        updated = self.client.put(f"/api/debate/sessions/{saved['id']}", json=payload)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["session"]["revision"], saved["revision"] + 1)
        restored_after_update = self.client.get(f"/api/debate/sessions/{saved['id']}")
        self.assertEqual(
            restored_after_update.get_json()["session"]["participants"][0]["avatarSeed"],
            0,
        )
        self.assertNotIn(
            "avatarSeed",
            restored_after_update.get_json()["session"]["participants"][1],
        )

    def test_chat_session_validates_avatar_seed_like_role_generation(self):
        for invalid_seed in (True, 1.5, -1, MAX_AVATAR_SEED + 1, "101", None):
            with self.subTest(avatarSeed=invalid_seed):
                payload = chat_session_payload()
                payload["participants"][0]["avatarSeed"] = invalid_seed
                response = self.client.post("/api/debate/sessions", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("avatarSeed must be an integer", response.get_json()["errors"][0])

    def test_chat_session_preserves_custom_agent_colors_across_edits(self):
        payload = chat_session_payload()
        payload["participants"][0]["avatarColor"] = "#8260A4"
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201, created.get_json())
        saved = created.get_json()["session"]
        self.assertEqual(saved["participants"][0]["avatarColor"], "#8260a4")
        self.assertNotIn("avatarColor", saved["participants"][1])
        payload["participants"][0]["avatarColor"] = "#347b73"
        payload["expectedRevision"] = saved["revision"]
        updated = self.client.put(f"/api/debate/sessions/{saved['id']}", json=payload)
        self.assertEqual(updated.status_code, 200)
        restored = self.client.get(f"/api/debate/sessions/{saved['id']}").get_json()["session"]
        self.assertEqual(restored["participants"][0]["avatarColor"], "#347b73")

    def test_chat_session_rejects_invalid_avatar_colors(self):
        for color in (None, 123, "#fff", "red", "#123456;background:url(https://example.com)"):
            with self.subTest(color=color):
                payload = chat_session_payload()
                payload["participants"][0]["avatarColor"] = color
                response = self.client.post("/api/debate/sessions", json=payload)
                self.assertEqual(response.status_code, 400)
                self.assertIn("avatarColor", response.get_json()["errors"][0])

    def test_chat_session_rejects_stale_revision_without_overwriting_newer_data(self):
        created = self.client.post("/api/debate/sessions", json=chat_session_payload())
        saved = created.get_json()["session"]

        first_tab = chat_session_payload()
        first_tab["question"] = "The first tab's newer question"
        first_tab["expectedRevision"] = saved["revision"]
        updated = self.client.put(f"/api/debate/sessions/{saved['id']}", json=first_tab)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["session"]["revision"], saved["revision"] + 1)

        stale_tab = chat_session_payload()
        stale_tab["question"] = "An older tab's stale question"
        stale_tab["expectedRevision"] = saved["revision"]
        conflict = self.client.put(f"/api/debate/sessions/{saved['id']}", json=stale_tab)
        self.assertEqual(conflict.status_code, 409)
        self.assertIn("updated elsewhere", conflict.get_json()["errors"][0])

        current = self.client.get(f"/api/debate/sessions/{saved['id']}").get_json()["session"]
        self.assertEqual(current["question"], "The first tab's newer question")
        self.assertEqual(current["revision"], saved["revision"] + 1)

    def test_chat_session_validation_and_prefix_aware_routes(self):
        invalid = chat_session_payload()
        invalid["participants"][1]["id"] = invalid["participants"][0]["id"]
        response = self.client.post("/api/debate/sessions", json=invalid)
        self.assertEqual(response.status_code, 400)
        self.assertIn("must be unique", response.get_json()["errors"][0])

        temp_dir, app = make_app("/agent-debate")
        try:
            client = authenticated_client(app)
            created = client.post("/agent-debate/api/debate/sessions", json=chat_session_payload())
            self.assertEqual(created.status_code, 201)
            session_id = created.get_json()["session"]["id"]
            self.assertEqual(client.get(f"/agent-debate/api/debate/sessions/{session_id}").status_code, 200)
            self.assertEqual(client.get(f"/api/debate/sessions/{session_id}").status_code, 404)
        finally:
            temp_dir.cleanup()

    def test_chat_session_message_limit_rejects_without_truncating_saved_messages(self):
        payload = chat_session_payload()
        payload["messages"] = [
            {"kind": "user", "body": f"Message {index}"}
            for index in range(100)
        ]
        created = self.client.post("/api/debate/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        session = created.get_json()["session"]
        session_id = session["id"]
        self.assertEqual(len(created.get_json()["session"]["messages"]), 100)

        payload["messages"].append({"kind": "user", "body": "One more message"})
        payload["expectedRevision"] = session["revision"]
        rejected = self.client.put(f"/api/debate/sessions/{session_id}", json=payload)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("at most 100", rejected.get_json()["errors"][0])

        restored = self.client.get(f"/api/debate/sessions/{session_id}").get_json()["session"]
        self.assertEqual(len(restored["messages"]), 100)
        self.assertEqual(restored["messages"][-1]["body"], "Message 99")

    def test_prefix_applies_to_pages_assets_and_debate_api(self):
        temp_dir, app = make_app("/agent-debate")
        try:
            client = authenticated_client(app)
            page = client.get("/agent-debate/app/debate")
            self.assertEqual(page.status_code, 200)
            page_text = page.get_data(as_text=True)
            self.assertIn('<base href="/agent-debate/">', page_text)
            self.assertIn('/agent-debate/app/text/debate.js', page_text)
            for path in (
                "/agent-debate/app/text/debate.js",
                "/agent-debate/app/text/debate.css",
            ):
                response = client.get(path)
                try:
                    self.assertEqual(response.status_code, 200)
                finally:
                    response.close()
            self.assertEqual(client.get("/agent-debate/api/debate/status").status_code, 200)
            archive_response = client.get("/agent-debate/api/debate/source.zip")
            self.assertEqual(archive_response.status_code, 200)
            self.assertEqual(client.get("/api/debate/source.zip").status_code, 404)
            self.assertEqual(client.get("/api/debate/status").status_code, 404)
        finally:
            temp_dir.cleanup()


class DebateFrontendWiringTests(unittest.TestCase):
    """Browserless source-contract checks for Debate's request wiring."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.debate_js = (root / "server/gizmoapp_server/static/app/text/debate.js").read_text(encoding="utf-8")
        cls.debate_css = (root / "server/gizmoapp_server/static/app/text/debate.css").read_text(encoding="utf-8")
        cls.index_text = (root / "server/gizmoapp_server/templates/index_text.html").read_text(encoding="utf-8")
        cls.debate_api_py = (root / "server/gizmoapp_server/debate_api.py").read_text(encoding="utf-8")
        cls.debate_chat_py = (root / "server/gizmoapp_server/debate_chat.py").read_text(encoding="utf-8")
        cls.api_js = (root / "server/gizmoapp_server/static/app/api.js").read_text(encoding="utf-8")

    def test_summary_panel_uses_current_session_json_request_without_loop_or_persistence(self):
        summary_request = self.debate_js[
            self.debate_js.index("async function requestSummary(regenerate = false)"):
            self.debate_js.index("function isMessageListNearBottom(list)")
        ]
        self.assertIn('id="summary-dialog"', self.index_text)
        self.assertIn('data-action="summary"', self.debate_js)
        self.assertIn('data-action="regenerate-summary"', self.debate_js)
        self.assertIn('data-action="close-summary"', self.debate_js)
        self.assertIn("summaryDialog?.addEventListener", self.debate_js)
        self.assertIn("if (!summaryDialog) return;", self.debate_js)
        self.assertLess(summary_request.index("await saveSessionNow()"), summary_request.index("await requestJson("))
        self.assertIn("sessionSummaryApi(sessionId)", summary_request)
        self.assertIn("summaryRequestIsCurrent(requestId, sessionId)", summary_request)
        self.assertIn("state.summaryContentVersion", summary_request)
        self.assertIn("if (state.summaryText && !state.summaryDirty && !regenerate)", summary_request)
        self.assertIn("await state.saveQueue", summary_request)
        self.assertNotIn("state.sessionRevisions.get(sessionId) !== result.sourceRevision", summary_request)
        self.assertIn('action === "regenerate-summary"', self.debate_js)
        self.assertIn("void requestSummary(true)", self.debate_js)
        self.assertNotIn("ensureDiscussionLoop", summary_request)
        self.assertNotIn("toggleDiscussionPause", summary_request)
        self.assertIn("state.summarySourceRevision", self.debate_js)
        self.assertIn("state.summaryDirty", self.debate_js)
        self.assertIn("Update summary", self.debate_js)
        snapshot = self.debate_js[
            self.debate_js.index("function sessionSnapshot()"):
            self.debate_js.index("function isMockConversationMessage")
        ]
        self.assertNotIn("summaryText", snapshot)
        self.assertIn("requestJson(url, options = {})", self.api_js)

    def test_account_menu_replaces_developer_download_without_header_clutter(self):
        header = self.debate_js[
            self.debate_js.index("function renderStepper()"):
            self.debate_js.index("function conversationStyle()")
        ]
        workspace = (Path(__file__).resolve().parents[1] / "server/gizmoapp_server/static/app/text/workspace.js").read_text()
        self.assertIn('account.menu()', workspace)
        self.assertNotIn('Download code', workspace)
        self.assertIn('data-action="toggle-pause"', header)
        self.assertNotIn("Download code", header)
        self.assertNotIn('<nav class="stepper"', header)
        self.assertIn('scoped_path(prefix, "api/debate/source.zip")', self.debate_api_py)

    def test_question_composer_keeps_icon_and_omits_routine_status_copy(self):
        question_view = self.debate_js[
            self.debate_js.index("function renderQuestion()"):
            self.debate_js.index("function renderRoleList()")
        ]
        controls = self.debate_js[
            self.debate_js.index("function syncRoleGenerationControls()"):
            self.debate_js.index("function stageUrl(stage)")
        ]
        save_status = self.debate_js[
            self.debate_js.index("function renderSaveStatus()"):
            self.debate_js.index("function markSessionConflict(sessionId)")
        ]

        self.assertIn("Think it through.", question_view)
        self.assertLess(question_view.index("question-title"), question_view.index("question-input-shell"))
        self.assertIn('<button class="button button-primary build-group-button"', question_view)
        self.assertIn('icon("send")', question_view)
        self.assertIn('class="button-loading-spinner"', question_view)
        self.assertIn('submit.setAttribute("aria-label", buttonLabel);', controls)
        self.assertNotIn("submit.innerHTML", controls)
        self.assertIn("position: absolute;", self.debate_css[self.debate_css.index(".build-group-button {"):])
        self.assertIn("if (!state.saveFailed || !state.saveStatus) return \"\";", save_status)
        for removed_copy in (
            "Generating three participants…",
            "All changes saved.",
            "Choose who joins and how they contribute.",
            "Waiting…",
            "Waiting for ${role.name",
        ):
            self.assertNotIn(removed_copy, self.debate_js)
        composer = self.debate_js[
            self.debate_js.index("function renderDebate()"):
            self.debate_js.index("function render()")
        ]
        self.assertNotIn("state.replyPending || state.paused ? \"disabled\" : \"\"", composer)
        self.assertNotIn('state.paused ? "disabled"', composer)
        self.assertIn("if (!state.replyError || !state.replyMessage) {", self.debate_js)
        self.assertIn("${state.pendingUserMessages.map(renderPendingChatMessage).join(\"\")}", composer)
        self.assertIn("${renderStreamingReply()}", composer)

    def test_reply_stream_uses_the_existing_reply_route(self):
        stream_helper = self.api_js[
            self.api_js.index("export async function requestEventStream("):
            self.api_js.index("export function fetchBootstrap(")
        ]
        self.assertIn('Accept: "text/event-stream"', stream_helper)
        self.assertIn("response.body.getReader()", stream_helper)
        self.assertIn("await dispatch(frame);", stream_helper)
        self.assertIn("return sessionApi(`/${encodeURIComponent(sessionId)}/reply`);", self.debate_js)
        self.assertIn('request.headers.get("Accept", "")', self.debate_api_py)

    def test_reply_diagnostics_are_filtered_but_not_exposed_in_the_error_area(self):
        json_helper = self.api_js[
            self.api_js.index("export async function requestJson("):
            self.api_js.index("export async function requestEventStream(")
        ]
        stream_helper = self.api_js[
            self.api_js.index("export async function requestEventStream("):
            self.api_js.index("export function fetchBootstrap(")
        ]
        normalizer = self.debate_js[
            self.debate_js.index("function normalizeReplyDiagnostics(value)"):
            self.debate_js.index("function stageFromLocation()")
        ]
        reply_status = self.debate_js[
            self.debate_js.index("function renderReplyStatus()"):
            self.debate_js.index("function renderParticipants()")
        ]

        self.assertIn("error.diagnostics = payload.diagnostics;", json_helper)
        self.assertIn("error.diagnostics = payload?.diagnostics;", stream_helper)
        self.assertIn("error.diagnostics = event.diagnostics;", self.debate_js)
        self.assertIn("state.replyDiagnostics = normalizeReplyDiagnostics(diagnostics);", self.debate_js)
        self.assertIn("function formatReplyDiagnostics(value)", self.debate_js)
        self.assertNotIn("data-reply-diagnostic", reply_status)
        self.assertIn('data-action="retry-role-reply"', reply_status)
        self.assertIn("async function copyReplyDiagnostic()", self.debate_js)
        self.assertIn("if (!state.replyError || !state.replyMessage) {", reply_status)
        self.assertIn("value.requestId", normalizer)
        self.assertIn("Array.isArray(value.attempts)", normalizer)
        self.assertIn("typeof structure.markerPrefixFound !== \"boolean\"", normalizer)
        self.assertIn("REPLY_DIAGNOSTIC_PARSE_FAILURES.has(item.parseFailure)", normalizer)
        self.assertIn("parseFailure,", normalizer)
        self.assertIn('return { version: REPLY_DIAGNOSTIC_VERSION, status: "failed", requestId, attempts };', normalizer)
        self.assertIn('REPLY_DIAGNOSTIC_VERSION = "debate_reply_diag_v2"', self.debate_js)
        self.assertIn("/prefix=${Number(structure.markerPrefixFound)}", self.debate_js)

    def test_composer_stays_focused_and_message_list_respects_scroll_intent(self):
        render = self.debate_js[
            self.debate_js.index("function isMessageListNearBottom(list)"):
            self.debate_js.index("function syncRoleGenerationControls()")
        ]
        input_handler = self.debate_js[
            self.debate_js.index("function handleInput(event)"):
            self.debate_js.index("function handleChange(event)")
        ]
        submit = self.debate_js[
            self.debate_js.index("async function submitChatMessage()"):
            self.debate_js.index("function retryRoleReply()")
        ]

        self.assertIn("list.scrollHeight - list.scrollTop - list.clientHeight", render)
        self.assertIn(
            "const shouldFollowLatest = !previousMessageList || isMessageListNearBottom(previousMessageList);",
            render,
        )
        self.assertIn("messageList.scrollTop = shouldFollowLatest", render)
        self.assertIn("const preserveChatInputFocus = document.activeElement === previousChatInput;", render)
        self.assertIn("chatInput.focus({ preventScroll: true });", render)
        self.assertIn("chatInput.setSelectionRange(", render)
        self.assertIn('state.replyPending && !target.matches("[data-chat-input]")', input_handler)
        self.assertNotIn("if (state.replyPending || state.paused", submit)
        self.assertIn("state.pendingUserMessages.push(pendingMessage);", submit)
        self.assertIn("const nearBottom = isMessageListNearBottom(list);", self.debate_js)

    def test_generated_roles_keep_reusable_profiles_separate_from_case_facts(self):
        self.assertIn("plain, familiar English", SYSTEM_PROMPT)
        self.assertIn("varied personal names written in Latin script and familiar to English readers", SYSTEM_PROMPT)
        self.assertIn(DEFAULT_LANGUAGE_INSTRUCTION, SYSTEM_PROMPT)
        self.assertNotIn("primary language of discussionQuestion", SYSTEM_PROMPT)
        self.assertIn("brief, concrete current role/status label", SYSTEM_PROMPT)
        self.assertIn("not a sentence, past event, achievement, or biography", SYSTEM_PROMPT)
        self.assertIn("Do not invent or claim past accomplishments, personal experience", SYSTEM_PROMPT)
        self.assertIn("General knowledge may inform reasoning but must not be presented as lived experience", SYSTEM_PROMPT)
        self.assertIn("The discussionQuestion is case context, not a persona assignment", SYSTEM_PROMPT)
        self.assertIn("Never transfer its numbers, deadlines, availability, tasks, responsibilities", SYSTEM_PROMPT)
        self.assertIn("A standard numeric subject label such as a four-day workweek may appear in a prompt only to name the topic", SYSTEM_PROMPT)
        self.assertIn("project ownership, team membership", SYSTEM_PROMPT)
        self.assertIn("reusable, long-term identity lens", SYSTEM_PROMPT)
        self.assertIn("knowledge boundaries", SYSTEM_PROMPT)
        self.assertIn("what the participant does not know", SYSTEM_PROMPT)
        self.assertIn("a natural speaking manner", SYSTEM_PROMPT)
        self.assertIn("substantively different areas of attention", SYSTEM_PROMPT)
        self.assertNotIn("Ground concerns in the question", SYSTEM_PROMPT)
        self.assertNotIn("why its viewpoint matters to", SYSTEM_PROMPT)
        self.assertIn("Do not prescribe an answer", SYSTEM_PROMPT)
        self.assertIn("assume a participant supports or opposes any option", SYSTEM_PROMPT)
        self.assertIn("Leave judgment open to the question's evidence and later discussion", SYSTEM_PROMPT)
        self.assertIn("roleType must not define a participant's focus, stance, personality", SYSTEM_PROMPT)
        self.assertNotIn("Lin/Chen/Li", SYSTEM_PROMPT)
        self.assertNotIn("used WeChat", SYSTEM_PROMPT)
        self.assertNotIn("campus marketplaces", SYSTEM_PROMPT)
        self.assertIn("Checks facts", self.debate_js)
        self.assertIn("Plans practical steps", self.debate_js)
        self.assertIn("Connects viewpoints", self.debate_js)

    def test_compatibility_type_is_hidden_but_role_type_stays_in_saved_snapshot(self):
        self.assertNotIn('data-role-field="roleType"', self.debate_js)
        self.assertNotIn('<label for="role-type">Compatibility type</label>', self.debate_js)
        self.assertIn("participants: state.roles.map((role) => ({ ...role }))", self.debate_js)
        self.assertIn('"roleType": "Analyst"', json.dumps(chat_session_payload()))

    def test_recent_name_memory_is_optional_bounded_names_only_and_success_only(self):
        self.assertIn("MAX_RECENT_GENERATED_NAMES = 12", self.debate_js)
        remember = self.debate_js[
            self.debate_js.index("function rememberGeneratedNames(roles)"):
            self.debate_js.index("function roleReplyStreamApi")
        ]
        self.assertIn("window.localStorage.getItem(RECENT_GENERATED_NAMES_KEY)", self.debate_js)
        self.assertIn("window.localStorage.setItem(RECENT_GENERATED_NAMES_KEY, JSON.stringify(names))", remember)
        self.assertIn("roles.map((role) => role.name)", remember)
        self.assertNotIn("question", remember)
        self.assertNotIn("prompt", remember)
        self.assertIn("Browser storage is optional", remember)
        submit = self.debate_js[
            self.debate_js.index("async function handleSubmit(event)"):
            self.debate_js.index("async function restoreSession()")
        ]
        self.assertIn("...(avoidNames.length ? { avoidNames } : {})", submit)
        self.assertIn("if (enteredParticipants) rememberGeneratedNames(generatedRoles);", submit)

    def test_mock_success_response_keeps_role_fields_and_enters_participants(self):
        mock_response = {
            "roles": [
                {
                    "id": "a" * 32,
                    "name": "Evidence Mapper",
                    "identity": "student who checks local evidence before a campus project",
                    "roleType": "Analyst",
                    "prompt": "Find evidence.",
                    "avatarSeed": 101,
                },
                {
                    "id": "b" * 32,
                    "name": "Staff Advocate",
                    "identity": "campus club organizer familiar with volunteer schedules",
                    "roleType": "Challenger",
                    "prompt": "Surface risks.",
                    "avatarSeed": 202,
                },
                {
                    "id": "c" * 32,
                    "name": "Pilot Designer",
                    "identity": "recent graduate exploring small pilot projects",
                    "roleType": "Builder",
                    "prompt": "Design a trial.",
                    "avatarSeed": 303,
                },
            ]
        }
        self.assertEqual(len(mock_response["roles"]), 3)
        self.assertTrue(
            all(
                set(role) == {"id", "name", "identity", "roleType", "prompt", "avatarSeed"}
                for role in mock_response["roles"]
            )
        )
        self.assertIn("return `${apiBase()}/debate/roles/generate`;", self.debate_js)
        self.assertIn("return `${apiBase()}/debate/roles/generate-one`;", self.debate_js)
        self.assertIn("requestJson(\n        roleGenerationApi(),", self.debate_js)
        self.assertIn("question: question.trim(),", self.debate_js)
        self.assertIn("...(avoidNames.length ? { avoidNames } : {})", self.debate_js)
        self.assertIn("const { id, name, roleType, prompt, avatarSeed } = role;", self.debate_js)
        self.assertIn("const { identity } = role;", self.debate_js)
        self.assertIn("...role,", self.debate_js)
        self.assertIn("state.roles = generatedRoles;", self.debate_js)
        self.assertIn('const enteredParticipants = await setStage("participants", true, requestId);', self.debate_js)

    def test_role_generation_shows_generation_and_save_phases_with_bounded_timeout(self):
        question_view = self.debate_js[
            self.debate_js.index("function renderQuestion()"):
            self.debate_js.index("function renderRoleList()")
        ]
        sync_controls = self.debate_js[
            self.debate_js.index("function syncRoleGenerationControls()"):
            self.debate_js.index("function stageUrl(stage)")
        ]
        submit = self.debate_js[
            self.debate_js.index("async function handleSubmit(event)"):
            self.debate_js.index("async function restoreSession()")
        ]
        self.assertIn("const ROLE_GENERATION_TIMEOUT_MS = 240_000;", self.debate_js)
        self.assertIn("state.roleGenerationPending || state.roleGenerationError", question_view)
        self.assertNotIn("Finding the right perspectives…", submit)
        self.assertIn("Preparing your conversation…", submit)
        self.assertIn("state.roleGenerationPending || state.roleGenerationError", sync_controls)
        self.assertIn("ROLE_GENERATION_TIMEOUT_MS", submit)

    def test_mock_503_response_is_retryable_and_stale_results_are_ignored(self):
        mock_error_response = {
            "errors": [
                "Live AI role generation is unavailable because course model access is not configured. No roles were generated."
            ],
            "requestId": "mock-request",
        }
        self.assertIn("not configured", mock_error_response["errors"][0])
        self.assertIn("error.status = response.status;", self.api_js)
        self.assertIn("error?.status === 503", self.debate_js)
        role_error_message = self.debate_js[
            self.debate_js.index("function roleGenerationErrorMessage(error)"):
            self.debate_js.index("function invalidateRoleGeneration(message")
        ]
        self.assertNotIn("error?.diagnostics", role_error_message)
        self.assertNotIn("reason: ${reason}", role_error_message)
        self.assertNotIn("type: expected ${expectedType}, got ${actualType}", role_error_message)
        self.assertNotIn("allowed roleType:", role_error_message)
        self.assertIn("Couldn’t create the agents. Try again.", role_error_message)
        self.assertIn("state.roleGenerationMessage = requestPhase === \"generation\"", self.debate_js)
        self.assertNotIn("GIZMO_LLM_API_KEY", self.debate_js)
        self.assertNotIn("GIZMO_LLM_BASE_URL", self.debate_js)
        self.assertNotIn("GIZMO_LLM_MODEL", self.debate_js)
        self.assertIn("state.question === question", self.debate_js)
        self.assertIn("state.roleEditVersion === roleEditVersion", self.debate_js)
        self.assertIn(
            "state.roleGenerationPending || state.activeRoleGenerationRequestId !== null",
            self.debate_js,
        )
        submit_handler = self.debate_js[
            self.debate_js.index("async function handleSubmit(event)"):
            self.debate_js.index("async function restoreSession()")
        ]
        self.assertNotIn("DEFAULT_ROLES", submit_handler)
        error_handler = submit_handler[
            submit_handler.index("} catch (error)"):
            submit_handler.index("} finally {")
        ]
        self.assertNotIn("state.question =", error_handler)
        self.assertNotIn("state.roles =", error_handler)

    def test_single_role_reply_actions_preserve_history_and_revision_saves(self):
        self.assertIn("function roleReplyStreamApi(sessionId)", self.debate_js)
        self.assertIn("return `${apiBase()}/debate/sessions${path}`;", self.debate_js)
        self.assertIn("return sessionApi(`/${encodeURIComponent(sessionId)}/reply`);", self.debate_js)
        reply_error_handler = self.debate_js[
            self.debate_js.index("function roleReplyErrorMessage(error)"):
            self.debate_js.index("function showReplyError(message")
        ]
        self.assertIn("This conversation or agent is no longer available.", reply_error_handler)
        self.assertIn("This reply stopped early.", reply_error_handler)
        self.assertNotIn("control marker", reply_error_handler)
        self.assertNotIn("preview version", reply_error_handler)
        self.assertIn("async function requestRoleReply(roleId, initial = false, clearDraft = false)", self.debate_js)
        self.assertIn("async function startDebate()", self.debate_js)
        self.assertIn("async function submitChatMessage()", self.debate_js)
        self.assertIn("function retryRoleReply()", self.debate_js)
        self.assertIn('jsonOptions("POST", roleId ? { participantId: roleId } : {}', self.debate_js)
        self.assertIn("await saveSessionNow()", self.debate_js)
        self.assertIn("expectedRevision", self.debate_js)
        self.assertIn("markSessionConflict(sessionId)", self.debate_js)

        click_handler = self.debate_js[
            self.debate_js.index("function handleClick(event)"):
            self.debate_js.index("async function handleSubmit(event)")
        ]
        self.assertIn('void startDebate();', click_handler)
        self.assertIn('void retryRoleReply();', click_handler)
        submit_handler = self.debate_js[
            self.debate_js.index("async function handleSubmit(event)"):
            self.debate_js.index("async function restoreSession()")
        ]
        self.assertIn("await submitChatMessage();", submit_handler)

        restore_handler = self.debate_js[
            self.debate_js.index("async function restoreSession()"):
            self.debate_js.index("async function handlePopState()")
        ]
        self.assertIn("state.messages = saved.messages;", restore_handler)
        self.assertNotIn("No opening participant reply is saved yet.", restore_handler)
        for removed_mock_reference in (
            "scheduleMockMessage",
            "clearMockTimer",
            "makeDemoMessages",
            "state.demoMessages",
            "state.mockIndex",
        ):
            self.assertNotIn(removed_mock_reference, self.debate_js)

    def test_continuous_discussion_is_context_scheduled_and_serial(self):
        mention = self.debate_js[
            self.debate_js.index("function roleForChatMessage(message)"):
            self.debate_js.index("function updateComposeDraft(value)")
        ]
        loop = self.debate_js[
            self.debate_js.index("async function runDiscussionLoop(runId"):
            self.debate_js.index("function nextReplyForSavedConversation()")
        ]
        reply = self.debate_js[
            self.debate_js.index("async function requestRoleReply(roleId"):
            self.debate_js.index("function discussionLoopIsCurrent(runId)")
        ]
        submit = self.debate_js[
            self.debate_js.index("async function submitChatMessage()"):
            self.debate_js.index("function retryRoleReply()")
        ]
        pause = self.debate_js[
            self.debate_js.index("async function toggleDiscussionPause()"):
            self.debate_js.index("function roleReplyErrorMessage(error)")
        ]

        self.assertIn("return mentionedRole(message, state.roles);", mention)
        self.assertNotIn("state.roles[0]", mention)
        self.assertIn("Choose an agent from the @ menu", submit)
        self.assertIn("role?.id || null", submit)
        self.assertEqual(loop.count("requestRoleReply("), 1)
        self.assertIn("nextReply = { roleId: null, initial: false };", loop)
        self.assertIn("state.discussionLoopTask", self.debate_js)
        self.assertIn("await requestRoleReply(reply.roleId, reply.initial, reply.clearDraft)", loop)
        self.assertGreaterEqual(reply.count("await saveSessionNow()"), 2)
        self.assertIn('jsonOptions("POST", roleId ? { participantId: roleId } : {}', reply)
        self.assertIn("await requestEventStream(", reply)
        self.assertLess(reply.index("if (state.paused) return false;"), reply.index("await requestEventStream("))
        self.assertIn("state.streamingReply.body += event.text;", reply)
        self.assertIn("setTimeout(resolve, STREAM_CHUNK_DELAY_MS)", reply)
        self.assertIn("state.priorityReply = { roleId: role?.id || null, initial: false, clearDraft: true }", submit)
        self.assertIn("state.pendingUserMessages.push(pendingMessage);", submit)
        self.assertIn("if (!await saveSessionNow()) return false;", submit)
        self.assertIn("void ensureDiscussionLoop(null, true);", submit)
        self.assertIn("state.paused = false;", submit)
        self.assertIn("state.discussionLoopVersion += 1", pause)
        self.assertIn("await saveSessionNow()", pause)
        self.assertIn("discussionTurnDelay(state.messages.at(-1)?.body)", self.debate_js)
        self.assertIn("state.pendingUserMessages.shift()", loop)
        self.assertIn("Add one small, useful contribution", self.debate_chat_py)
        self.assertNotIn("selectNextParticipantId", self.debate_js)
        self.assertNotIn("DISCUSSION_MOVE_SCORES", self.debate_js)

    def test_auto_clarification_wait_is_disabled_but_manual_pause_is_preserved(self):
        reply = self.debate_js[
            self.debate_js.index("async function requestRoleReply(roleId"):
            self.debate_js.index("function discussionLoopIsCurrent(runId)")
        ]
        loop = self.debate_js[
            self.debate_js.index("async function runDiscussionLoop(runId"):
            self.debate_js.index("function nextReplyForSavedConversation()")
        ]
        pause = self.debate_js[
            self.debate_js.index("async function toggleDiscussionPause()"):
            self.debate_js.index("function roleReplyErrorMessage(error)")
        ]
        restore = self.debate_js[
            self.debate_js.index("async function restoreSession()"):
            self.debate_js.index("async function handlePopState()")
        ]

        self.assertIn("const AUTO_CLARIFICATION_WAIT_ENABLED = false;", self.debate_js)
        self.assertIn('typeof event.replyMode !== "string"', reply)
        self.assertIn("state.replyMode = event.replyMode", reply)
        self.assertNotIn("Reply mode: ${esc(state.replyMode)}", self.debate_js)
        self.assertIn('typeof event.waitingForUser !== "boolean"', reply)
        self.assertIn("waitingForUser = AUTO_CLARIFICATION_WAIT_ENABLED ? event.waitingForUser : false;", reply)
        self.assertNotIn("Waiting for your answer.", self.debate_js)
        self.assertIn("if (state.waitingForUser) return;", loop)
        self.assertLess(loop.index("state.pendingUserMessages.shift()"), loop.index("if (state.waitingForUser) return;"))
        self.assertIn("state.waitingForUser = false;", restore)
        self.assertNotIn("state.messages.at(-1)?.waitingForUser", restore)
        self.assertIn("state.paused = saved.paused || state.roundComplete;", restore)
        resume_guard = "if (state.stage === \"debate\" && state.debateStarted && !state.paused && !state.replyError && !state.readOnly && workspace.isSignedIn())"
        self.assertIn(resume_guard, restore)
        self.assertLess(restore.index("state.paused = saved.paused || state.roundComplete;"), restore.index(resume_guard))
        self.assertIn("state.discussionLoopVersion += 1", pause)
        self.assertIn("state.paused = true;", pause)
        self.assertIn("await saveSessionNow()", pause)


if __name__ == "__main__":
    unittest.main()
