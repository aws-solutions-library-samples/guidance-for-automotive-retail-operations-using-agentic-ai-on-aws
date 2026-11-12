"use client";

// OEM Workspace · Amazon Quick (behind the quick flag): Quick Flows and
// Quick Sight dashboards shared with your scope. The QuickPanel renders a
// setup state (no API calls) when the flag is off.

import { Page, ViewHeader } from "@/components/app";
import { QuickPanel } from "../_shared/QuickPanel";

export default function OemQuickPage() {
  return (
    <Page>
      <ViewHeader title="Amazon Quick" subtitle="Quick Flows and Quick Sight dashboards shared with your scope" />
      <QuickPanel />
    </Page>
  );
}
