"use client";

// OEM Workspace · Account detail (design §4.3): tabs [Account Plan]
// Opportunity Briefs · Win Strategy · Action Plans · Contact Reports — each
// tab renders the latest versioned artifact with inline-expandable citations
// and [Regenerate] / [Publish for review] (POST
// /accounts/{id}/plans/{type}/generate), plus the embedded "District Sales vs
// Target" chart placeholder. A6 drafts; publication goes through approvals.

import { humanizeCode, kpiLabel } from "@/lib/text";
import Link from "next/link";
import { ReactNode, useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, DateTime, EmptyState, Kpi, KpiGrid, Pct, ProgressBar, Tabs, useToast,
} from "@/components/app";
import { apiPost } from "@/lib/api";
import { useApi, useMutation, useRouteId } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView, SEVERITY_TONE, sevClass } from "../../_shared/WorkspaceView";

type Tab = "account_plan" | "opportunity_brief" | "win_strategy" | "action_plans" | "contact_reports";
const SUBMIT = ["oem_dm", "oem_regional", "oem_ci_planning", "oem_sales_ops", "oem_finance", "oem_cx", "oem_marketing"];
const DECIDE = ["oem_regional", "admin_tenant", "admin_dealer", "platform_admin"];

interface Artifact {
  plan_id?: string;
  version?: number;
  status?: string;
  approval_status?: string;
  author?: string | null;
  generated_at?: string;
  body: ReactNode;
  citations: { label: string; ref?: string }[];
  blocked?: boolean;
  safe_alternative?: string;
}

/** Latest versioned plan row → display artifact. */
function fromPlan(p: any): Artifact {
  return {
    plan_id: p.plan_id, version: p.version ?? 1, status: p.status, approval_status: p.approval_status,
    author: p.author_user_name || p.author_user_id, generated_at: p.dated_at || p.effective_from || p.updated_at || p.created_at,
    body: (
      <ul className="mt-2 text-[.8rem] pl-4 list-disc">
        {(p.objectives || []).map((o: any, i: number) => (
          <li key={i}><b>{kpiLabel(o.kpi)}</b>: {o.current != null ? `${o.current} → ` : ""}{o.target}{o.action ? ` — ${o.action}` : ""}
            {o.owner ? <span className="dim"> · owner {o.owner}</span> : null}
            {o.due ? <span className="dim"> · due {o.due}</span> : null}
            {o.status ? <> <Badge tone={o.status === "done" ? "green" : o.status === "at_risk" ? "red" : "gray"}>{String(o.status).replace(/_/g, " ")}</Badge></> : null}</li>
        ))}
      </ul>
    ),
    citations: (p.objectives || []).filter((o: any) => o.source_ref).map((o: any) => ({ label: o.source_ref })),
  };
}

