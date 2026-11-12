"use client";

// Marketing Studio · Journey Ops (design §5.2, spec §5.16, G-08, acceptance #24).
// The OEM CX lead edits A2's nurture configuration — policy (cadence caps, quiet
// hours, stop conditions, escalation), templates with a seeded-customer preview
// and A/B variants (keyboard-operable preview toggles), consent rules by state,
// customer-surface content, and nurture analytics — as a versioned draft, then
// publishes it to PLAT_ConfigNurture; A2 reads the active version on its next
// invocation (no redeploy). Every version is kept; rollback republishes an old one.

import { humanizeCode } from "@/lib/text";
import { useEffect, useMemo, useState } from "react";
import { AiInsight, Async, Badge, Btn, Card, DataTable, DateTime, EmptyState, ErrorState, Field, Kpi, KpiGrid, LoadingBlock, Modal, Page, Pct, Tabs, ViewHeader, useToast, sourceLabel } from "@/components/app";
import { apiGet, apiPost, apiPut } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Tab = "policy" | "templates" | "consent" | "surface" | "analytics" | "proposals" | "versions";
type Cfg = Record<string, any>;
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x ?? null));

export default function JourneyOps() {
  const toast = useToast();
  const q = useApi<any>("/config/nurture");
  const analytics = useApi<any>("/config/nurture/analytics");
  const [tab, setTab] = useState<Tab>("policy");
  const [work, setWork] = useState<Cfg | null>(null);
  const [dirty, setDirty] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<null | { kind: "publish" | "rollback"; version: number }>(null);

  const base = q.data ? (q.data.draft || q.data.active) : null;
  useEffect(() => { if (base) { setWork(clone(base)); setDirty(new Set()); } }, [q.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const canEdit = Boolean(q.data?.can_edit);
  const set = (section: string, value: any) => { setWork((w) => ({ ...(w || {}), [section]: value })); setDirty((d) => new Set(d).add(section)); };

  const save = async () => {
    if (!work) return;
    setBusy(true); setErrors(null);
    try {
      const body: Cfg = {};
      dirty.forEach((s) => { body[s] = work[s]; });
      await apiPut("/config/nurture", body);
      toast("Draft saved", "success");
      await q.reload();
    } catch (e: any) { setErrors(e.message); toast("Draft not saved — fix the highlighted issues", "error"); }
    finally { setBusy(false); }
  };
  const run = async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      const out: any = confirm.kind === "publish"
        ? await apiPost("/config/nurture/publish", { version: confirm.version })
        : await apiPost("/config/nurture/rollback", { to_version: confirm.version });
      toast(confirm.kind === "publish" ? `Published v${out.published}; the Journey Agent uses it on its next run` : `Rolled back: v${out.published} now active (content of v${out.rolled_back_to})`, "success");
      setConfirm(null);
      await q.reload(); analytics.reload();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  };

  const active = q.data?.active;
  const draft = q.data?.draft;
  return (
    <Page>
      <ViewHeader title="Journey Ops" subtitle="Nurture policy, templates, consent rules and customer-surface content used by the Journey Agent"
        actions={<>
          {active && <Badge tone="green">active v{active.version}</Badge>}
          {draft && <Badge tone="orange">draft v{draft.version}</Badge>}
          {!canEdit && <Badge tone="gray">read only</Badge>}
          {canEdit && <Btn kind="secondary" size="sm" disabled={busy || dirty.size === 0} onClick={save}>{dirty.size ? `Save draft (${dirty.size})` : "Saved"}</Btn>}
          {canEdit && draft && <Btn kind="primary" size="sm" disabled={busy || dirty.size > 0} title={dirty.size ? "Save the draft first" : undefined}
                                     onClick={() => setConfirm({ kind: "publish", version: draft.version })}>Publish v{draft.version}</Btn>}
        </>} />
      {errors && <div className="mb-3"><ErrorState error={errors} /></div>}
      <Async q={q} rows={8}>
        {(d) => !work ? <EmptyState /> : (
          <>
            <Tabs value={tab} onChange={setTab} tabs={[
              { id: "policy", label: "Nurture policy" }, { id: "templates", label: "Templates", count: work.templates?.length },
              { id: "consent", label: "Consent rules" }, { id: "surface", label: "Customer surface" },
              { id: "analytics", label: "Analytics" }, { id: "proposals", label: "Journey Agent proposals" },
              { id: "versions", label: "Versions", count: d.versions.length }]} />
            {tab === "policy" && <PolicyEditor work={work} set={set} canEdit={canEdit} stopConditions={d.stop_conditions} />}
            {tab === "templates" && <TemplatesEditor work={work} set={set} canEdit={canEdit} meta={d} />}
            {tab === "consent" && <ConsentEditor work={work} set={set} canEdit={canEdit} />}
            {tab === "surface" && <SurfaceEditor work={work} set={set} canEdit={canEdit} />}
            {tab === "analytics" && <NurtureAnalytics q={analytics} />}
            {tab === "proposals" && <Proposals />}
            {tab === "versions" && (
              <Card title="Version history">
                <DataTable rows={d.versions} rowKey={(v: any) => String(v.version)}
                  columns={[
                    { key: "v", header: "Version", render: (v: any) => <b>v{v.version}</b> },
                    { key: "s", header: "Status", render: (v: any) => <Badge tone={v.status === "active" ? "green" : v.status === "draft" ? "orange" : "gray"}>{humanizeCode(v.status)}</Badge> },
                    { key: "eff", header: "Effective", render: (v: any) => <DateTime value={v.effective_from} /> },
                    { key: "by", header: "By", render: (v: any) => v.published_by || v.updated_by || "—" },
                    { key: "n", header: "Templates", num: true, render: (v: any) => v.template_count },
                    { key: "note", header: "Note", render: (v: any) => <span className="text-xs">{v.note || "—"}</span> },
                    { key: "act", header: "", render: (v: any) => canEdit && v.status === "retired"
                        ? <Btn kind="secondary" size="sm" onClick={() => setConfirm({ kind: "rollback", version: v.version })}>Roll back to this</Btn> : null },
                  ]} />
              </Card>
            )}
          </>
        )}
      </Async>
      <Modal open={!!confirm} onClose={() => setConfirm(null)}
        title={confirm?.kind === "publish" ? `Publish v${confirm?.version}?` : `Roll back to v${confirm?.version}?`}
        footer={<><Btn kind="secondary" onClick={() => setConfirm(null)}>Cancel</Btn><Btn kind="primary" disabled={busy} onClick={run}>{confirm?.kind === "publish" ? "Publish" : "Roll back"}</Btn></>}>
        <p className="text-sm">{confirm?.kind === "publish"
          ? "The Journey Agent will use this configuration for every nurture decision from its next invocation. The current active version is retired and kept for rollback."
          : "The selected version's content is republished as a new version; history is never rewritten."}</p>
      </Modal>
    </Page>
  );
}

// ---- policy -------------------------------------------------------------------

function NumField({ label, value, onChange, disabled }: { label: string; value: any; onChange: (v: number) => void; disabled?: boolean }) {
  return <Field label={label}>{(id) => <input id={id} type="number" min={0} className="input" value={value ?? ""} disabled={disabled}
                                               onChange={(e) => onChange(Number(e.target.value))} />}</Field>;
}

function PolicyEditor({ work, set, canEdit, stopConditions }: { work: Cfg; set: (s: string, v: any) => void; canEdit: boolean; stopConditions: string[] }) {
  const caps = work.cadence_caps || {};
  const pol = work.policy || {};
  const setCaps = (patch: Cfg) => set("cadence_caps", { ...caps, ...patch });
  const setPol = (patch: Cfg) => set("policy", { ...pol, ...patch });
  const stops: string[] = pol.stop_conditions || [];
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="Cadence caps">
        <div className="grid grid-cols-2 gap-3">
          <NumField label="Max per day" value={caps.per_day} disabled={!canEdit} onChange={(v) => setCaps({ per_day: v })} />
          <NumField label="Max per week" value={caps.per_week} disabled={!canEdit} onChange={(v) => setCaps({ per_week: v })} />
          {["email", "sms", "chat"].map((ch) => (
            <NumField key={ch} label={`${ch.toUpperCase()} per week`} value={caps.per_channel_week?.[ch]} disabled={!canEdit}
                      onChange={(v) => setCaps({ per_channel_week: { ...(caps.per_channel_week || {}), [ch]: v } })} />
          ))}
        </div>
        <div className="dim text-xs" title={caps.source_ref}>Source: {sourceLabel(String(caps.source_ref || "")).replace(/^curated:/, "")}</div>
      </Card>
      <Card title="Quiet hours, stop conditions, escalation">
        <div className="grid grid-cols-2 gap-3">
          {(["start", "end"] as const).map((k) => (
            <Field key={k} label={`Quiet hours ${k}`}>{(id) => <input id={id} type="time" className="input" disabled={!canEdit}
              value={pol.quiet_hours?.[k] || ""} onChange={(e) => setPol({ quiet_hours: { ...(pol.quiet_hours || {}), [k]: e.target.value } })} />}</Field>
          ))}
        </div>
        <div className="section-label">Stop conditions</div>
        <div className="flex flex-wrap gap-3 my-2">
          {stopConditions.map((s) => (
            <label key={s} className="toggle">
              <input type="checkbox" checked={stops.includes(s)} disabled={!canEdit || s === "opt_out"}
                     onChange={(e) => setPol({ stop_conditions: e.target.checked ? [...stops, s] : stops.filter((x) => x !== s) })} />
              {s.replace(/_/g, " ")}{s === "opt_out" && <Badge tone="gray">hard rule</Badge>}
            </label>
          ))}
        </div>
        <div className="section-label">Escalation</div>
        <Field label="Credit / legal questions">{(id) => (
          <select id={id} className="select" disabled={!canEdit} value={pol.escalation?.credit_or_legal_questions || "handoff_to_human"}
                  onChange={(e) => setPol({ escalation: { ...(pol.escalation || {}), credit_or_legal_questions: e.target.value } })}>
            <option value="handoff_to_human">Hand off to a human</option><option value="task_only">Create a dealer task only</option>
          </select>)}</Field>
        <Field label="Negative sentiment">{(id) => (
          <select id={id} className="select" disabled={!canEdit} value={pol.escalation?.negative_sentiment || "handoff_to_human"}
                  onChange={(e) => setPol({ escalation: { ...(pol.escalation || {}), negative_sentiment: e.target.value } })}>
            <option value="handoff_to_human">Hand off to a human</option><option value="pause_nurture">Pause nurture</option>
          </select>)}</Field>
        <NumField label="Escalate unanswered replies after (hours)" value={pol.escalation?.unanswered_after_hours} disabled={!canEdit}
                  onChange={(v) => setPol({ escalation: { ...(pol.escalation || {}), unanswered_after_hours: v } })} />
      </Card>
    </div>
  );
}

