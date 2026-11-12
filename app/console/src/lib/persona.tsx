"use client";

// The signed-in persona (§5.2), resolved by the API (GET /me/home) from the same
// persona matrix the server authorizes with — the console never keeps its own
// role table. Exposes role, home, permitted screens, scope, and market.

import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { apiGet, authToken } from "./api";
import { AppId, appsForScreens, canOpen } from "./nav";
import { setActiveMarket } from "./market";

export interface Persona {
  role: string;
  roleName: string;
  name: string;
  userId: string;
  home: string;
  screens: string[];
  scopeNodeId?: string | null;
  dealerIds: string[];
  marketId: string;
  isOem: boolean;
  customerId?: string | null;
  apps: AppId[];
  impersonating: boolean;
  impersonatedBy?: string | null;
}

interface State { persona: Persona | null; loading: boolean; error: string | null; reload: () => void }
const Ctx = createContext<State>({ persona: null, loading: true, error: null, reload: () => {} });

export function hasSession(): boolean {
  return Boolean(authToken());
}

export function PersonaProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Omit<State, "reload">>({ persona: null, loading: true, error: null });
  const load = () => {
    if (!hasSession()) { setState({ persona: null, loading: false, error: null }); return; }
    setState((s) => ({ ...s, loading: true }));
    apiGet<any>("/me/home")
      .then((d) => { setActiveMarket(d.market_id || "us"); setState({
        loading: false, error: null,
        persona: {
          role: d.role, roleName: d.role_name || d.role, name: d.name || d.user_id, userId: d.user_id,
          home: d.url, screens: d.screens || [],
          scopeNodeId: d.scope_node_id, dealerIds: d.dealer_ids || [], marketId: d.market_id || "us",
          isOem: Boolean(d.is_oem), customerId: d.customer_id, apps: appsForScreens(d.screens || []),
          impersonating: Boolean(d.impersonating), impersonatedBy: d.impersonated_by,
        },
      }); })
      .catch((e) => setState({ persona: null, loading: false, error: e.message }));
  };
  useEffect(load, []);
  return <Ctx.Provider value={{ ...state, reload: load }}>{children}</Ctx.Provider>;
}

export const usePersona = () => useContext(Ctx);

/** True when the persona may open a persona-matrix screen id. */
export function useCan(screen: string): boolean {
  const { persona } = usePersona();
  return Boolean(persona?.screens.includes(screen));
}

/** `can(screen)` for a page that gates several links: a link to a screen is shown
 *  only when the persona holds that screen, as in the sidebar. */
export function useCanOpen(): (screen: string) => boolean {
  const { persona } = usePersona();
  return (screen: string) => canOpen(persona?.screens, screen);
}
