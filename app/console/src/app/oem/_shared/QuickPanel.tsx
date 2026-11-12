"use client";

// AWS Quick (quicksight) — plug-and-play surface for the OEM Workspace.
// Off by default: renders a setup state and makes no API call. On: lists Quick
// Flows + dashboards and embeds a dashboard with a registered-user embed URL.
// Flows/dashboards are authored in the Quick console; this screen consumes them.

import { useState } from "react";
import { apiGet } from "@/lib/api";
import { quickEnabled } from "@/lib/flags";
import { useApi } from "@/lib/hooks";
import { Async, Badge, Btn, Card, EmptyState, ErrorState, LoadingBlock } from "@/components/app";

export function QuickPanel({ showFlows = true }: { showFlows?: boolean }) {
  const enabled = quickEnabled();
  const flows = useApi<any>(enabled && showFlows ? "/oem/quick/flows" : null);
  const dashboards = useApi<any>(enabled ? "/oem/quick/dashboards" : null);
  const [embed, setEmbed] = useState<any>(null);

  if (!enabled) {
    return (
      <Card title={<span>AWS Quick <Badge tone="gray">off</Badge></span>}>
        <p className="text-sm muted">
          Quick dashboards, Q topics and the Quick-hosted Field Sales Agent are built in the AWS Quick console
          (the Quick App). Enable with <code>NEXT_PUBLIC_ENABLE_QUICK=true</code> on the console and
          <code> ENABLE_QUICK=true</code> on the service. Nothing is called while off; the application
          views continue to use their scoped API read models and Bedrock-synthesized assistants.
        </p>
      </Card>
    );
  }

  const open = async (id: string) => {
    setEmbed({ loading: true });
    try { setEmbed(await apiGet(`/oem/quick/dashboards/${encodeURIComponent(id)}/embed-url`)); }
    catch (e: any) { setEmbed({ error: e.message }); }
  };

  return (
    <>
      {showFlows && (
        <Card title="Quick Flows">
          <Async q={flows}>
            {(d) => (d.flows || []).length === 0 ? <EmptyState>{d.note || d.error || "No Quick Flows authored yet."}</EmptyState> : (
              <ul className="list-none p-0 m-0">
                {d.flows.map((f: any) => (
                  <li key={f.flow_id} className="info-row"><span className="info-label">{f.name}</span><span className="mono text-xs">{f.flow_id}</span></li>
                ))}
              </ul>
            )}
          </Async>
        </Card>
      )}
      <Card title="Quick Sight dashboards">
        <Async q={dashboards}>
          {(d) => (d.dashboards || []).length === 0 ? <EmptyState>{d.note || "No dashboards shared with you."}</EmptyState> : (
            <ul className="list-none p-0 m-0">
              {d.dashboards.map((x: any) => (
                <li key={x.dashboard_id} className="info-row">
                  <span className="info-label">{x.name}</span>
                  <Btn kind="secondary" size="sm" onClick={() => open(x.dashboard_id)}>Embed</Btn>
                </li>
              ))}
            </ul>
          )}
        </Async>
      </Card>
      {embed && (
        <Card title="Dashboard">
          {embed.loading ? <LoadingBlock /> : embed.embed_url ? (
            <iframe title="Quick Sight dashboard" src={embed.embed_url}
                    className="w-full rounded" style={{ height: 520, border: "1px solid var(--border)" }} />
          ) : <ErrorState error={embed.error || embed.note || "No embed URL returned."} />}
        </Card>
      )}
    </>
  );
}
