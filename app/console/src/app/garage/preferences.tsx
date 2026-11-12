"use client";

// Preferences & consent (design §6.1, spec §5.12, §8.2): channels, quiet hours,
// unsubscribe, data download / delete — every control clearly labeled and
// keyboard reachable (WCAG). Saving writes CUST_Consent, which the nurture
// agent's consent check reads on the next cycle. Also used in-thread on /shop/chat.

import { useEffect, useState } from "react";
import { Async, Badge, Btn, Card, DateTime, Field, SectionLabel, useToast } from "@/components/app";
import { apiPut } from "@/lib/api";
import { useApi, useMutation } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";

export function Preferences({ compact }: { compact?: boolean }) {
  const { persona } = usePersona();
  const cid = persona?.customerId || persona?.userId || "";
  const q = useApi<any>(cid ? `/customers/${encodeURIComponent(cid)}/consent` : null);
  const req = useMutation();
  const toast = useToast();
  const [form, setForm] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (q.data) setForm({ ...q.data, quiet_hours: q.data.quiet_hours || { start: "", end: "" } }); }, [q.data]);

  const save = async (patch?: any) => {
    setSaving(true);
    try {
      const body = patch || {
        email_opt_in: !!form.email_opt_in, sms_opt_in: !!form.sms_opt_in, voice_opt_in: !!form.voice_opt_in,
        preferred_channel: form.preferred_channel || "email",
        quiet_hours: form.quiet_hours?.start && form.quiet_hours?.end ? form.quiet_hours : null,
      };
      const out: any = await apiPut(`/customers/${encodeURIComponent(cid)}/consent`, body);
      toast(out.changed?.length ? "Preferences saved" : "No changes", "success");
      q.reload();
    } catch (e: any) { toast(e.message || "Could not save", "error"); }
    finally { setSaving(false); }
  };
  const dataRequest = async (kind: "download" | "delete") => {
    if (kind === "delete" && !window.confirm("Request deletion of your personal data? Your dealer will confirm before anything is removed.")) return;
    const out = await req.run("/garage/data-request", { kind });
    if (out) { toast(kind === "download" ? "Download requested — we'll notify you when it's ready" : "Deletion requested", "success"); q.reload(); }
    else toast(req.error || "Could not submit", "error");
  };
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));

  return (
    <Card title="Preferences & consent">
      <Async q={q}>
        {() => !form ? null : (
          <div className="flex flex-col gap-3">
            <fieldset className="flex flex-wrap gap-4 border-0 p-0 m-0">
              <legend className="text-xs font-semibold muted mb-1">Contact me by</legend>
              {[["email_opt_in", "Email"], ["sms_opt_in", "Text message"], ["voice_opt_in", "Phone call"]].map(([k, l]) => (
                <label key={k} className="toggle min-h-[44px]">
                  <input type="checkbox" checked={!!form[k]} onChange={(e) => set(k, e.target.checked)} /> {l}
                </label>
              ))}
            </fieldset>
            <div className="grid gap-3 md:grid-cols-3">
              <Field label="Preferred channel">
                {(id) => (
                  <select id={id} className="select" value={form.preferred_channel || "email"} onChange={(e) => set("preferred_channel", e.target.value)}>
                    <option value="email">Email</option><option value="sms">Text</option><option value="chat">Chat</option><option value="voice">Phone</option>
                  </select>
                )}
              </Field>
              <Field label="Quiet hours — no messages from">
                {(id) => <input id={id} className="input" type="time" value={form.quiet_hours?.start || ""}
                                onChange={(e) => set("quiet_hours", { ...form.quiet_hours, start: e.target.value })} />}
              </Field>
              <Field label="Quiet hours — until">
                {(id) => <input id={id} className="input" type="time" value={form.quiet_hours?.end || ""}
                                onChange={(e) => set("quiet_hours", { ...form.quiet_hours, end: e.target.value })} />}
              </Field>
            </div>
            <div className="flex flex-wrap gap-2">
              <Btn kind="primary" onClick={() => save()} disabled={saving}>{saving ? "Saving…" : "Save preferences"}</Btn>
              {!q.data?.unsubscribed_all
                ? <Btn kind="alt" onClick={() => window.confirm("Stop all automated messages?") && save({ unsubscribe_all: true })} disabled={saving}>
                    Unsubscribe from all automated messages
                  </Btn>
                : <Badge tone="orange">Unsubscribed from automated messages</Badge>}
            </div>
            {!compact && (
              <div className="flex flex-col gap-2 pt-2" style={{ borderTop: "1px solid var(--border)" }}>
                <SectionLabel>Your data</SectionLabel>
                <div className="flex gap-2 flex-wrap">
                  <Btn kind="alt" size="sm" onClick={() => dataRequest("download")} disabled={req.pending}>Download my data</Btn>
                  <Btn kind="alt" size="sm" onClick={() => dataRequest("delete")} disabled={req.pending}>Delete my data</Btn>
                </div>
                {q.data?.data_requests?.map((r: any) => (
                  <div key={r.request_id} className="text-xs muted">
                    {r.kind === "download" ? "Download" : "Deletion"} request · {r.status} · <DateTime value={r.requested_at} />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </Async>
    </Card>
  );
}