// ---- templates -------------------------------------------------------------------

function TemplatesEditor({ work, set, canEdit, meta }: { work: Cfg; set: (s: string, v: any) => void; canEdit: boolean; meta: any }) {
  const templates: any[] = work.templates || [];
  const [family, setFamily] = useState("sales");
  const [channel, setChannel] = useState("all");
  const [sel, setSel] = useState<string | null>(null);
  const [journeyId, setJourneyId] = useState("J-US-J9");
  const [preview, setPreview] = useState<any>(null);
  const [pErr, setPErr] = useState<string | null>(null);
  const shown = templates.filter((t) => t.family === family && (channel === "all" || t.channel === channel));
  const idx = templates.findIndex((t) => t.template_id === sel);
  const t = idx >= 0 ? templates[idx] : null;
  const update = (patch: Cfg) => { const next = clone(templates); next[idx] = { ...next[idx], ...patch }; set("templates", next); };
  const tokens = useMemo(() => {
    const found = new Set<string>();
    if (t) [t.subject, t.body, ...(t.variants || []).map((v: any) => `${v.subject || ""} ${v.body || ""}`)].forEach((x) => (String(x || "").match(/\{(\w+)\}/g) || []).forEach((m) => found.add(m.slice(1, -1))));
    return Array.from(found);
  }, [t]);
  const unknown = tokens.filter((k) => !meta.allowed_tokens.includes(k));

  const doPreview = async (variant?: string) => {
    if (!t) return;
    setPErr(null);
    try { setPreview({ ...(await apiPost<any>("/config/nurture/preview", { template: t, journey_id: journeyId || undefined, variant_id: variant })), variant }); }
    catch (e: any) { setPErr(e.message); setPreview(null); }
  };
  const addTemplate = () => {
    const id = `T-NEW-${Date.now().toString(36)}`;
    set("templates", [...templates, { template_id: id, family, channel: channel === "all" ? "email" : channel, stage_or_trigger: "DISCOVER",
                                      intent: "custom", subject: "", body: "Hi {first_name}, " }]);
    setSel(id);
  };
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,420px)_1fr]">
      <Card title="Templates" actions={canEdit ? <Btn kind="secondary" size="sm" onClick={addTemplate}>Add</Btn> : null}>
        <div className="filter-pills">
          {meta.families.map((f: string) => <button key={f} className={`filter-pill${family === f ? " active" : ""}`} aria-pressed={family === f} onClick={() => setFamily(f)}>{f}</button>)}
          {["all", ...meta.channels].map((c: string) => <button key={c} className={`filter-pill${channel === c ? " active" : ""}`} aria-pressed={channel === c} onClick={() => setChannel(c)}>{c}</button>)}
        </div>
        <ul className="list-none p-0 m-0 max-h-[60vh] overflow-y-auto" role="listbox" aria-label="Templates">
          {shown.map((x) => (
            <li key={x.template_id} role="option" aria-selected={x.template_id === sel}>
              <button className={`w-full text-left alert-item ${x.template_id === sel ? "severity-low" : ""}`} onClick={() => { setSel(x.template_id); setPreview(null); }}>
                <div className="alert-title">{x.stage_or_trigger} · {x.channel} {x.variants?.length ? <Badge tone="purple">A/B {x.variants.length + 1}</Badge> : null}</div>
                <div className="alert-desc truncate">{x.subject || x.body}</div>
              </button>
            </li>
          ))}
          {shown.length === 0 && <EmptyState>No templates for this filter.</EmptyState>}
        </ul>
      </Card>
      <Card title={t ? <span>{t.template_id} <Badge tone="gray">{t.family} · {t.channel}</Badge></span> : "Select a template"}>
        {!t ? <EmptyState>Choose a template to edit and preview it with a seeded customer.</EmptyState> : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Stage / trigger">{(id) => <input id={id} className="input" value={t.stage_or_trigger || ""} disabled={!canEdit} onChange={(e) => update({ stage_or_trigger: e.target.value })} />}</Field>
              <Field label="Intent">{(id) => <input id={id} className="input" value={t.intent || ""} disabled={!canEdit} onChange={(e) => update({ intent: e.target.value })} />}</Field>
            </div>
            {t.channel === "email" && <Field label="Subject">{(id) => <input id={id} className="input" value={t.subject || ""} disabled={!canEdit} onChange={(e) => update({ subject: e.target.value })} />}</Field>}
            <Field label="Body (variant A)" hint={`Placeholders: ${meta.allowed_tokens.map((k: string) => `{${k}}`).join(" ")}`}
                   error={unknown.length ? `Unknown placeholders: ${unknown.join(", ")}` : null}>
              {(id) => <textarea id={id} className="textarea" value={t.body || ""} disabled={!canEdit} onChange={(e) => update({ body: e.target.value })} />}
            </Field>
            <div className="section-label">A/B variants</div>
            {(t.variants || []).map((v: any, i: number) => (
              <div key={v.variant_id} className="alert-item">
                <div className="flex gap-2 items-center mb-2">
                  <b className="text-sm">Variant {v.variant_id}</b>
                  <label className="text-xs flex items-center gap-1">weight %<input type="number" min={0} max={100} className="input w-20" value={v.weight ?? 50} disabled={!canEdit}
                    onChange={(e) => { const vs = clone(t.variants); vs[i].weight = Number(e.target.value); update({ variants: vs }); }} /></label>
                  <Btn kind="ghost" size="sm" onClick={() => doPreview(v.variant_id)}>Preview</Btn>
                  {canEdit && <Btn kind="ghost" size="sm" onClick={() => update({ variants: t.variants.filter((_: any, j: number) => j !== i) })} aria-label={`Remove variant ${v.variant_id}`}>Remove</Btn>}
                </div>
                <textarea className="textarea" value={v.body || ""} disabled={!canEdit} aria-label={`Variant ${v.variant_id} body`}
                  onChange={(e) => { const vs = clone(t.variants); vs[i].body = e.target.value; update({ variants: vs }); }} />
              </div>
            ))}
            {canEdit && <Btn kind="secondary" size="sm" onClick={() => update({ variants: [...(t.variants || []), { variant_id: String.fromCharCode(66 + (t.variants?.length || 0)), body: t.body, weight: 50 }] })}>Add variant</Btn>}
            <div className="section-label mt-4">Preview with a seeded customer</div>
            <div className="flex gap-2 items-end">
              <Field label="Journey id">{(id) => <input id={id} className="input" value={journeyId} onChange={(e) => setJourneyId(e.target.value)} />}</Field>
              <div className="mb-[.85rem]"><Btn kind="primary" size="sm" onClick={() => doPreview()}>Preview A</Btn></div>
            </div>
            {pErr && <ErrorState error={pErr} />}
            {preview && (
              <div className="ai-insight" aria-live="polite">
                <div className="ai-insight-title">Preview{preview.variant ? ` · variant ${preview.variant}` : ""} · {preview.customer_id}</div>
                {preview.subject && <p><b>{preview.subject}</b></p>}
                <p className="whitespace-pre-wrap">{preview.body}</p>
                <div>{Object.entries(preview.facts_used || {}).map(([k, v]) => <span key={k} className="data-source">{k}={String(v)}</span>)}</div>
              </div>
            )}
            {(t.variants || []).length > 0 && <p className="dim text-xs mt-2">Variants are stored with the template version; the Journey Agent sends variant A until the variant split is enabled in the nurture engine.</p>}
          </>
        )}
      </Card>
    </div>
  );
}

