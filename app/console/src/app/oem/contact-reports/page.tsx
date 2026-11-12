"use client";

// OEM Workspace · Contact Reports (design §4.1 field tools): recent reports
// with a per-dealer filter, plus a new-report form with voice dictation
// (useVoiceAssistant, AR-7). The list is mined from the Today payload and the
// selected dealer's account detail; filing posts to the designed route and
// keeps an unsynced draft locally when the write API is not deployed yet.

import { useMemo, useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DateTime, EmptyState, Field, Kpi, KpiGrid, cx, useToast,
} from "@/components/app";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { useVoiceAssistant } from "@/lib/voice";
import { WorkspaceView } from "../_shared/WorkspaceView";

const VISIT_TYPES = ["in_person", "phone", "video", "email"];

interface Followup { text: string; due: string }

export default function OemContactReports() {
  const toast = useToast();
  const today = useApi<any>("/oem/today");
  const accounts = useApi<any>("/oem/accounts");
  const [dealer, setDealer] = useState<string>("");
  const dealers = useMemo(() => ((accounts.data?.accounts || []) as any[]).filter((a) => a.account_type === "dealer" && a.dealer_id), [accounts.data]);
  const selAccount = dealers.find((a) => a.dealer_id === dealer);
  const detail = useApi<any>(selAccount ? `/oem/accounts/${encodeURIComponent(selAccount.account_id)}` : null);

  // --- new-report form ------------------------------------------------------
  const [form, setForm] = useState({ dealer_id: "", visit_type: "in_person", topics: "", summary: "", next_action: "", sentiment: "neutral" });
  const [followups, setFollowups] = useState<Followup[]>([]);
  const [drafts, setDrafts] = useState<any[]>([]);
  const [saving, setSaving] = useState(false);
  const voice = useVoiceAssistant({ continuous: false, onFinal: (t) => setForm((f) => ({ ...f, summary: f.summary ? `${f.summary} ${t}` : t })) });
  const setFollowup = (i: number, patch: Partial<Followup>) =>
    setFollowups((xs) => xs.map((f, j) => (j === i ? { ...f, ...patch } : f)));

  const file = async () => {
    if (!form.dealer_id || !form.summary.trim()) { toast("Pick a dealer and dictate or type a summary", "error"); return; }
    setSaving(true);
    const report = {
      ...form,
      topics: form.topics.split(",").map((t) => t.trim()).filter(Boolean),
      followups: followups.filter((f) => f.text.trim()),
      visit_ts: new Date().toISOString(),
    };
    try {
      await apiPost("/oem/contact-reports", report);
      toast("Contact report filed", "success");
      today.reload();
    } catch (e: any) {
      setDrafts((xs) => [{ ...report, report_id: `local-${Date.now()}`, unsynced: true }, ...xs]);
      toast(`Saved locally — the write API is not reachable (${e?.message || "error"}). It will show as unsynced.`, "info");
    } finally {
      setForm({ dealer_id: form.dealer_id, visit_type: "in_person", topics: "", summary: "", next_action: "", sentiment: "neutral" });
      setFollowups([]);
      setSaving(false);
    }
  };

  const recent: any[] = [
    ...drafts.filter((r) => !dealer || r.dealer_id === dealer),
    ...(dealer
      ? (detail.data?.contact_reports || [])
      : (today.data?.activity?.recent_reports || [])),
  ];
  const filedThisWeek = today.data?.activity?.contact_reports ?? 0;
  const kpis: Kpi[] = [
    { label: "Filed this week", value: filedThisWeek, trend: filedThisWeek > 0 ? "up" : undefined,
      delta: filedThisWeek > 0 ? "on pace vs weekly cadence" : "none yet — file after each visit" },
    { label: "Dealers in scope", value: dealers.length, delta: `${today.data?.kpis?.dealers_at_risk ?? 0} at risk` },
    { label: "Unsynced drafts", value: drafts.length, trend: drafts.length ? "down" : undefined,
      delta: drafts.length ? "retry when online" : "all synced" },
    { label: "Dictation", value: voice.engine === "none" ? "Off" : "Ready",
      delta: voice.engine === "none" ? "no microphone engine available" : `engine: ${voice.engine}` },
  ];

  return (
    <WorkspaceView view="contact-reports" title="Contact Reports"
      subtitle="File a report after every dealer contact — dictation-first on mobile"
      prompts={["Summarize my last visit to the weakest dealer", "Which dealers have no contact report this month?"]}>
      <KpiGrid items={kpis} />
      <AiInsight title="Contact cadence" sources={["OEM_ContactReports", "OEM_Accounts"]}>
        <p>{filedThisWeek > 0
          ? `${filedThisWeek} report(s) filed this week. Prioritize an in-person visit for each at-risk dealer without a recent report, and dictate the summary on the way out of the store.`
          : "No contact reports this week yet. Start with the at-risk dealers — a dictated summary right after the visit is the highest-signal input to the account plan."}</p>
      </AiInsight>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="New contact report">
          <Field label="Dealer">
            {(id) => (
              <select id={id} className="input" value={form.dealer_id} onChange={(e) => setForm({ ...form, dealer_id: e.target.value })}>
                <option value="">Select a dealer…</option>
                {dealers.map((a) => <option key={a.dealer_id} value={a.dealer_id}>{a.name} ({a.dealer_id})</option>)}
              </select>
            )}
          </Field>
          <Field label="Visit type">
            {(id) => (
              <select id={id} className="input" value={form.visit_type} onChange={(e) => setForm({ ...form, visit_type: e.target.value })}>
                {VISIT_TYPES.map((v) => <option key={v} value={v}>{v.replace(/_/g, " ")}</option>)}
              </select>
            )}
          </Field>
          <Field label="Key topics" hint="Comma-separated, e.g. allocation, EV certification, weekend staffing">
            {(id) => <input id={id} className="input" value={form.topics} onChange={(e) => setForm({ ...form, topics: e.target.value })} />}
          </Field>
          <Field label="Summary" hint={voice.engine !== "none" ? "Use the mic to dictate — the text lands here." : undefined}>
            {(id) => (
              <div className="flex gap-2 items-start">
                <textarea id={id} className="input flex-1" rows={4}
                          value={voice.interim ? `${form.summary} ${voice.interim}` : form.summary}
                          onChange={(e) => setForm({ ...form, summary: e.target.value })} />
                {voice.engine !== "none" && (
                  <button type="button" className={cx("mic-btn", voice.listening && "listening")} onClick={voice.toggle}
                          aria-pressed={voice.listening} aria-label={voice.listening ? "Stop dictation" : "Dictate summary"}>🎤</button>
                )}
              </div>
            )}
          </Field>
          <Field label="Next action">
            {(id) => <input id={id} className="input" value={form.next_action} onChange={(e) => setForm({ ...form, next_action: e.target.value })} />}
          </Field>
          <Field label="Follow-up items" hint="Each follow-up becomes a dealer task with a deadline.">
            {() => (
              <div className="flex flex-col gap-2">
                {followups.map((f, i) => (
                  <div key={i} className="flex gap-2 items-center">
                    <input className="input flex-1" placeholder="Follow-up item…" aria-label={`Follow-up item ${i + 1}`}
                           value={f.text} onChange={(e) => setFollowup(i, { text: e.target.value })} />
                    <input className="input" type="date" aria-label={`Follow-up ${i + 1} deadline`}
                           value={f.due} onChange={(e) => setFollowup(i, { due: e.target.value })} />
                    <button type="button" className="text-xs underline" aria-label={`Remove follow-up ${i + 1}`}
                            onClick={() => setFollowups((xs) => xs.filter((_, j) => j !== i))}>Remove</button>
                  </div>
                ))}
                <Btn kind="secondary" size="sm" onClick={() => setFollowups((xs) => [...xs, { text: "", due: "" }])}>+ Add follow-up</Btn>
              </div>
            )}
          </Field>
          <Field label="Sentiment">
            {(id) => (
              <select id={id} className="input" value={form.sentiment} onChange={(e) => setForm({ ...form, sentiment: e.target.value })}>
                <option value="positive">positive</option><option value="neutral">neutral</option><option value="negative">negative</option>
              </select>
            )}
          </Field>
          {voice.error && <div className="text-[.75rem]" style={{ color: "var(--danger)" }}>{voice.error}</div>}
          <Btn kind="primary" disabled={saving} onClick={file}>{saving ? "Filing…" : "File report"}</Btn>
        </Card>
        <Card title="Recent reports"
          actions={
            <select className="input text-xs" aria-label="Filter by dealer" value={dealer} onChange={(e) => setDealer(e.target.value)}>
              <option value="">All dealers</option>
              {dealers.map((a) => <option key={a.dealer_id} value={a.dealer_id}>{a.name}</option>)}
            </select>
          }>
          <Async q={dealer ? detail : today} rows={4}>
            {() => recent.length === 0 ? <EmptyState>No contact reports{dealer ? " for this dealer" : ""} yet.</EmptyState> : (
              <>
                {recent.map((r: any) => (
                  <div key={r.report_id} className="alert-item">
                    <div className="alert-title">{r.dealer_id} · {String(r.visit_type || "").replace(/_/g, " ")}
                      {" "}<Badge tone={r.sentiment === "positive" ? "green" : r.sentiment === "negative" ? "red" : "gray"}>{r.sentiment || "—"}</Badge>
                      {r.unsynced && <Badge tone="orange" title="Not yet saved to the API">unsynced</Badge>}
                      {r.ai_prep && <Badge tone="blue" title="The Field Sales Agent prepared next-visit focus from this report">AI Prep: {String(r.ai_prep)}</Badge>}</div>
                    <div className="alert-desc">{r.summary}</div>
                    {Array.isArray(r.topics) && r.topics.length > 0 && (
                      <div className="mt-1">{r.topics.map((t: string) => <Badge key={t} tone="gray">{t}</Badge>)}</div>
                    )}
                    {Array.isArray(r.followups) && r.followups.length > 0 && (
                      <ul className="mt-1 text-[.75rem] pl-4 list-disc">
                        {r.followups.map((f: any, i: number) => <li key={i}>{f.text}{f.due ? ` — due ${f.due}` : ""}</li>)}
                      </ul>
                    )}
                    <div className="alert-time"><DateTime value={r.visit_ts} mode="date" />{r.next_action ? <> · next: {r.next_action}</> : null}</div>
                  </div>
                ))}
              </>
            )}
          </Async>
        </Card>
      </div>
    </WorkspaceView>
  );
}
