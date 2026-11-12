"use client";

// Insight dock — moves a page's [AI INSIGHT] card up into the AgentBar, so the
// insight sits next to the persona's agent panel instead of inside the page body.
//
// <AiInsight/> registers itself here on mount. If the signed-in persona has an
// agent panel (agentsForRole ≠ null), the FIRST registered insight renders in
// the bar and suppresses its in-body card; later insights on the same page (tab
// cards, per-section notes) keep rendering in place. Personas without a panel
// (OEM field roles on Quick/A6, admin) are untouched — their insights stay in
// the page body, because there is no bar to dock into.
//
// Same module-store pattern as AgentBar's action store: the bar is mounted by
// the Shell above the routed page, so React context cannot reach it.

import { ReactNode, useEffect, useId, useState } from "react";

export interface DockedInsight {
  id: string;
  title?: ReactNode;
  body: ReactNode;
  sources?: (string | null | undefined)[];
}

const store: { items: DockedInsight[]; listeners: Set<() => void> } = { items: [], listeners: new Set() };
const emit = () => store.listeners.forEach((l) => l());

/** The visible text of a ReactNode tree (strings and numbers only; element props are walked). */
function textOf(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (typeof node === "object" && "props" in node) {
    const p = (node as { props?: { children?: ReactNode } }).props;
    return textOf(p?.children);
  }
  return "";
}

/** Register an insight; returns true while this insight is the docked (first) one. */
export function useRegisterInsight(entry: Omit<DockedInsight, "id"> | null): boolean {
  const id = useId();
  const [isDocked, setDocked] = useState(false);

  // body/title are ReactNodes; re-registering on every render would thrash the dock, but an
  // insight rendered outside <Async> (the Service Board's "lane today") changes its text
  // once the data lands, so key the registration on the body's rendered text as well —
  // otherwise the dock keeps the first-render empty state ("No open service journeys")
  // next to a board full of journeys.
  const bodyKey = entry ? textOf(entry.body) + "|" + textOf(entry.title) : "";

  useEffect(() => {
    if (!entry) return;
    const row: DockedInsight = { id, ...entry };
    const i = store.items.findIndex((x) => x.id === id);
    if (i >= 0) store.items[i] = row; else store.items.push(row);
    emit();
    return () => {
      store.items = store.items.filter((x) => x.id !== id);
      emit();
    };
  }, [id, !!entry, bodyKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const l = () => setDocked(store.items.length > 0 && store.items[0]?.id === id);
    l();
    store.listeners.add(l);
    return () => { store.listeners.delete(l); };
  }, [id]);

  return isDocked;
}

/** The bar's view: the first registered insight on the current page, if any. */
export function useDockedInsight(): DockedInsight | null {
  const [, bump] = useState(0);
  useEffect(() => {
    const l = () => bump((n) => n + 1);
    store.listeners.add(l);
    return () => { store.listeners.delete(l); };
  }, []);
  return store.items[0] || null;
}
