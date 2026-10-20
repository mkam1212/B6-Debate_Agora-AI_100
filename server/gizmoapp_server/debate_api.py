"""HTTP routes for Agent Debate discussions, examples, and exports."""

from __future__ import annotations

import json
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from flask import Flask, Response, current_app, g, jsonify, make_response, request, stream_with_context

from . import llm
from .debate_language import DEFAULT_LANGUAGE_INSTRUCTION
from .debate_examples import get_example, get_sample_discussion, list_examples
from .debate_runner import (
    ModelUnavailable,
    model_status,
    reconcile_orphaned_run,
    start_run,
)
from .debate_store import (
    create_discussion,
    create_version,
    delete_discussion,
    fetch_discussion,
    list_discussions,
    save_draft,
    save_reflection,
    update_run,
)
from .debate_sessions import create_session, fetch_session, update_session
from .debate_roles import (
    MAX_AVATAR_SEED,
    MAX_AVOID_NAMES,
    MAX_ROLE_IDENTITY_LENGTH,
    MAX_ROLE_NAME_LENGTH,
    MAX_ROLE_QUESTION_LENGTH,
    RoleGenerationOutputError,
    RoleGenerationUnavailable,
    generate_roles as generate_participant_roles,
)
from .debate_chat import (
    MAX_CHAT_REPLY_TOKENS,
    _saved_discussion_turns,
    generate_role_reply,
    is_mock_message,
    select_participant_for_turn,
    stream_role_reply,
)
from .config import scoped_path
from .db import get_db

MAX_QUESTION_LENGTH = 4_000
MAX_TEXT_LENGTH = 6_000
MAX_TITLE_LENGTH = 120
MAX_OPTIONS = 12
MAX_SOURCES = 8
MAX_SOURCE_LENGTH = 12_000
MAX_TOTAL_SOURCE_LENGTH = 24_000
MAX_PERSPECTIVES = 5
MAX_FOLLOW_UP_LENGTH = 1_500
ROLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")
SESSION_ID_RE = re.compile(r"^[a-f0-9]{32}$")
CHAT_ROLE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,47}$")
CHAT_MESSAGE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
CHAT_ROLE_TYPES = {"Analyst", "Challenger", "Builder", "Skeptic", "Synthesizer"}
MAX_SESSION_QUESTION_LENGTH = 2_000
MAX_SESSION_PARTICIPANTS = 16
MAX_SESSION_MESSAGES = 100
MAX_SESSION_MESSAGE_LENGTH = 2_000
MAX_SESSION_SNAPSHOT_BYTES = 900_000
MIN_SUMMARY_PARTICIPANT_REPLIES = 3
_SOURCE_ARCHIVE_DIRS = (
    ("server/gizmoapp_server", frozenset({".py", ".js", ".css", ".html", ".svg"})),
    ("tests", frozenset({".py"})),
    ("scripts", frozenset({".py", ".sh"})),
)
_SOURCE_ARCHIVE_FILES = (
    "Makefile",
    "server/__init__.py",
    "server/manage.py",
    "server/wsgi.py",
    "server/wsgi_text.py",
    "server/wsgi_graphical.py",
    "server/requirements.txt",
    "server/requirements-ml.txt",
    "server/requirements-visual.txt",
)
_SOURCE_ARCHIVE_SKIP_PARTS = frozenset(
    {"__pycache__", ".git", ".venv", "node_modules", ".pytest_cache", "var", "instance", "uploads"}
)
_DEBATE_SUMMARY_SYSTEM_PROMPT = (
    "Summarize this saved Debate conversation as plain text. "
    f"{DEFAULT_LANGUAGE_INSTRUCTION} "
    "Write all four concise section labels in the selected output language: main views, "
    "disagreements, open questions/conditions, and optional next-step suggestions. Attribute viewpoints using each saved "
    "name exactly as written and its concrete identity when useful; do not transliterate names to pinyin or output internal "
    "roleType labels or metadata. Do not force consensus. Aim for 150–250 English words by default, or an equivalent "
    "natural length when another language is explicitly requested; "
    "a soft target, not a cutoff; complete the summary and do not truncate a thought to meet it. Use plain-text labels, "
    "not Markdown asterisk emphasis. Summarize the discussion rather than restating the opening question in full. The opening question and "
    "user-authored messages are the only sources for user facts and goals. Every participant statement is that speaker's "
    "claim or suggestion, not a confirmed user fact, shared policy, agreed decision, or team requirement. Keep unknowns "
    "unresolved, preserve who said what and all numbers/scopes, and do not invent context. Mark next steps as suggestions, "
    "not assigned or agreed actions. Return only the summary text: no JSON, control marker, or metadata."
)
SELECTION_FALLBACK_REASONS = frozenset(
    {
        "model_error",
        "invalid_response_size",
        "invalid_json",
        "invalid_shape",
        "invalid_fields",
        "invalid_participant_id",
        "unknown_participant_id",
        "invalid_focus",
        "invalid_intent",
        "unknown",
    }
)


def _error(
    message: str,
    status: int = 400,
    *,
    diagnostics: dict[str, Any] | None = None,
    reply_mode: str | None = None,
):
    payload: dict[str, Any] = {"errors": [message], "requestId": getattr(g, "request_id", None)}
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    if reply_mode is not None:
        payload["replyMode"] = reply_mode
    return jsonify(payload), status


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _reply_selection_metadata(
    participant_id: str, selection_mode: str, fallback_reason: str | None
) -> dict[str, str]:
    if selection_mode not in {"coordinator", "fallback", "explicit"}:
        selection_mode = "fallback"
    if selection_mode == "fallback":
        reason = fallback_reason if fallback_reason in SELECTION_FALLBACK_REASONS else "unknown"
    else:
        reason = "none"
    return {
        "speakerId": participant_id,
        "selectionMode": selection_mode,
        "fallbackReason": reason,
    }


