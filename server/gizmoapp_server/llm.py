"""Small fail-closed helper for the AI100 course model gateway."""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from typing import Any, Callable

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - dependency missing until installation
    OpenAI = None

DEFAULT_MAX_TOKENS = 2048
MAX_ALLOWED_TOKENS = 4096
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 55.0
ALLOWED_ROLES = frozenset({"system", "user", "assistant"})
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
# Tried in order, most specific first, falling back only on a 400.
NO_THINKING_ATTEMPTS = (
    {"reasoning_effort": "none", "extra_body": NO_THINKING},
    {"reasoning_effort": "none"},
    {},
)
CompletionObserver = Callable[[dict[str, Any]], None]


class CourseLLMError(RuntimeError):
    """A safe, user-displayable course-model failure."""


class CourseLLMTruncatedError(CourseLLMError):
    """The gateway explicitly reports that generation stopped at its token limit."""


_client = None
_client_signature: tuple[str, str, float] | None = None


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise CourseLLMError(
            f"{name} is not set. The AI100 platform supplies all course-model settings; "
            "restart the app or server session and try again."
        )
    return value


def _timeout_seconds() -> float:
    raw_value = os.environ.get("GIZMO_LLM_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise CourseLLMError("GIZMO_LLM_TIMEOUT_SECONDS must be numeric.") from exc
    if value <= 0 or value > MAX_TIMEOUT_SECONDS:
        raise CourseLLMError(
            f"GIZMO_LLM_TIMEOUT_SECONDS must be greater than zero and at most {MAX_TIMEOUT_SECONDS:g}."
        )
    return value


def _get_client():
    global _client, _client_signature
    if OpenAI is None:
        raise CourseLLMError(
            "The 'openai' package is not installed. Run: pip install -r server/requirements.txt"
        )

    api_key = _required_environment("GIZMO_LLM_API_KEY")
    base_url = _required_environment("GIZMO_LLM_BASE_URL")
    timeout = _timeout_seconds()
    signature = (api_key, base_url, timeout)
    if _client is None or _client_signature != signature:
        _client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=1,
        )
        _client_signature = signature
    return _client


def model_name() -> str:
    """Return the model id explicitly assigned by the course platform."""
    return _required_environment("GIZMO_LLM_MODEL")


def is_configured() -> bool:
    """Report whether a server-side model call can be attempted, without exposing settings."""
    return OpenAI is not None and all(
        os.environ.get(name, "").strip()
        for name in ("GIZMO_LLM_API_KEY", "GIZMO_LLM_BASE_URL", "GIZMO_LLM_MODEL")
    )


def _validate_max_tokens(max_tokens: int) -> int:
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
        raise CourseLLMError("max_tokens must be an integer.")
    if max_tokens < 1 or max_tokens > MAX_ALLOWED_TOKENS:
        raise CourseLLMError(f"max_tokens must be between 1 and {MAX_ALLOWED_TOKENS}.")
    return max_tokens


def _validate_messages(messages: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence) or not messages:
        raise CourseLLMError("messages must be a non-empty sequence of message objects.")

    validated: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise CourseLLMError("each message must be an object with role and content strings.")
        role = message.get("role")
        content = message.get("content")
        if role not in ALLOWED_ROLES or not isinstance(content, str) or not content.strip():
            raise CourseLLMError(
                "each message must have a system, user, or assistant role and non-empty text content."
            )
        validated.append({"role": role, "content": content})
    return validated


def _create_completion(client: Any, **arguments: Any) -> tuple[Any, dict[str, str]]:
    """Create a completion, asking the model not to think out loud first.

    The course model is a reasoning model: left alone it spends the whole
    max_tokens budget on hidden reasoning and returns empty content, so the
    hints ask it to answer directly. reasoning_effort is the standard OpenAI
    parameter; chat_template_kwargs is what some gateways expect instead.
    Gateways reject what they do not recognise with a 400, so drop one at a time
    rather than all of them at once: a gateway that understands only
    reasoning_effort still gets a usable answer instead of an empty one.
    Newer OpenAI models may require max_completion_tokens; switch to it only
    when the API explicitly rejects max_tokens as unsupported.
    """
    last = len(NO_THINKING_ATTEMPTS) - 1
    arguments = dict(arguments)
    position = 0
    while position <= last:
        hints = NO_THINKING_ATTEMPTS[position]
        try:
            response = client.chat.completions.create(**arguments, **hints)
            request_metadata = {
                "token_parameter": (
                    "max_completion_tokens" if "max_completion_tokens" in arguments else "max_tokens"
                ),
                "reasoning_hints": "+".join(sorted(hints)) or "none",
            }
            return response, request_metadata
        except Exception as exc:
            if "max_tokens" in arguments and _is_unsupported_parameter(exc, "max_tokens"):
                arguments["max_completion_tokens"] = arguments.pop("max_tokens")
                continue
            if position < last and getattr(exc, "status_code", None) == 400:
                position += 1
                continue
            raise


