// Frontend logic: keeps the conversation history, calls the backend,
// and renders replies. The history lives here (in the browser) and the whole
// list is sent on every request so the model remembers earlier messages.

const messagesEl = document.getElementById("messages");
const emptyState = document.getElementById("empty-state");
const form = document.getElementById("chat-form");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const micBtn = document.getElementById("mic");
const newChatBtn = document.getElementById("new-chat");
const speakToggle = document.getElementById("speak-toggle");

let history = []; // [{ role: "user" | "assistant", content: "..." }]
let busy = false;

function scrollToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addMessage(role, text) {
  emptyState.hidden = true;
  const el = document.createElement("div");
  el.className = `message ${role}`;
  if (role === "bot") {
    // Render Markdown, then sanitise it so the reply can't inject scripts.
    el.innerHTML = DOMPurify.sanitize(marked.parse(text));
  } else {
    el.textContent = text; // user text and errors are shown as plain text
  }
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
    if (busy) return;
    el.remove();
    userBubble.remove();
    sendMessage(failedText);
  });
  el.appendChild(retry);
}

function setBusy(value) {
  busy = value;
  sendBtn.disabled = value;
  sendBtn.textContent = value ? "…" : "Send";
}

async function sendMessage(text) {
  history.push({ role: "user", content: text });
  const userBubble = addMessage("user", text);
  setBusy(true);
  const typing = showTyping();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Server error (${res.status})`);

    history.push({ role: "assistant", content: data.reply });
    typing.remove();
    addMessage("bot", data.reply);
    speak(data.reply);
  } catch (err) {
    // Drop the failed message from history so the next attempt starts clean.
    history.pop();
    typing.remove();
    const msg = err instanceof TypeError ? "Can't reach the server. Is app.py running?" : err.message;
    showError(msg, text, userBubble);
  } finally {
    setBusy(false);
    input.focus();
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || busy) return;
  input.value = "";
  autoResize();
  sendMessage(text);
});

// Enter sends, Shift+Enter adds a new line.
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

function autoResize() {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}
input.addEventListener("input", autoResize);

newChatBtn.addEventListener("click", () => {
  history = [];
  messagesEl.querySelectorAll(".message").forEach((el) => el.remove());
  emptyState.hidden = false;
  speechSynthesis?.cancel();
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
  if (!speakToggle.checked) speechSynthesis?.cancel();
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
