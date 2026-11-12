// Small text helpers for things the API returns as codes or model prose.
//
// humanizeCode: "web_form" → "Web form", "service_lane" → "Service lane",
// "predictive_maintenance" → "Predictive maintenance". Ids that look like
// identifiers (MER-TRAILHAND, D-101) are left alone — callers prefer a name
// field and fall back to this.
//
// stripMarkdown: Bedrock answers sometimes arrive with **bold**, `code`, or
// "- " bullets; the chat bubble renders plain text, so those markers show up
// literally. This drops the markers and keeps the words and line breaks.

// words that stay upper-case when a code is turned into a label ("csi_decline" → "CSI decline")
const ACRONYMS: Record<string, string> = {
  csi: "CSI", sla: "SLA", ev: "EV", roi: "ROI", mpi: "MPI", apr: "APR", dms: "DMS", crm: "CRM",
  vin: "VIN", ro: "RO", ice: "ICE", phev: "PHEV", hev: "HEV", kpi: "KPI", tsb: "TSB", dtc: "DTC",
};

export function humanizeCode(v: string | null | undefined): string {
  if (!v) return "";
  if (/^[A-Z0-9]+(-[A-Z0-9]+)+$/.test(v)) return v;       // an identifier, not a label
  const s = v.replace(/[_-]+/g, " ").trim().split(/\s+/).map((w) => ACRONYMS[w.toLowerCase()] || w).join(" ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// A plan objective's KPI code as a label: "alert:csi_decline" → "CSI decline alert",
// "lead_sla_minutes" → "Lead SLA minutes". The stored code is unchanged.
export function kpiLabel(v: string | null | undefined): string {
  if (!v) return "";
  const m = /^alert:(.+)$/.exec(v);
  return m ? `${humanizeCode(m[1])} alert` : humanizeCode(v);
}

export function stripMarkdown(text: string | null | undefined): string {
  if (!text) return "";
  // function replacers (not "$1" strings): the console build's hard-coded-currency guard
  // reads a literal "$1" as an amount
  const inner = (_m: string, g: string) => g;
  return text
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")                                   // headings
    .replace(/\*\*(.+?)\*\*/g, inner)                                      // **bold**
    .replace(/__(.+?)__/g, inner)                                          // __bold__
    .replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*(?!\*)/g, (_m, pre: string, g: string) => pre + g)  // *italic*
    .replace(/`([^`\n]+)`/g, inner)                                        // `code`
    .replace(/^\s*[-*•]\s+/gm, "• ")                                      // list markers → one bullet style
    .replace(/\[([^\]]+)\]\([^)]+\)/g, inner)                              // [text](url)
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

// What a required-context key means ("shortlist" → "a saved shortlist"), as the backend's
// common/labels.CONTEXT_LABEL does — so "missing ['shortlist']" never reaches the screen.
const CONTEXT_WORDS: Record<string, string> = {
  shortlist: "a saved shortlist of vehicles", saved_build: "a saved build (trim, packs and price)",
  budget: "the customer's budget", timeline: "the purchase timeline", finance_offer: "a finance offer presented",
  valuation: "a trade-in valuation", deal_structure: "an agreed deal structure",
  credit_compliance: "the credit compliance checks", delivered: "the delivery completed",
  estimate_priced_with_source: "a priced, sourced estimate", customer_decision: "the customer's decision on the estimate",
};
export function contextLabel(k: string): string {
  const m = /^line_unsourced:(.+)$/.exec(k);
  if (m) return `labor op, price source and coverage on line ${m[1]}`;
  return CONTEXT_WORDS[k] || humanizeCode(k).toLowerCase();
}
export function missingText(keys: string[] | null | undefined): string {
  const xs = (keys || []).map(contextLabel);
  return xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}
