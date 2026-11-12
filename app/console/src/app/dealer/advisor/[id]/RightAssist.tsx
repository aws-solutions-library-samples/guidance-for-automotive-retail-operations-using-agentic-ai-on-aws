"use client";

// Advisor RIGHT — Live Assistance (§5.3): live transcript → objection detection
// (< 2 s target) with transcript-replay demo mode, AI suggested response with
// Use This / Alternative (records outcome), competitive intel, value snapshot,
// eligible incentives, A2 Next Best Action one-click.

import { fmtStamp } from "@/lib/market";
import { useCallback, useEffect, useRef, useState } from "react";
import { AgentActionsProvider } from "@/components/AgentBar";
import {
  AiResponseCard, Btn, CompeteCard, Distance, ErrorState, IncentiveRow, MiniStat, Money, ObjectionAlert,
  Panel, SectionLabel, StatGrid, useToast,
} from "@/components/app";
import { apiBase, apiGet, apiPost, authToken } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { useVoiceAssistant } from "@/lib/voice";

// Connect is plug-and-play (§5.3): the voice option renders only when the
// deployment enables it; the server's OutreachProvider owns the actual channel.
const CONNECT_ENABLED = process.env.NEXT_PUBLIC_ENABLE_CONNECT === "true";
// Streaming floor path (spec R2): the SSE flavour of the floor route on the
// SAME authenticated API (POST /journeys/{id}/utterance-sse). The transport is
// buffered today (API Gateway REST + python3.12 cannot stream) but the wire
// format is SSE, so a true streaming transport is a URL swap. The earlier
// public Lambda Function URL was retired: the account's
// lambda_function_policy_block_public_access control auto-mitigates any
// world-accessible function, by design (ADR 0008).
const STREAM_ENABLED = (process.env.NEXT_PUBLIC_RETAIL_OPS_STREAM ?? "1") !== "0";

const NBA_ACTIONS: Record<string, { label: string; kind?: string; book?: boolean; channel?: string }> = {
  book_test_drive: { label: "Book test drive", book: true },
  present_offer: { label: "Send finance link", kind: "finance_link" },
  start_prequal: { label: "Send finance link", kind: "finance_link" },
  nudge: { label: "Send follow-up", kind: "follow_up" },
  handoff_to_human: { label: "Schedule callback", kind: "callback" },
  capture_context: { label: "Schedule callback", kind: "callback" },
  value_trade: { label: "Schedule callback", kind: "callback" },
};