// ---- consent rules ------------------------------------------------------------------

function ConsentEditor({ work, set, canEdit }: { work: Cfg; set: (s: string, v: any) => void; canEdit: boolean }) {
  const rules: Cfg = work.consent_rules || {};
  const [code, setCode] = useState("");
  const upd = (region: string, patch: Cfg) => set("consent_rules", { ...rules, [region]: { ...(rules[region] || {}), ...patch } });
  const rows = Object.entries(rules).map(([region, r]) => ({ region, ...(r as Cfg) }));
  return (
    <Card title="Consent rules by state / region" actions={canEdit ? (
      <span className="flex gap-2">
        <input className="input w-24" placeholder="e.g. CA" value={code} onChange={(e) => setCode(e.target.value.toUpperCase().slice(0, 3))} aria-label="Region code" />
        <Btn kind="secondary" size="sm" disabled={!code || Boolean(rules[code])} onClick={() => { upd(code, { sms_double_opt_in: true, call_window: "08:00-21:00" }); setCode(""); }}>Add rule</Btn>
      </span>) : null}>
      <p className="dim text-xs mb-2">Global consent (opt-in per channel, opt-out stops everything) is a hard rule; these add stricter state rules on top.</p>
      <DataTable rows={rows} rowKey={(r: any) => r.region} empty="No state-specific rules — the global consent policy applies."
        columns={[
          { key: "region", header: "Region", render: (r: any) => <b>{r.region}</b> },
          { key: "dbl", header: "SMS double opt-in", render: (r: any) => <input type="checkbox" checked={!!r.sms_double_opt_in} disabled={!canEdit} onChange={(e) => upd(r.region, { sms_double_opt_in: e.target.checked })} aria-label={`${r.region} SMS double opt-in`} /> },
          { key: "email", header: "Email only", render: (r: any) => <input type="checkbox" checked={!!r.email_only} disabled={!canEdit} onChange={(e) => upd(r.region, { email_only: e.target.checked })} aria-label={`${r.region} email only`} /> },
          { key: "win", header: "Contact window", render: (r: any) => <input className="input w-32" value={r.call_window || ""} disabled={!canEdit} onChange={(e) => upd(r.region, { call_window: e.target.value })} aria-label={`${r.region} contact window`} /> },
          { key: "x", header: "", render: (r: any) => canEdit ? <Btn kind="ghost" size="sm" onClick={() => { const n = { ...rules }; delete n[r.region]; set("consent_rules", n); }}>Remove</Btn> : null },
        ]} />
    </Card>
  );
}