def _role_reply_event_response(
    session: dict[str, Any],
    participant: dict[str, Any],
    participant_id: str,
    *,
    coordination_focus: str | None = None,
    coordination_intent: str = "respond",
    coordination_reply_to_message_id: str | None = None,
    selection_mode: str = "explicit",
    fallback_reason: str | None = None,
):
    selection_metadata = _reply_selection_metadata(participant_id, selection_mode, fallback_reason)

    @stream_with_context
    def events():
        try:
            reply_mode = None
            waiting_for_user = None
            for event in stream_role_reply(
                session,
                participant,
                coordination_focus=coordination_focus,
                coordination_intent=coordination_intent,
                coordination_reply_to_message_id=coordination_reply_to_message_id,
            ):
                if not isinstance(event, dict):
                    raise llm.CourseLLMError("The course model returned an invalid participant reply stream.")
                if event.get("type") == "mode":
                    candidate_mode = event.get("replyMode")
                    if (
                        reply_mode is not None
                        or not isinstance(candidate_mode, str)
                        or not re.fullmatch(r"[a-z0-9_]{1,64}", candidate_mode)
                    ):
                        raise llm.CourseLLMError("The participant reply stream has an invalid implementation mode.")
                    reply_mode = candidate_mode
                    yield _sse({"type": "start", **selection_metadata, "replyMode": reply_mode})
                elif event.get("type") == "metadata":
                    marker = event.get("waitingForUser")
                    if reply_mode is None or waiting_for_user is not None or not isinstance(marker, bool):
                        raise llm.CourseLLMError("The course model returned an invalid reply control marker.")
                    waiting_for_user = marker
                elif event.get("type") == "delta":
                    text = event.get("text")
                    if reply_mode is None or waiting_for_user is None or not isinstance(text, str) or not text:
                        raise llm.CourseLLMError("The course model returned an invalid participant reply stream.")
                    yield _sse({"type": "delta", "text": text})
                else:
                    raise llm.CourseLLMError("The course model returned an unknown participant reply event.")
            if reply_mode is None or waiting_for_user is None:
                raise llm.CourseLLMError("The course model returned no reply control marker.")
            yield _sse(
                {
                    "type": "done",
                    **selection_metadata,
                    "waitingForUser": waiting_for_user,
                    "replyMode": reply_mode,
                }
            )
        except llm.CourseLLMError as exc:
            error_event: dict[str, Any] = {
                "type": "error",
                "message": f"AI role reply is unavailable: {exc}",
            }
            if reply_mode is not None:
                error_event["replyMode"] = reply_mode
            diagnostics = getattr(exc, "public_diagnostics", None)
            if isinstance(diagnostics, dict):
                error_event["diagnostics"] = diagnostics
            yield _sse(error_event)

    return Response(
        events(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _json_payload() -> tuple[dict[str, Any] | None, tuple[Any, int] | None]:
    if not request.is_json:
        return None, _error("Content-Type must be application/json", 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _error("Request body must be a JSON object", 400)
    return payload, None


def _normalize_session_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    question = _string(payload, "question", MAX_SESSION_QUESTION_LENGTH, required=True)
    raw_participants = payload.get("participants")
    if not isinstance(raw_participants, list) or not 1 <= len(raw_participants) <= MAX_SESSION_PARTICIPANTS:
        raise ValueError(f"participants must be a list of 1 to {MAX_SESSION_PARTICIPANTS} items")

    participants = []
    participant_ids = set()
    for participant in raw_participants:
        if not isinstance(participant, dict):
            raise ValueError("each participant must be an object")
        participant_id = _string(participant, "id", 48, required=True)
        if CHAT_ROLE_ID_RE.fullmatch(participant_id) is None:
            raise ValueError("participant IDs may contain only letters, numbers, underscores, and hyphens")
        if participant_id in participant_ids:
            raise ValueError("participant IDs must be unique")
        participant_ids.add(participant_id)
        role_type = _string(participant, "roleType", 32, required=True)
        if role_type not in CHAT_ROLE_TYPES:
            raise ValueError("roleType must be a supported participant role")
        normalized_participant = {
            "id": participant_id,
            "avatar": _string(participant, "avatar", 16, required=True),
            "name": _string(participant, "name", 48),
            "prompt": _string(participant, "prompt", 1_000),
            "roleType": role_type,
        }
        if "identity" in participant:
            normalized_participant["identity"] = _string(participant, "identity", MAX_ROLE_IDENTITY_LENGTH)
        if "avatarSeed" in participant:
            avatar_seed = participant["avatarSeed"]
            if (
                isinstance(avatar_seed, bool)
                or not isinstance(avatar_seed, int)
                or not 0 <= avatar_seed <= MAX_AVATAR_SEED
            ):
                raise ValueError(f"avatarSeed must be an integer from 0 to {MAX_AVATAR_SEED}")
            normalized_participant["avatarSeed"] = avatar_seed
        participants.append(normalized_participant)

    raw_messages = payload.get("messages", [])
    raw_pending_messages = payload.get("pendingUserMessages", [])
    if (
        not isinstance(raw_messages, list)
        or not isinstance(raw_pending_messages, list)
        or len(raw_messages) + len(raw_pending_messages) > MAX_SESSION_MESSAGES
    ):
        raise ValueError(f"messages must be a list of at most {MAX_SESSION_MESSAGES} items")
    messages = []
    pending_user_messages = []
    seen_message_ids = set()

    def record_message_id(message_id: Any, *, required: bool) -> str | None:
        if message_id is None and not required:
            return None
        if not isinstance(message_id, str) or CHAT_MESSAGE_ID_RE.fullmatch(message_id) is None:
            raise ValueError("message id is invalid")
        if message_id in seen_message_ids:
            raise ValueError("message IDs must be unique")
        seen_message_ids.add(message_id)
        return message_id

    for message in raw_messages:
        if not isinstance(message, dict):
            raise ValueError("each message must be an object")
        kind = _string(message, "kind", 16, required=True)
        body = _string(message, "body", MAX_SESSION_MESSAGE_LENGTH, required=True)
        if kind == "user":
            normalized_message = {"kind": "user", "body": body}
            message_id = record_message_id(message.get("id"), required=False)
            if message_id is not None:
                normalized_message["id"] = message_id
            participant_id = message.get("participantId")
            if participant_id is not None:
                if not isinstance(participant_id, str) or participant_id not in participant_ids:
                    raise ValueError("message participantId must identify a saved participant")
                normalized_message["participantId"] = participant_id
            messages.append(normalized_message)
        elif kind == "participant":
            role_id = _string(message, "roleId", 48, required=True)
            if CHAT_ROLE_ID_RE.fullmatch(role_id) is None:
                raise ValueError("message participant ID is invalid")
            letter = _string(message, "letter", 1, required=True)
            if re.fullmatch(r"[A-Z]", letter) is None:
                raise ValueError("participant message labels must be a single uppercase letter")
            normalized_message = {"kind": "participant", "roleId": role_id, "letter": letter, "body": body}
            message_id = record_message_id(message.get("id"), required=False)
            if message_id is not None:
                normalized_message["id"] = message_id
            waiting_for_user = message.get("waitingForUser", False)
            if not isinstance(waiting_for_user, bool):
                raise ValueError("participant message waitingForUser must be a boolean")
            if waiting_for_user:
                normalized_message["waitingForUser"] = True
            messages.append(normalized_message)
        else:
            raise ValueError("message kind must be user or participant")

    for message in raw_pending_messages:
        if not isinstance(message, dict):
            raise ValueError("each pending user message must be an object")
        message_id = record_message_id(message.get("id"), required=True)
        body = _string(message, "body", MAX_SESSION_MESSAGE_LENGTH, required=True)
        participant_id = message.get("participantId")
        if participant_id is not None and (
            not isinstance(participant_id, str) or participant_id not in participant_ids
        ):
            raise ValueError("pending message participantId must identify a saved participant")
        pending = {"id": message_id, "body": body}
        if participant_id is not None:
            pending["participantId"] = participant_id
        pending_user_messages.append(pending)

    debate_started = payload.get("debateStarted", False)
    paused = payload.get("paused", False)
    if not isinstance(debate_started, bool) or not isinstance(paused, bool):
        raise ValueError("debateStarted and paused must be boolean values")
    snapshot = {
        "question": question,
        "participants": participants,
        "messages": messages,
        "pendingUserMessages": pending_user_messages,
        "draft": _string(payload, "draft", MAX_SESSION_MESSAGE_LENGTH),
        "debateStarted": debate_started,
        "paused": paused,
    }
    encoded_size = len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if encoded_size > MAX_SESSION_SNAPSHOT_BYTES:
        raise ValueError("saved session content is too large")
    return snapshot


def _expected_session_revision(payload: dict[str, Any]) -> int:
    revision = payload.get("expectedRevision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("expectedRevision must be a positive integer")
    return revision


def _string(payload: dict[str, Any], key: str, maximum: int, *, required: bool = False) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key} is required")
    if len(value) > maximum:
        raise ValueError(f"{key} must be at most {maximum} characters")
    return value


def _avoid_names(payload: dict[str, Any]) -> list[str]:
    raw_names = payload.get("avoidNames", [])
    if not isinstance(raw_names, list) or len(raw_names) > MAX_AVOID_NAMES:
        raise ValueError(f"avoidNames must be a list of at most {MAX_AVOID_NAMES} names")
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_names:
        if not isinstance(raw_name, str):
            raise ValueError("avoidNames entries must be text")
        name = raw_name.strip()
        if not name or len(name) > MAX_ROLE_NAME_LENGTH:
            raise ValueError(f"avoidNames entries must be non-empty text of at most {MAX_ROLE_NAME_LENGTH} characters")
        normalized = name.casefold()
        if normalized not in seen:
            names.append(name)
            seen.add(normalized)
    return names


def _default_perspectives(question: dict[str, Any]) -> list[dict[str, str]]:
    lower = question.get("question", "").lower()
    if any(word in lower for word in ("story", "narrative", "write", "creative", "character")):
        return [
            {
                "id": "fresh-angle",
                "name": "Fresh angle",
                "focus": "Find one surprising choice that serves the intended effect without adding needless complexity.",
            },
            {
                "id": "story-logic",
                "name": "Story logic",
                "focus": "Test whether the choice feels earned and clear in the actual story, not just in outline.",
            },
            {
                "id": "audience-effect",
                "name": "Audience effect",
                "focus": "Predict what the audience will notice or feel, and name one detail that could change that reaction.",
            },
        ]
    if any(word in lower for word in ("plan", "workshop", "launch", "review", "overlook")):
        return [
            {
                "id": "smallest-test",
                "name": "Smallest useful test",
                "focus": "Identify the smallest action that can test the plan's riskiest assumption before more work is committed.",
            },
            {
                "id": "first-breakpoint",
                "name": "What breaks first",
                "focus": "Find the most likely practical failure under the stated time, people, and resource limits.",
            },
            {
                "id": "affected-people",
                "name": "Who feels the trade-off",
                "focus": "Name who benefits, who carries the cost, and the one outcome that matters most to them.",
            },
        ]
    if question.get("options"):
        return [
            {
                "id": "goal-fit",
                "name": "Closest fit",
                "focus": "Compare only the listed options against the stated goal; explain the decisive difference, not a full pros-and-cons list.",
            },
            {
                "id": "constraint-check",
                "name": "Hard-constraint check",
                "focus": "Test the leading option against each hard constraint and identify the most likely way it could fail.",
            },
            {
                "id": "small-test",
                "name": "A smaller test",
                "focus": "Look for one low-cost test or genuinely better third path that could change the choice.",
            },
        ]
    return [
        {
            "id": "core-assumption",
            "name": "Core assumption",
            "focus": "Find the assumption carrying the most weight and say what evidence would support or weaken it.",
        },
        {
            "id": "countercase",
            "name": "Strongest countercase",
            "focus": "Build the strongest specific case against the apparent answer, without inventing disagreement.",
        },
        {
            "id": "decision-trigger",
            "name": "What changes the answer",
            "focus": "Name the missing fact or small test most likely to change the decision, then state why.",
        },
    ]


def _normalize_question(payload: dict[str, Any], old_question: dict | None = None) -> dict[str, Any]:
    old_question = old_question or {}
    question = _string(payload, "question", MAX_QUESTION_LENGTH, required=True)
    title = _string(payload, "title", MAX_TITLE_LENGTH)
    if not title:
        title = question.splitlines()[0].strip()[:MAX_TITLE_LENGTH] or "New discussion"
    raw_options = payload.get("options", old_question.get("options", []))
    if isinstance(raw_options, str):
        raw_options = raw_options.splitlines()
    if not isinstance(raw_options, list) or len(raw_options) > MAX_OPTIONS:
        raise ValueError(f"options must be a list of at most {MAX_OPTIONS} items")
    options = []
    for option in raw_options:
        if not isinstance(option, str):
            raise ValueError("each option must be text")
        option = option.strip()
        if len(option) > 180:
            raise ValueError("each option must be at most 180 characters")
        if option and option not in options:
            options.append(option)
    return {
        "title": title,
        "question": question,
        "goal": _string(payload, "goal", MAX_TEXT_LENGTH),
        "constraints": _string(payload, "constraints", MAX_TEXT_LENGTH),
        "options": options,
        "context": _string(payload, "context", MAX_TEXT_LENGTH),
        "currentView": _string(payload, "currentView", MAX_TEXT_LENGTH),
    }


def _normalize_perspectives(payload: dict[str, Any], question: dict[str, Any]) -> list[dict[str, str]]:
    raw_perspectives = payload.get("perspectives")
    if raw_perspectives is None:
        return _default_perspectives(question)
    if not isinstance(raw_perspectives, list) or not 2 <= len(raw_perspectives) <= MAX_PERSPECTIVES:
        raise ValueError(f"Choose between 2 and {MAX_PERSPECTIVES} analysis perspectives")
    perspectives = []
    seen_ids = set()
    for index, item in enumerate(raw_perspectives, start=1):
        if not isinstance(item, dict):
            raise ValueError("each perspective must be an object")
        name = _string(item, "name", 80, required=True)
        focus = _string(item, "focus", 400, required=True)
        role_id = item.get("id", f"role-{index}")
        if not isinstance(role_id, str) or not ROLE_ID_RE.fullmatch(role_id) or role_id in seen_ids:
            raise ValueError("perspective ids must be unique short lowercase names")
        seen_ids.add(role_id)
        perspectives.append({"id": role_id, "name": name, "focus": focus})
    return perspectives


def _normalize_sources(payload: dict[str, Any], existing: list[dict] | None = None) -> list[dict[str, str]]:
    raw_sources = payload.get("sources", [])
    if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCES:
        raise ValueError(f"sources must be a list of at most {MAX_SOURCES} items")
    old_by_id = {source.get("id"): source for source in (existing or [])}
    seen_ids: set[str] = set()
    seen_supplied_ids: set[str] = set()
    total_length = 0
    sources = []
    for item in raw_sources:
        if not isinstance(item, dict):
            raise ValueError("each source must be an object")
        name = _string(item, "name", 120)
        text = _string(item, "text", MAX_SOURCE_LENGTH)
        if not name and not text:
            continue
        if not name or not text:
            raise ValueError("each source needs both a name and source text")
        total_length += len(text)
        if total_length > MAX_TOTAL_SOURCE_LENGTH:
            raise ValueError(f"combined source text must be at most {MAX_TOTAL_SOURCE_LENGTH} characters")
        supplied_id = item.get("id")
        if isinstance(supplied_id, str) and supplied_id:
            if supplied_id in seen_supplied_ids:
                raise ValueError("source IDs must be unique")
            seen_supplied_ids.add(supplied_id)
        old_source = old_by_id.get(supplied_id) if isinstance(supplied_id, str) else None
        stable_id = supplied_id if old_source and old_source.get("text") == text else f"source-{uuid.uuid4().hex[:12]}"
        if stable_id in seen_ids:
            raise ValueError("source IDs must be unique")
        seen_ids.add(stable_id)
        sources.append({"id": stable_id, "name": name, "text": text, "type": "user-provided"})
    return sources


def _export_text(discussion: dict[str, Any], *, full: bool, markdown: bool, sample: bool = False) -> str:
    lines: list[str] = []

    def heading(text: str, level: int = 2) -> None:
        lines.append(f"{'#' * level} {text}" if markdown else f"{text.upper()}")
        lines.append("")

    def bullets(items: list[str]) -> None:
        lines.extend((f"- {item}" if markdown else f"• {item}") for item in items)
        if items:
            lines.append("")

    title = discussion.get("title", "Discussion")
    if sample or discussion.get("mode") == "sample":
        heading("Sample discussion — prewritten example", 1)
    else:
        heading(title, 1)
    if discussion.get("status") != "completed":
        lines.extend(["This discussion stopped before a conclusion was ready.", ""])
    question = discussion.get("question", {})
    heading("Question")
    lines.extend([question.get("question", ""), ""])
    if question.get("goal"):
        lines.extend(["What matters most: " + question["goal"], ""])
    if question.get("constraints"):
        lines.extend(["Constraints: " + question["constraints"], ""])
    if question.get("options"):
        heading("Options")
        bullets(question["options"])
    if question.get("context"):
        heading("Context")
        lines.extend([question["context"], ""])
    if discussion.get("sources"):
        heading("Sources supplied by the user")
        for source in discussion["sources"]:
            lines.extend([f"{source['name']} ({source['id']})", source["text"], ""])

    synthesis = discussion.get("synthesis")
    if synthesis:
        heading("Direct answer" if synthesis.get("kind") == "direct_answer" else "Recommendation")
        lines.extend([synthesis.get("recommendation", ""), ""])
        for label, key in (
            ("Conditions", "conditions"),
            ("Reasons", "reasons"),
            ("Key trade-offs", "tradeoffs"),
            ("Alternatives", "alternatives"),
            ("Open questions", "openQuestions"),
            ("Next steps", "nextSteps"),
            ("Needs verification", "needsVerification"),
        ):
            values = synthesis.get(key, [])
            if values:
                heading(label)
                bullets(values)
        if synthesis.get("strongestObjection"):
            heading("Strongest objection")
            lines.extend([synthesis["strongestObjection"], ""])
        if synthesis.get("optionComparison"):
            heading("Option comparison")
            for option in synthesis["optionComparison"]:
                fit = "Meets listed constraints" if option["meetsConstraints"] else "Does not meet listed constraints"
                lines.extend([f"- {option['option']}: {fit}. {option['assessment']}", ""])
    else:
        heading("Conclusion")
        lines.extend(["This discussion stopped before a conclusion was ready.", ""])

    if full:
        role_names = {role["id"]: role["name"] for role in discussion.get("perspectives", [])}
        for stage, label in (("initial", "Initial perspectives"), ("challenge", "Challenges and revisions")):
            contributions = [item for item in discussion.get("contributions", []) if item.get("stage") == stage]
            if not contributions:
                continue
            heading(label)
            for item in contributions:
                heading(role_names.get(item.get("roleId"), "Analysis role"), 3)
                lines.extend([item.get("claim", ""), ""])
                if item.get("reasons"):
                    bullets(item["reasons"])
                if item.get("respondsTo"):
                    lines.extend(["Responds to: " + ", ".join(item["respondsTo"]), ""])
                if item.get("revisionStatus") in {"retained", "revised", "withdrawn"} and stage == "challenge":
                    lines.extend([f"View {item['revisionStatus']}: {item.get('revisionReason', '')}", ""])

    if discussion.get("reflection"):
        heading("What changed your view?")
        lines.extend([discussion["reflection"], ""])
    if sample or discussion.get("mode") == "sample":
        lines.extend(["Prewritten content. No live model call was made for this discussion.", ""])
    return "\n".join(lines).strip() + "\n"


def _safe_filename(title: str, version: int, extension: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "discussion"
    return f"{slug}-v{version}.{extension}"


def _source_archive_entries(root: Path) -> list[tuple[Path, str]]:
    entries: dict[str, Path] = {}
    for relative_directory, allowed_suffixes in _SOURCE_ARCHIVE_DIRS:
        directory = root / relative_directory
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            relative = path.relative_to(root)
            if (
                path.is_symlink()
                or not path.is_file()
                or path.suffix.lower() not in allowed_suffixes
                or any(part.startswith(".") or part in _SOURCE_ARCHIVE_SKIP_PARTS for part in relative.parts)
            ):
                continue
            entries[relative.as_posix()] = path

    for relative_name in _SOURCE_ARCHIVE_FILES:
        path = root / relative_name
        if path.is_file() and not path.is_symlink():
            entries[relative_name] = path
    return [(path, relative_name) for relative_name, path in sorted(entries.items())]


def register_debate_routes(app: Flask) -> None:
    prefix = app.config["URL_PREFIX"]

    @app.post(scoped_path(prefix, "api/debate/roles/generate"))
    def generate_roles():
        payload, error = _json_payload()
        if error:
            return error
        try:
            question = _string(payload, "question", MAX_ROLE_QUESTION_LENGTH, required=True)
            avoid_names = _avoid_names(payload)
        except ValueError as exc:
            return _error(str(exc))
        try:
            roles = generate_participant_roles(question, avoid_names)
        except RoleGenerationUnavailable as exc:
            return _error(str(exc), 503)
        except RoleGenerationOutputError as exc:
            return _error(str(exc), 502, diagnostics=exc.diagnostics)
        except llm.CourseLLMError as exc:
            return _error(
                f"AI role generation is unavailable. {exc}",
                503,
                diagnostics=getattr(exc, "role_generation_diagnostics", None),
            )
        return jsonify({"roles": roles})

    @app.get(scoped_path(prefix, "api/debate/status"))
    def debate_status():
        return jsonify(model_status())

    @app.get(scoped_path(prefix, "api/debate/examples"))
    def examples():
        return jsonify({"examples": list_examples()})

    @app.get(scoped_path(prefix, "api/debate/examples/sample"))
    def sample_discussion():
        return jsonify({"sample": True, "discussion": get_sample_discussion()})

    @app.get(scoped_path(prefix, "api/debate/examples/sample/export"))
    def export_sample_discussion():
        discussion = get_sample_discussion()
        markdown = request.args.get("format", "md") != "txt"
        full = request.args.get("full", "0") == "1"
        extension = "md" if markdown else "txt"
        body = _export_text(discussion, full=full, markdown=markdown, sample=True)
        response = make_response(body)
        response.mimetype = "text/markdown" if markdown else "text/plain"
        response.headers["Content-Disposition"] = f"attachment; filename={_safe_filename(discussion['title'], 1, extension)}"
        return response

    @app.get(scoped_path(prefix, "api/debate/source.zip"))
    def download_source_code():
        root = Path(__file__).resolve().parents[2]
        archive_bytes = BytesIO()
        with ZipFile(archive_bytes, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
            for path, relative_name in _source_archive_entries(root):
                archive.write(path, arcname=f"debate-agora/{relative_name}")
        response = make_response(archive_bytes.getvalue())
        response.mimetype = "application/zip"
        response.headers["Content-Disposition"] = 'attachment; filename="debate-agora-source.zip"'
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get(scoped_path(prefix, "api/debate/examples/<example_id>"))
    def example_detail(example_id: str):
        example = get_example(example_id)
        if example is None:
            return _error("Example not found", 404)
        return jsonify({"example": example})

    @app.post(scoped_path(prefix, "api/debate/sessions"))
    def create_chat_session():
        payload, error = _json_payload()
        if error:
            return error
        try:
            snapshot = _normalize_session_snapshot(payload)
        except ValueError as exc:
            return _error(str(exc))
        session = create_session(get_db(), snapshot)
        return jsonify({"session": session}), 201

    @app.route(
        scoped_path(prefix, "api/debate/sessions/<session_id>"),
        methods=["GET", "PUT"],
    )
    def chat_session(session_id: str):
        if SESSION_ID_RE.fullmatch(session_id) is None:
            return _error("Session not found", 404)
        connection = get_db()
        if request.method == "GET":
            session = fetch_session(connection, session_id)
            if session is None:
                return _error("Session not found", 404)
            return jsonify({"session": session})

        payload, error = _json_payload()
        if error:
            return error
        try:
            expected_revision = _expected_session_revision(payload)
            snapshot = _normalize_session_snapshot(payload)
        except ValueError as exc:
            return _error(str(exc))
        session = update_session(connection, session_id, snapshot, expected_revision)
        if session is None:
            if fetch_session(connection, session_id) is None:
                return _error("Session not found", 404)
            return _error("This session was updated elsewhere. Reload the latest session before saving.", 409)
        return jsonify({"session": session})

    @app.post(scoped_path(prefix, "api/debate/sessions/<session_id>/reply"))
    def chat_role_reply(session_id: str):
        if SESSION_ID_RE.fullmatch(session_id) is None:
            return _error("Session not found", 404)
        payload, error = _json_payload()
        if error:
            return error
        try:
            participant_id = (
                _string(payload, "participantId", 48, required=True)
                if "participantId" in payload
                else None
            )
            if participant_id is not None and CHAT_ROLE_ID_RE.fullmatch(participant_id) is None:
                raise ValueError("participantId is invalid")
        except ValueError as exc:
            return _error(str(exc))

        connection = get_db()
        session = fetch_session(connection, session_id)
        if session is None:
            return _error("Session not found", 404)
        if participant_id is None:
            selection = select_participant_for_turn(session)
            participant = selection.participant
            if participant is None:
                return _error("No participant is available to continue this discussion.", 409)
            participant_id = participant["id"]
            coordination_focus = selection.focus
            coordination_intent = selection.intent
            coordination_reply_to_message_id = selection.reply_to_message_id
            selection_mode = "coordinator" if selection.mode == "coordinator" else "fallback"
            fallback_reason = selection.fallback_reason
        else:
            participant = next((item for item in session["participants"] if item["id"] == participant_id), None)
            if participant is None:
                return _error("participantId is not part of this saved session.", 404)
            coordination_focus = None
            coordination_intent = "respond"
            coordination_reply_to_message_id = None
            selection_mode = "explicit"
            fallback_reason = None
        if "text/event-stream" in request.headers.get("Accept", ""):
            return _role_reply_event_response(
                session,
                participant,
                participant_id,
                coordination_focus=coordination_focus,
                coordination_intent=coordination_intent,
                coordination_reply_to_message_id=coordination_reply_to_message_id,
                selection_mode=selection_mode,
                fallback_reason=fallback_reason,
            )
        try:
            content, waiting_for_user, reply_mode = generate_role_reply(
                session,
                participant,
                coordination_focus=coordination_focus,
                coordination_intent=coordination_intent,
                coordination_reply_to_message_id=coordination_reply_to_message_id,
            )
        except llm.CourseLLMError as exc:
            diagnostics = getattr(exc, "public_diagnostics", None)
            return _error(
                f"AI role reply is unavailable: {exc}",
                503,
                diagnostics=diagnostics,
                reply_mode=getattr(exc, "reply_mode", None),
            )
        return jsonify(
            {
                "speakerId": participant_id,
                "selectionMode": selection_mode,
                "fallbackReason": _reply_selection_metadata(
                    participant_id, selection_mode, fallback_reason
                )["fallbackReason"],
                "content": content,
                "waitingForUser": waiting_for_user,
                "replyMode": reply_mode,
            }
        )

    @app.post(scoped_path(prefix, "api/debate/sessions/<session_id>/summary"))
    def summarize_chat_session(session_id: str):
        if SESSION_ID_RE.fullmatch(session_id) is None:
            return _error("Session not found", 404)
        payload, error = _json_payload()
        if error:
            return error
        del payload
        session = fetch_session(get_db(), session_id)
        if session is None:
            return _error("Session not found", 404)

        participant_reply_count = sum(
            1
            for message in session["messages"]
            if isinstance(message, dict)
            and message.get("kind") == "participant"
            and not is_mock_message(message)
        )
        if participant_reply_count < MIN_SUMMARY_PARTICIPANT_REPLIES:
            return _error(
                f"Summary is available after {MIN_SUMMARY_PARTICIPANT_REPLIES} saved participant replies.",
                409,
            )

        conversation = _saved_discussion_turns(session)
        summarized_message_count = sum(
            1
            for message in session["messages"]
            if isinstance(message, dict)
            and message.get("kind") in {"user", "participant"}
            and isinstance(message.get("body"), str)
            and not is_mock_message(message)
        )
        summary_context = {
            "question": session["question"],
            "sourceRevision": session["revision"],
            "participants": [
                {
                    "id": participant["id"],
                    "name": participant["name"],
                    "identity": participant.get("identity", ""),
                }
                for participant in session["participants"]
            ],
            "conversation": conversation,
        }
        try:
            summary = llm.chat(
                [
                    {"role": "system", "content": _DEBATE_SUMMARY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(summary_context, ensure_ascii=False, separators=(",", ":")),
                    },
                ],
                max_tokens=MAX_CHAT_REPLY_TOKENS,
            )
        except llm.CourseLLMError as exc:
            return _error(f"AI summary is unavailable: {exc}", 503)
        if not isinstance(summary, str) or not summary.strip():
            return _error("AI summary returned no usable text. Please retry.", 503)
        return jsonify(
            {
                "summary": summary.strip(),
                "sourceRevision": session["revision"],
                "summarizedMessageCount": summarized_message_count,
            }
        )

    @app.route(scoped_path(prefix, "api/debate/discussions"), methods=["GET", "POST"])
    def discussions():
        connection = get_db()
        if request.method == "GET":
            return jsonify({"discussions": list_discussions(connection)})
        payload, error = _json_payload()
        if error:
            return error
        try:
            question = _normalize_question(payload)
            perspectives = _normalize_perspectives(payload, question)
            sources = _normalize_sources(payload)
        except ValueError as exc:
            return _error(str(exc))
        discussion = create_discussion(connection, question, perspectives, sources)
        return jsonify({"discussion": discussion}), 201

    @app.get(scoped_path(prefix, "api/debate/discussions/<discussion_id>"))
    def discussion_detail(discussion_id: str):
        requested_version = request.args.get("version")
        try:
            version = int(requested_version) if requested_version else None
        except ValueError:
            return _error("version must be a positive integer")
        if version is not None and version < 1:
            return _error("version must be a positive integer")
        discussion = fetch_discussion(get_db(), discussion_id, version)
        if discussion is None:
            return _error("Discussion not found", 404)
        discussion = reconcile_orphaned_run(get_db(), discussion_id, discussion["version"])
        return jsonify({"discussion": discussion})

    @app.put(scoped_path(prefix, "api/debate/discussions/<discussion_id>"))
    def update_discussion(discussion_id: str):
        payload, error = _json_payload()
        if error:
            return error
        connection = get_db()
        current = fetch_discussion(connection, discussion_id)
        if current is None:
            return _error("Discussion not found", 404)
        if current["status"] != "draft":
            return _error("This discussion already has analysis. Create a new version to change its conditions.", 409)
        try:
            question = _normalize_question(payload, current["question"])
            perspectives = _normalize_perspectives(payload, question)
            sources = _normalize_sources(payload, current["sources"])
        except ValueError as exc:
            return _error(str(exc))
        if not save_draft(connection, discussion_id, current["version"], question, perspectives, sources):
            return _error("This discussion can no longer be edited as a draft. Create a new version instead.", 409)
        return jsonify({"discussion": fetch_discussion(connection, discussion_id)})

    @app.post(scoped_path(prefix, "api/debate/discussions/<discussion_id>/versions"))
    def create_discussion_version(discussion_id: str):
        payload, error = _json_payload()
        if error:
            return error
        connection = get_db()
        base = fetch_discussion(connection, discussion_id)
        if base is None:
            return _error("Discussion not found", 404)
        try:
            base_version = int(payload.get("baseVersion", base["currentVersion"]))
            if base_version < 1:
                raise ValueError
        except (TypeError, ValueError):
            return _error("baseVersion must be a positive integer")
        source = fetch_discussion(connection, discussion_id, base_version)
        if source is None:
            return _error("The selected base version does not exist", 404)
        try:
            question = _normalize_question(payload, source["question"])
            perspectives = _normalize_perspectives(payload, question)
            sources = _normalize_sources(payload, source["sources"])
        except ValueError as exc:
            return _error(str(exc))
        versioned = create_version(
            connection,
            discussion_id,
            base_version,
            question,
            perspectives,
            sources,
            source["followUps"],
        )
        if versioned is None:
            return _error("The selected base version does not exist", 404)
        return jsonify({"discussion": versioned}), 201

    @app.post(scoped_path(prefix, "api/debate/discussions/<discussion_id>/followups"))
    def add_follow_up(discussion_id: str):
        payload, error = _json_payload()
        if error:
            return error
        try:
            follow_up = _string(payload, "text", MAX_FOLLOW_UP_LENGTH, required=True)
            base_version = int(payload.get("baseVersion", 0))
        except (ValueError, TypeError) as exc:
            return _error(str(exc))
        connection = get_db()
        current = fetch_discussion(connection, discussion_id, base_version or None)
        if current is None:
            return _error("Discussion version not found", 404)
        if current["status"] != "completed":
            return _error("Follow-ups are available after a discussion has a conclusion", 409)
        follow_ups = [*current["followUps"], follow_up]
        if len(follow_ups) > 10:
            return _error("A discussion can have at most 10 follow-ups", 400)
        versioned = create_version(
            connection,
            discussion_id,
            current["version"],
            current["question"],
            current["perspectives"],
            current["sources"],
            follow_ups,
        )
        if versioned is None:
            return _error("Discussion version not found", 404)
        return jsonify({"discussion": versioned}), 201

    @app.route(
        scoped_path(prefix, "api/debate/discussions/<discussion_id>/run"),
        methods=["GET", "POST"],
    )
    def discussion_run(discussion_id: str):
        connection = get_db()
        current = fetch_discussion(connection, discussion_id)
        if current is None:
            return _error("Discussion not found", 404)
        if request.method == "GET":
            current = reconcile_orphaned_run(connection, discussion_id, current["version"])
            return jsonify({"discussion": current})
        try:
            started = start_run(current_app._get_current_object(), discussion_id, current["version"], connection)
        except ModelUnavailable as exc:
            return _error(str(exc), 503)
        except LookupError as exc:
            return _error(str(exc), 404)
        except ValueError as exc:
            return _error(str(exc), 409)
        return jsonify({"discussion": started}), 202 if started.get("status") == "running" else 200

    @app.post(scoped_path(prefix, "api/debate/discussions/<discussion_id>/continue"))
    def continue_with_assumptions(discussion_id: str):
        payload, error = _json_payload()
        if error:
            return error
        if payload.get("useAssumptions") is not True:
            return _error("Choose to continue with the listed assumptions to proceed", 400)
        connection = get_db()
        current = fetch_discussion(connection, discussion_id)
        if current is None:
            return _error("Discussion not found", 404)
        if current["run"].get("state") != "needs_input":
            return _error("This discussion is not waiting for clarification", 409)
        update_run(
            connection,
            discussion_id,
            current["version"],
            {"continueWithAssumptions": True, "error": None},
            status="draft",
        )
        try:
            started = start_run(current_app._get_current_object(), discussion_id, current["version"], connection)
        except ModelUnavailable as exc:
            return _error(str(exc), 503)
        except (LookupError, ValueError) as exc:
            return _error(str(exc), 409)
        return jsonify({"discussion": started}), 202

    @app.post(scoped_path(prefix, "api/debate/discussions/<discussion_id>/cancel"))
    def cancel_discussion(discussion_id: str):
        connection = get_db()
        current = fetch_discussion(connection, discussion_id)
        if current is None:
            return _error("Discussion not found", 404)
        current = reconcile_orphaned_run(connection, discussion_id, current["version"])
        if current["run"].get("state") != "running":
            return _error("This discussion is not running", 409)
        run = current["run"]
        run["cancelRequested"] = True
        update_run(connection, discussion_id, current["version"], run, status="running")
        return jsonify({"discussion": fetch_discussion(connection, discussion_id)}), 202

    @app.put(scoped_path(prefix, "api/debate/discussions/<discussion_id>/reflection"))
    def save_discussion_reflection(discussion_id: str):
        payload, error = _json_payload()
        if error:
            return error
        try:
            reflection = _string(payload, "reflection", MAX_TEXT_LENGTH)
            version = int(payload.get("version", 0))
        except (ValueError, TypeError) as exc:
            return _error(str(exc))
        if version < 1:
            return _error("version must be a positive integer")
        connection = get_db()
        if not save_reflection(connection, discussion_id, version, reflection):
            return _error("Reflection can only be saved on a completed discussion version", 409)
        return jsonify({"discussion": fetch_discussion(connection, discussion_id, version)})

    @app.get(scoped_path(prefix, "api/debate/discussions/<discussion_id>/export"))
    def export_discussion(discussion_id: str):
        try:
            version = int(request.args.get("version", "0")) or None
        except ValueError:
            return _error("version must be a positive integer")
        discussion = fetch_discussion(get_db(), discussion_id, version)
        if discussion is None:
            return _error("Discussion not found", 404)
        markdown = request.args.get("format", "md") != "txt"
        full = request.args.get("full", "0") == "1"
        extension = "md" if markdown else "txt"
        body = _export_text(discussion, full=full, markdown=markdown)
        response = make_response(body)
        response.mimetype = "text/markdown" if markdown else "text/plain"
        response.headers["Content-Disposition"] = (
            f"attachment; filename={_safe_filename(discussion['title'], discussion['version'], extension)}"
        )
        return response

    @app.delete(scoped_path(prefix, "api/debate/discussions/<discussion_id>"))
    def remove_discussion(discussion_id: str):
        if not delete_discussion(get_db(), discussion_id):
            return _error("Discussion not found", 404)
        return jsonify({"deleted": True})
