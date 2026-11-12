"use client";

// "Open in My Garage" for one specific customer. The demo portal's Customer card and the
// "View as persona" menu always open the seeded customer (Jordan); only the step-by-step
// replay switched to the buyer it created. A buyer entered by hand (a walk-in, a web lead
// typed into the Lead Desk) had no way to be seen from her side — her delivery survey was
// there, but the My Garage on screen was someone else's. Shown only where the signed-in
// user may view as another persona (admins, or an admin already viewing as someone).

import { Btn } from "@/components/app";
import { usePersona } from "@/lib/persona";
import { actAsPersona } from "@/lib/replay";

const ADMIN_ROLES = ["admin_tenant", "admin_dealer", "platform_admin"];

export function ViewAsCustomer({ customerId, name, path = "/garage" }: { customerId?: string | null; name?: string | null; path?: string }) {
  const { persona } = usePersona();
  const can = Boolean(persona && (ADMIN_ROLES.includes(persona.role) || persona.impersonating));
  if (!can || !customerId) return null;
  const first = (name || "").split(" ")[0] || "the customer";
  return (
    <Btn kind="ghost" size="sm" aria-label={`Open ${name || customerId}'s My Garage as the customer`}
         onClick={() => { actAsPersona(`cust:${customerId}`); window.location.assign(path); }}>
      Open {first}&apos;s My Garage ↗
    </Btn>
  );
}