// ---- customer surface ----------------------------------------------------------------

function SurfaceEditor({ work, set, canEdit }: { work: Cfg; set: (s: string, v: any) => void; canEdit: boolean }) {
  const sc: Cfg = work.surface_content || {};
  const qs: string[] = sc.intake_questions || [];
  const faq: any[] = sc.faq || [];
  const upd = (patch: Cfg) => set("surface_content", { ...sc, ...patch });
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title={`Lifestyle intake questions (${qs.length}/4)`}>
        {qs.map((x, i) => (
          <div key={i} className="flex gap-2 mb-2">
            <input className="input" value={x} disabled={!canEdit} aria-label={`Question ${i + 1}`} onChange={(e) => { const n = [...qs]; n[i] = e.target.value; upd({ intake_questions: n }); }} />
            {canEdit && <Btn kind="ghost" size="sm" onClick={() => upd({ intake_questions: qs.filter((_, j) => j !== i) })}>Remove</Btn>}
          </div>
        ))}
        {canEdit && qs.length < 4 && <Btn kind="secondary" size="sm" onClick={() => upd({ intake_questions: [...qs, ""] })}>Add question</Btn>}
        <Field label="Shortlist heading">{(id) => <input id={id} className="input mt-3" value={sc.shortlist_copy || ""} disabled={!canEdit} onChange={(e) => upd({ shortlist_copy: e.target.value })} />}</Field>
      </Card>
      <Card title="FAQ used by answer_customer_question">
        {faq.length === 0 && <EmptyState>No FAQ entries.</EmptyState>}
        {faq.map((f, i) => (
          <div key={i} className="alert-item">
            <input className="input mb-2" placeholder="Question" value={f.q || ""} disabled={!canEdit} aria-label={`FAQ ${i + 1} question`} onChange={(e) => { const n = clone(faq); n[i].q = e.target.value; upd({ faq: n }); }} />
            <textarea className="textarea" placeholder="Answer" value={f.a || ""} disabled={!canEdit} aria-label={`FAQ ${i + 1} answer`} onChange={(e) => { const n = clone(faq); n[i].a = e.target.value; upd({ faq: n }); }} />
            {canEdit && <Btn kind="ghost" size="sm" onClick={() => upd({ faq: faq.filter((_, j) => j !== i) })}>Remove</Btn>}
          </div>
        ))}
        {canEdit && <Btn kind="secondary" size="sm" onClick={() => upd({ faq: [...faq, { q: "", a: "" }] })}>Add FAQ</Btn>}
      </Card>
    </div>
  );
}

