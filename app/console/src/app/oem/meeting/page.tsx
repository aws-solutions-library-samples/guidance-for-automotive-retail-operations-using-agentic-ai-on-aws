"use client";

// OEM Workspace · Meeting in a Box (design §4.1 field tools, spec §5.9): pick
// one of the meeting types (dealer / district / area / national month-end
// close, QBR, new-model launch prep, EV readiness, CSI action, fixed-ops
// monthly / quarterly, parts report) → A6 assembles the deck: agenda, cited
// talking points (guardrail-checked) and the metrics page from the account /
// district read models. Slide preview grid + Presentation mode. PPTX/PDF
// export is the Quick-hosted build_meeting_package Flow — attempted, then
// degraded to Presentation mode / print when the Flow is not deployed.

import { ReactNode, useEffect, useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, EmptyState, Field, InfoRow, Kpi, KpiGrid, LoadingBlock, Pct, cx, useToast,
} from "@/components/app";
import { apiGet, apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView } from "../_shared/WorkspaceView";

interface MeetingType { id: string; label: string; scope: "dealer" | "district"; focus: string }

const MEETING_TYPES: MeetingType[] = [
  { id: "dealer_month_end", label: "Dealer month-end close", scope: "dealer", focus: "month-to-date performance vs objective and the close-out asks" },
  { id: "district_month_end", label: "District month-end close", scope: "district", focus: "district objective achievement, at-risk dealers and supply" },
  { id: "area_month_end", label: "Area month-end close", scope: "district", focus: "area roll-up: objective, share and alerts across districts" },
  { id: "national_month_end", label: "National month-end close", scope: "district", focus: "national roll-up highlights and exceptions" },
  { id: "qbr", label: "Quarterly business review", scope: "dealer", focus: "the quarter: composite vs peers, plans, incentives and the 90-day outlook" },
  { id: "new_model_launch", label: "New-model launch prep", scope: "district", focus: "launch readiness: allocation, training, demo units and marketing support" },
  { id: "ev_readiness", label: "EV readiness", scope: "dealer", focus: "certification, chargers, training and EV inventory mix" },
  { id: "csi_action", label: "CSI action", scope: "dealer", focus: "CSI trend, drivers and the agreed recovery actions" },
  { id: "fixed_ops_monthly", label: "Fixed ops monthly", scope: "dealer", focus: "service lane volume, hours and retention for the month" },
  { id: "fixed_ops_quarterly", label: "Fixed ops quarterly", scope: "dealer", focus: "fixed-ops quarter in review and the coming quarter's plan" },
  { id: "parts_report", label: "Parts report", scope: "dealer", focus: "parts purchases, fill rates and back-orders" },
];

const AGENDA = [
  "Review performance vs objective",
  "Walk the composite: sales health, service health, days supply",
  "Open alerts and agreed actions",
  "Action-plan milestones and owners",
  "Next 30 days: inventory, incentives, marketing support",
];

interface Slide { label: string; body: ReactNode }
interface Pack {
  type: MeetingType; account: any | null;
  slides: Slide[];
  citations: { label: string; ref?: string }[];
  guardrail?: string | null;
}