export function RightAssist({ a, journeyId, readOnly, language, onChanged }: {
  a: any; journeyId: string; readOnly: boolean; language: string; onChanged: () => void }) {
  const toast = useToast();
  const [obj, setObj] = useState<any>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [used, setUsed] = useState(false);
  const [replay, setReplay] = useState<string[] | undefined>(undefined);
  const [typed, setTyped] = useState("");
  const [streamed, setStreamed] = useState("");
  const nba = useMutation();
  const busy = useRef(false);

  // SSE over the Function URL: `event: token` per delta, final `event: result`
  // with the same payload the REST route returns. Tokens land in `streamed` so
  // the AI-response area fills in as they arrive.
  const classifyStreaming = useCallback(async (utterance: string) => {
    setStreamed("");
    const res = await fetch(`${apiBase()}/journeys/${encodeURIComponent(journeyId)}/utterance-sse`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(authToken() ? { Authorization: `Bearer ${authToken()}` } : {}) },
      body: JSON.stringify({ utterance }),
    });
    if (!res.ok || !res.body) throw new Error(`stream failed (${res.status})`);
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let result: any = null;
    let text = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const frames = buf.split("\n\n");
      buf = frames.pop() ?? "";
      for (const frame of frames) {
        const name = /^event:\s*(\S+)/m.exec(frame)?.[1];
        const raw = /^data:\s*(.*)$/m.exec(frame)?.[1];
        if (!name || !raw) continue;
        const data = JSON.parse(raw);
        if (name === "token") { text += data.text ?? ""; setStreamed(text); }
        else if (name === "result") result = data;
      }
    }
    if (result?.error) throw new Error(result.error);
    return result;
  }, [journeyId]);

  const classify = useCallback(async (utterance: string) => {
    if (busy.current) return;
    busy.current = true;
    const t0 = performance.now();
    try {
      const out = STREAM_ENABLED
        ? await classifyStreaming(utterance)
        : await apiPost<any>(`/journeys/${encodeURIComponent(journeyId)}/objection`, { utterance });
      setLatency(Math.round(performance.now() - t0));
      if (out?.objection_detected) { setObj(out); setUsed(false); }
    } catch (e: any) {
      toast(`Objection check failed: ${e.message}`, "error");
    } finally {
      setStreamed("");
      busy.current = false;
    }
  }, [classifyStreaming, journeyId, toast]);

  const voice = useVoiceAssistant({ language, onFinal: classify, replayScript: replay, replayIntervalMs: 3500 });

  // Deep link: /dealer/advisor/<id>?say=<utterance> runs the objection coach on arrival, so a
  // demo replay or a shared link lands on the classified objection and its suggested line
  useEffect(() => {
    if (readOnly) return;
    const say = new URLSearchParams(window.location.search).get("say");
    if (say) { setTyped(say); void classify(say); }
  }, [journeyId]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadReplay = async () => {
    try {
      const s = await apiGet<any>(`/journeys/${encodeURIComponent(journeyId)}/replay-script`);
      setReplay(s.lines);
      toast("Demo transcript loaded — press Start listening", "info");
    } catch (e: any) { toast(e.message, "error"); }
  };

  const outcome = async (o: "used" | "alternative") => {
    if (!obj) return;
    try {
      await apiPost(`/journeys/${encodeURIComponent(journeyId)}/objection/outcome`,
        { strategy_id: obj.strategy_id, outcome: o, recommendation_id: obj.recommendation_id });
      if (o === "used") { setUsed(true); toast("Recorded — response used", "success"); }
      else toast("Noted — showing the library phrasing as the alternative", "info");
    } catch (e: any) { toast(e.message, "error"); }
  };

  const nbaChosen = a.next_best_action?.chosen;
  const act = nbaChosen ? NBA_ACTIONS[nbaChosen.action] : undefined;
  const runNba = async (which: { kind?: string; book?: boolean; channel?: string }) => {
    const rid = a.recommendation_ids?.nba;
    const out = which.book
      ? await nba.run(`/journeys/${encodeURIComponent(journeyId)}/book-test-drive`, {})
      : await nba.run(`/journeys/${encodeURIComponent(journeyId)}/outreach`, { kind: which.kind, channel: which.channel || "email", recommendation_id: rid });
    if (out) {
      if (which.book && rid) await apiPost(`/recommendations/${encodeURIComponent(rid)}/accept`).catch(() => {});
      toast(which.book ? `Test drive booked ${fmtStamp(out.slot_start)}`
            : out.task_id ? "Callback task created" : out.sent ? "Sent" : out.reason === "no_consent" ? "Not sent — no consent on this channel" : "Queued as a dealer task",
            out.reason === "no_consent" ? "error" : "success");
      onChanged();
    } else toast(nba.error || "Action failed", "error");
  };

  const comp = a.competitive || {};
  const tco = a.tco && !a.tco.error ? a.tco : null;
  const perDist = tco?.rows?.find((r: any) => r.key === "per_distance");
  const rng = tco?.rows?.find((r: any) => r.key === "range");
  const inc: any[] = a.incentives?.incentives || [];

  // The floor agents live in the global AgentBar; this page feeds it the live
  // run-now endpoints it already calls plus the journey trace scope (§1.6).
  return (
    <AgentActionsProvider value={{
      journeyId,
      actions: readOnly ? undefined : {
        "a2.followup_drafter": { label: "Draft & send", run: () => runNba({ kind: "follow_up" }) },
        ...(act ? { "a2.next_best_action": { label: `Do it: ${act.label}`, run: () => runNba(act) } } : {}),
      },
    }}>
    <Panel title="Live Assistance" badge={voice.listening ? "Listening" : "Active"} badgeKind="active" ariaLive>
      <div className="flex gap-2 flex-wrap items-center">
        {!readOnly && <Btn kind={voice.listening ? "danger" : "primary"} size="sm" onClick={voice.toggle}
                           disabled={voice.engine === "none"} aria-pressed={voice.listening}>
          {voice.listening ? "■ Stop" : "● Start listening"}</Btn>}
        {!readOnly && !replay && <Btn kind="alt" size="sm" onClick={loadReplay}>Demo transcript</Btn>}
        <span className="dim text-[.7rem]">{voice.engine === "replay" ? "replay mode" : voice.engine}{latency != null ? ` · ${latency} ms` : ""}</span>
      </div>
      {(voice.interim || voice.transcript.length > 0) && (
        <div className="text-[.75rem] muted" aria-live="polite">“{voice.interim || voice.transcript[voice.transcript.length - 1]}”</div>
      )}
      {voice.error && <ErrorState error={voice.error} />}
      {!readOnly && (
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) { classify(typed.trim()); setTyped(""); } }}>
          <input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Type what the customer said…" aria-label="Customer utterance" />
          <Btn kind="secondary" size="sm" type="submit">Check</Btn>
        </form>
      )}

      {streamed && !obj ? (
        <AiResponseCard approach="streaming">
          {streamed}
          <div className="dim text-[.68rem] mt-2">streaming…</div>
        </AiResponseCard>
      ) : null}
      {obj ? (
        <>
          <ObjectionAlert label={`Detected objection · ${obj.objection_type.replace(/_/g, " ")}`} quote={obj.utterance} />
          <AiResponseCard approach={obj.objection_type.replace(/_/g, " ")} successRate={obj.success_rate}
                          used={used} onUse={readOnly ? undefined : () => outcome("used")} onAlt={readOnly ? undefined : () => outcome("alternative")}>
            {obj.suggested_response}
            <div className="dim text-[.68rem] mt-2">{obj.success_basis} · grounded on {obj.strategy_id}</div>
          </AiResponseCard>
        </>
      ) : !streamed ? <div className="dim text-sm">No objection detected yet. Start listening or type what the customer said.</div> : null}

      {nbaChosen && (
        <div className="ai-response-card">
          <div className="ai-response-label"><span className="ai-tag">✦ Next best action · Journey Agent</span></div>
          <div className="ai-response-text"><strong>{nbaChosen.action.replace(/_/g, " ")}</strong> — {nbaChosen.reason}</div>
          {!readOnly && (
            <div className="response-btns">
              {act && <Btn kind="use" onClick={() => runNba(act)} disabled={nba.pending}>Do it: {act.label}</Btn>}
              <Btn kind="alt" size="sm" onClick={() => runNba({ book: true })} disabled={nba.pending}>Book test drive</Btn>
              <Btn kind="alt" size="sm" onClick={() => runNba({ kind: "finance_link" })} disabled={nba.pending}>Send finance link</Btn>
              <Btn kind="alt" size="sm" onClick={() => runNba({ kind: "callback" })} disabled={nba.pending}>Schedule callback</Btn>
              {CONNECT_ENABLED && (
                <Btn kind="alt" size="sm" onClick={() => runNba({ kind: "follow_up", channel: "voice" })} disabled={nba.pending}
                     title="Outbound voice through Amazon Connect (consent and quiet hours apply)">📞 Voice via Connect</Btn>
              )}
            </div>
          )}
        </div>
      )}

      <SectionLabel>Competitive intel</SectionLabel>
      {comp.error && <div className="dim text-xs">Unavailable: {comp.error}</div>}
      {(comp.competitors || []).slice(0, 3).map((c: any) => (
        <CompeteCard key={c.name} name={c.name} win={c.win_rate != null ? `${Math.round(c.win_rate * 100)}% win` : undefined}
          pros={[...(c.price_delta != null && c.price_delta < 0 ? [<>Priced <Money value={-c.price_delta} /> below</>] : []), ...c.why_we_win.slice(0, 2)]}
          cons={c.why_we_lose.slice(0, 2)} />
      ))}

      <SectionLabel>Value snapshot</SectionLabel>
      <StatGrid>
        <MiniStat label={perDist?.label || "Cost / distance"} value={perDist ? <Money value={perDist.ev} decimals={3} /> : "—"} />
        <MiniStat label="Range" value={rng?.ev != null ? <Distance value={rng.ev} /> : "—"} />
        <MiniStat label="Energy / yr" value={tco ? <Money value={tco.rows.find((r: any) => r.key === "energy_year")?.ev} /> : "—"} />
        <MiniStat label="In stock" value={a.inventory?.in_stock_count ?? "—"} />
      </StatGrid>

      <SectionLabel>Eligible incentives</SectionLabel>
      {inc.length === 0 && <div className="dim text-xs">None for this powertrain and region.</div>}
      {inc.map((x) => (
        <IncentiveRow key={x.incentive_id} eligible={x.amount != null && !/deduction/i.test(x.name)} name={x.name}
          amount={<>{x.amount != null ? <Money value={x.amount} /> : "terms vary"} · {x.tier}{/deduction/i.test(x.name) ? " · tax deduction, not a price reduction" : ""}</>} />
      ))}
    </Panel>
    </AgentActionsProvider>
  );
}
