"use client";

// Marketing Studio view frame (DM Assistant reference, light "marketing" theme):
// a scrolling page body. The ask box is no longer a rail on this frame — the
// global AgentBar (mounted by the Shell) carries the A5 Campaign ROI Optimizer
// and opens its chat drawer on "Interact", routed by AGENT_CHAT.A5 to
// `/marketing/ask`. The API synthesizes from scoped analytics read models with
// Bedrock; a future Quick-native analytics assistant can replace that backend
// without changing this frame. We keep the frame's `view` / `dealerId` as
// chat context so answers stay grounded on the view the analyst is looking at.
// Local copy for the marketing route group (shared primitives come from
// @/components/app; this frame is a feature component, so it lives here).

import { ReactNode } from "react";
import { AgentActionsProvider } from "@/components/AgentBar";
import { Page, ViewHeader } from "@/components/app";

export function WorkspaceView({ title, subtitle, actions, children, view, dealerId }: {
  title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode; prompts?: string[];
  view: string; askPath?: string; dealerId?: string | null; chatTitle?: string }) {
  return (
    <AgentActionsProvider value={{ chatContext: dealerId ? `${view} view, dealer ${dealerId}` : `${view} view` }}>
      <Page>
        <ViewHeader title={title} subtitle={subtitle} actions={actions} />
        {children}
      </Page>
    </AgentActionsProvider>
  );
}
