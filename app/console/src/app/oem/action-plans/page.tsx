"use client";

// OEM Workspace · Action Plans (design §4.1 field tools): on-track / at-risk
// status per account, create (A6 draft via POST /oem/accounts/{id}/draft-plan)
// and track — publication always goes through regional approval.

import { kpiLabel } from "@/lib/text";
import Link from "next/link";
import { useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, DateTime, Kpi, KpiGrid, useToast,
} from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { WorkspaceView } from "../_shared/WorkspaceView";

const DRAFTERS = ["oem_dm", "oem_regional", "oem_ci_planning", "oem_sales_ops", "oem_finance", "oem_cx", "oem_marketing"];

export default function OemActionPlans() {
  const toast = useToast();
  const { persona } = usePersona();
  const accounts = useApi<any>("/oem/accounts");
  const today = useApi<any>("/oem/today");
  const draft = useMutation();
  const [drafted, setDrafted] = useState<Record<string, any>>({});
  const canDraft = DRAFTERS.includes(persona?.role || "");

  const doDraft = async (a: any) => {
    const out: any = await draft.run(`/oem/accounts/${encodeURIComponent(a.account_id)}/draft-plan`);
    if (!out) return toast(draft.error || "Draft failed", "error");
    if (out.blocked) toast(`Blocked by guardrail: ${out.safe_alternative}`, "error");
    else {
      setDrafted((m) => ({ ...m, [a.account_id]: out }));
      toast(`Drafted ${out.plan_id} for ${a.name} — open the account to review and publish`, "success");
    }
  };

  return (
    <WorkspaceView view="action-plans" title="Action Plans"
      subtitle="Draft, track and publish volume-recovery plans — the Field Sales Agent drafts, people approve"
      prompts={["Which action plans are at risk?", "Which dealer needs a plan this week?"]}>
      <Async q={accounts} rows={8}>
        {(d) => {
          const dealers = (d.accounts as any[]).filter((a) => a.account_type === "dealer");
          const atRisk = dealers.filter((a) => a.at_risk);
          const activePlans = today.data?.activity?.action_plans ?? 0;
          const kpis: Kpi[] = [
            { label: "Active plans", value: activePlans, delta: "across your scope" },
            { label: "On track", value: dealers.length - atRisk.length, trend: "up", delta: "accounts without risk flags" },
            { label: "At risk", value: atRisk.length, trend: atRisk.length ? "down" : "up",
              delta: atRisk.length ? "need a plan — action needed" : "none at risk" },
            { label: "Drafted this session", value: Object.keys(drafted).length, delta: "pending review" },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Plan coverage" sources={["OEM_Accounts", "OEM_AccountPlans"]}>
                <p>{atRisk.length
                  ? `${atRisk.length} account(s) are flagged at risk. Draft an action plan for each — the draft is grounded in the account composite and alerts, then goes to regional approval before anything publishes.`
                  : "No at-risk accounts right now. Keep existing plans on track and review milestones at the weekly district call."}</p>
              </AiInsight>
              <Card title="Accounts and plan status">
                <DataTable rows={dealers} rowKey={(a: any) => a.account_id}
                  filterText={(a: any) => `${a.name} ${a.dealer_id || ""}`} filterPlaceholder="Filter dealers…"
                  initialSort={{ key: "status", dir: "desc" }}
                  columns={[
                    { key: "name", header: "Dealer", sort: (a: any) => a.name,
                      render: (a: any) => <Link className="underline" href={`/oem/accounts/${encodeURIComponent(a.account_id)}`}><b>{a.name}</b></Link> },
                    { key: "status", header: "Status", sort: (a: any) => (a.at_risk ? 1 : 0),
                      render: (a: any) => a.at_risk ? <Badge tone="red">At risk</Badge> : <Badge tone="green">On track</Badge> },
                    { key: "cond", header: "Condition", render: (a: any) => a.condition ? <Badge tone="orange">{String(a.condition).replace(/_/g, " ")}</Badge> : "—" },
                    { key: "gap", header: "Target gap", num: true, sort: (a: any) => a.target_gap,
                      render: (a: any) => a.target_gap == null ? "—" : `${a.target_gap > 0 ? "+" : ""}${(a.target_gap * 100).toFixed(0)} pts` },
                    { key: "draft", header: "", render: (a: any) => drafted[a.account_id]
                      ? <Badge tone="green">✓ {drafted[a.account_id].plan_id} drafted</Badge>
                      : (canDraft ? <Btn kind="secondary" size="sm" disabled={draft.pending} onClick={() => doDraft(a)}>Draft plan</Btn> : null) },
                  ]} />
              </Card>
              {Object.keys(drafted).length > 0 && (
                <Card title="Drafted this session">
                  {Object.entries(drafted).map(([accountId, p]: [string, any]) => (
                    <div key={p.plan_id} className="alert-item">
                      <div className="alert-title">{p.plan_id} <Badge tone="gray">draft v{p.version ?? 1}</Badge></div>
                      <ul className="mt-2 text-[.8rem] pl-4 list-disc">
                        {(p.objectives || []).slice(0, 4).map((o: any, i: number) => (
                          <li key={i}><b>{kpiLabel(o.kpi)}</b>: {o.target}{o.action ? ` — ${o.action}` : ""}
                            {o.owner ? ` · owner ${o.owner}` : ""}{o.due ? ` · due ${o.due}` : ""}</li>
                        ))}
                      </ul>
                      <div className="alert-time">Effective <DateTime value={p.effective_from} mode="date" /> · <Link className="underline" href={`/oem/accounts/${encodeURIComponent(accountId)}`}>open account to publish for review</Link></div>
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
