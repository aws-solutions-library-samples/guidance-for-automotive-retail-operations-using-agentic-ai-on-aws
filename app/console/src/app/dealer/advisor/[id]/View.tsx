"use client";

// Advisor (live) — spec §5.3, one-for-one with docs/use-cases/sales-advisor-v2.html:
// top-bar (session-info + live timer AR-8 + nav-tabs), 3 columns 320/1fr/340,
// bottom AI Log. Mobile (≤480 px) renders the quick-look: Customer 360 + top
// suggestion + objection response (§5.19). OEM roles see it read-only.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  AiLog, Async, ErrorState, InfoRow, LoadingBlock, MarketProvider, Modal, SERVICE_STAGES, Stepper, ThreeColumn,
} from "@/components/app";
import { Left360 } from "./Left360";
import { CenterRecs } from "./CenterRecs";
import { RightAssist } from "./RightAssist";
import { useApi, useElapsed, useRouteId } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { useMarketCtx } from "@/components/app";

function useIsPhone() {
  const [phone, setPhone] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 480px)");
    const h = () => setPhone(mq.matches);
    h(); mq.addEventListener("change", h);
    return () => mq.removeEventListener("change", h);
  }, []);
  return phone;
}

function GarageMirror({ customerId, open, onClose }: { customerId?: string; open: boolean; onClose: () => void }) {
  const q = useApi<any>(open && customerId ? `/garage/${encodeURIComponent(customerId)}` : null);
  return (
    <Modal open={open} onClose={onClose} title="What the customer sees · My Garage (read-only)">
      <Async q={q}>
        {(g) => (
          <div className="flex flex-col gap-3">
            {g.disclaimer && <div className="dim text-xs">{g.disclaimer}</div>}
            {g.purchase?.stage && (<div><div className="section-label">Your purchase</div><Stepper current={g.purchase.stage} /></div>)}
            {(g.service?.active_journeys || []).map((sj: any) => (
              <div key={sj.service_journey_id}><div className="section-label">Service · {sj.vin}</div><Stepper stages={SERVICE_STAGES} current={sj.stage} /></div>
            ))}
            {(g.vehicles || []).map((v: any) => <InfoRow key={v.vin} label={`${v.year || ""} ${v.make || ""} ${v.model || ""}`.trim() || v.vin} value={v.vin} />)}
            {(g.service_history || []).slice(0, 3).map((r: any) => <InfoRow key={r.ro_number} label={`RO ${r.ro_number}`} value={r.status} />)}
            {g.preferences && <InfoRow label="Preferred channel" value={g.preferences.preferred_channel} />}
          </div>
        )}
      </Async>
    </Modal>
  );
}

function TopBar({ a, started }: { a: any; started: number }) {
  const timer = useElapsed(started);
  const cfg = useMarketCtx();
  const c = a.customer_360 || {};
  return (
    <div className="top-bar">
      <div className="session-info">
        <span className="live-dot" aria-hidden />
        <span>Live session · <strong>{c.name || a.journey.customer_id}</strong></span>
        <span className="dim">· {a.journey.journey_id} · {a.journey.dealer_id}{cfg ? ` · ${cfg.market_id.toUpperCase()}` : ""}</span>
      </div>
      <nav className="nav-tabs" aria-label="Floor tools">
        <Link className="nav-tab" href="/dealer/journeys">Journeys</Link>
        <span className="nav-tab active" aria-current="page">Advisor</span>
        <Link className="nav-tab" href={`/dealer/discovery?journey=${encodeURIComponent(a.journey.journey_id)}`}>Discovery</Link>
        <Link className="nav-tab" href={`/dealer/trade-in?journey=${encodeURIComponent(a.journey.journey_id)}`}>Trade-In</Link>
        <Link className="nav-tab" href={`/dealer/assistant?journey=${encodeURIComponent(a.journey.journey_id)}`}>Assistant</Link>
      </nav>
      <span className="timer" aria-label="Session timer">{timer}</span>
    </div>
  );
}

export default function AdvisorView() {
  const id = useRouteId(2);
  const { persona } = usePersona();
  const q = useApi<any>(id ? `/journeys/${encodeURIComponent(id)}/advisor` : null);
  const [started] = useState(() => Date.now());
  const [mirror, setMirror] = useState(false);
  const phone = useIsPhone();
  const readOnly = Boolean(persona?.isOem) || !(persona?.screens || []).includes("dealer.advisor");
  const language = useMemo(() => (q.data?.journey?.market_id === "de" ? "de-DE" : "en-US"), [q.data]);

  if (!id) return <div className="page"><ErrorState error="No journey selected." /> <Link className="underline" href="/dealer/advisor">Pick a journey</Link></div>;
  if (q.error && !q.data) return <div className="page"><ErrorState error={q.error} onRetry={q.reload} /></div>;
  if (!q.data) return <div className="page"><LoadingBlock rows={6} /></div>;
  const a = q.data;
  const refresh = () => { q.reload(); };

  return (
    <MarketProvider marketId={a.journey.market_id}>
      <div className="flex flex-col flex-1 min-h-0">
        <TopBar a={a} started={started} />
        {readOnly && <div className="context-strip">Read-only view for your role — actions are disabled.</div>}
        {phone ? (
          <div className="flex flex-col gap-3 p-3">
            <Left360 a={a} onViewAsCustomer={() => setMirror(true)} onChanged={refresh} />
            {(a.suggestions?.suggestions || [])[0] && (
              <div className="talk-track">“{a.suggestions.suggestions[0].text}” <span className="dim">— {a.suggestions.suggestions[0].why}</span></div>
            )}
            <RightAssist a={a} journeyId={a.journey.journey_id} readOnly={readOnly} language={language} onChanged={refresh} />
          </div>
        ) : (
          <ThreeColumn
            left={<Left360 a={a} onViewAsCustomer={() => setMirror(true)} onChanged={refresh} />}
            center={<CenterRecs a={a} journeyId={a.journey.journey_id} readOnly={readOnly} onChanged={refresh} />}
            right={<RightAssist a={a} journeyId={a.journey.journey_id} readOnly={readOnly} language={language} onChanged={refresh} />}
          />
        )}
        <AiLog journeyId={a.journey.journey_id} />
        <GarageMirror customerId={a.journey.customer_id} open={mirror} onClose={() => setMirror(false)} />
      </div>
    </MarketProvider>
  );
}
