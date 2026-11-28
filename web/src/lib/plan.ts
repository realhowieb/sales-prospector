"use client";

import { useSession } from "@/components/AuthGate";

export const PRICES = { month: 29, year: 290 };
export const FREE_LIMITS = { leads: 10, pipeline: 25 };

/** Pro if the account's app_metadata says so (set only by the server's Stripe webhook). */
export function usePlan() {
  const session = useSession();
  const meta = (session?.user.app_metadata ?? {}) as { plan?: string; plan_status?: string };
  const pro = (meta.plan === "pro" || meta.plan === "admin") && ["active", "trialing", "past_due", undefined].includes(meta.plan_status);
  return { pro, status: meta.plan_status ?? (pro ? "active" : "free") };
}
