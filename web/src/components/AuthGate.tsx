"use client";

import type { Session } from "@supabase/supabase-js";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { authConfigured, supabase } from "@/lib/supabase";

const SessionContext = createContext<Session | null>(null);
export const useSession = () => useContext(SessionContext);

const PUBLIC_PATHS = ["/login/", "/reset/"];

export default function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const pathname = usePathname() ?? "/";
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p) || `${pathname}/` === p);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      if (event === "PASSWORD_RECOVERY") router.replace("/reset/");
    });
    return () => data.subscription.unsubscribe();
  }, [router]);

  useEffect(() => {
    if (ready && !session && !isPublic) router.replace("/login/");
  }, [ready, session, isPublic, router]);

  if (!authConfigured) {
    return <div className="page"><div className="notice error">Sign-in isn&apos;t configured for this site yet.</div></div>;
  }
  if (!ready || (!session && !isPublic)) return <div className="page"><p className="muted">Loading…</p></div>;
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}
