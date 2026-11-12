"use client";

// Advisor (live) picker — today's active journeys for the signed-in advisor
// (appointments first), or every journey in scope. Opens /dealer/advisor/<id>.

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Async, Badge, Card, DataTable, DateTime, Page, Tabs, ViewHeader, stageLabel } from "@/components/app";
import { useApi } from "@/lib/hooks";

export default function AdvisorPicker() {
  const router = useRouter();
  const [tab, setTab] = useState<"mine" | "all">("mine");
  const q = useApi<any>(`/advisor/today${tab === "all" ? "?all=1" : ""}`);
  const open = (id: string) => router.push(`/dealer/advisor/${encodeURIComponent(id)}`);
  return (
    <Page>
      <ViewHeader title="Advisor (live)" subtitle="Pick the customer in front of you — appointments today are listed first." />
      <Card>
        <Tabs value={tab} onChange={setTab} tabs={[{ id: "mine", label: "My customers" }, { id: "all", label: "Whole store" }]} />
        <Async q={q}>
          {(d) => (
            <DataTable rows={d.journeys} rowKey={(j: any) => j.journey_id} onRowClick={(j: any) => open(j.journey_id)}
              filterText={(j: any) => `${j.customer_name} ${j.journey_id}`} filterPlaceholder="Find a customer…"
              empty="No active journeys."
              columns={[
                { key: "customer", header: "Customer", sort: (j: any) => j.customer_name, render: (j: any) => <b>{j.customer_name}</b> },
                { key: "stage", header: "Stage", sort: (j: any) => j.stage, render: (j: any) => stageLabel(j.stage) },
                { key: "appt", header: "Today", render: (j: any) => j.appointment ? <><Badge tone="green">{j.appointment.type.replace(/_/g, " ")}</Badge> <DateTime value={j.appointment.slot_start} mode="time" /></> : "—" },
                { key: "flags", header: "", render: (j: any) => <>{j.stalled && <Badge tone="red">Stalled</Badge>} {j.source === "service_lane" && <Badge tone="purple">service lane</Badge>}</> },
                { key: "dealer", header: "Dealer", render: (j: any) => j.dealer_id },
              ]} />
          )}
        </Async>
      </Card>
    </Page>
  );
}
