"use client";

// OEM Workspace · Compliance (design §4.1 intelligence): dealer standards
// compliance checklist / status from the per-account compliance read model.

import { humanizeCode } from "@/lib/text";
import { useState } from "react";
import {
  AiInsight, Async, Badge, Card, DataTable, EmptyState, Kpi, KpiGrid,
} from "@/components/app";
import { useApi } from "@/lib/hooks";
import { WorkspaceView } from "../_shared/WorkspaceView";

const toneFor = (status: string) =>
  String(status).toLowerCase().startsWith("non") ? "red" : String(status).toLowerCase() === "conditional" ? "orange" : "green";

export default function OemCompliance() {
  const accounts = useApi<any>("/oem/accounts");
  const [sel, setSel] = useState<any>(null);
  const detail = useApi<any>(sel ? `/oem/accounts/${encodeURIComponent(sel.account_id)}/compliance` : null);
  return (
    <WorkspaceView view="compliance" title="Compliance" dealerId={sel?.dealer_id}
      subtitle="Dealer standards compliance checklist and status"
      prompts={["Which dealers are non-compliant?", "What should the compliance review cover?"]}>
      <Async q={accounts} rows={8}>
        {(d) => {
          const dealers = (d.accounts as any[]).filter((a) => a.account_type === "dealer");
          const flagged = dealers.filter((a) => a.condition);
          const kpis: Kpi[] = [
            { label: "Dealers", value: dealers.length, delta: "in scope" },
            { label: "Condition flags", value: flagged.length, trend: flagged.length ? "down" : "up",
              delta: flagged.length ? "review standards — action needed" : "none flagged" },
            { label: "Selected dealer", value: sel && detail.data ? String(detail.data.overall) : "—",
              trend: sel && detail.data ? (detail.data.overall === "fully" ? "up" : "down") : undefined,
              delta: sel ? sel.name : "select a dealer" },
            { label: "Areas needing action", value: sel && detail.data ? (detail.data.needs_action?.length ?? 0) : "—",
              delta: "facility · training · process standards" },
          ];
          return (
            <>
              <KpiGrid items={kpis} />
              <AiInsight title="Compliance priorities" sources={["OEM_Accounts", "OEM_Compliance"]}>
                <p>{flagged.length
                  ? `${flagged.length} dealer(s) carry a condition flag (${flagged.slice(0, 3).map((a) => a.name).join(", ")}${flagged.length > 3 ? "…" : ""}). Open each checklist, agree a cure plan with the GM, and track it as an action-plan milestone.`
                  : "No condition flags in scope. Select a dealer to review the standards checklist before the next franchise review."}</p>
              </AiInsight>
              <Card title="Dealers">
                <DataTable rows={dealers} rowKey={(a: any) => a.account_id}
                  onRowClick={(a: any) => setSel(a)}
                  filterText={(a: any) => `${a.name} ${a.dealer_id || ""}`} filterPlaceholder="Filter dealers…"
                  columns={[
                    { key: "name", header: "Dealer", sort: (a: any) => a.name,
                      render: (a: any) => <span><b>{a.name}</b> <span className="dim text-xs">{a.dealer_id}</span></span> },
                    { key: "cond", header: "Condition", sort: (a: any) => a.condition || "",
                      render: (a: any) => a.condition ? <Badge tone="orange">{String(a.condition).replace(/_/g, " ")}</Badge> : <span className="dim text-xs">none</span> },
                    { key: "risk", header: "Status", render: (a: any) => a.at_risk ? <Badge tone="red">At risk</Badge> : <Badge tone="green">On track</Badge> },
                    { key: "open", header: "", render: () => <span className="text-xs underline">Checklist →</span> },
                  ]} />
              </Card>
              {sel && (
                <Card title={<span>{sel.name} — standards checklist {detail.data && <Badge tone={toneFor(detail.data.overall)}>{detail.data.overall}</Badge>}</span>}
                      actions={<button className="text-xs underline" onClick={() => setSel(null)}>Close</button>}>
                  <Async q={detail} rows={4}>
                    {(r) => !r.by_area?.length ? <EmptyState>No compliance records for this dealer.</EmptyState> : (
                      <>
                        {r.by_area.map((c: any) => (
                          <div key={c.area} className="info-row">
                            <span className="info-label">{String(c.area).replace(/_/g, " ")}</span>
                            <span className="info-value"><Badge tone={toneFor(c.status)}>{humanizeCode(c.status)}</Badge></span>
                          </div>
                        ))}
                        {r.source_ref && <div className="dim text-xs mt-2">{r.source_ref}</div>}
                      </>
                    )}
                  </Async>
                </Card>
              )}
            </>
          );
        }}
      </Async>
    </WorkspaceView>
  );
}
