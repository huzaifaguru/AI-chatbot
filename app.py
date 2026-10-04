"""Flask backend for Nova, a Gemini-powered chatbot.

The browser keeps the conversation history and sends it on every request.
This server validates it, applies a rate limit, trims it to fit the context
budget, adds the system prompt, and forwards it to the Gemini API. Replies are
streamed back to the browser as Server-Sent Events.
"""

import itertools
import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from google import genai
from google.genai import errors, types

# Read GEMINI_API_KEY etc. from .env. override=True makes edits to .env win
# over values already in the environment (e.g. from the debug auto-reloader).
load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent

# ---- Configuration (all overridable from .env) ----
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
SYSTEM_PROMPT_FILE = Path(os.getenv("SYSTEM_PROMPT_FILE", BASE_DIR / "prompts" / "system_prompt.md"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "15"))
MAX_HISTORY_MESSAGES = 20  # never send more than the last N messages...
MAX_HISTORY_CHARS = 16000  # ...or more than this many characters in total
MAX_MESSAGE_CHARS = 4000
EMPTY_REPLY = "The model returned an empty reply (it may have been blocked by safety filters)."

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chatbot")

SYSTEM_PROMPT = SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()

app = Flask(__name__)
_client = None


class ConfigError(Exception):
    """Raised when the server isn't set up correctly (e.g. no API key)."""


def get_client():
    """Create the Gemini client once, on first use."""
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ConfigError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
        _client = genai.Client(
            api_key=api_key,
            # The free tier is sometimes overloaded (503). Retry a few times with
            # increasing delays before giving up and showing an error.
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=4,
                    initial_delay=1,
                    max_delay=8,
                    http_status_codes=[500, 502, 503, 504],
                )
            ),
        )
    return _client


# ---- Rate limiting: at most N requests per minute per IP address ----
_request_times = defaultdict(deque)
_rate_lock = threading.Lock()


def is_rate_limited(ip):
    now = time.monotonic()
    with _rate_lock:
        times = _request_times[ip]
        while times and now - times[0] > 60:
            times.popleft()
        if len(times) >= RATE_LIMIT_PER_MINUTE:
            return True
        times.append(now)
        return False


# ---- Request handling helpers ----
def validate_messages(messages):
    """Return an error string if the request body is malformed, else None."""
    if not isinstance(messages, list) or not messages:
        return "'messages' must be a non-empty list."
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            return "Each message needs a role of 'user' or 'assistant'."
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            return "Each message needs non-empty text content."
        if len(content) > MAX_MESSAGE_CHARS:
            return f"Messages must be under {MAX_MESSAGE_CHARS} characters."
    if messages[-1]["role"] != "user":
        return "The last message must come from the user."
    return None


def trim_history(messages):
    """Keep the newest messages that fit in the message and character budgets.

    The newest message is always kept. Older ones are dropped first, so long
    chats keep working instead of growing until the request fails.
    """
    kept, total = [], 0
    for m in reversed(messages[-MAX_HISTORY_MESSAGES:]):
        total += len(m["content"])
        if kept and total > MAX_HISTORY_CHARS:
            break
        kept.append(m)
    kept.reverse()
    while kept[0]["role"] != "user":  # the conversation should start with the user
        kept.pop(0)
    return kept


def to_gemini_contents(messages):
    """Convert [{role: 'user'|'assistant', content}] into Gemini's format.

    Gemini calls the assistant role "model".
    """
    return [
        types.Content(
            role="model" if m["role"] == "assistant" else "user",
            parts=[types.Part(text=m["content"])],
        )
        for m in messages
    ]


def gemini_request(messages):
    """Arguments shared by the normal and the streaming Gemini calls."""
    return dict(
        model=MODEL,
        contents=to_gemini_contents(messages),
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            # We don't use tools, so turn off automatic function calling.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )


def describe_error(exc):
    """Turn an exception into a (user-friendly message, HTTP status) pair."""
    if isinstance(exc, ConfigError):
        return str(exc), 500
    if isinstance(exc, errors.ClientError):  # 4xx from Gemini
        log.warning("Gemini client error: %s", exc)
        if exc.code == 429:
            return "Gemini's free-tier limit was reached. Wait a minute and try again.", 502
        if exc.code in (400, 401, 403) and "key" in str(exc).lower():
            return "The Gemini API key was rejected. Check GEMINI_API_KEY in your .env file.", 502
        if exc.code == 404:
            return f"Model '{MODEL}' was not found. Set GEMINI_MODEL in .env to a valid model.", 502
        return f"Gemini rejected the request ({exc.code}): {exc.message}", 502
    if isinstance(exc, errors.ServerError):  # 5xx from Gemini, after retries
        log.warning("Gemini server error: %s", exc)
        return "Gemini is busy right now. Please try again in a moment.", 502
    log.exception("Unexpected error calling Gemini")
    return "Couldn't reach the AI service. Check your internet connection.", 502


def parse_chat_request():
    """Apply the rate limit and validate the body.

    Returns (messages, None) on success or (None, error_response) on failure.
    """
    if is_rate_limited(request.remote_addr):
        log.info("Rate limited %s", request.remote_addr)
        return None, (jsonify(error="You're sending messages too fast. Please wait a minute."), 429)
    messages = (request.get_json(silent=True) or {}).get("messages")
    error = validate_messages(messages)
    if error:
        return None, (jsonify(error=error), 400)
    return trim_history(messages), None


def sse(data):
    """Format one Server-Sent Event."""
    return f"data: {json.dumps(data)}\n\n"


# ---- Routes ----
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    """Quick check that the server is up and configured."""
    return jsonify(status="ok", model=MODEL, api_key_configured=bool(os.getenv("GEMINI_API_KEY")))


@app.post("/api/chat")
def chat():
    """Return the whole reply at once as JSON."""
    messages, error_response = parse_chat_request()
    if error_response:
        return error_response

    started = time.monotonic()
    try:
        response = get_client().models.generate_content(**gemini_request(messages))
    except Exception as e:
        message, status = describe_error(e)
        return jsonify(error=message), status

    if not response.text:
        return jsonify(error=EMPTY_REPLY), 502
    log.info("chat ok messages=%d latency=%.2fs", len(messages), time.monotonic() - started)
    return jsonify(reply=response.text, model=MODEL)


@app.post("/api/chat/stream")
def chat_stream():
    """Stream the reply piece by piece as Server-Sent Events.

    Each event is `data: {...}` with one of:
      {"text": "..."}   the next piece of the reply
      {"done": true}    the reply finished
      {"error": "..."}  something went wrong after streaming started
    """
    messages, error_response = parse_chat_request()
    if error_response:
        return error_response

    started = time.monotonic()
    try:
        stream = get_client().models.generate_content_stream(**gemini_request(messages))
        # Wait for the first chunk here, so problems like a bad key or an
        # overloaded model come back as a normal HTTP error response.
        first = next(stream, None)
    except Exception as e:
        message, status = describe_error(e)
        return jsonify(error=message), status

    def events():
        got_text = False
        try:
            chunks = itertools.chain([first], stream) if first is not None else stream
            for chunk in chunks:
                if chunk.text:
                    got_text = True
                    yield sse({"text": chunk.text})
        except Exception as e:  # the connection dropped mid-reply
            yield sse({"error": describe_error(e)[0]})
            return
        if not got_text:
            yield sse({"error": EMPTY_REPLY})
            return
        log.info("stream ok messages=%d latency=%.2fs", len(messages), time.monotonic() - started)
        yield sse({"done": True})

    return Response(
        stream_with_context(events()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