// ---- A2 proposals ---------------------------------------------------------------

/** Review items drafted by A2's weekly propose_policy_change run (§3.2,
 *  2026-09-22): template, cadence, or suggestion-matrix changes where outcomes
 *  under-perform. A2 never self-publishes — a human reviews here and applies the
 *  change through the normal draft → publish flow. Attempts the designed route
 *  and degrades with an explanation where the service does not expose it yet. */
function Proposals() {
  const [state, setState] = useState<{ loading: boolean; data: any | null; degraded: string | null; error: string | null }>({
    loading: true, data: null, degraded: null, error: null });
  useEffect(() => {
    let live = true;
    apiGet<any>("/config/nurture/proposals")
      .then((d) => { if (live) setState({ loading: false, data: d, degraded: null, error: null }); })
      .catch((e: any) => {
        if (!live) return;
        const notDeployed = e?.status === 404 || e?.status === 405 || e?.status === 501;
        setState({ loading: false, data: null, error: notDeployed ? null : (e?.message || String(e)),
                   degraded: notDeployed
                     ? "The nurture proposer's review-item feed is not exposed by this environment yet. When it ships, the Journey Agent's weekly run posts under-performing template, cadence, and suggestion-matrix changes here with their outcome evidence; a human reviews and publishes them through the normal draft flow — Journey Agent never self-publishes."
                     : null });
      });
    return () => { live = false; };
  }, []);
  if (state.loading) return <Card title="Journey Agent proposed changes"><LoadingBlock rows={3} /></Card>;
  if (state.error) return <Card title="Journey Agent proposed changes"><ErrorState error={state.error} /></Card>;
  if (state.degraded) {
    return (
      <Card title="Journey Agent proposed changes" accent="warning">
        <p className="text-sm">{state.degraded}</p>
        <p className="dim text-xs">Designed route: GET /config/nurture/proposals · spec §3.2 propose_policy_change (2026-09-22).</p>
      </Card>
    );
  }
  const rows: any[] = state.data?.proposals || [];
  return (
    <Card title={`Journey Agent proposed changes (${rows.length})`}>
      <p className="dim text-xs mb-2">Drafted weekly by the Journey Agent from the outcome loop; nothing here is live until a human applies it to the draft and publishes.</p>
      <DataTable rows={rows} rowKey={(p: any) => p.proposal_id || `${p.kind}-${p.created_at}`} empty="No open proposals — outcomes are within policy bounds."
        columns={[
          { key: "k", header: "Kind", render: (p: any) => <Badge tone="purple">{String(p.kind || "change").replace(/_/g, " ")}</Badge> },
          { key: "w", header: "Proposed change", render: (p: any) => <span className="text-sm">{p.summary || p.description || "—"}</span> },
          { key: "e", header: "Evidence", render: (p: any) => <span className="text-xs dim">{p.evidence || p.reason || "—"}</span> },
          { key: "t", header: "Proposed", render: (p: any) => <DateTime value={p.created_at} /> },
          { key: "s", header: "Status", render: (p: any) => <Badge tone={p.status === "applied" ? "green" : p.status === "dismissed" ? "gray" : "orange"}>{p.status || "open"}</Badge> },
        ]} />
    </Card>
  );
}

