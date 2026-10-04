"""Flask backend for a simple Gemini-powered chatbot.

The browser keeps the conversation history and sends the whole thing on every
request. This server adds the system prompt, forwards the conversation to the
Gemini API, and returns the model's reply as JSON.
"""

import os

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from google import genai
from google.genai import errors, types

# Read GEMINI_API_KEY etc. from .env. override=True makes edits to .env win
# over values already in the environment (e.g. from the debug auto-reloader).
load_dotenv(override=True)

MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "You are a friendly, helpful assistant. Answer clearly and concisely. "
    "Use Markdown (lists, bold, code blocks) when it makes the answer easier to read.",
)
MAX_HISTORY = 20  # only send the last N messages to keep requests small
MAX_MESSAGE_CHARS = 4000

app = Flask(__name__)
_client = None


def get_client():
    """Create the Gemini client once, on first use."""
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your key."
            )
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


def to_gemini_contents(messages):
    """Convert [{role: 'user'|'assistant', content}] into Gemini's format.

    Gemini calls the assistant role "model".
    """
    return [
        types.Content(
            role="model" if m["role"] == "assistant" else "user",
            parts=[types.Part(text=m["content"])],
        )
        for m in messages[-MAX_HISTORY:]
    ]


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


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    messages = data.get("messages")

    error = validate_messages(messages)
    if error:
        return jsonify(error=error), 400

    try:
        response = get_client().models.generate_content(
            model=MODEL,
            contents=to_gemini_contents(messages),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                # We don't use tools, so turn off automatic function calling.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
    except RuntimeError as e:  # missing API key
        return jsonify(error=str(e)), 500
    except errors.ClientError as e:  # 4xx from Gemini
        if e.code == 429:
            msg = "Rate limit reached on the free tier. Wait a minute and try again."
        elif e.code in (400, 401, 403) and "key" in str(e).lower():
            msg = "The Gemini API key was rejected. Check GEMINI_API_KEY in your .env file."
        elif e.code == 404:
            msg = f"Model '{MODEL}' was not found. Set GEMINI_MODEL in .env to a valid model."
        else:
            msg = f"Gemini rejected the request ({e.code}): {e.message}"
        app.logger.warning("Gemini client error: %s", e)
        return jsonify(error=msg), 502
    except errors.ServerError as e:  # 5xx from Gemini
        app.logger.warning("Gemini server error: %s", e)
        return jsonify(error="Gemini is having problems right now. Please try again."), 502
    except Exception:
        app.logger.exception("Unexpected error calling Gemini")
        return jsonify(error="Couldn't reach the AI service. Check your internet connection."), 502

    reply = response.text
    if not reply:
        return jsonify(
            error="The model returned an empty reply (it may have been blocked by safety filters)."
        ), 502
    return jsonify(reply=reply, model=MODEL)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
