"use client";

// Marketing Studio · Quick dashboards (A5 analytics half; plug-and-play, behind
// the quickEnabled() flag — the panel renders a setup card and calls nothing
// while the flag is off).

import { Page, ViewHeader } from "@/components/app";
import { QuickPanel } from "../QuickPanel";

export default function MarketingQuickPage() {
  return (
    <Page>
      <ViewHeader title="Quick dashboards" subtitle="Campaign ROI dashboards and Q topics authored in AWS Quick" />
      <QuickPanel showFlows={false} />
    </Page>
  );
}