// ---- analytics -----------------------------------------------------------------

function NurtureAnalytics({ q }: { q: any }) {
  return (
    <Async q={q} rows={4}>
      {(a: any) => {
        const kpis: Kpi[] = [
          { label: "Touches sent", value: a.touches },
          { label: "Replies", value: a.replies },
          { label: "Reply rate", value: a.reply_rate != null ? <Pct value={a.reply_rate} /> : "—" },
          { label: "Nurture plans", value: a.plans },
        ];
        const conv = (a.by_intent as any[]).reduce((s, x) => s + x.converted, 0);
        return (
          <>
            <KpiGrid items={kpis} />
            <AiInsight title="Nurture performance" sources={[a.source_ref]}>
              <p>{conv} nurtured journey(s) reached ownership. {a.by_channel.length ? `Most replies come via ${[...a.by_channel].sort((x: any, y: any) => y.replies - x.replies)[0].channel}.` : ""}</p>
            </AiInsight>
            <div className="grid gap-4 xl:grid-cols-2">
              <Card title="By channel">
                <DataTable rows={a.by_channel} rowKey={(r: any) => r.channel} columns={[
                  { key: "c", header: "Channel", render: (r: any) => <b>{r.channel}</b> },
                  { key: "t", header: "Touches", num: true, sort: (r: any) => r.touches, render: (r: any) => r.touches },
                  { key: "r", header: "Replies", num: true, sort: (r: any) => r.replies, render: (r: any) => r.replies },
                ]} />
              </Card>
              <Card title="By intent">
                <DataTable rows={a.by_intent} rowKey={(r: any) => r.intent} empty="No nurture plans." columns={[
                  { key: "i", header: "Intent", render: (r: any) => <b>{String(r.intent).replace(/_/g, " ")}</b> },
                  { key: "p", header: "Plans", num: true, sort: (r: any) => r.plans, render: (r: any) => r.plans },
                  { key: "a", header: "Active", num: true, render: (r: any) => r.active },
                  { key: "c", header: "Converted", num: true, sort: (r: any) => r.converted, render: (r: any) => r.converted },
                ]} />
              </Card>
            </div>
          </>
        );
      }}
    </Async>
  );
}
