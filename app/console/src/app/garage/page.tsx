"use client";

// Customer Web · My Garage (design §6.1, spec §5.12) — the customer's home.
// Follows the same page grammar as every other app home: ViewHeader, a KPI
// summary strip (KpiGrid), then one card per journey concern in a two-column
// grid. The pending-estimate banner jumps straight to approval — the most
// common mobile action in the system. OEM brand (light) theme via the Shell.

import Link from "next/link";
import { AgentActionsProvider } from "@/components/AgentBar";
import { Kpi, KpiGrid, Page, ViewHeader } from "@/components/app";
import { useApi } from "@/lib/hooks";
import { BrandScope, useBrandName } from "./Brand";
import {
  AppointmentsSection, BuildsSection, DeliverySurveyCard, DocumentsSection, FinanceSection,
  JourneySection, OwnershipSection, TradeSection,
} from "./sections";
import { Preferences } from "./preferences";

export default function GaragePage() {
  return (
    <BrandScope>
      <GarageView />
    </BrandScope>
  );
}

function GarageView() {
  const brandName = useBrandName();
  const j = useApi<any>("/garage/journey");
  const svc = useApi<any>("/garage/service", { pollMs: 60000 });
  const own = useApi<any>("/garage/ownership");
  const builds = useApi<any>("/garage/builds");
  const waiting = (svc.data?.active || []).filter((s: any) => s.awaiting_approval);

  const nextDue = own.data?.vehicles?.find((v: any) => v.next_service_due)?.next_service_due;
  const kpis: Kpi[] = [
    { label: "Your vehicles", value: own.data?.vehicles?.length ?? "—" },
    { label: "Journey stage", value: j.data?.journey ? (j.data.journey.stage || "—").replace(/_/g, " ") : "none" },
    { label: "Service in progress", value: svc.data?.active?.length ?? "—",
      delta: waiting.length ? `${waiting.length} awaiting your approval` : undefined,
      trend: waiting.length ? "down" : undefined },
    { label: "Saved builds", value: builds.data?.builds?.length ?? "—" },
  ];

  return (
    // The agents quietly working on the customer's behalf (A1/A2/A7) are in the
    // global AgentBar; scope its activity to this customer's journey.
    <AgentActionsProvider value={{ journeyId: j.data?.journey?.journey_id || null }}>
    <Page>
      <ViewHeader
        title={`Welcome${j.data?.name ? `, ${j.data.name}` : ""}`}
        subtitle={`Your vehicles, journey, service and offers with ${brandName} — all in one place.`}
        actions={<>
          <Link href="/garage/service" className="btn btn-secondary btn-sm">Service</Link>
          <Link href="/shop" className="btn btn-secondary btn-sm">Shop</Link>
          <Link href="/shop/chat" className="btn btn-secondary btn-sm">Chat</Link>
        </>}
      />
      <KpiGrid items={kpis} />
      {waiting.length > 0 && (
        <div className="card" style={{ borderLeft: "4px solid var(--warning)" }} role="status">
          <b>Your estimate is ready.</b> <span className="muted">Review and approve the work on your vehicle — it takes a minute.</span>
          <div className="mt-2">
            <Link href={`/garage/service/${encodeURIComponent(waiting[0].service_journey_id)}`} className="btn btn-primary btn-lg">
              Review estimate
            </Link>
          </div>
        </div>
      )}
      <DeliverySurveyCard />
      <JourneySection />
      <div className="grid gap-4 lg:grid-cols-2 items-start">
        <BuildsSection />
        <AppointmentsSection />
        <FinanceSection />
        <TradeSection />
        <OwnershipSection />
        <DocumentsSection />
        <Preferences />
      </div>
    </Page>
    </AgentActionsProvider>
  );
}
