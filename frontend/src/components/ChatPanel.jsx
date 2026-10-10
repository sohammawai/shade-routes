import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

const SUGGESTIONS = [
  "Top 5 streets to plant",
  "Shadiest route from the metro to the market at 3 PM",
  "Which area has the least shade at noon?",
];

export default function ChatPanel({ areaKey, areaName, onActions }) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([
    { role: "assistant", text: "Hi! Ask me where to plant trees first, what planting would change, or the shadiest way to walk somewhere." },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef(null);

  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [messages, open]);

  const send = async (text) => {
    const message = text.trim();
    if (!message || busy) return;
    const history = messages.slice(-8);
    setMessages((m) => [...m, { role: "user", text: message }]);
    setInput("");
    setBusy(true);
    try {
      const res = await api.chat(message, areaKey, history);
      setMessages((m) => [...m, { role: "assistant", text: res.answer }]);
      if (res.actions) onActions(res.actions);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", text: `Sorry, I couldn't reach the server (${e.message}).` }]);
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button className="chat-fab" onClick={() => setOpen(true)} aria-label="Ask Shade Routes">
        <svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 5h16v10H9l-5 4V5z" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinejoin="round" />
        </svg>
      </button>
    );
  }

  return (
    <section className="chat" aria-label="Ask Shade Routes">
      <header>
        <div>
          <strong>Ask Shade Routes</strong>
          <span>{areaName} · simulated pilot estimates</span>
        </div>
        <button className="close" onClick={() => setOpen(false)} aria-label="Close chat">×</button>
      </header>
      <div className="messages">
        {messages.map((m, i) => <p key={i} className={`bubble ${m.role}`}>{m.text}</p>)}
        {busy && <p className="bubble assistant typing">Thinking…</p>}
        <div ref={end} />
      </div>
      <div className="chips">
        {SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)} disabled={busy}>{s}</button>)}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); send(input); }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about planting or routes…"
               aria-label="Your question" />
        <button type="submit" disabled={busy || !input.trim()} aria-label="Send">
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
            <path d="M3 9h11M10 4.5L14.5 9 10 13.5" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </form>
    </section>
  );
}
