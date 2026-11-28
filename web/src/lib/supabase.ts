import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export const authConfigured = Boolean(url && anon);

// supabase-js normally takes a browser-wide lock (navigator.locks) shared by every FirstKnock tab, and
// waits on it forever. One stuck tab then freezes sign-in everywhere — the page sits on "Loading…" or
// "Checking your reset link…". supabase-js still orders its own work inside a tab, so we skip the
// cross-tab lock; at worst two tabs refresh the token at the same moment, which Supabase allows.
const tabLock = <R,>(_name: string, _acquireTimeout: number, fn: () => Promise<R>): Promise<R> => fn();

// detectSessionInUrl lets the reset-password link sign the user in on /reset.
export const supabase: SupabaseClient = createClient(url || "https://not-configured.invalid", anon || "missing", {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, lock: tabLock },
});
