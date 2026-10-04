# AI Chatbot

A web chatbot built with Python (Flask) and Google's Gemini API. You type a message (or say it), the server sends it to Gemini along with the earlier conversation, and the reply appears in the chat formatted as Markdown. It can also read the reply out loud.

![Screenshot](docs/screenshot.png)

## What I built

The core requirement works end to end: the user sends a message, the app sends it to the AI, gets the response back and shows it.

I also added these bonus features:

| Feature | How it works |
|---|---|
| Conversation history | The browser keeps the whole chat and sends it with every request, so the model remembers earlier messages. Only the last 20 messages are sent. |
| System prompt | Set on the server (`SYSTEM_PROMPT` in `app.py`, can be overridden in `.env`). It sets the bot's tone and asks it to use Markdown. |
| Loading state | An animated "typing" bubble appears while the server waits for Gemini, and the Send button is disabled. |
| Error handling | The server validates input and turns Gemini errors (bad key, rate limit, model not found, outage, network) into readable messages. The UI shows them as red bubbles, and the failed message is removed from history so you can just retry. |
| Basic UI | Chat bubbles, auto-growing input, Enter to send / Shift+Enter for a new line, "New chat" button, dark mode support. |
| Markdown responses | Replies are rendered with `marked` and cleaned with `DOMPurify` so the model's output can't inject HTML or scripts into the page. |
| Voice input | 🎤 button uses the browser's Web Speech API to turn speech into text (Chrome / Edge). |
| Voice output | "Voice replies" toggle reads answers aloud using the browser's speech synthesis. |

## Technology used

- **Python 3.10+** with **Flask** for the backend
- **Google Gemini API** via the official `google-genai` SDK (free tier, model `gemini-flash-latest`)
- **python-dotenv** to load the API key from `.env`
- **HTML, CSS and plain JavaScript** for the frontend (no framework)
- **marked** + **DOMPurify** (loaded from a CDN) for safe Markdown rendering
- **Web Speech API** (built into the browser) for voice input and output
- **unittest** for backend tests, with the Gemini client mocked

## How to run it

1. **Get a free Gemini API key** at https://aistudio.google.com/apikey

2. **Clone the repo and install dependencies**
   ```bash
   git clone <your-repo-url>
   cd AI-chatbot
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS / Linux
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Add your key.** Copy `.env.example` to `.env` and paste your key in:
   ```
   GEMINI_API_KEY=your-real-key
   ```
   `.env` is listed in `.gitignore`, so the key never gets committed.

4. **Start the server**
   ```bash
   python app.py
   ```

5. Open **http://127.0.0.1:5000** in your browser and start chatting.

**Run the tests** (no API key needed):
```bash
python -m unittest discover tests
```

## How it works

```
Browser (static/app.js)            Flask (app.py)                 Gemini API
───────────────────────            ──────────────                 ──────────
user types message
history.push(user msg)
POST /api/chat {messages} ───────▶ validate input
                                   convert roles (assistant→model)
                                   add system prompt
                                   generate_content() ──────────▶ model replies
                                   ◀──────────────────────────── response.text
◀─────────────────── {reply} ───── return JSON (or {error})
render Markdown, speak (optional)
history.push(assistant msg)
```

### Project structure

```
app.py               Flask server: serves the page and the /api/chat endpoint
templates/index.html The chat page
static/app.js        Frontend logic: history, fetch, rendering, voice
static/style.css     Styling (light + dark)
tests/test_app.py    Backend tests with a mocked Gemini client
.env.example         Template for your API key
```

## API integration approach

- **The API key stays on the server.** The browser only talks to my Flask backend, never to Gemini directly, so the key is never sent to the user's browser. The key is read from `.env`, which is gitignored.
- **Stateless backend.** The server doesn't store conversations. The frontend sends the full history on each request, and the server forwards the last 20 messages. That keeps the server simple, and a restart doesn't break anything.
- **Message format conversion.** The frontend uses the common `user` / `assistant` roles. Gemini calls the assistant role `model`, so `to_gemini_contents()` converts them before calling `client.models.generate_content()`.
- **System prompt** is passed through `GenerateContentConfig(system_instruction=...)`, separate from the chat messages.
- **Errors are mapped to friendly messages.** The SDK raises `ClientError` for 4xx responses and `ServerError` for 5xx. I check the status code (429 means rate limited, 404 means bad model name, and so on) and send back a clear message instead of a stack trace.
- **Model is configurable.** `GEMINI_MODEL` in `.env` lets you switch models without touching code. The default, `gemini-flash-latest`, is an alias that always points at the current Flash model.

## What I learned

> *Edit this section in your own words before submitting.*

- How a chat API works: the model has no memory, so "conversation history" just means sending the earlier messages back with each request.
- Why API keys belong on a backend and in `.env`, not in frontend code or Git.
- How system prompts shape the model's behaviour separately from user messages.
- Handling the ways an external API can fail (bad key, rate limits, outages) so the app doesn't just crash.
- Model output is untrusted input. Rendering it as HTML without sanitising would open the page to XSS.
- Testing code that calls an external API by mocking the client.

## What I would improve next

- **Streaming responses** so text shows up word by word instead of all at once (Gemini supports `generate_content_stream`).
- **Save chats** in the browser (localStorage) or a database so they survive a page refresh, and allow several conversations.
- **Smarter history handling:** summarise old messages instead of just dropping everything past the last 20.
- **Deploy it** (Render, Railway, etc.) with basic rate limiting so a public URL can't burn through the API quota.
- **File and image upload,** since Gemini is multimodal.
- Let the user pick a model or change the system prompt from the UI.
