"use client";

// Admin Console overview (design §7, spec §5.17): section cards linking to every
// admin area, plus live health — connectors, feature flags, pending data
// requests and approvals, guardrail blocks this week, and the most recent admin
// actions. Dealer admins see the store-scoped subset. Tenant admins also get the
// "Demo data" card (readiness + reset to the seed; dev demo stack only).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Async, Badge, Card, DataTable, DateTime, KpiGrid, Page, SectionLabel, ViewHeader } from "@/components/app";
import { useApi } from "@/lib/hooks";
import { usePersona } from "@/lib/persona";
import { DemoDataCard } from "./DemoDataCard";

const SECTIONS: { href: string; title: string; desc: string; tenantOnly?: boolean }[] = [
  { href: "/admin/users", title: "Users & roles", desc: "Cognito groups, dealer/district assignment, Quick reader provisioning." },
  { href: "/admin/org", title: "Org hierarchy", desc: "OEM ▸ region ▸ district ▸ dealer group ▸ dealer, with PMA." },
  { href: "/admin/market", title: "Market & brand", desc: "Markets, state rules, brand tokens for both themes.", tenantOnly: true },
  { href: "/admin/connectors", title: "Connectors", desc: "Sandbox/live per system, credential rotation, health, last sync.", tenantOnly: true },
  { href: "/admin/flags", title: "Feature flags", desc: "ENABLE_CONNECT, Personalize, Quick embedding — versioned switches.", tenantOnly: true },
  { href: "/admin/policies", title: "Guardrails & policies", desc: "GR rules editor with test harness; suggestion matrix.", tenantOnly: true },
  { href: "/admin/autonomy", title: "Autonomy", desc: "Per-action autonomy matrix (/config/autonomy) with hard-rule ceilings.", tenantOnly: true },
  { href: "/admin/agents", title: "Agents", desc: "Agent registry, health and the agent trace log.", tenantOnly: true },
  { href: "/admin/audit", title: "Audit", desc: "Journey/service event export, guardrail events, admin actions.", tenantOnly: true },
  { href: "/admin/data-requests", title: "Data requests", desc: "Customer data download / delete queue.", tenantOnly: true },
  { href: "/admin/entities", title: "Entity merge queue", desc: "Low-confidence ID matches across CRM, CDP and DMS.", tenantOnly: true },
  { href: "/admin/approvals", title: "Approvals", desc: "Budget shifts, incentive asks, goodwill — the admin view." },
];

export default function AdminOverview() {
  const router = useRouter();
  const { persona } = usePersona();
  const q = useApi<any>("/admin/overview", { pollMs: 60000 });
  const tenantRole = persona?.role === "admin_tenant" || persona?.role === "platform_admin";
  return (
    <Page>
      <ViewHeader title="Admin Console" subtitle="Identity, configuration, and operations for the tenant." />
      <SectionLabel>Sections</SectionLabel>
      <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-4 mb-4">
        {SECTIONS.filter((s) => tenantRole || !s.tenantOnly).map((s) => (
          <Link key={s.href} href={s.href} className="no-underline">
            <Card title={s.title} className="h-full"><p className="muted text-xs m-0">{s.desc}</p></Card>
          </Link>
        ))}
      </div>
      {tenantRole && (
        <>
          <SectionLabel>Demo</SectionLabel>
          <div className="mb-4"><DemoDataCard /></div>
        </>
      )}
      <SectionLabel>Live status</SectionLabel>
      <Async q={q}>
        {(d) => {
          const tenant = d.scope === "tenant";
          const conn = d.connectors || {};
          return (
            <>
              <KpiGrid items={[
                { label: "Users", value: d.users.count, delta: `${d.users.inactive} inactive`, onClick: () => router.push("/admin/users") },
                { label: "Approvals pending", value: d.approvals.pending, delta: `${d.approvals.mine_to_decide} for you`,
                  trend: d.approvals.mine_to_decide ? "down" : undefined, onClick: () => router.push("/admin/approvals") },
                ...(tenant ? [
                  { label: "Connectors OK", value: `${conn.ok || 0}/${Object.values(conn).reduce((a: number, b: any) => a + Number(b || 0), 0)}`,
                    delta: conn.failed ? `${conn.failed} failed` : `${conn.unknown || 0} unchecked`, trend: conn.failed ? "down" as const : undefined,
                    onClick: () => router.push("/admin/connectors") },
                  { label: "Guardrail blocks · 7d", value: d.guardrail_blocks_7d?.total ?? 0,
                    delta: Object.entries(d.guardrail_blocks_7d?.by_rule || {}).map(([k, v]) => `${d.guardrail_blocks_7d?.rule_names?.[k] || k} ×${v}`).slice(0, 2).join(" · "),
                    onClick: () => router.push("/admin/policies") },
                ] : []),
              ]} />
              {tenant && (
                <div className="grid gap-4 md:grid-cols-3">
                  <Card title="Feature flags" actions={<Link className="underline text-xs" href="/admin/flags">Manage</Link>}>
                    <div className="flex flex-col gap-2">
                      {Object.entries(d.flags || {}).map(([k, v]) => (
                        <div key={k} className="flex justify-between text-sm"><span className="mono text-xs">{k}</span><Badge tone={v ? "green" : "gray"}>{v ? "on" : "off"}</Badge></div>
                      ))}
                    </div>
                  </Card>
                  <Card title="Data-subject requests" actions={<Link className="underline text-xs" href="/admin/data-requests">Open queue</Link>}>
                    <div className="kpi-value">{d.data_requests?.pending ?? 0}</div>
                    <div className="kpi-label" style={{ textAlign: "left" }}>pending download / delete</div>
                  </Card>
                  <Card title="Entity merge queue" actions={<Link className="underline text-xs" href="/admin/entities">Review</Link>}>
                    <div className="kpi-value">{d.entities_pending ?? 0}</div>
                    <div className="kpi-label" style={{ textAlign: "left" }}>low-confidence ID matches</div>
                  </Card>
                </div>
              )}
              <div className="grid gap-4 md:grid-cols-2">
                <Card title="Users by group" actions={<Link className="underline text-xs" href="/admin/users">Users &amp; roles</Link>}>
                  <div className="flex flex-wrap gap-2">
                    {Object.entries(d.users.by_group || {}).sort().map(([g, n]) => <Badge key={g} tone="blue" title={g}>{d.users.group_labels?.[g] || g} · {String(n)}</Badge>)}
                  </div>
                </Card>
                {tenant && (
                  <Card title="Recent admin actions" actions={<Link className="underline text-xs" href="/admin/audit">Audit</Link>}>
                    <DataTable dense rows={d.recent_admin_actions || []} rowKey={(r: any, i) => `${r.ts}-${i}`}
                      empty="No admin actions yet."
                      columns={[
                        { key: "ts", header: "When", render: (r: any) => <DateTime value={r.ts} /> },
                        { key: "actor", header: "Actor", render: (r: any) => <span className="mono text-xs">{r.actor}</span> },
                        { key: "tool", header: "Action", render: (r: any) => <span className="mono text-xs">{r.tool}</span> },
                        { key: "target", header: "Target", render: (r: any) => <span className="mono text-xs">{r.target}</span> },
                      ]} />
                  </Card>
                )}
              </div>
            </>
          );
        }}
      </Async>
    </Page>
  );
}
