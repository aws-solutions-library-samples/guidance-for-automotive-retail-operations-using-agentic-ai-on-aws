"use client";

// Parts desk (§5.20.6, design doc §3.3) — parts counter home: ROs held for
// parts (with promised-time risk), open orders by ETA, back-orders with
// supersessions; per row: part_no · qty · on hand/ordered/ETA · [create order]
// [notify advisor when received]. "Received — notify advisor" emits
// parts.received so A7 resumes REPAIR. ETA / back-order status rendered as
// text; the table is keyboard-operable (sortable headers, focusable actions).

import Link from "next/link";
import { useState } from "react";
import {
  Async, Badge, Btn, Card, DataTable, DateTime, EmptyState, Field, KpiGrid, Page, Tabs, ViewHeader, useToast,
} from "@/components/app";
import { useApi, useMutation, timeAgo } from "@/lib/hooks";

type Tab = "held" | "orders" | "back" | "received";

export default function PartsDesk() {
  const toast = useToast();
  const q = useApi<any>("/service/parts/desk", { pollMs: 30000 });
  const patch = useMutation("PATCH");
  const create = useMutation();
  const [tab, setTab] = useState<Tab>("held");
  const [form, setForm] = useState({ ro_id: "", part_number: "", qty: 1, eta: "", status: "ordered", supersedes: "" });

  const update = async (o: any, body: any, label: string) => {
    const out = await patch.run(`/service/parts/orders/${encodeURIComponent(o.parts_order_id)}`, body);
    if (out) { toast(`${o.parts_order_id}: ${label}${out.parts_hold === false ? " — RO released from parts hold, advisor notified" : ""}`, "success"); q.reload(); }
    else toast(patch.error || "Update failed", "error");
  };
  const submit = async () => {
    const out = await create.run("/service/parts/orders", { ...form, eta: form.eta ? `${form.eta}T09:00:00Z` : null, supersedes: form.supersedes || null });
    if (out) { toast(`Order placed${out.description ? ` for ${out.description}` : ""}`, "success"); setForm({ ...form, part_number: "", supersedes: "" }); q.reload(); }
    else toast(create.error || "Order failed", "error");
  };

  const orderCols = [
    { key: "id", header: "Order", render: (o: any) => <span className="mono">{o.parts_order_id}</span> },
    { key: "ro", header: "RO", render: (o: any) => <Link className="underline" href={`/dealer/service/${encodeURIComponent(o.service_journey_id)}`}>{o.ro_id}</Link> },
    { key: "part", header: "Part", render: (o: any) => <>{o.description ? <>{o.description} <span className="mono text-xs dim">{o.part_number}</span></> : (o.part_number || "—")}{o.supersedes && <span className="dim text-xs"> (supersedes {o.supersedes})</span>}</> },
    { key: "qty", header: "Qty", num: true, render: (o: any) => o.qty ?? "—" },
    { key: "status", header: "Status", sort: (o: any) => o.status, render: (o: any) => <Badge tone={o.status === "backordered" ? "red" : o.status === "received" ? "green" : "blue"}>{o.status === "backordered" ? "back-ordered" : o.status}</Badge> },
    { key: "eta", header: "ETA", sort: (o: any) => o.eta, render: (o: any) => <><DateTime value={o.eta} mode="date" /> <span className="dim text-xs">{timeAgo(o.eta)}</span></> },
    { key: "act", header: "", render: (o: any) => o.status !== "received" && o.status !== "cancelled" ? (
      <span className="flex gap-1">
        <Btn kind="success" size="sm" disabled={patch.pending} onClick={() => update(o, { status: "received" }, "received")}>Received — notify advisor</Btn>
        {o.status !== "backordered" && <Btn kind="alt" size="sm" disabled={patch.pending} onClick={() => update(o, { status: "backordered" }, "back-ordered")}>Back-order</Btn>}
      </span>) : null },
  ];

  return (
    <Page>
      <ViewHeader title="Parts desk" subtitle="Held ROs, open orders, back-orders — receiving a part notifies the advisor and releases the RO back to REPAIR." />
      <Async q={q} rows={6}>
        {(d) => (
          <>
            <KpiGrid items={[
              { label: "ROs held for parts", value: d.held_ros.length, onClick: () => setTab("held") },
              { label: "Open orders", value: d.open_orders.length, onClick: () => setTab("orders") },
              { label: "Back-orders", value: d.backorders.length, trend: d.backorders.length ? "down" : undefined, delta: d.backorders.length ? "chase supplier" : "none", onClick: () => setTab("back") },
              { label: "Received recently", value: d.received_recent.length, onClick: () => setTab("received") },
            ]} />
            <div className="grid gap-4 lg:grid-cols-3">
              <Card className="lg:col-span-2">
                <Tabs value={tab} onChange={setTab} tabs={[
                  { id: "held", label: "Held ROs", count: d.held_ros.length }, { id: "orders", label: "Open orders", count: d.open_orders.length },
                  { id: "back", label: "Back-orders", count: d.backorders.length }, { id: "received", label: "Received", count: d.received_recent.length },
                ]} />
                {tab === "held" && (d.held_ros.length ? (
                  <DataTable rows={d.held_ros} rowKey={(h: any) => h.ro_id} initialSort={{ key: "eta", dir: "asc" }} columns={[
                    { key: "ro", header: "RO", render: (h: any) => <Link className="underline" href={`/dealer/service/${encodeURIComponent(h.service_journey_id)}`}>{h.ro_id}</Link> },
                    { key: "vin", header: "VIN", render: (h: any) => <span className="mono">{h.vin}</span> },
                    { key: "n", header: "Orders", num: true, render: (h: any) => h.orders },
                    { key: "eta", header: "Soonest ETA", sort: (h: any) => h.soonest_eta, render: (h: any) => <DateTime value={h.soonest_eta} mode="date" /> },
                    { key: "risk", header: "Promise risk", sort: (h: any) => (h.overdue_promise ? 0 : h.promised_at || ""), render: (h: any) =>
                      h.overdue_promise ? <Badge tone="red"><span aria-hidden>⏰</span> past promise</Badge>
                        : h.promised_at ? <>promised <DateTime value={h.promised_at} mode="datetime" /></> : "—" },
                  ]} />
                ) : <EmptyState>No ROs held for parts.</EmptyState>)}
                {tab === "orders" && <DataTable rows={d.open_orders} rowKey={(o: any) => o.parts_order_id} columns={orderCols} filterText={(o: any) => `${o.parts_order_id} ${o.ro_id} ${o.part_number || ""}`} />}
                {tab === "back" && <DataTable rows={d.backorders} rowKey={(o: any) => o.parts_order_id} columns={orderCols} />}
                {tab === "received" && <DataTable rows={d.received_recent} rowKey={(o: any) => o.parts_order_id} columns={orderCols} />}
              </Card>
              <Card title="New order">
                <Field label="RO">{(id) => <input id={id} className="input" value={form.ro_id} onChange={(e) => setForm({ ...form, ro_id: e.target.value })} placeholder="RO-…" list="held-ros" />}</Field>
                <datalist id="held-ros">{d.held_ros.map((h: any) => <option key={h.ro_id} value={h.ro_id} />)}</datalist>
                <Field label="Part number">{(id) => <input id={id} className="input" value={form.part_number} onChange={(e) => setForm({ ...form, part_number: e.target.value })} />}</Field>
                <Field label="Supersedes (optional)">{(id) => <input id={id} className="input" value={form.supersedes} onChange={(e) => setForm({ ...form, supersedes: e.target.value })} />}</Field>
                <div className="flex gap-2">
                  <Field label="Qty">{(id) => <input id={id} className="input" type="number" min={1} value={form.qty} onChange={(e) => setForm({ ...form, qty: Number(e.target.value) })} />}</Field>
                  <Field label="ETA">{(id) => <input id={id} className="input" type="date" value={form.eta} onChange={(e) => setForm({ ...form, eta: e.target.value })} />}</Field>
                </div>
                <Field label="Status">{(id) => (
                  <select id={id} className="select" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                    <option value="ordered">Ordered</option><option value="in_transit">In transit</option><option value="backordered">Back-ordered</option>
                  </select>)}</Field>
                <Btn kind="primary" className="w-full" onClick={submit} disabled={!form.ro_id || !form.part_number || create.pending}>Create order</Btn>
              </Card>
            </div>
          </>
        )}
      </Async>
    </Page>
  );
}
