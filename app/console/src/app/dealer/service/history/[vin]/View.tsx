"use client";

// Service history for a VIN (§5.20.6): every visit, inspection outcome,
// declined work, recalls and the next due service, across dealers in the
// tenant. "Export" prints a customer-ready report (print stylesheet).

import Link from "next/link";
import {
  Async, Badge, Btn, Card, DataTable, DateTime, Distance, EmptyState, InfoRow, KpiGrid, MarketProvider, Money, Page,
  ViewHeader, vehicleLabel,
} from "@/components/app";
import { useApi, useRouteId } from "@/lib/hooks";
import { StageBadge } from "../../common";

export default function HistoryView() {
  const vin = useRouteId(3);
  const q = useApi<any>(vin ? `/service/history/${encodeURIComponent(vin)}` : null);
  return (
    <MarketProvider marketId={q.data?.vehicle?.market_id}>
      <Page>
        <style>{`@media print { .shell-bar, .app-sidebar, .bottom-tabs, .no-print { display: none !important; } .page { padding: 0 } }`}</style>
        <ViewHeader title="Service history" subtitle={<span className="mono">{vin}</span>}
                    actions={<Btn kind="secondary" className="no-print" onClick={() => window.print()}>Export / print</Btn>} />
        <Async q={q} rows={6}>
          {(h) => (
            <>
              <KpiGrid items={[
                { label: "Visits", value: h.visits.length },
                { label: "Red findings", value: h.visits.reduce((n: number, v: any) => n + v.inspection.red, 0) },
                { label: "Declined items", value: h.declined_work.length },
                { label: "Open recalls", value: h.recalls.length },
              ]} />
              <div className="grid gap-4 lg:grid-cols-3">
                <Card title="Vehicle">
                  <InfoRow label="Model" value={vehicleLabel(h.vehicle)} />
                  <InfoRow label="Odometer" value={<Distance value={h.vehicle?.odometer} />} />
                  <InfoRow label="In service" value={<DateTime value={h.vehicle?.delivered_at} mode="date" />} />
                  <InfoRow label="Ownership" value={h.vehicle?.ownership} />
                  {h.next_service && <InfoRow label="Next service" value={<>at <Distance value={h.next_service.next_service_mileage} /> or by <DateTime value={h.next_service.due_by} mode="date" /></>} />}
                  {h.masked && <Badge tone="gray">pricing hidden</Badge>}
                </Card>
                <Card title="Declined work" className="lg:col-span-2">
                  {h.declined_work.length ? (
                    <DataTable rows={h.declined_work} rowKey={(d: any, i) => d.declined_id || String(i)} columns={[
                      { key: "item", header: "Item", sort: (d: any) => d.item },
                      { key: "at", header: "Declined", sort: (d: any) => d.declined_at, render: (d: any) => <DateTime value={d.declined_at} mode="date" /> },
                      ...(h.masked ? [] : [{ key: "amt", header: "Quoted", num: true, render: (d: any) => <Money value={d.amount} /> }]),
                      { key: "st", header: "Status", render: (d: any) => <Badge tone={d.status === "booked" ? "green" : "orange"}>{d.status || "open"}</Badge> },
                    ]} />
                  ) : <EmptyState>No declined work.</EmptyState>}
                </Card>
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Card title="Recalls">
                  {(h.recalls || []).length ? (
                    <DataTable rows={h.recalls} rowKey={(r: any, i) => r.recall_id || String(i)} columns={[
                      { key: "t", header: "Recall", sort: (r: any) => r.title || r.recall_id, render: (r: any) => (
                        <>{r.title || r.recall_id} {r.source_ref && <span className="data-source">{r.source_ref}</span>}</>) },
                      { key: "rem", header: "Remedy", render: (r: any) => r.remedy || "remedy at dealer" },
                      { key: "st", header: "Status", render: (r: any) => (
                        <Badge tone={["completed", "closed"].includes(r.status) ? "green" : "red"}>
                          <span aria-hidden>{["completed", "closed"].includes(r.status) ? "✓" : "⚠"}</span> {r.status || "open"}
                        </Badge>) },
                    ]} />
                  ) : <EmptyState>No recalls on record for this VIN.</EmptyState>}
                </Card>
                <Card title="Prior estimates">
                  {(h.estimates || []).length ? (
                    <DataTable rows={h.estimates} rowKey={(e: any, i) => e.estimate_id || String(i)} initialSort={{ key: "at", dir: "desc" }} columns={[
                      { key: "id", header: "Estimate", render: (e: any) => <span className="mono">{e.estimate_id}</span> },
                      { key: "at", header: "Date", sort: (e: any) => e.created_at || e.presented_at || "", render: (e: any) => <DateTime value={e.created_at || e.presented_at} mode="date" /> },
                      { key: "st", header: "Status", render: (e: any) => <Badge tone={e.status === "approved" ? "green" : "blue"}>{(e.status || "").replace(/_/g, " ")}</Badge> },
                      ...(h.masked ? [] : [{ key: "total", header: "Total", num: true, render: (e: any) => <Money value={e.total} /> }]),
                    ]} />
                  ) : <EmptyState>Prior estimates aren&apos;t in the history read model yet — open a visit&apos;s RO for its estimate.</EmptyState>}
                </Card>
              </div>
              <Card title="Visits">
                {h.visits.length ? (
                  <DataTable rows={h.visits} rowKey={(v: any) => v.service_journey_id} initialSort={{ key: "opened", dir: "desc" }} columns={[
                    { key: "opened", header: "Opened", sort: (v: any) => v.opened_at, render: (v: any) => <DateTime value={v.opened_at} mode="date" /> },
                    { key: "ro", header: "RO", render: (v: any) => <Link className="underline no-print" href={`/dealer/service/${encodeURIComponent(v.service_journey_id)}`}>{v.ro_number || v.service_journey_id}</Link> },
                    { key: "dealer", header: "Dealer", render: (v: any) => v.dealer_id },
                    { key: "stage", header: "Stage", render: (v: any) => <StageBadge stage={v.stage} /> },
                    { key: "mpi", header: "MPI", render: (v: any) => <span className="flex gap-1"><Badge tone="red">{v.inspection.red}</Badge><Badge tone="orange">{v.inspection.yellow}</Badge><Badge tone="green">{v.inspection.green}</Badge></span> },
                    { key: "findings", header: "Findings", render: (v: any) => v.findings.map((f: any) => f.item).join(", ") || "—" },
                    ...(h.masked ? [] : [{ key: "total", header: "RO total", num: true, render: (v: any) => <Money value={v.ro_total} /> }]),
                  ]} />
                ) : <EmptyState>No visits on record.</EmptyState>}
              </Card>
              <div className="dim text-xs">{h.source_ref}</div>
            </>
          )}
        </Async>
      </Page>
    </MarketProvider>
  );
}
