"use client";

// Admin › Market & brand (§2.5, §5.17): the OEM_Markets row (MarketConfig) and
// OEM_StateRules for each market, plus brand tokens for both themes with a live
// preview. Agents and UIs read these at runtime, so no redeploy is needed.

import { useEffect, useState } from "react";
import { apiPut } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Btn, Card, DataTable, Drawer, Field, InfoRow, Page, ViewHeader, useToast } from "@/components/app";
import { MarketPicker, TenantOnly } from "../common";

const COLORS = [
  { key: "primary", label: "Customer brand primary", fallback: "#1F3A5F" },
  { key: "accent", label: "Customer brand accent", fallback: "#E07A1F" },
  { key: "oem_header", label: "OEM workspace header", fallback: "#002C5F" },
  { key: "oem_sidebar", label: "OEM workspace sidebar", fallback: "#1a2332" },
  { key: "oem_accent", label: "OEM workspace accent", fallback: "#00aad2" },
];

export default function MarketPage() {
  const toast = useToast();
  const [market, setMarket] = useState("us");
  const q = useApi<any>(`/admin/market?market_id=${market}`);
  const [form, setForm] = useState<any>(null);
  const [rule, setRule] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const m = q.data?.market;
    if (!m) return;
    const colors = m.brand?.colors || {};
    setForm({
      fuel_price_default: m.fuel_price_default, energy_price_default: m.energy_price_default,
      annual_distance_default: m.annual_distance_default,
      regulatory_disclosures: (m.regulatory_disclosures || []).join("\n"),
      brand: { name: m.brand?.name || "", tagline: m.brand?.tagline || "", captive_finance: m.brand?.captive_finance || "",
               colors: Object.fromEntries(COLORS.map((c) => [c.key, colors[c.key] || c.fallback])) },
    });
  }, [q.data]);

  const save = async () => {
    setBusy(true);
    try {
      await apiPut("/admin/market", {
        market_id: market, fuel_price_default: Number(form.fuel_price_default),
        energy_price_default: Number(form.energy_price_default), annual_distance_default: Number(form.annual_distance_default),
        regulatory_disclosures: String(form.regulatory_disclosures).split("\n").map((s) => s.trim()).filter(Boolean),
        brand: form.brand,
      });
      toast(`Market ${market} saved — formatters and brand pick it up on next load`, "success");
      q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  const saveRule = async () => {
    setBusy(true);
    try {
      const body: any = { market_id: market };
      for (const k of q.data.editable_state_fields) if (rule[k] !== undefined) body[k] = rule[k] === "" ? null : rule[k];
      await apiPut(`/admin/market/state-rules/${encodeURIComponent(rule.region_code)}`, body);
      toast(`${rule.region_code} saved`, "success"); setRule(null); q.reload();
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  const set = (k: string, v: any) => setForm({ ...form, [k]: v });
  const setBrand = (k: string, v: any) => setForm({ ...form, brand: { ...form.brand, [k]: v } });
  const setColor = (k: string, v: any) => setForm({ ...form, brand: { ...form.brand, colors: { ...form.brand.colors, [k]: v } } });

  return (
    <Page>
      <ViewHeader title="Market & brand" subtitle="MarketConfig, regional tax and fee rules, and brand tokens for every theme."
        actions={<MarketPicker value={market} onChange={setMarket} />} />
      <TenantOnly what="Market & brand">
        <Async q={q}>
          {(d) => form && (
            <>
              <div className="grid gap-4 md:grid-cols-2">
                <Card title={<span>MarketConfig <Badge tone="blue">{d.market.market_id}</Badge></span>}
                      actions={<Btn size="sm" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save market"}</Btn>}>
                  <InfoRow label="Locale" value={d.market.locale} />
                  <InfoRow label="Currency" value={d.market.currency} />
                  <InfoRow label="Distance / efficiency units" value={`${d.market.distance_unit} · ${d.market.efficiency_unit_ice} · ${d.market.efficiency_unit_ev}`} />
                  <InfoRow label="Tax model" value={d.market.tax_model || (d.market.list_price_includes_tax ? "VAT included" : "—")} />
                  <div className="grid gap-3 mt-3 md:grid-cols-3">
                    <Field label={`Fuel price default (${d.market.currency} / ${d.market.fuel_unit})`}>{(id) => <input id={id} type="number" step="0.001" className="input" value={form.fuel_price_default} onChange={(e) => set("fuel_price_default", e.target.value)} />}</Field>
                    <Field label={`Energy price default (${d.market.currency} / kWh)`}>{(id) => <input id={id} type="number" step="0.001" className="input" value={form.energy_price_default} onChange={(e) => set("energy_price_default", e.target.value)} />}</Field>
                    <Field label={`Annual distance (${d.market.distance_unit})`}>{(id) => <input id={id} type="number" className="input" value={form.annual_distance_default} onChange={(e) => set("annual_distance_default", e.target.value)} />}</Field>
                  </div>
                  <Field label="Regulatory disclosures (one per line)">{(id) => <textarea id={id} className="textarea" value={form.regulatory_disclosures} onChange={(e) => set("regulatory_disclosures", e.target.value)} />}</Field>
                </Card>
                <Card title="Brand">
                  <div className="grid gap-3 md:grid-cols-2">
                    <Field label="Brand name">{(id) => <input id={id} className="input" value={form.brand.name} onChange={(e) => setBrand("name", e.target.value)} />}</Field>
                    <Field label="Captive finance">{(id) => <input id={id} className="input" value={form.brand.captive_finance} onChange={(e) => setBrand("captive_finance", e.target.value)} />}</Field>
                  </div>
                  <Field label="Tagline">{(id) => <input id={id} className="input" value={form.brand.tagline} onChange={(e) => setBrand("tagline", e.target.value)} />}</Field>
                  <div className="grid gap-2 md:grid-cols-2">
                    {COLORS.map((c) => (
                      <label key={c.key} className="flex items-center gap-2 text-xs">
                        <input type="color" value={form.brand.colors[c.key]} onChange={(e) => setColor(c.key, e.target.value)} aria-label={c.label} />
                        <span className="mono">{form.brand.colors[c.key]}</span><span className="muted">{c.label}</span>
                      </label>
                    ))}
                  </div>
                  <div className="section-label mt-3">Preview</div>
                  <div className="grid gap-2 md:grid-cols-2 mt-1" aria-label="Theme preview">
                    <div className="rounded overflow-hidden border" style={{ borderColor: "#e5e7eb" }}>
                      <div className="px-3 py-2 text-white text-sm font-bold" style={{ background: form.brand.colors.primary }}>{form.brand.name || "Brand"} · My Garage</div>
                      <div className="p-3 bg-white text-xs" style={{ color: "#111827" }}>{form.brand.tagline}
                        <div className="mt-2"><span className="px-2 py-1 rounded text-white" style={{ background: form.brand.colors.accent }}>Book service</span></div></div>
                    </div>
                    <div className="rounded overflow-hidden border flex" style={{ borderColor: "#e5e7eb", minHeight: 90 }}>
                      <div style={{ width: 54, background: form.brand.colors.oem_sidebar }} />
                      <div className="flex-1">
                        <div className="px-3 py-2 text-white text-xs font-bold tracking-widest" style={{ background: form.brand.colors.oem_header }}>DISTRICT MANAGER ASSISTANT</div>
                        <div className="p-2 bg-white text-xs" style={{ color: "#000", borderLeft: `3px solid ${form.brand.colors.oem_accent}` }}>Today · KPIs</div>
                      </div>
                    </div>
                  </div>
                </Card>
              </div>
              <Card title={`Regional rules (${d.state_rules.length})`}>
                <DataTable rows={d.state_rules} rowKey={(r: any) => r.region_code} dense
                  filterText={(r: any) => `${r.region_code} ${r.region_name || ""}`} filterPlaceholder="Filter regions…"
                  onRowClick={(r: any) => setRule({ ...r })}
                  columns={[
                    { key: "code", header: "Region", sort: (r: any) => r.region_code, render: (r: any) => <span><b className="mono">{r.region_code}</b> <span className="muted">{r.region_name}</span></span> },
                    { key: "tax", header: "Sales tax %", num: true, sort: (r: any) => r.sales_tax_pct ?? -1, render: (r: any) => r.sales_tax_pct ?? r.vat_pct ?? "—" },
                    { key: "local", header: "Avg local %", num: true, sort: (r: any) => r.avg_local_tax_pct ?? -1, render: (r: any) => r.avg_local_tax_pct ?? "—" },
                    { key: "doc", header: "Doc-fee cap", num: true, render: (r: any) => r.doc_fee_cap ?? "none" },
                    { key: "ev", header: "EV fee", num: true, render: (r: any) => r.ev_annual_fee ?? "—" },
                    { key: "fuel", header: "Fuel price", num: true, render: (r: any) => r.fuel_price ?? "—" },
                    { key: "energy", header: "Energy price", num: true, render: (r: any) => r.energy_price ?? "—" },
                    { key: "verify", header: "", render: (r: any) => r.verify ? <Badge tone="orange">verify</Badge> : <Badge tone="green">reviewed</Badge> },
                  ]} />
              </Card>
            </>
          )}
        </Async>
      </TenantOnly>
      <Drawer open={!!rule} onClose={() => setRule(null)} title={`Edit ${rule?.region_code || ""}`}
        footer={<><Btn kind="secondary" onClick={() => setRule(null)}>Cancel</Btn><Btn onClick={saveRule} disabled={busy}>Save</Btn></>}>
        {rule && q.data?.editable_state_fields.map((k: string) => (
          <Field key={k} label={k.replace(/_/g, " ")}>{(id) => (
            k === "vehicle_tax_note"
              ? <textarea id={id} className="textarea" value={rule[k] ?? ""} onChange={(e) => setRule({ ...rule, [k]: e.target.value })} />
              : <input id={id} type="number" step="0.01" className="input" value={rule[k] ?? ""} onChange={(e) => setRule({ ...rule, [k]: e.target.value === "" ? "" : Number(e.target.value) })} />
          )}</Field>
        ))}
        {rule?.source_refs && <div className="dim text-xs">Sources: {rule.source_refs.join(" · ")}</div>}
      </Drawer>
    </Page>
  );
}