export default function OemMeeting() {
  const toast = useToast();
  const { persona } = usePersona();
  const district = persona?.scopeNodeId && persona.scopeNodeId.startsWith("DIST") ? persona.scopeNodeId : "DIST-07";
  const accounts = useApi<any>("/oem/accounts");
  const today = useApi<any>("/oem/today");
  const [typeId, setTypeId] = useState<string>("dealer_month_end");
  const [accountId, setAccountId] = useState("");
  const [pack, setPack] = useState<Pack | null>(null);
  const [busy, setBusy] = useState(false);
  const [present, setPresent] = useState<number | null>(null);
  const dealers = ((accounts.data?.accounts || []) as any[]).filter((a) => a.account_type === "dealer");
  const mtype = MEETING_TYPES.find((t) => t.id === typeId)!;
  const needsDealer = mtype.scope === "dealer";

  // Presentation mode: arrow keys + Escape.
  useEffect(() => {
    if (present == null || !pack) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setPresent(null);
      if (e.key === "ArrowRight") setPresent((i) => (i == null ? i : Math.min(pack.slides.length - 1, i + 1)));
      if (e.key === "ArrowLeft") setPresent((i) => (i == null ? i : Math.max(0, i - 1)));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [present, pack]);

  const generate = async () => {
    const account = needsDealer ? dealers.find((a) => a.account_id === accountId) : null;
    if (needsDealer && !account) { toast("This meeting type needs a dealer — pick one first", "error"); return; }
    setBusy(true);
    setPack(null);
    try {
      const scopeName = account ? account.name : `district ${district}`;
      const [detail, ask] = await Promise.all([
        account ? apiGet<any>(`/oem/accounts/${encodeURIComponent(account.account_id)}`) : Promise.resolve(null),
        apiPost<any>("/oem/ask", {
          question: `Prepare ${mtype.label} talking points for ${scopeName}: ${mtype.focus}. Include performance vs objective, open alerts, and the asks.`,
          view: "meeting", dealer_id: account?.dealer_id || undefined,
        }),
      ]);
      const s = detail?.summary || {};
      const t = today.data;
      const metrics: Slide = {
        label: "Metrics",
        body: account ? (
          <>
            <InfoRow label="Sales health" value={s.sales_health_index != null ? <Pct value={s.sales_health_index} /> : "—"} />
            <InfoRow label="Service health" value={s.service_health_index != null ? <Pct value={s.service_health_index} /> : "—"} />
            <InfoRow label="Days supply" value={detail?.account?.days_supply ?? "—"} />
            <InfoRow label="Open alerts" value={`${s.open_alerts ?? 0}${s.high_alerts ? ` (${s.high_alerts} HIGH)` : ""}`} />
            <InfoRow label="Plans on file" value={detail?.plans?.length ?? 0} />
            <InfoRow label="Recent contact reports" value={detail?.contact_reports?.length ?? 0} />
          </>
        ) : (
          <>
            <InfoRow label="District" value={t?.scope_node || district} />
            <InfoRow label="Dealers" value={t?.kpis?.dealers ?? "—"} />
            <InfoRow label="Avg sales health" value={t?.kpis?.avg_sales_health != null ? <Pct value={t.kpis.avg_sales_health} /> : "—"} />
            <InfoRow label="Avg days supply" value={t?.kpis?.avg_days_supply ?? "—"} />
            <InfoRow label="Dealers at risk" value={t?.kpis?.dealers_at_risk ?? 0} />
            <InfoRow label="Open alerts" value={t?.alerts?.open_total ?? 0} />
          </>
        ),
      };
      const alerts = account ? (detail?.alerts || []) : [...(t?.alerts?.high || []), ...(t?.alerts?.medium || [])];
      setPack({
        type: mtype, account,
        citations: (ask.citations || []).map((c: any) => ({ label: c.label || String(c), ref: c.ref })),
        guardrail: ask.guardrail && (Array.isArray(ask.guardrail) ? ask.guardrail.join(", ") : String(ask.guardrail)),
        slides: [
          { label: "Cover", body: <div><div className="text-lg font-bold">{mtype.label}</div><div className="dim text-sm mt-1">{scopeName} · prepared by the Field Sales Agent, reviewed by {persona?.name || "the DM"}</div></div> },
          { label: "Agenda", body: <ol className="pl-5 list-decimal text-sm flex flex-col gap-1">{AGENDA.map((x) => <li key={x}>{x}</li>)}</ol> },
          metrics,
          { label: "Alerts & actions", body: alerts.length === 0 ? <p className="text-sm">No open alerts to cover.</p> : (
            <ul className="pl-4 list-disc text-sm">{alerts.slice(0, 6).map((a: any) => <li key={a.alert_id}>{String(a.severity).toUpperCase()}: {a.title}</li>)}</ul>) },
          { label: "Talking points", body: <p className="text-sm whitespace-pre-wrap">{ask.answer}</p> },
          { label: "Next steps", body: <ul className="pl-4 list-disc text-sm"><li>Agree owners and dates for each action</li><li>File the contact report after the meeting</li><li>Track milestones on the action plan</li></ul> },
        ],
      });
      toast("Meeting pack generated", "success");
    } catch (e: any) {
      toast(e?.message || "Could not generate the pack", "error");
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    if (!pack) return;
    try {
      // Designed contract: the Quick-hosted build_meeting_package Flow renders
      // PPTX/PDF to S3. Attempt it, then degrade.
      const out = await apiPost<any>("/oem/meeting-box", { type: pack.type.id, account_id: pack.account?.account_id, district_id: district });
      if (out?.url) { window.open(out.url, "_blank"); return; }
      toast("Export accepted — the deck lands in your documents", "success");
    } catch {
      toast("PPTX/PDF export is produced by the Quick-hosted build_meeting_package Flow, which is not deployed in this sandbox — use Presentation mode or print this page.", "info");
    }
  };

  const kpis: Kpi[] = [
    { label: "Meeting types", value: MEETING_TYPES.length, delta: "month-end · QBR · launch · EV · CSI · fixed ops · parts" },
    { label: "Selected", value: mtype.label, delta: needsDealer ? "dealer-scoped" : "district-scoped" },
    { label: "Dealers in scope", value: dealers.length, delta: `${accounts.data?.at_risk ?? 0} at risk` },
    { label: "Deck", value: pack ? `${pack.slides.length} slides` : "—",
      trend: pack ? "up" : undefined, delta: pack ? "generated — review before you present" : "generate to preview" },
  ];

  return (
    <WorkspaceView view="meeting" title="Meeting in a Box" dealerId={pack?.account?.dealer_id}
      subtitle="Pick the meeting type — the Field Sales Agent assembles the deck with cited, guardrail-checked talking points"
      prompts={["What should I lead with in this meeting?", "What asks should I bring to the GM?"]}>
      <KpiGrid items={kpis} />
      <AiInsight title="Meeting prep" sources={["OEM_Accounts", "OEM_Alerts", "Field Sales Agent ask (guardrail-checked)"]}>
        <p>Pick the meeting type and scope, generate, and walk in with the agenda, cited talking points and the metrics page. Everything the Field Sales Agent writes is guardrail-checked (GR-001…008) — pricing, allocation and personnel asks are rewritten to safe alternatives.</p>
      </AiInsight>
      <Card title="Select meeting type">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" role="group" aria-label="Meeting type">
          {MEETING_TYPES.map((t) => (
            <button key={t.id} type="button" aria-pressed={typeId === t.id} onClick={() => setTypeId(t.id)}
                    className={cx("card text-left p-3 cursor-pointer", typeId === t.id && "outline outline-2")}
                    style={typeId === t.id ? { outlineColor: "var(--oem-accent, var(--primary))" } : undefined}>
              <div className="text-sm font-semibold">{t.label}</div>
              <div className="dim text-xs mt-1">{t.scope === "dealer" ? "per dealer" : "district / area"}</div>
            </button>
          ))}
        </div>
      </Card>
      <Card title="Build the deck">
        <Async q={accounts} rows={2}>
          {() => (
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-64 flex-1">
                <Field label={needsDealer ? "Dealer" : "Dealer (optional for district-scoped meetings)"}>
                  {(id) => (
                    <select id={id} className="input" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
                      <option value="">{needsDealer ? "Select a dealer…" : `Whole district (${district})`}</option>
                      {dealers.map((a) => <option key={a.account_id} value={a.account_id}>{a.name} ({a.dealer_id || a.account_id})</option>)}
                    </select>
                  )}
                </Field>
              </div>
              <Btn kind="primary" disabled={busy || (needsDealer && !accountId)} onClick={generate}>{busy ? "Generating…" : "Generate"}</Btn>
              <Btn kind="secondary" disabled={!pack} onClick={download}>Download</Btn>
              <Btn kind="secondary" disabled={!pack} onClick={() => setPresent(0)}>Presentation mode</Btn>
            </div>
          )}
        </Async>
      </Card>
      {busy && <LoadingBlock rows={6} />}
      {pack && (
        <Card title={<span>Slide preview — {pack.type.label}
          {pack.guardrail && <Badge tone="orange" title="A guardrail rewrote part of the talking points">🛡 guardrail applied</Badge>}</span>}>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {pack.slides.map((sl, i) => (
              <button key={sl.label} type="button" className="card text-left p-3 cursor-pointer" onClick={() => setPresent(i)}
                      aria-label={`Open slide ${i + 1}: ${sl.label} in presentation mode`}>
                <div className="section-label">Slide {i + 1} · {sl.label}</div>
                <div className="mt-2 max-h-40 overflow-hidden text-sm">{sl.body}</div>
              </button>
            ))}
          </div>
          {pack.citations.length > 0 && (
            <div className="mt-3">
              <span className="text-xs dim">Citations: </span>
              {pack.citations.map((c, i) => <span key={i} className="data-source" title={c.ref}>{c.label}</span>)}
            </div>
          )}
        </Card>
      )}
      {!pack && !busy && <EmptyState>No deck yet — pick the meeting type, then Generate.</EmptyState>}
      {present != null && pack && (
        <div className="fixed inset-0 z-50 flex flex-col" role="dialog" aria-modal="true"
             aria-label={`Presentation: ${pack.type.label}, slide ${present + 1} of ${pack.slides.length}`}
             style={{ background: "var(--bg, #fff)" }}>
          <div className="flex items-center justify-between px-6 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
            <div className="text-sm font-semibold">{pack.type.label} · {pack.account?.name || `District ${district}`}</div>
            <div className="flex items-center gap-2">
              <span className="text-xs dim">Slide {present + 1} / {pack.slides.length} — arrow keys to navigate, Esc to exit</span>
              <Btn kind="secondary" size="sm" onClick={() => setPresent(null)}>Exit</Btn>
            </div>
          </div>
          <div className="flex-1 overflow-auto flex items-center justify-center p-8">
            <div className="card w-full max-w-3xl p-8">
              <div className="section-label mb-3">{pack.slides[present].label}</div>
              {pack.slides[present].body}
            </div>
          </div>
          <div className="flex justify-center gap-2 pb-6">
            <Btn kind="secondary" size="sm" disabled={present === 0} onClick={() => setPresent(Math.max(0, present - 1))}>← Previous</Btn>
            <Btn kind="primary" size="sm" disabled={present === pack.slides.length - 1} onClick={() => setPresent(Math.min(pack.slides.length - 1, present + 1))}>Next →</Btn>
          </div>
        </div>
      )}
    </WorkspaceView>
  );
}
