"use client";

// Bottom-bar AI Log (§5.2.1): PLAT_AgentTraces for the active journey or service
// journey, newest first, mono timestamp + status dot. aria-live so screen
// readers hear agent actions as they land (§5.11). Correlation id on hover.

import { agentLabel, toolLabel } from "@/lib/agents";
import { useApi } from "@/lib/hooks";
import { fmtClock } from "@/lib/market";
import { usePersona } from "@/lib/persona";

export function AiLog({ journeyId, serviceJourneyId, pollMs = 15000 }: {
  journeyId?: string | null; serviceJourneyId?: string | null; pollMs?: number }) {
  const qs = journeyId ? `journey_id=${encodeURIComponent(journeyId)}`
    : serviceJourneyId ? `service_journey_id=${encodeURIComponent(serviceJourneyId)}` : null;
  const q = useApi<any>(qs ? `/traces?${qs}&limit=25` : null, { pollMs, background: true });
  const items: any[] = q.data?.traces || [];
  const { persona } = usePersona();
  return (
    <footer className="bottom-bar" aria-label="AI Log">
      <span className="log-label">AI Log</span>
      <div className="log-items" aria-live="polite">
        {q.error && <span className="log-item"><span className="log-dot err" />log unavailable</span>}
        {!q.error && items.length === 0 && <span className="log-item"><span className="log-dot" />No agent activity yet</span>}
        {items.map((t) => (
          <span key={t.trace_id} className="log-item" title={t.correlation_id ? `correlation ${t.correlation_id}` : undefined}>
            <span className={`log-dot${t.outcome && t.outcome !== "ok" && t.outcome !== "success" ? " err" : ""}`} />
            <span className="log-time">{fmtClock(t.ts, persona?.marketId, true)}</span>
            <span>{t.agent_id ? `${agentLabel(t.agent_id)} · ` : ""}{toolLabel(t.tool)}</span>
            {t.autonomy_level && <span className="dim">· {t.autonomy_level}</span>}
          </span>
        ))}
      </div>
    </footer>
  );
}