/** One artifact tab: latest version + citations + Regenerate / Publish for review. */
function ArtifactPanel({ accountId, type, title, latest, canSubmit, canDecide, onChanged, prompt }: {
  accountId: string; type: string; title: string; latest: Artifact | null;
  canSubmit: boolean; canDecide: boolean; onChanged: () => void; prompt: string }) {
  const toast = useToast();
  const [generated, setGenerated] = useState<Artifact | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = useMutation();
  const decide = useMutation();
  const art = generated || latest;

  const regenerate = async () => {
    setBusy(true);
    try {
      // Designed contract first (§4.3); fall back to the live sandbox routes.
      let out: any = null;
      try {
        out = await apiPost<any>(`/accounts/${encodeURIComponent(accountId)}/plans/${type}/generate`);
      } catch {
        if (type === "account_plan" || type === "action_plan") {
          out = await apiPost<any>(`/oem/accounts/${encodeURIComponent(accountId)}/draft-plan`);
        } else {
          const a = await apiPost<any>("/oem/ask", { question: prompt, view: `account_${type}`, dealer_id: undefined });
          out = { generated_text: a.answer, citations: a.citations, guardrail: a.guardrail };
        }
      }
      if (out?.blocked) {
        setGenerated({ body: <p className="text-sm">{out.safe_alternative}</p>, citations: [], blocked: true, safe_alternative: out.safe_alternative });
        toast(`Blocked by guardrail — safe alternative shown`, "error");
      } else if (out?.generated_text) {
        setGenerated({
          generated_at: new Date().toISOString(),
          body: <p className="text-sm whitespace-pre-wrap">{out.generated_text}</p>,
          citations: (out.citations || []).map((c: any) => ({ label: c.label || String(c), ref: c.ref })),
        });
        toast(`${title} regenerated${out.guardrail ? " · 🛡 guardrail applied" : ""}`, "success");
      } else if (out?.plan_id) {
        setGenerated(fromPlan(out));
        toast(`Drafted ${out.plan_id} — review, then publish for review`, "success");
        onChanged();
      } else {
        toast("Nothing generated", "error");
      }
    } catch (e: any) {
      toast(e?.message || "Generate failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!art?.plan_id) return;
    const out = await submit.run(`/approvals/plan/${encodeURIComponent(art.plan_id)}/submit`);
    if (out) { toast(`Submitted ${art.plan_id} for regional review`, "success"); onChanged(); }
    else toast(submit.error || "Submit failed", "error");
  };
  const doDecide = async (decision: "approve" | "reject") => {
    if (!art?.plan_id) return;
    const out = await decide.run(`/approvals/plan/${encodeURIComponent(art.plan_id)}/decide`, { decision });
    if (out) { toast(`${art.plan_id} ${decision === "approve" ? "approved and published" : "rejected"}`, "success"); onChanged(); }
    else toast(decide.error || "Decision failed", "error");
  };

  return (
    <Card title={title}
      actions={canSubmit && (
        <span className="flex gap-2">
          <Btn kind="secondary" size="sm" disabled={busy} onClick={regenerate}>{busy ? "Generating…" : "Regenerate"}</Btn>
          <Btn kind="primary" size="sm" disabled={!art?.plan_id || submit.pending || art.blocked}
               title={art?.plan_id ? "Submit the latest version for regional review" : "Generate a saved version first"}
               onClick={publish}>Publish for review</Btn>
        </span>
      )}>
      {!art ? (
        <EmptyState>No {title.toLowerCase()} on file yet — use Regenerate to have the Field Sales Agent draft one from the account data.</EmptyState>
      ) : (
        <div className={art.blocked ? "guardrail-blocked" : undefined}>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {art.plan_id && <span className="mono">{art.plan_id}</span>}
            {art.version != null && <Badge tone="gray">v{art.version}</Badge>}
            {art.status && <Badge tone={art.status === "published" ? "green" : art.status === "submitted" ? "orange" : "gray"}>{humanizeCode(art.status)}</Badge>}
            {art.approval_status && <Badge tone={art.approval_status === "approved" ? "green" : art.approval_status === "rejected" ? "red" : "orange"}>{humanizeCode(art.approval_status)}</Badge>}
            {art.blocked && <Badge tone="red" title="A guardrail (GR-001…008) blocked the generated artifact">🛡 guardrail blocked</Badge>}
            {art.generated_at && <span className="dim"><DateTime value={art.generated_at} mode="date" /></span>}
            {art.author && <span className="dim">by {art.author}</span>}
          </div>
          {art.body}
          {art.citations.length > 0 && (
            <details className="mt-2">
              <summary className="text-xs cursor-pointer underline">Citations ({art.citations.length})</summary>
              <div className="mt-1">{art.citations.map((c, i) => <span key={i} className="data-source" title={c.ref}>{c.label}</span>)}</div>
            </details>
          )}
          {canDecide && art.approval_status === "pending" && (
            <div className="flex gap-2 mt-3">
              <Btn kind="success" size="sm" disabled={decide.pending} onClick={() => doDecide("approve")}>Approve</Btn>
              <Btn kind="secondary" size="sm" disabled={decide.pending} onClick={() => doDecide("reject")}>Reject</Btn>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

/** "District Sales vs Target" — a text summary (the Amazon Quick dashboard embeds on the Quick tab). */
function SalesVsTargetChart({ summary }: { summary: any }) {
  const sales = summary?.sales_health_index;
  return (
    <Card title="District Sales vs Target" actions={<Badge tone="gray">Summary view</Badge>}>
      <div role="img" aria-label={sales != null ? `Sales at ${(sales * 100).toFixed(0)} percent of target` : "No sales-vs-target data yet"}>
        <div className="info-row items-center">
          <span className="info-label w-28">Sales</span>
          <span className="flex-1 flex items-center gap-3">
            <ProgressBar value={(sales ?? 0) * 100} tone={sales != null && sales >= 0.7 ? "green" : "red"} />
            <span className="text-xs whitespace-nowrap">{sales != null ? <Pct value={sales} /> : "—"}</span>
          </span>
        </div>
        <div className="info-row items-center">
          <span className="info-label w-28">Target</span>
          <span className="flex-1 flex items-center gap-3">
            <ProgressBar value={100} tone="blue" />
            <span className="text-xs whitespace-nowrap"><Pct value={1} /></span>
          </span>
        </div>
      </div>
      <p className="dim text-xs mt-2">The embedded Amazon Quick &quot;District Sales vs Target&quot; dashboard renders here when Quick is enabled.</p>
    </Card>
  );
}

export default function AccountView() {
  const id = useRouteId(2);
  const { persona } = usePersona();
  const [tab, setTab] = useState<Tab>("account_plan");
  const [openPlanId, setOpenPlanId] = useState<string | null>(null);
  const q = useApi<any>(id ? `/oem/accounts/${encodeURIComponent(id)}` : null);
  const canSubmit = SUBMIT.includes(persona?.role || "");
  const canDecide = DECIDE.includes(persona?.role || "");

  return (
    <WorkspaceView view="account" dealerId={q.data?.account?.dealer_id}
      title={q.data?.account?.name || id || "Account"}
      subtitle={q.data ? <>{q.data.account.account_type} · {q.data.account.district_id} · tier {q.data.account.tier || "—"} · <Link className="underline" href="/oem/accounts">All accounts</Link></> : null}
      prompts={["Why is this dealer below peers?", "What should the win strategy focus on?"]}>
      <Async q={q} rows={8}>
        {(d) => {
          const a = d.account, s = d.summary || {};
          const plans = (d.plans || []) as any[];
          // the version picked in the history (else the newest — a fresh draft included)
          const shown = plans.find((p) => p.plan_id === openPlanId) || plans[0];
          const latestPlan = shown ? fromPlan(shown) : null;
          const kpis: Kpi[] = [
            { label: "Sales health", value: s.sales_health_index != null ? <Pct value={s.sales_health_index} /> : "—",
              trend: s.target_gap != null ? (s.target_gap >= 0 ? "up" : "down") : undefined,
              delta: s.target_gap != null ? `${s.target_gap >= 0 ? "+" : ""}${(s.target_gap * 100).toFixed(0)} pts vs peers` : undefined },
            { label: "Service health", value: s.service_health_index != null ? <Pct value={s.service_health_index} /> : "—", delta: "CSI proxy" },
            { label: "Days supply", value: a.days_supply ?? "—", trend: a.days_supply != null ? (a.days_supply <= 60 ? "up" : "down") : undefined, delta: "vs 60-day target" },
            { label: "Open alerts", value: s.open_alerts ?? 0, trend: s.high_alerts ? "down" : undefined, delta: s.high_alerts ? `${s.high_alerts} HIGH — action needed` : "none HIGH" },
          ];
          const artifactProps = { accountId: id, canSubmit, canDecide, onChanged: q.reload };
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Account read-out" sources={[d.source_ref, d.composite?.source_ref]}>
                <p>{s.at_risk ? `${a.name} is flagged at risk` + (a.condition ? ` (${String(a.condition).replace(/_/g, " ")})` : "") + ". " : `${a.name} is on track. `}
                  {plans.length ? `${plans.length} plan version(s) on file; latest status ${plans[0].status}${plans[0].approval_status ? ` / ${plans[0].approval_status}` : ""}.` : "No plan on file — regenerate the account plan to start."}
                  {d.ci_programs?.length ? ` Aligned C&I program: ${d.ci_programs[0].name}.` : ""}</p>
              </AiInsight>
              <SalesVsTargetChart summary={s} />
              <Tabs value={tab} onChange={setTab} tabs={[
                { id: "account_plan", label: "Account Plan", count: plans.length || undefined },
                { id: "opportunity_brief", label: "Opportunity Briefs" },
                { id: "win_strategy", label: "Win Strategy" },
                { id: "action_plans", label: "Action Plans" },
                { id: "contact_reports", label: "Contact Reports", count: d.contact_reports?.length }]} />
              {tab === "account_plan" && (
                <ArtifactPanel key={shown?.plan_id || "none"} {...artifactProps} type="account_plan" title="Account plan" latest={latestPlan}
                  prompt={`Draft an account plan summary for ${a.name}: objectives, gaps vs district peers, and recommended actions.`} />
              )}
              {tab === "opportunity_brief" && (
                <ArtifactPanel {...artifactProps} type="opportunity_brief" title="Opportunity brief" latest={null}
                  prompt={`Draft an opportunity brief for ${a.name}: where is the volume and share opportunity, grounded in the composite and alerts?`} />
              )}
              {tab === "win_strategy" && (
                <ArtifactPanel {...artifactProps} type="win_strategy" title="Win strategy" latest={null}
                  prompt={`Draft a win strategy for ${a.name}: how do we close the target gap this quarter?`} />
              )}
              {tab === "action_plans" && (
                <>
                  <ArtifactPanel key={shown?.plan_id || "none"} {...artifactProps} type="action_plan" title="Action plan" latest={latestPlan}
                    prompt={`Draft a volume-recovery action plan for ${a.name} with milestones and owners.`} />
                  {plans.length > 1 && (
                    <Card title="Version history">
                      {plans.map((p: any) => {
                        const open = p.plan_id === shown?.plan_id;
                        return (
                          <button key={p.plan_id} type="button" className="info-row w-full text-left"
                                  aria-pressed={open} onClick={() => setOpenPlanId(p.plan_id)}
                                  title={open ? "Shown above" : "Open this version above"}
                                  style={open ? { background: "var(--bg-active, rgba(59,130,246,.08))" } : undefined}>
                            <span className="info-label">{p.plan_id} <Badge tone="gray">v{p.version ?? 1}</Badge></span>
                            <span className="info-value">{humanizeCode(p.status)}{p.approval_status ? ` / ${humanizeCode(p.approval_status)}` : ""} · {p.status === "draft" ? "saved " : ""}<DateTime value={p.dated_at || p.effective_from} mode="date" />{open ? " · shown" : ""}</span>
                          </button>
                        );
                      })}
                    </Card>
                  )}
                </>
              )}
              {tab === "contact_reports" && (
                <Card title="Recent contact reports" actions={<Link className="text-xs underline" href="/oem/contact-reports">All contact reports</Link>}>
                  <DataTable rows={d.contact_reports || []} rowKey={(r: any) => r.report_id} empty="No contact reports."
                    columns={[
                      { key: "ts", header: "Visit", sort: (r: any) => r.visit_ts, render: (r: any) => <DateTime value={r.visit_ts} mode="date" /> },
                      { key: "type", header: "Type", render: (r: any) => r.visit_type },
                      { key: "sent", header: "Sentiment", render: (r: any) => <Badge tone={r.sentiment === "positive" ? "green" : r.sentiment === "negative" ? "red" : "gray"}>{r.sentiment || "—"}</Badge> },
                      { key: "sum", header: "Summary", render: (r: any) => <span className="text-xs">{r.summary}</span> },
                      { key: "next", header: "Next action", render: (r: any) => <span className="text-xs">{r.next_action || "—"}</span> },
                    ]} />
                </Card>
              )}
              {(d.alerts || []).length > 0 && (
                <Card title="Open alerts on this account">
                  {d.alerts.slice(0, 5).map((x: any) => (
                    <div key={x.alert_id} className={`alert-item ${sevClass(String(x.severity).toUpperCase())}`}>
                      <div className="alert-title"><Badge tone={SEVERITY_TONE[String(x.severity).toUpperCase()] || "blue"}>{String(x.severity).toUpperCase()}</Badge> {x.title}</div>
                      <div className="alert-time">{x.status} · opened <DateTime value={x.opened_at} mode="date" /></div>
                    </div>
                  ))}
                </Card>
              )}
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
