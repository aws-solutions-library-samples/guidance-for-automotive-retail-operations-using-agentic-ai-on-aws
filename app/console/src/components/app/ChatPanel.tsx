"use client";

// Persistent right-hand AI chat panel (DM Assistant `chat-panel`; reused as the
// A3 Assistant chat, the A7 service chat, and the customer A2 chat). The caller
// supplies `send(question) → {answer, citations?, actions?, guardrail?}` so the
// panel is agent-agnostic. Voice input via useVoiceAssistant (AR-7).

import { ReactNode, useEffect, useRef, useState } from "react";
import { useVoiceAssistant } from "@/lib/voice";
import { humanizeAgents } from "@/lib/agents";
import { stripMarkdown } from "@/lib/text";
import { Btn, cx } from "./primitives";

export interface ChatAnswer {
  answer: string;
  citations?: { label: string; ref?: string }[];
  /** retrieved knowledge-base excerpts — rendered as collapsed metadata, never in the answer text */
  references?: { label: string; text: string }[];
  actions?: { label: string; onClick: () => void }[];
  guardrail?: string | null;   // set when a guardrail rewrote/blocked the answer
  engine?: string | null;      // actual API response engine (bedrock, strands, deterministic fallback)
}
interface Msg { role: "user" | "ai"; text: string; meta?: ChatAnswer }

export function ChatPanel({ title = "AI Assistant", badge = "Live", placeholder, suggestions = [], send, intro,
                            language, inline, open }: {
  title?: ReactNode; badge?: ReactNode; placeholder?: string; suggestions?: string[];
  send: (q: string) => Promise<ChatAnswer>; intro?: string; language?: string; inline?: boolean; open?: boolean }) {
  const [msgs, setMsgs] = useState<Msg[]>(intro ? [{ role: "ai", text: intro }] : []);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const voice = useVoiceAssistant({ language, continuous: false, onFinal: (t) => setQ((x) => (x ? `${x} ${t}` : t)) });

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs]);

  const ask = async (text: string) => {
    const t = text.trim();
    if (!t || busy) return;
    setQ("");
    setMsgs((m) => [...m, { role: "user", text: t }]);
    setBusy(true);
    try {
      const a = await send(t);
      setMsgs((m) => [...m, { role: "ai", text: a.answer, meta: a }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "ai", text: `Sorry — ${e?.message || "the assistant is unavailable"}.` }]);
    } finally {
      setBusy(false);
    }
  };

  // inline: fill the host (flex column or fixed-height box) and let only
  // `.chat-messages` scroll, so the input is always on screen.
  return (
    <aside className={cx(!inline && "chat-panel", open && "open", inline && "flex flex-col flex-1 min-h-0 h-full")} aria-label={typeof title === "string" ? title : "AI chat"}>
      <div className="chat-header">
        <span className="chat-title">{title}</span>
        {badge && <span className="panel-badge badge-live">{badge}</span>}
      </div>
      <div className="chat-messages" aria-live="polite">
        {msgs.map((m, i) => (
          <div key={i} className={cx("chat-msg", m.role)}>
            <div className="chat-label">{m.role === "user" ? "You" : "AI"}{m.meta?.engine ? ` · ${m.meta.engine}` : ""}{m.meta?.guardrail ? " · 🛡 guardrail applied" : ""}</div>
            <div className="chat-bubble whitespace-pre-wrap">{m.role === "user" ? m.text : stripMarkdown(m.text)}</div>
            {m.meta?.citations && m.meta.citations.length > 0 && (
              <div className="mt-1">{m.meta.citations.map((c, j) => <span key={j} className="data-source" title={c.ref}>{humanizeAgents(c.label)}</span>)}</div>
            )}
            {m.meta?.references && m.meta.references.length > 0 && (
              <details className="chat-refs">
                <summary>From the knowledge base ({m.meta.references.length})</summary>
                {m.meta.references.map((r, j) => (
                  <div key={j} className="chat-ref">
                    <span className="data-source" title={r.label}>{humanizeAgents(r.label)}</span>
                    <span className="chat-ref-text">{r.text}</span>
                  </div>
                ))}
              </details>
            )}
            {m.meta?.actions && m.meta.actions.length > 0 && (
              <div className="flex gap-1 flex-wrap mt-1">{m.meta.actions.map((a, j) => <Btn key={j} kind="alt" size="sm" onClick={a.onClick}>{a.label}</Btn>)}</div>
            )}
          </div>
        ))}
        {busy && <div className="chat-msg ai"><div className="chat-bubble dim">Thinking…</div></div>}
        <div ref={end} />
      </div>
      {suggestions.length > 0 && (
        <div className="chat-suggestions">
          {suggestions.map((s) => <button key={s} className="filter-pill text-[.7rem]" onClick={() => ask(s)}>{s}</button>)}
        </div>
      )}
      <form className="chat-input-area" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input className="chat-input" value={voice.interim ? `${q} ${voice.interim}` : q} onChange={(e) => setQ(e.target.value)}
               placeholder={placeholder || "Ask a question…"} aria-label="Message" />
        {voice.engine !== "none" && (
          <button type="button" className={cx("mic-btn", voice.listening && "listening")} onClick={voice.toggle}
                  aria-pressed={voice.listening} aria-label={voice.listening ? "Stop dictation" : "Dictate"}>🎤</button>
        )}
        <Btn kind="primary" size="sm" type="submit" disabled={busy || !q.trim()}>Send</Btn>
      </form>
      {voice.error && <div className="px-3 pb-2 text-[.7rem]" style={{ color: "var(--danger)" }}>{voice.error}</div>}
    </aside>
  );
}
