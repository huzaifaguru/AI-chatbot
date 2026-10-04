"""Backend tests. The Gemini client is mocked, so no API key or network is needed.

Run with:  python -m unittest discover tests
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from google.genai import errors

import app as chatbot

HI = [{"role": "user", "content": "hi"}]


def fake_client(reply="Hello!", exc=None, chunks=None):
    """A stand-in for the Gemini client.

    reply  -> text returned by generate_content
    exc    -> exception raised by both calls
    chunks -> list of texts (or an exception) yielded by generate_content_stream
    """
    client = MagicMock()
    if exc:
        client.models.generate_content.side_effect = exc
        client.models.generate_content_stream.side_effect = exc
        return client
    client.models.generate_content.return_value = MagicMock(text=reply)

    def stream(**kwargs):
        for c in chunks or []:
            if isinstance(c, Exception):
                raise c
            yield MagicMock(text=c)

    client.models.generate_content_stream.side_effect = stream
    return client


def server_error():
    return errors.ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})


def parse_sse(body):
    return [json.loads(line[6:]) for line in body.decode().split("\n\n") if line.startswith("data: ")]


class ChatTestCase(unittest.TestCase):
    def setUp(self):
        self.http = chatbot.app.test_client()
        chatbot._request_times.clear()  # reset the rate limiter between tests

    def post(self, messages, path="/api/chat"):
        return self.http.post(path, json={"messages": messages})


class PageTests(ChatTestCase):
    def test_index_page_loads(self):
        res = self.http.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Nova", res.data)

    def test_health(self):
        data = self.http.get("/api/health").get_json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["model"], chatbot.MODEL)
        self.assertIn("api_key_configured", data)


class SystemPromptTests(unittest.TestCase):
    def test_prompt_loaded_from_file(self):
        self.assertIn("You are Nova", chatbot.SYSTEM_PROMPT)
        self.assertEqual(chatbot.SYSTEM_PROMPT, chatbot.SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip())


class ChatEndpointTests(ChatTestCase):
    def test_returns_reply_and_sends_history_with_system_prompt(self):
        client = fake_client("Hi there")
        with patch.object(chatbot, "get_client", return_value=client):
            res = self.post([
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi! How can I help?"},
                {"role": "user", "content": "What's 2+2?"},
            ])

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["reply"], "Hi there")
        kwargs = client.models.generate_content.call_args.kwargs
        self.assertEqual([c.role for c in kwargs["contents"]], ["user", "model", "user"])
        self.assertEqual(kwargs["config"].system_instruction, chatbot.SYSTEM_PROMPT)

    def test_rejects_bad_input(self):
        bad_inputs = [
            None,
            [],
            [{"role": "user", "content": "   "}],
            [{"role": "system", "content": "hi"}],
            [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}],
            [{"role": "user", "content": "x" * (chatbot.MAX_MESSAGE_CHARS + 1)}],
        ]
        for messages in bad_inputs:
            with self.subTest(messages=str(messages)[:60]):
                self.assertEqual(self.post(messages).status_code, 400)

    def test_missing_api_key(self):
        with patch.object(chatbot, "_client", None), patch.dict("os.environ", {}, clear=True):
            res = self.post(HI)
        self.assertEqual(res.status_code, 500)
        self.assertIn("GEMINI_API_KEY", res.get_json()["error"])

    def test_rate_limit_error_from_gemini(self):
        exc = errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        with patch.object(chatbot, "get_client", return_value=fake_client(exc=exc)):
            res = self.post(HI)
        self.assertEqual(res.status_code, 502)
        self.assertIn("limit", res.get_json()["error"])

    def test_server_error(self):
        with patch.object(chatbot, "get_client", return_value=fake_client(exc=server_error())):
            res = self.post(HI)
        self.assertEqual(res.status_code, 502)
        self.assertIn("busy", res.get_json()["error"])

    def test_empty_reply(self):
        with patch.object(chatbot, "get_client", return_value=fake_client(reply=None)):
            res = self.post(HI)
        self.assertEqual(res.status_code, 502)
        self.assertIn("empty", res.get_json()["error"])


class HistoryTrimmingTests(unittest.TestCase):
    def test_keeps_last_n_messages(self):
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
        kept = chatbot.trim_history(messages)
        self.assertEqual(len(kept), chatbot.MAX_HISTORY_MESSAGES)
        self.assertEqual(kept[-1]["content"], "msg 49")

    def test_respects_character_budget(self):
        big = "x" * 3000
        messages = []
        for _ in range(9):
            messages += [{"role": "user", "content": big}, {"role": "assistant", "content": big}]
        messages.append({"role": "user", "content": "latest"})
        kept = chatbot.trim_history(messages)
        self.assertLessEqual(sum(len(m["content"]) for m in kept), chatbot.MAX_HISTORY_CHARS)
        self.assertEqual(kept[-1]["content"], "latest")
        self.assertEqual(kept[0]["role"], "user")  # never starts with an assistant turn

    def test_always_keeps_newest_message(self):
        messages = [{"role": "user", "content": "x" * chatbot.MAX_MESSAGE_CHARS}] * 10
        self.assertGreaterEqual(len(chatbot.trim_history(messages)), 1)


class RateLimitTests(ChatTestCase):
    def test_blocks_after_limit(self):
        with patch.object(chatbot, "RATE_LIMIT_PER_MINUTE", 2), \
             patch.object(chatbot, "get_client", return_value=fake_client()):
            codes = [self.post(HI).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


class StreamEndpointTests(ChatTestCase):
    def stream(self, client, messages=HI):
        with patch.object(chatbot, "get_client", return_value=client):
            return self.post(messages, path="/api/chat/stream")

    def test_streams_text_then_done(self):
        res = self.stream(fake_client(chunks=["Hel", "lo", " world"]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "text/event-stream")
        events = parse_sse(res.data)
        self.assertEqual("".join(e.get("text", "") for e in events), "Hello world")
        self.assertEqual(events[-1], {"done": True})

    def test_error_before_streaming_is_a_normal_http_error(self):
        res = self.stream(fake_client(chunks=[server_error()]))
        self.assertEqual(res.status_code, 502)
        self.assertIn("busy", res.get_json()["error"])

    def test_error_mid_stream_sends_error_event(self):
        res = self.stream(fake_client(chunks=["partial", server_error()]))
        events = parse_sse(res.data)
        self.assertEqual(events[0], {"text": "partial"})
        self.assertIn("error", events[-1])

    def test_empty_stream_sends_error_event(self):
        events = parse_sse(self.stream(fake_client(chunks=[])).data)
        self.assertEqual(len(events), 1)
        self.assertIn("empty", events[0]["error"])

    def test_validation_applies_to_stream(self):
        self.assertEqual(self.stream(fake_client(), messages=[]).status_code, 400)


if __name__ == "__main__":
    unittest.main()
