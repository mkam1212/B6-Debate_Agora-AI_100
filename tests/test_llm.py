from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server.gizmoapp_server import llm


class CourseLLMTests(unittest.TestCase):
    def setUp(self):
        llm._client = None
        llm._client_signature = None

    def test_missing_gateway_or_model_fails_closed(self):
        with patch.dict(os.environ, {"GIZMO_LLM_API_KEY": "test-key"}, clear=True), patch.object(llm, "OpenAI", Mock()):
            with self.assertRaisesRegex(llm.CourseLLMError, "GIZMO_LLM_BASE_URL"):
                llm.ask("hello")

    def test_client_has_bounded_timeout_and_retry(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"))]
        )
        client = Mock()
        client.chat.completions.create.return_value = completion
        client_factory = Mock(return_value=client)
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
            "GIZMO_LLM_TIMEOUT_SECONDS": "12",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "OpenAI", client_factory):
            self.assertEqual(llm.ask("hello", max_tokens=100), "Hello")

        client_factory.assert_called_once_with(
            api_key="test-key",
            base_url="https://gateway.example/v1",
            timeout=12.0,
            max_retries=1,
        )
        client.chat.completions.create.assert_called_once_with(
            model="course-model",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
            reasoning_effort="none",
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

    def test_stream_chat_yields_text_deltas_in_order(self):
        chunks = [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))]),
            SimpleNamespace(choices=[]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=" there."))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop")]),
        ]
        client = Mock()
        client.chat.completions.create.return_value = iter(chunks)
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
        }
        completion_events = []
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "OpenAI", Mock(return_value=client)):
            self.assertEqual(
                list(
                    llm.stream_chat(
                        [{"role": "user", "content": "hello"}],
                        max_tokens=100,
                        on_completion=completion_events.append,
                    )
                ),
                ["Hello", " there."],
            )

        self.assertEqual(
            completion_events,
            [
                {
                    "finish_reason": "stop",
                    "output_chars": len("Hello there."),
                    "token_parameter": "max_tokens",
                    "reasoning_hints": "extra_body+reasoning_effort",
                    "failure": None,
                }
            ],
        )

        client.chat.completions.create.assert_called_once_with(
            model="course-model",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
            stream=True,
            reasoning_effort="none",
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

    def test_stream_chat_surfaces_explicit_token_limit_completion(self):
        chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="WAITING_FOR_USER: tr"), finish_reason=None)]
            ),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="length")]),
        ]
        client = Mock()
        client.chat.completions.create.return_value = iter(chunks)
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
        }
        completion_events = []
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "OpenAI", Mock(return_value=client)):
            with self.assertRaisesRegex(llm.CourseLLMTruncatedError, "token limit"):
                list(
                    llm.stream_chat(
                        [{"role": "user", "content": "hello"}],
                        max_tokens=320,
                        on_completion=completion_events.append,
                    )
                )

        client.chat.completions.create.assert_called_once()
        self.assertEqual(completion_events[0]["finish_reason"], "length")
        self.assertEqual(completion_events[0]["output_chars"], len("WAITING_FOR_USER: tr"))
        self.assertEqual(completion_events[0]["failure"], "truncated")

    def test_chat_completion_observer_reports_finish_reason_and_output_size(self):
        completion = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="A complete response"),
                    finish_reason="stop",
                )
            ]
        )
        client = Mock()
        client.chat.completions.create.return_value = completion
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
        }
        completion_events = []
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "OpenAI", Mock(return_value=client)):
            result = llm.chat(
                [{"role": "user", "content": "hello"}],
                max_tokens=320,
                on_completion=completion_events.append,
            )

        self.assertEqual(result, "A complete response")
        self.assertEqual(
            completion_events,
            [
                {
                    "finish_reason": "stop",
                    "output_chars": len("A complete response"),
                    "token_parameter": "max_tokens",
                    "reasoning_hints": "extra_body+reasoning_effort",
                    "failure": None,
                }
            ],
        )

    def test_chat_surfaces_explicit_token_limit_completion(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="partial"), finish_reason="length")]
        )
        client = Mock()
        client.chat.completions.create.return_value = completion
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
        }
        completion_events = []
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "OpenAI", Mock(return_value=client)):
            with self.assertRaisesRegex(llm.CourseLLMTruncatedError, "token limit"):
                llm.chat(
                    [{"role": "user", "content": "hello"}],
                    max_tokens=320,
                    on_completion=completion_events.append,
                )

        client.chat.completions.create.assert_called_once()
        self.assertEqual(completion_events[0]["finish_reason"], "length")
        self.assertEqual(completion_events[0]["output_chars"], len("partial"))
        self.assertEqual(completion_events[0]["failure"], "truncated")

    def test_stream_chat_reports_an_empty_stream(self):
        client = Mock()
        client.chat.completions.create.return_value = iter([])
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "OpenAI", Mock(return_value=client)):
            with self.assertRaisesRegex(llm.CourseLLMError, "empty response"):
                list(llm.stream_chat([{"role": "user", "content": "hello"}]))

    def _gateway_rejecting(self, how_many_hints):
        """A client that answers 400 until the first `how_many_hints` are gone."""
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"))]
        )
        rejections = []
        for _ in range(how_many_hints):
            rejection = Exception("unrecognised request field")
            rejection.status_code = 400
            rejections.append(rejection)
        client = Mock()
        client.chat.completions.create.side_effect = [*rejections, completion]
        return client

    def _ask_through(self, client):
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "OpenAI", Mock(return_value=client)):
            return llm.ask("hello", max_tokens=100)

    def test_gateway_rejecting_chat_template_kwargs_keeps_reasoning_effort(self):
        """One hint is dropped at a time, so a partial gateway still gets a hint."""
        client = self._gateway_rejecting(1)
        self.assertEqual(self._ask_through(client), "Hello")

        self.assertEqual(client.chat.completions.create.call_count, 2)
        client.chat.completions.create.assert_called_with(
            model="course-model",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
            reasoning_effort="none",
        )

    def test_unsupported_max_tokens_retries_with_max_completion_tokens(self):
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"))]
        )
        rejection = Exception("unsupported parameter")
        rejection.status_code = 400
        rejection.body = {
            "error": {"code": "unsupported_parameter", "param": "max_tokens"}
        }
        client = Mock()
        client.chat.completions.create.side_effect = [rejection, completion]

        self.assertEqual(self._ask_through(client), "Hello")

        calls = client.chat.completions.create.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs["max_tokens"], 100)
        self.assertNotIn("max_completion_tokens", calls[0].kwargs)
        self.assertEqual(calls[1].kwargs["max_completion_tokens"], 100)
        self.assertNotIn("max_tokens", calls[1].kwargs)

    def test_gateway_rejecting_every_hint_falls_back_to_a_plain_request(self):
        client = self._gateway_rejecting(2)
        self.assertEqual(self._ask_through(client), "Hello")

        self.assertEqual(client.chat.completions.create.call_count, 3)
        client.chat.completions.create.assert_called_with(
            model="course-model",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=100,
        )

    def test_a_plain_request_that_still_fails_is_not_retried_forever(self):
        client = self._gateway_rejecting(3)
        with self.assertRaisesRegex(llm.CourseLLMError, "temporarily unavailable"):
            self._ask_through(client)
        self.assertEqual(client.chat.completions.create.call_count, 3)

    def test_gateway_outage_is_not_retried_without_the_hint(self):
        outage = Exception("service unavailable")
        outage.status_code = 503
        client = Mock()
        client.chat.completions.create.side_effect = outage
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "OpenAI", Mock(return_value=client)):
            with self.assertRaisesRegex(llm.CourseLLMError, "temporarily unavailable"):
                llm.ask("hello", max_tokens=100)

        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_invalid_prompts_messages_and_token_limits_are_rejected(self):
        for call in (
            lambda: llm.ask(""),
            lambda: llm.chat([]),
            lambda: llm.chat([{"role": "tool", "content": "bad"}]),
            lambda: llm.ask("hello", max_tokens=0),
            lambda: llm.ask("hello", max_tokens=True),
        ):
            with self.subTest(call=call), self.assertRaises(llm.CourseLLMError):
                call()

    def test_provider_errors_are_safe_and_chained(self):
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("secret provider detail")
        environment = {
            "GIZMO_LLM_API_KEY": "test-key",
            "GIZMO_LLM_BASE_URL": "https://gateway.example/v1",
            "GIZMO_LLM_MODEL": "course-model",
        }
        with patch.dict(os.environ, environment, clear=True), patch.object(llm, "_get_client", return_value=client):
            with self.assertRaisesRegex(llm.CourseLLMError, "temporarily unavailable") as caught:
                llm.ask("hello")
        self.assertNotIn("secret provider detail", str(caught.exception))
        self.assertIsNotNone(caught.exception.__cause__)


if __name__ == "__main__":
    unittest.main()
