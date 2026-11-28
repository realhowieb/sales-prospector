"use client";

import { useSession } from "@/components/AuthGate";
import { supabase } from "./supabase";

// Mirrors trades.TRADES in the API (ids must match; the API falls back to security for unknown ids).
export const TRADES = [
  { id: "security", label: "Security & low-voltage", emoji: "🔐", sells: "alarms, cameras, access control and monitoring" },
  { id: "solar", label: "Solar & energy", emoji: "☀️", sells: "solar, batteries, EV charging and efficiency upgrades" },
  { id: "hvac", label: "HVAC & mechanical", emoji: "❄️", sells: "HVAC install, service contracts and controls" },
  { id: "cleaning", label: "Commercial cleaning", emoji: "🧽", sells: "janitorial, post-construction and recurring cleaning" },
  { id: "it", label: "IT, telecom & internet", emoji: "🌐", sells: "managed IT, internet, phones and structured cabling" },
  { id: "pos", label: "POS & payments", emoji: "💳", sells: "point of sale, card processing and online ordering" },
  { id: "insurance", label: "Business insurance", emoji: "🛡️", sells: "commercial property, liability and workers' comp" },
  { id: "furniture", label: "Office furniture & interiors", emoji: "🪑", sells: "furniture, workspace design and installation" },
] as const;

export type TradeId = (typeof TRADES)[number]["id"];
export const DEFAULT_TRADE: TradeId = "security";

export const tradeInfo = (id: string | undefined) => TRADES.find((t) => t.id === id) ?? TRADES[0];

/** The signed-in rep's trade (stored in their Supabase user metadata). */
export function useTrade() {
  const session = useSession();
  const id = (session?.user.user_metadata?.trade as string | undefined) ?? DEFAULT_TRADE;
  return tradeInfo(id);
}

export async function saveTrade(id: string) {
  const { error } = await supabase.auth.updateUser({ data: { trade: id } });
  if (error) throw error;
}

/** For API calls made outside React (reads the current session). */
export async function currentTrade(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  return (data.session?.user.user_metadata?.trade as string | undefined) ?? DEFAULT_TRADE;
}
