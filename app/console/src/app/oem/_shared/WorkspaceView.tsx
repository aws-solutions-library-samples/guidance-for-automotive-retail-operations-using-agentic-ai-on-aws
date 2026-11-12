"use client";

// OEM Workspace view frame (design §4 chrome): scrolling center view plus the
// persistent 340px right-hand A6 chat panel — cited answers, guardrail shield
// when a guardrail rewrote the answer (GR-001…008), mic, and per-view
// suggested prompts. On narrow screens the chat collapses behind an "Ask AI"
// button. The Shell owns the light theme, header, and sidebar.

import { ReactNode, useState } from "react";
import { apiPost } from "@/lib/api";
import { usePersona } from "@/lib/persona";
import { Btn, ChatPanel, Page, ViewHeader } from "@/components/app";

export function WorkspaceView({ title, subtitle, actions, children, prompts = [], view, askPath = "/oem/ask",
                                dealerId, chatTitle = "District Manager Assistant" }: {
  title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; prompts?: string[];
  view: string; askPath?: string; dealerId?: string | null; chatTitle?: string }) {
  const { persona } = usePersona();
  const [chatOpen, setChatOpen] = useState(false);
  const send = async (question: string) => {
    const d = await apiPost<any>(askPath, { question, view, dealer_id: dealerId || undefined });
    return {
      answer: d.answer,
      citations: (d.citations || []).map((c: any) => ({ label: c.label, ref: c.ref })),
      guardrail: d.guardrail && (Array.isArray(d.guardrail) ? d.guardrail.join(", ") : String(d.guardrail)),
      engine: d.engine || d.via,
    };
  };
  return (
    <div className="flex flex-1 min-h-0">
      <div className="flex-1 min-w-0 overflow-y-auto">
        <Page>
          <ViewHeader title={title} subtitle={subtitle}
                      actions={<>{actions}<span className="min-[1101px]:hidden"><Btn kind="secondary" size="sm" onClick={() => setChatOpen((o) => !o)} aria-expanded={chatOpen}>{chatOpen ? "Close AI" : "Ask AI"}</Btn></span></>} />
          {children}
        </Page>
      </div>
      <ChatPanel
        title={chatTitle}
        badge="Field Sales Agent"
        open={chatOpen}
        language={persona?.marketId === "de" ? "de-DE" : "en-US"}
        placeholder="Ask about dealers, performance, strategy…"
        suggestions={prompts}
        intro="Ask about your dealers, their performance and what to do next. Answers are grounded in the live OEM data you can see here and in the knowledge base; each answer says where it came from."
        send={send}
      />
    </div>
  );
}

export const SEVERITY_TONE: Record<string, "red" | "orange" | "blue"> = { HIGH: "red", MEDIUM: "orange", LOW: "blue" };
export const sevClass = (s: string) => `severity-${String(s || "low").toLowerCase()}`;
