"use client";

// Admin Console "Demo data" card (tenant admins, dev demo stack only): how many rehearsals
// each end-to-end scenario has left, the last reset, and a Reset button. The reset runs in
// the demo-reset Lambda (app/demo/reset_handler.py) for longer than the API may run, so the card starts it
// and polls GET /admin/demo until the status row says done or failed.

import { useEffect, useRef, useState } from "react";
import { Badge, Btn, Card, DataTable, DateTime, useToast } from "@/components/app";
import { useApi, useMutation } from "@/lib/hooks";
import { ConfirmModal } from "./common";

interface Scenario { scenario_id: string; name: string; runs_left: number; uses: string; detail: string }
interface LastReset {
  state: "running" | "done" | "failed" | "stale"; actor?: string; actor_name?: string; started_at?: string; finished_at?: string;
  removed?: number; written?: number; tables?: number; seconds?: number; backup_tag?: string; error?: string;
}
interface DemoInfo {
  enabled: boolean; env: string; reason?: string; confirm_word: string;
  last_reset?: LastReset | null; readiness?: Scenario[] | null; readiness_error?: string;
}

const UNLIMITED = 99;

function StateBadge({ s }: { s: LastReset }) {
  if (s.state === "running") return <Badge tone="blue">resetting…</Badge>;
  if (s.state === "done") return <Badge tone="green">done</Badge>;
  if (s.state === "stale") return <Badge tone="orange">did not finish</Badge>;
  return <Badge tone="red">failed</Badge>;
}

export function DemoDataCard() {
  const toast = useToast();
  const [running, setRunning] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const q = useApi<DemoInfo>("/admin/demo", { pollMs: running ? 10000 : 0, background: true });
  const m = useMutation<{ confirm: string }, { started: boolean }>("POST");
  const last = q.data?.last_reset || null;
  const wasRunning = useRef(false);

  // follow the status row: poll while it says running, report the outcome once
  useEffect(() => {
    const now = last?.state === "running";
    setRunning(now);
    if (wasRunning.current && !now && last) {
      if (last.state === "done") toast(`Demo data reset: ${last.removed ?? 0} rows removed, ${last.written ?? 0} seed rows written`, "success");
      else toast(`Demo reset ${last.state === "stale" ? "did not finish" : "failed"}${last.error ? `: ${last.error}` : ""}`, "error");
    }
    wasRunning.current = now;
  }, [last, toast]);

  const start = async () => {
    setConfirm(false);
    const out = await m.run("/admin/demo/reset", { confirm: q.data?.confirm_word || "reset" });
    if (out?.started) {
      toast("Demo reset started — this takes about a minute", "info");
      wasRunning.current = true;
      setRunning(true);
      q.reload();
    }
  };

  if (q.error) {
    return <Card title="Demo data"><p className="muted text-sm m-0">Demo data status is unavailable: {q.error}</p></Card>;
  }
  const d = q.data;
  if (!d) return <Card title="Demo data"><p className="muted text-sm m-0">Loading…</p></Card>;
  if (!d.enabled) return <Card title="Demo data"><p className="muted text-sm m-0">{d.reason}</p></Card>;

  const rows = d.readiness || [];
  const dry = rows.filter((s) => s.runs_left <= 0).length;
  const low = rows.filter((s) => s.runs_left > 0 && s.runs_left < 3).length;
  return (
    <Card title="Demo data"
          accent={dry ? "danger" : low ? "warning" : undefined}
          actions={<Btn kind="danger" size="sm" disabled={running || m.pending} onClick={() => setConfirm(true)}
                        aria-label="Reset demo data to the seed">
            {running ? "Resetting…" : "Reset demo data"}</Btn>}>
      <div className="flex flex-col gap-3">
        <p className="muted text-sm m-0">
          Puts every table on the <span className="mono">{d.env}</span> stack back to the seed, rebased to today, so each
          scenario starts fresh. Takes about a minute; every table is backed up first.
        </p>
        {last && (
          <div className="flex flex-wrap items-center gap-2 text-sm" aria-live="polite">
            <span className="muted">Last reset</span><StateBadge s={last} />
            {last.started_at && <DateTime value={last.finished_at || last.started_at} />}
            {(last.actor_name || last.actor) && <span className="text-xs">by {last.actor_name || last.actor}</span>}
            {last.state === "done" && (
              <span className="muted text-xs">
                {last.removed} rows removed · {last.written} written · {last.tables} tables · {last.seconds}s
              </span>
            )}
            {last.error && <span className="text-xs" style={{ color: "var(--danger)" }}>{last.error}</span>}
          </div>
        )}
        {m.error && <div className="text-sm" role="alert" style={{ color: "var(--danger)" }}>{m.error}</div>}
        {d.readiness_error && <div className="muted text-xs">Readiness unavailable: {d.readiness_error}</div>}
        {rows.length > 0 && (
          <DataTable dense rows={rows} rowKey={(r: Scenario) => r.scenario_id}
            columns={[
              { key: "name", header: "Scenario", render: (r: Scenario) => r.name },
              { key: "runs_left", header: "Runs left", render: (r: Scenario) => (
                <Badge tone={r.runs_left <= 0 ? "red" : r.runs_left < 3 ? "orange" : "green"}>
                  {r.runs_left >= UNLIMITED ? "∞" : r.runs_left}
                </Badge>) },
              { key: "detail", header: "Pool", render: (r: Scenario) => <span className="muted text-xs">{r.detail}</span> },
            ]} />
        )}
      </div>
      <ConfirmModal open={confirm} danger title="Reset demo data" confirmLabel="Reset now" pending={m.pending}
        requireText={d.confirm_word} onClose={() => setConfirm(false)} onConfirm={start}
        body={<div className="flex flex-col gap-2">
          <p>Every row a rehearsal wrote is removed — journeys, leads, bookings, handoffs, approvals, agent traces — and
            the seed is written back for both markets.</p>
          <p className="muted">SSO administrators are kept. Each table is backed up first (on-demand DynamoDB backups),
            so this can be undone from a backup if needed.</p>
        </div>} />
    </Card>
  );
}
