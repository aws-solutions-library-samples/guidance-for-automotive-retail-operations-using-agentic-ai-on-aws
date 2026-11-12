"use client";

// OEM Workspace · AI Guardrails (design §4 / acceptance 15b): the GR-001…008
// rule table, the "blocks this week" roll-up, and a test harness — enter a
// query, see the blocked response plus the safe alternative
// (guardrail-blocked / guardrail-compliant classes).

import { useState } from "react";
import {
  AiInsight, Async, Badge, Btn, Card, DataTable, DateTime, EmptyState, ErrorState, Field, Kpi, KpiGrid,
} from "@/components/app";
import { apiPost } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { WorkspaceView } from "../_shared/WorkspaceView";

const ACTION_TONE: Record<string, "red" | "orange" | "blue"> = { block: "red", require_approval: "orange", warn: "blue" };

export default function OemGuardrails() {
  const q = useApi<any>("/oem/guardrails", { pollMs: 300000 });
  const [text, setText] = useState("");
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const check = async () => {
    if (!text.trim()) return;
    setChecking(true);
    setError(null);
    try {
      setResult(await apiPost<any>("/oem/guardrails/check", { text, log: false }));
    } catch (e: any) {
      setError(e?.message || "Check failed");
      setResult(null);
    } finally {
      setChecking(false);
    }
  };

  const rules = (q.data?.rules || []) as any[];
  // GET /oem/guardrails returns `blocks` as a roll-up object ({total, by_rule, by_outcome, ...});
  // older/other shapes may send a plain event array. Accept both.
  const raw = q.data?.blocks as any;
  const byRule: Record<string, number> = !Array.isArray(raw) && raw?.by_rule ? raw.by_rule : {};
  const blocks = (Array.isArray(raw)
    ? raw
    : Object.entries(byRule).map(([rule_id, n]) => ({
        rule_id, rule_name: rules.find((r) => r.rule_id === rule_id)?.name || rule_id,
        action: "block", outcome: `${n} event${Number(n) === 1 ? "" : "s"} in the last ${raw?.window_days ?? 7} days`,
      }))) as any[];
  const blockTotal: number = Array.isArray(raw) ? raw.length : Number(raw?.total || 0);
  const kpis: Kpi[] = [
    { label: "Active rules", value: rules.length || 8, delta: "GR-001…GR-008" },
    { label: "Blocking", value: rules.filter((r) => r.action === "block").length, delta: "hard-blocked topics" },
    { label: "Require approval", value: rules.filter((r) => r.action === "require_approval").length, delta: "held for a person" },
    { label: "Blocks this week", value: blockTotal, trend: blockTotal ? "down" : "up",
      delta: blockTotal ? "answers rewritten or held" : "no blocks logged" },
  ];

  return (
    <WorkspaceView view="guardrails" title="AI Guardrails"
      subtitle="What the Field Sales Agent will never do, and proof that it holds"
      prompts={["What topics does the Field Sales Agent refuse?", "Why was my last answer rewritten?"]}>
      <Async q={q} rows={6}>
        {() => (
          <>
            <KpiGrid items={kpis} />
            <AiInsight title="Guardrail effectiveness" sources={["PLAT_ConfigGuardrails", "guardrail event log"]}>
              <p>{blockTotal
                ? `${blockTotal} request(s) hit a guardrail this week. Every blocked answer returns a safe alternative instead of silence, and the 🛡 shield marks any chat answer a guardrail rewrote. Use the test harness to see exactly how a query is handled.`
                : "No guardrail blocks logged this week. The rules below still screen every Field Sales Agent answer — try the test harness to see a block and its safe alternative."}</p>
            </AiInsight>
            <Card title="Test harness" actions={<Badge tone="gray">nothing you type here is sent to a dealer</Badge>}>
              <Field label="Try a query against the active rules"
                     hint={`e.g. "Should we reduce Metro Motors' wholesale allocation?" or "What price should the dealer advertise?"`}>
                {(id) => (
                  <div className="flex gap-2">
                    <input id={id} className="input flex-1" value={text} onChange={(e) => setText(e.target.value)}
                           onKeyDown={(e) => e.key === "Enter" && check()} />
                    <Btn kind="primary" disabled={checking || !text.trim()} onClick={check}>{checking ? "Checking…" : "Check"}</Btn>
                  </div>
                )}
              </Field>
              {error && <ErrorState error={error} />}
              {result && (
                <div aria-live="polite" className="mt-2">
                  {result.outcome === "pass" ? (
                    <div className="guardrail-compliant">
                      <div className="font-semibold text-sm">✓ Compliant — no guardrail triggered</div>
                      <p className="text-sm mt-1">The Field Sales Agent would answer this normally, cited from the workspace read models.</p>
                    </div>
                  ) : (
                    <div className="guardrail-blocked">
                      <div className="font-semibold text-sm">🛡 {result.outcome === "block" ? "Blocked" : result.outcome === "require_approval" ? "Held for approval" : "Warned"}</div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {(result.violations || []).map((v: any) => (
                          <Badge key={v.rule_id} tone={ACTION_TONE[String(v.action || "block")] || "red"} title={v.rule_name}>{v.rule_id} · {v.rule_name}</Badge>
                        ))}
                      </div>
                      {result.safe_alternative && (
                        <p className="text-sm mt-2"><b>Safe alternative:</b> {result.safe_alternative}</p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </Card>
            <Card title="Active rules (GR-001…008)">
              <DataTable rows={rules} rowKey={(r: any) => r.rule_id} empty="No rules loaded — the built-in GR-001…008 set applies."
                columns={[
                  { key: "id", header: "Rule", sort: (r: any) => r.rule_id, render: (r: any) => <b className="mono">{r.rule_id}</b> },
                  { key: "name", header: "What the Field Sales Agent will not do", render: (r: any) => r.name },
                  { key: "action", header: "Action", sort: (r: any) => r.action,
                    render: (r: any) => <Badge tone={ACTION_TONE[String(r.action)] || "red"}>{String(r.action).replace(/_/g, " ")}</Badge> },
                ]} />
            </Card>
            <Card title="Blocks this week">
              {blocks.length === 0 ? <EmptyState>No guardrail events logged this week.</EmptyState> : blocks.map((b: any, i: number) => (
                <div key={i} className="alert-item severity-medium">
                  <div className="alert-title"><Badge tone={ACTION_TONE[String(b.action || "block")] || "red"}>{b.rule_id}</Badge> {b.rule_name || b.rule_id}</div>
                  {b.text_sample && <div className="alert-desc">&quot;{b.text_sample}&quot;</div>}
                  <div className="alert-time">{b.outcome || "blocked"}{b.ts ? <> · <DateTime value={b.ts} /></> : null}{b.user_id ? <> · {b.user_id}</> : null}</div>
                </div>
              ))}
            </Card>
          </>
        )}
      </Async>
    </WorkspaceView>
  );
}