def _is_unsupported_parameter(exc: Exception, parameter: str) -> bool:
    """Match the structured OpenAI 400 used when a request field is unsupported."""
    if getattr(exc, "status_code", None) != 400:
        return False
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    details = body.get("error", body)
    return (
        isinstance(details, dict)
        and details.get("code") == "unsupported_parameter"
        and details.get("param") == parameter
    )


def _notify_completion(
    observer: CompletionObserver | None,
    *,
    finish_reason: Any = None,
    output_chars: int = 0,
    request_metadata: dict[str, str] | None = None,
    failure: str | None = None,
) -> None:
    if observer is None:
        return
    metadata = {
        "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
        "output_chars": max(0, int(output_chars)),
        "token_parameter": (request_metadata or {}).get("token_parameter", "unknown"),
        "reasoning_hints": (request_metadata or {}).get("reasoning_hints", "unknown"),
        "failure": failure,
    }
    try:
        observer(metadata)
    except Exception:
        # Diagnostic callbacks must never change a model request's behavior.
        return


def chat(
    messages: Sequence[dict[str, Any]],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    *,
    on_completion: CompletionObserver | None = None,
) -> str:
    """Send validated messages and return the assistant's text response."""
    validated_messages = _validate_messages(messages)
    validated_max_tokens = _validate_max_tokens(max_tokens)
    request_metadata: dict[str, str] = {}
    try:
        client = _get_client()
        response, request_metadata = _create_completion(
            client,
            model=model_name(),
            messages=validated_messages,
            max_tokens=validated_max_tokens,
        )
    except CourseLLMError:
        _notify_completion(on_completion, request_metadata=request_metadata, failure="course_llm_error")
        raise
    except Exception as exc:
        _notify_completion(on_completion, request_metadata=request_metadata, failure="transport_error")
        raise CourseLLMError(
            "The course model is temporarily unavailable. Please wait a moment and try again."
        ) from exc

    if not response.choices:
        _notify_completion(
            on_completion,
            request_metadata=request_metadata,
            failure="empty_response",
        )
        raise CourseLLMError("The course model returned no response. Please try again.")
    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    content = getattr(getattr(choice, "message", None), "content", None)
    output_chars = len(content) if isinstance(content, str) else 0
    failure = "truncated" if finish_reason == "length" else None
    if not isinstance(content, str) or not content.strip():
        failure = "empty_response"
    _notify_completion(
        on_completion,
        finish_reason=finish_reason,
        output_chars=output_chars,
        request_metadata=request_metadata,
        failure=failure,
    )
    if finish_reason == "length":
        raise CourseLLMTruncatedError(
            "The course model reached its reply token limit before completing the response. Please retry."
        )
    if not isinstance(content, str) or not content.strip():
        raise CourseLLMError("The course model returned an empty response. Please try again.")
    return content


def stream_chat(
    messages: Sequence[dict[str, Any]],
    max_tokens: int = DEFAULT_MAX_TOKENS,
    *,
    on_completion: CompletionObserver | None = None,
) -> Iterator[str]:
    """Yield assistant text as the course model produces it."""
    validated_messages = _validate_messages(messages)
    validated_max_tokens = _validate_max_tokens(max_tokens)
    request_metadata: dict[str, str] = {}
    finish_reason = None
    output_chars = 0
    completion_reported = False

    def report(failure: str | None = None) -> None:
        nonlocal completion_reported
        if completion_reported:
            return
        completion_reported = True
        _notify_completion(
            on_completion,
            finish_reason=finish_reason,
            output_chars=output_chars,
            request_metadata=request_metadata,
            failure=failure,
        )

    try:
        client = _get_client()
        response, request_metadata = _create_completion(
            client,
            model=model_name(),
            messages=validated_messages,
            max_tokens=validated_max_tokens,
            stream=True,
        )
        emitted = False
        for chunk in response:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            choice = choices[0]
            current_finish_reason = getattr(choice, "finish_reason", None)
            if isinstance(current_finish_reason, str):
                finish_reason = current_finish_reason
            if finish_reason == "length":
                report("truncated")
                raise CourseLLMTruncatedError(
                    "The course model reached its reply token limit before completing the response. Please retry."
                )
            delta = getattr(choice, "delta", None)
            content = getattr(delta, "content", None)
            if isinstance(content, str) and content:
                emitted = True
                output_chars += len(content)
                yield content
        if not emitted:
            report("empty_response")
            raise CourseLLMError("The course model returned an empty response. Please try again.")
        report()
    except CourseLLMError:
        report("course_llm_error")
        raise
    except Exception as exc:
        report("transport_error")
        raise CourseLLMError(
            "The course model is temporarily unavailable. Please wait a moment and try again."
        ) from exc


def ask(prompt: str, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Send one user prompt to the course model."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise CourseLLMError("prompt must be non-empty text.")
    return chat([{"role": "user", "content": prompt}], max_tokens=max_tokens)
