"use client";

import { ApprovalsInbox } from "@/app/approvals/ApprovalsInbox";

// Admin › Approvals (§4.8): tenant admins see and may decide every approval type.
export default function AdminApprovalsPage() {
  return <ApprovalsInbox title="All approvals" />;
}
