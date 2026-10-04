"""Backend tests. The Gemini client is mocked, so no API key or network is needed.

Run with:  python -m unittest discover tests
"""

import unittest
from unittest.mock import MagicMock, patch

from google.genai import errors

import app as chatbot


def fake_client(reply="Hello!", exc=None):
    client = MagicMock()
    if exc:
        client.models.generate_content.side_effect = exc
    else:
        client.models.generate_content.return_value = MagicMock(text=reply)
    return client


class ChatEndpointTests(unittest.TestCase):
    def setUp(self):
        self.http = chatbot.app.test_client()

    def post(self, messages):
        return self.http.post("/api/chat", json={"messages": messages})

    def test_index_page_loads(self):
        res = self.http.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"AI Chatbot", res.data)

    def test_returns_model_reply_and_sends_history(self):
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
        roles = [c.role for c in kwargs["contents"]]
        self.assertEqual(roles, ["user", "model", "user"])  # assistant -> model
        self.assertEqual(kwargs["config"].system_instruction, chatbot.SYSTEM_PROMPT)

    def test_history_is_trimmed(self):
        client = fake_client()
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
        with patch.object(chatbot, "get_client", return_value=client):
            self.post(messages)
        sent = client.models.generate_content.call_args.kwargs["contents"]
        self.assertEqual(len(sent), chatbot.MAX_HISTORY)
        self.assertEqual(sent[-1].parts[0].text, "msg 49")

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
            res = self.post([{"role": "user", "content": "hi"}])
        self.assertEqual(res.status_code, 500)
        self.assertIn("GEMINI_API_KEY", res.get_json()["error"])

    def test_rate_limit_error(self):
        exc = errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        with patch.object(chatbot, "get_client", return_value=fake_client(exc=exc)):
            res = self.post([{"role": "user", "content": "hi"}])
        self.assertEqual(res.status_code, 502)
        self.assertIn("Rate limit", res.get_json()["error"])

    def test_server_error(self):
        exc = errors.ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})
        with patch.object(chatbot, "get_client", return_value=fake_client(exc=exc)):
            res = self.post([{"role": "user", "content": "hi"}])
        self.assertEqual(res.status_code, 502)

    def test_empty_reply(self):
        with patch.object(chatbot, "get_client", return_value=fake_client(reply=None)):
            res = self.post([{"role": "user", "content": "hi"}])
        self.assertEqual(res.status_code, 502)
        self.assertIn("empty", res.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
