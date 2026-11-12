"use client";

// Admin › Feature flags (§7.4, §5.17): versioned in PLAT_Config — draft, review
// the diff, publish, roll back. Flags gate optional modules (Connect, Personalize,
// Quick embedding, Federate sign-in, telematics) without code changes.

import { useState } from "react";
import { Page, ViewHeader } from "@/components/app";
import { MarketPicker, TenantOnly, VersionedConfig } from "../common";

const DESCRIPTIONS: Record<string, string> = {
  ENABLE_CONNECT: "Amazon Connect outreach provider (voice callbacks, Contact Lens). Off = SES/SNS + dealer tasks.",
  ENABLE_PERSONALIZE: "Amazon Personalize ranking for the Discovery Agent. Off = transparent rules ranker.",
  ENABLE_QUICK_EMBED: "Embedded AWS Quick dashboards and agents in OEM Workspace / Marketing Studio.",
  ENABLE_FEDERATE: "Tenant intent to use the enterprise IdP (SSO) for staff sign-in. The login page's SSO buttons follow the external identity providers actually registered on the Cognito pool.",
  ENABLE_TELEMATICS: "Connected-car alerts connector for the Service Agent's proactive booking.",
};

export default function FlagsPage() {
  const [market, setMarket] = useState("us");
  return (
    <Page>
      <ViewHeader title="Feature flags" subtitle="Versioned module switches. Changes apply on the next request after publish."
        actions={<MarketPicker value={market} onChange={setMarket} />} />
      <TenantOnly what="Feature flags">
        <VersionedConfig<{ flags: Record<string, boolean> }> kind="flags" market={market} title="Flags"
          renderEditor={(c, set) => (
            <div className="flex flex-col gap-1">
              {Object.entries(c.flags || {}).map(([k, v]) => (
                <label key={k} className="flex items-start gap-3 py-2" style={{ borderBottom: "1px solid var(--border)" }}>
                  <input type="checkbox" className="mt-1" checked={!!v} onChange={(e) => set({ flags: { ...c.flags, [k]: e.target.checked } })} />
                  <span><span className="mono text-sm font-semibold">{k}</span><div className="muted text-xs">{DESCRIPTIONS[k]}</div></span>
                </label>
              ))}
            </div>
          )} />
      </TenantOnly>
    </Page>
  );
}
