"use client";

// Marketing Studio · Campaigns (design §5.1, spec §5.7): the OEM marketing
// analyst home — embedded dashboard area, A5 Campaign ROI by campaign / dealer /
// channel, funnel drop-off by source, the budget-shift recommendation card
// (approve routing to /approvals), and the NL ask box with a live-region answer.

import { CampaignROI } from "./CampaignROI";
import { WorkspaceView } from "../WorkspaceView";

export default function MarketingCampaigns() {
  return (
    <WorkspaceView view="campaigns" askPath="/marketing/ask" chatTitle="Ask your campaign data"
      title="Campaigns" subtitle="ROI by campaign, dealer and channel · funnel drop-off by source"
      prompts={["Which campaign has the best ROI?", "Where is the biggest funnel drop by source?", "Should I shift budget?", "Which dealers have the lowest cost per conversion?"]}>
      <CampaignROI />
    </WorkspaceView>
  );
}
