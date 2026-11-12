"use client";

// Customer Web · /shop/chat — the A2 nurture conversation (design §6.1, spec
// §5.10). Full-page chat in the ChatPanel style: journey-aware suggestions,
// grounded cited answers with one-click actions, and escalation to a human
// advisor. Resumable from any nurture message by magic link (?token= is
// consumed by the API client; ?journey= scopes the thread). Consent and
// channel preferences are editable in-thread (spec §5.10). Mobile-first.

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Async, Badge, Btn, Drawer, Page, ViewHeader, cx, stageLabel } from "@/components/app";
import { BrandScope, useBrandName } from "@/app/garage/Brand";
import { Preferences } from "@/app/garage/preferences";
import { apiPost } from "@/lib/api";
import { useApi, useQueryParam } from "@/lib/hooks";
import { useVoiceAssistant } from "@/lib/voice";

interface Msg {
  direction: "inbound" | "outbound"; body: string;
  citations?: { label: string; ref?: string }[]; actions?: { id: string; label: string }[];
  escalated?: { advisor?: string } | null; engine?: string | null;
}

const ACTION_HREF: Record<string, string> = {
  book_test_drive: "/shop", start_prequal: "/garage", get_trade_estimate: "/garage",
  view_build: "/garage", book_service: "/garage/service", review_estimate: "/garage/service",
};

/** Journey-aware suggestion chips: what a customer most likely needs per stage. */
const SUGGESTIONS: Record<string, string[]> = {
  DISCOVER: ["What fits my budget?", "Compare electric vs gas for me", "Book a test drive"],
  CONFIGURE: ["Which packages do you recommend?", "What incentives do I qualify for?", "What would my monthly payment be?"],
  QUALIFY: ["What would my monthly payment be?", "Does pre-qualification affect my credit?", "How much is my trade worth?"],
  FINANCE: ["Show my payment options", "What documents will I need?", "How much is my trade worth?"],
  TRADE_IN: ["How much is my trade worth?", "What if I add new mileage?", "What's my equity?"],
  AGREE: ["What's left to sign?", "When can I pick it up?", "What should I bring?"],
  DELIVER: ["When is my delivery?", "Can I reschedule my delivery?", "How do I set up the app?"],
  OWN: ["When is my next service due?", "Do I have any open recalls?", "What's my car worth now?"],
};
const DEFAULT_SUGGESTIONS = ["What would my monthly payment be?", "What incentives do I qualify for?", "How much is my trade worth?"];

export default function ShopChatPage() {
  return (
    <BrandScope>
      <ChatView />
    </BrandScope>
  );
}

