// Frontend logic: keeps the conversation history, streams replies from the
// backend, and renders them. The history lives here (in the browser), is saved
// to localStorage so a refresh doesn't lose it, and the whole list is sent on
// every request so the model remembers earlier messages.

const messagesEl = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const form = document.getElementById("chat-form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const micBtn = document.getElementById("mic");
const newChatBtn = document.getElementById("new-chat");
const speakToggle = document.getElementById("speak-toggle");

const STORAGE_KEY = "nova-chat-history";
let history = loadHistory(); // [{ role: "user" | "assistant", content: "..." }]
let controller = null; // AbortController for the reply being streamed, if any
let chatId = 0; // bumped by "New chat" so a stopped reply isn't saved into the new chat

// ---- Saving the chat so it survives a page refresh ----
function loadHistory() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (!Array.isArray(saved)) return [];
    return saved.filter(
      (m) => (m.role === "user" || m.role === "assistant") && typeof m.content === "string"
    );
  } catch {
    return []; // storage blocked or corrupted: start fresh
  }
}

function saveHistory() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  } catch {
    // storage full or blocked: the chat still works, it just won't be saved
  }
}

// ---- Rendering ----
function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderMarkdown(el, markdown) {
  // Render Markdown, then sanitise it so the reply can't inject scripts.
  el.innerHTML = DOMPurify.sanitize(marked.parse(markdown));
}

function addMessage(role, text) {
  emptyState.hidden = true;
  const el = document.createElement("div");
  el.className = `message ${role}`;
  if (role === "bot") renderMarkdown(el, text);
  else el.textContent = text; // user text and errors are shown as plain text
  messagesEl.appendChild(el);
  scrollToBottom();
  return el;
}

function showTyping() {
  const el = document.createElement("div");
  el.className = "message bot";
  el.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
  messagesEl.appendChild(el);
  scrollToBottom();
  return el;
}

// Error bubble with a "Try again" button. The failed user message is removed
// from the screen too, because sendMessage() will add it again on retry.
function showError(msg, failedText, userBubble) {
  const el = addMessage("error", `⚠️ ${msg} `);
  const retry = document.createElement("button");
  retry.className = "secondary retry";
  retry.textContent = "Try again";
  retry.addEventListener("click", () => {
    if (controller) return;
    el.remove();
    userBubble.remove();
    sendMessage(failedText);
  });
  el.appendChild(retry);
}

function setBusy(busy) {
  sendBtn.textContent = busy ? "Stop" : "Send";
  sendBtn.classList.toggle("stop", busy);
}

// ---- Talking to the backend ----

// Read the Server-Sent Events stream from /api/chat/stream. Calls onText with
// the full reply so far after every piece, and returns the finished reply.
async function readStream(res, onText) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let text = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop(); // the last piece may be incomplete
    for (const event of events) {
      if (!event.startsWith("data: ")) continue;
      const data = JSON.parse(event.slice(6));
      if (data.error) throw new Error(data.error);
      if (data.text) {
        text += data.text;
        onText(text);
      }
    }
  }
  if (!text) throw new Error("The reply was cut off. Please try again.");
  return text;
}

async function sendMessage(text) {
  history.push({ role: "user", content: text });
  saveHistory();
  const userBubble = addMessage("user", text);
  const botBubble = showTyping();
  controller = new AbortController();
  setBusy(true);
  const myChat = chatId;
  let reply = "";

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `Server error (${res.status})`);
    }
    await readStream(res, (soFar) => {
      reply = soFar;
      renderMarkdown(botBubble, reply);
      scrollToBottom();
    });
    history.push({ role: "assistant", content: reply });
    speak(reply);
  } catch (err) {
    if (myChat !== chatId) {
      // "New chat" was clicked mid-reply: the old chat is already cleared.
    } else if (err.name === "AbortError" && reply) {
      // Stopped part-way: keep what arrived so far.
      history.push({ role: "assistant", content: reply });
    } else if (err.name === "AbortError") {
      // Stopped before anything arrived: undo the message, put the text back.
      history.pop();
      botBubble.remove();
      userBubble.remove();
      input.value = text;
      if (!history.length) emptyState.hidden = false;
    } else {
      // Drop the failed message from history so a retry starts clean.
      history.pop();
      botBubble.remove();
      const msg = err instanceof TypeError ? "Can't reach the server. Is app.py running?" : err.message;
      showError(msg, text, userBubble);
    }
  } finally {
    saveHistory();
    controller = null;
    setBusy(false);
    input.focus();
  }
}

// ---- Input handling ----
form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (controller) return controller.abort(); // the button says "Stop" while busy
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  autoResize();
  sendMessage(text);
});

// Enter sends, Shift+Enter adds a new line.
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (!controller) form.requestSubmit();
  }
});

function autoResize() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
  // Only show a scrollbar once the text is taller than the max height.
  input.style.overflowY = input.scrollHeight > 160 ? "auto" : "hidden";
}
input.addEventListener("input", autoResize);

document.querySelectorAll(".suggestion").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (!controller) sendMessage(btn.dataset.prompt);
  });
});

newChatBtn.addEventListener("click", () => {
  chatId++;
  controller?.abort();
  history = [];
  saveHistory();
  messagesEl.querySelectorAll(".message").forEach((el) => el.remove());
  emptyState.hidden = false;
  window.speechSynthesis?.cancel();
  input.focus();
});

// ---- Voice output (browser Web Speech API, no extra API needed) ----
function speak(markdown) {
  if (!speakToggle.checked || !("speechSynthesis" in window)) return;
  // Strip Markdown symbols so they aren't read out loud.
  const plain = markdown.replace(/```[\s\S]*?```/g, " code block ").replace(/[*_#`>|-]/g, "");
  speechSynthesis.cancel();
  speechSynthesis.speak(new SpeechSynthesisUtterance(plain));
}
speakToggle.addEventListener("change", () => {
  if (!speakToggle.checked) window.speechSynthesis?.cancel();
});

// ---- Voice input (supported in Chrome and Edge) ----
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (!SpeechRecognition) {
  micBtn.disabled = true;
  micBtn.title = "Voice input isn't supported in this browser (try Chrome or Edge)";
} else {
  const recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.interimResults = true;
  let listening = false;

  recognition.onresult = (e) => {
    input.value = Array.from(e.results).map((r) => r[0].transcript).join("");
    autoResize();
  };
  recognition.onend = () => {
    listening = false;
    micBtn.classList.remove("listening");
  };
  recognition.onerror = (e) => {
    if (e.error === "not-allowed") addMessage("error", "⚠️ Microphone permission was denied.");
  };

  micBtn.addEventListener("click", () => {
    if (listening) return recognition.stop();
    listening = true;
    micBtn.classList.add("listening");
    recognition.start();
  });
}

// ---- Restore the saved conversation on page load ----
history.forEach((m) => addMessage(m.role === "assistant" ? "bot" : "user", m.content));
