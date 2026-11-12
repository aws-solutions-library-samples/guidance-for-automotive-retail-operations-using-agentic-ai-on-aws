"use client";

// Admin › Audit (§5.17, §8): journey events, service-journey events, guardrail
// events, and admin actions — filter by date and text, export to CSV (the
// export itself is audit-logged).

import { useState } from "react";
import { apiGet } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Btn, Card, DataTable, Field, Page, Tabs, ViewHeader, useToast } from "@/components/app";
import { downloadFile, TenantOnly } from "../common";

type Kind = "admin_actions" | "journey_events" | "service_events" | "guardrail_events";

export default function AuditPage() {
  const toast = useToast();
  const [kind, setKind] = useState<Kind>("admin_actions");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [text, setText] = useState("");
  const [applied, setApplied] = useState({ since: "", until: "", q: "" });
  const qs = new URLSearchParams({ kind, limit: "500" });
  if (applied.since) qs.set("since", applied.since);
  if (applied.until) qs.set("until", applied.until);
  if (applied.q) qs.set("q", applied.q);
  const q = useApi<any>(`/admin/audit?${qs.toString()}`);
  const [busy, setBusy] = useState(false);

  const exportCsv = async () => {
    setBusy(true);
    try {
      const qx = new URLSearchParams(qs); qx.set("limit", "5000");
      const r = await apiGet<any>(`/admin/audit/export?${qx.toString()}`);
      downloadFile(r.filename, r.csv);
      toast(`Exported ${r.rows} rows${r.truncated ? " (truncated at 5,000)" : ""}`, "success");
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  return (
    <Page>
      <ViewHeader title="Audit" subtitle="Who did what, when — across agents, journeys, guardrails, and administrators."
        actions={<Btn kind="secondary" size="sm" onClick={exportCsv} disabled={busy}>{busy ? "Exporting…" : "Export CSV"}</Btn>} />
      <TenantOnly what="Audit export">
        <Card>
          <Tabs value={kind} onChange={setKind} tabs={[
            { id: "admin_actions", label: "Admin actions" }, { id: "journey_events", label: "Journey events" },
            { id: "service_events", label: "Service events" }, { id: "guardrail_events", label: "Guardrail events" }]} />
          <form className="flex flex-wrap items-end gap-3 mb-3" onSubmit={(e) => { e.preventDefault(); setApplied({ since, until, q: text }); }}>
            <Field label="From">{(id) => <input id={id} type="date" className="input" value={since} onChange={(e) => setSince(e.target.value)} />}</Field>
            <Field label="To">{(id) => <input id={id} type="date" className="input" value={until} onChange={(e) => setUntil(e.target.value)} />}</Field>
            <Field label="Contains">{(id) => <input id={id} className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="id, actor, rule…" />}</Field>
            <div className="field"><Btn type="submit" size="sm">Apply</Btn></div>
          </form>
          <Async q={q}>
            {(d) => (
              <>
                <div className="dim text-xs mb-2">{d.total} rows{d.truncated ? " · showing newest 500" : ""}</div>
                <DataTable dense rows={d.rows} rowKey={(r: any, i) => `${i}`}
                  columns={d.columns.map((c: string) => ({
                    key: c, header: c.replace(/_/g, " "), sort: (r: any) => r[c],
                    render: (r: any) => c === "outcome" ? <Badge tone={["ok", "allowed"].includes(r[c]) ? "green" : r[c] === "blocked" ? "red" : "orange"}>{r[c]}</Badge>
                      : <span className={c.endsWith("_id") || c === "tool" || c === "target" ? "mono text-xs" : "text-xs"}>{String(r[c] ?? "—")}</span>,
                  }))} />
              </>
            )}
          </Async>
        </Card>
      </TenantOnly>
    </Page>
  );
}