function ChatView() {
  const router = useRouter();
  const brandName = useBrandName();
  const journeyParam = useQueryParam("journey");
  const resumeToken = useQueryParam("token");
  const thread = useApi<any>(`/shop/chat${journeyParam ? `?journey=${encodeURIComponent(journeyParam)}` : ""}`);
  const journey = useApi<any>("/garage/journey");

  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [prefsOpen, setPrefsOpen] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const voice = useVoiceAssistant({ continuous: false, onFinal: (t) => setText((x) => (x ? `${x} ${t}` : t)) });

  useEffect(() => { if (thread.data) setMsgs(thread.data.messages || []); }, [thread.data]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);

  const stage: string | null = thread.data?.journey?.stage || journey.data?.journey?.stage || null;
  const advisorName: string | null = journey.data?.journey?.advisor?.name || null;
  const suggestions = (stage && SUGGESTIONS[stage]) || DEFAULT_SUGGESTIONS;
  const resumed = Boolean(journeyParam || resumeToken) && (thread.data?.messages?.length ?? 0) > 0;

  const send = async (t: string, escalate = false) => {
    const q = t.trim();
    if (!q || busy) return;
    setText("");
    setMsgs((m) => [...m, { direction: "inbound", body: q }]);
    setBusy(true);
    try {
      const r: any = await apiPost("/shop/chat", {
        text: q,
        ...(journeyParam ? { journey_id: journeyParam } : {}),
        ...(escalate ? { escalate: true } : {}),
      });
      setMsgs((m) => [...m, { direction: "outbound", body: r.answer, citations: r.citations,
        actions: r.actions, escalated: r.escalated, engine: r.engine }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { direction: "outbound", body: `Sorry — ${e.message}. Please try again.` }]);
    } finally { setBusy(false); }
  };

  return (
    <Page className="flex flex-col min-h-0 h-full pb-0">
      <ViewHeader
        title="Chat with us"
        subtitle={`Answers come from your own build, offers and dealer at ${brandName} — a person steps in whenever you want.`}
        actions={<>
          <Btn kind="secondary" size="sm" onClick={() => send(advisorName ? `I'd like ${advisorName} to take over this conversation.` : "I'd like to talk to a person.", true)}
               disabled={busy}>
            Talk to a person
          </Btn>
          <Btn kind="secondary" size="sm" onClick={() => setPrefsOpen(true)}>Message preferences</Btn>
          <Link href="/garage" className="btn btn-secondary btn-sm">My Garage</Link>
        </>}
      />
      <div className="panel flex-1 flex flex-col min-h-0 max-w-3xl w-full mx-auto">
        {resumed && (
          <div className="context-strip m-3 mb-0" role="status">
            <span aria-hidden>↪</span>
            <span>Picking up where you left off{stage ? <> — you&apos;re at the <b>{stageLabel(stage)}</b> step</> : null}.</span>
          </div>
        )}
        <div className="chat-messages flex-1" aria-live="polite" role="log">
          <Async q={thread} rows={3}>
            {() => (
              <>
                {msgs.length === 0 && (
                  <div className="chat-msg ai">
                    <div className="chat-label">Assistant</div>
                    <div className="chat-bubble">Hi! Ask me about your build, payments, your trade-in, or booking a test drive{advisorName ? ` — or ask for ${advisorName} any time` : ""}.</div>
                  </div>
                )}
                {msgs.map((m, i) => (
                  <div key={i} className={cx("chat-msg", m.direction === "inbound" ? "user" : "ai")}>
                    <div className="chat-label">{m.direction === "inbound" ? "You" : `Assistant${m.engine ? ` · ${m.engine}` : ""}`}</div>
                    <div className="chat-bubble whitespace-pre-wrap">{m.body}</div>
                    {m.citations && m.citations.length > 0 && (
                      <div className="mt-1">{m.citations.map((c, j) => <span key={j} className="data-source" title={c.ref}>{c.label}</span>)}</div>
                    )}
                    {m.escalated && (
                      <div className="mt-1">
                        <Badge tone="orange">Passed to {m.escalated.advisor || "your advisor"}</Badge>
                        <span className="dim text-xs"> — they&apos;ll reply here or by your preferred channel.</span>
                      </div>
                    )}
                    {m.actions && m.actions.length > 0 && (
                      <div className="flex gap-2 flex-wrap mt-2">
                        {m.actions.map((a) => (
                          <Btn key={a.id} kind="alt" size="sm" onClick={() => router.push(ACTION_HREF[a.id] || "/garage")}>{a.label}</Btn>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {busy && <div className="chat-msg ai"><div className="chat-bubble dim">Typing…</div></div>}
              </>
            )}
          </Async>
          <div ref={end} />
        </div>
        <div className="chat-suggestions" aria-label="Suggested questions">
          {suggestions.map((s) => (
            <button key={s} className="filter-pill text-[.72rem] min-h-[36px]" onClick={() => send(s)}>{s}</button>
          ))}
        </div>
        <form className="chat-input-area" onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <input className="chat-input min-h-[44px]" value={voice.interim ? `${text} ${voice.interim}` : text}
                 onChange={(e) => setText(e.target.value)} placeholder="Type your question…" aria-label="Message" />
          {voice.engine !== "none" && (
            <button type="button" className={cx("mic-btn", voice.listening && "listening")} onClick={voice.toggle}
                    aria-pressed={voice.listening} aria-label={voice.listening ? "Stop dictation" : "Dictate"}>🎤</button>
          )}
          <Btn kind="primary" type="submit" disabled={busy || !text.trim()}>Send</Btn>
        </form>
      </div>

      {/* consent & channel preferences, editable in-thread (spec §5.10) */}
      <Drawer open={prefsOpen} onClose={() => setPrefsOpen(false)} title="How we message you">
        <p className="text-sm muted m-0">
          Choose the channels we may use, set quiet hours, or stop automated messages — changes apply
          from the next message we send.
        </p>
        <Preferences compact />
      </Drawer>
    </Page>
  );
}
