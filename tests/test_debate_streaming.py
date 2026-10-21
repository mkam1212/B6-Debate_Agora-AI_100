import unittest
from unittest.mock import patch

from server.gizmoapp_server import debate_chat, llm


class LiveReplyStreamingTests(unittest.TestCase):
    def setUp(self):
        self.role = {"id": "role-2", "name": "Jack", "roleType": "Challenger", "prompt": "Check the trade-off."}
        self.session = {"question": "Should fares be free?", "participants": [self.role], "messages": []}

    def test_first_delta_reaches_the_caller_before_the_model_produces_the_rest(self):
        produced = []

        def model(*args, **kwargs):
            produced.append("first")
            yield "Grace, "
            produced.append("second")
            yield "that leaves less money for buses."

        with patch.object(debate_chat.llm, "stream_chat", side_effect=model):
            events = debate_chat.stream_role_reply(self.session, self.role)
            self.assertEqual(next(events)["type"], "mode")
            self.assertEqual(next(events), {"type": "metadata", "waitingForUser": False})
            self.assertEqual(next(events), {"type": "delta", "text": "Grace, "})
            self.assertEqual(produced, ["first"])
            self.assertEqual(next(events)["text"], "that leaves less money for buses.")
            self.assertEqual(list(events), [])

    def test_a_mid_stream_failure_does_not_become_a_successful_completed_reply(self):
        def model(*args, **kwargs):
            yield "One concern is "
            raise llm.CourseLLMError("network disconnected")

        with patch.object(debate_chat.llm, "stream_chat", side_effect=model):
            events = debate_chat.stream_role_reply(self.session, self.role)
            next(events)
            next(events)
            self.assertEqual(next(events)["text"], "One concern is ")
            with self.assertRaises(llm.CourseLLMError):
                next(events)

    def test_an_oversized_chunk_is_rejected_before_it_is_forwarded(self):
        with patch.object(debate_chat.llm, "stream_chat", return_value=iter(["A point.", "x" * 2001])):
            events = debate_chat.stream_role_reply(self.session, self.role)
            next(events)
            next(events)
            self.assertEqual(next(events)["text"], "A point.")
            with self.assertRaises(llm.CourseLLMError):
                next(events)
