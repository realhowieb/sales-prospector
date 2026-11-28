"use client";

import { useSession } from "@/components/AuthGate";
import { supabase } from "./supabase";

import { TRADES } from "./tradeData";

export { TRADES };
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
