"use client";

import { ApprovalsInbox } from "./ApprovalsInbox";

// Approvals (§4.8) for every approver persona (regional, finance, C&I, dealer
// and service managers); the same inbox as Admin › Approvals. Renders in the
// caller's own app theme via the Shell.
export default function ApprovalsPage() {
  return <ApprovalsInbox />;
}
