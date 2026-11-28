"use client";

import type { Session } from "@supabase/supabase-js";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { authConfigured, supabase } from "@/lib/supabase";

const SessionContext = createContext<Session | null>(null);
export const useSession = () => useContext(SessionContext);

const MARKETING_PATHS = ["/welcome/", "/for/"];
const PUBLIC_PATHS = ["/login/", "/reset/", ...MARKETING_PATHS];

export default function AuthGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const pathname = usePathname() ?? "/";
  const router = useRouter();
  const matches = (p: string) => pathname.startsWith(p) || `${pathname}/` === p;
  const isPublic = PUBLIC_PATHS.some(matches);
  const isMarketing = MARKETING_PATHS.some(matches);

  useEffect(() => {
    // Never leave the app on "Loading…": if the saved session can't be read or refreshed (stale token,
    // network hiccup), drop it locally and fall through to sign-in.
    let settled = false;
    const finish = (s: Session | null) => {
      if (settled) return;
      settled = true;
      setSession(s);
      setReady(true);
    };
    const giveUp = () => {
      supabase.auth.signOut({ scope: "local" }).catch(() => {});
      finish(null);
    };
    supabase.auth
      .getSession()
      .then(({ data, error }) => (error ? giveUp() : finish(data.session)))
      .catch(giveUp);
    const timer = setTimeout(() => !settled && giveUp(), 8000);
    const { data } = supabase.auth.onAuthStateChange((event, s) => {
      setSession(s);
      if (event === "INITIAL_SESSION" || event === "SIGNED_IN") finish(s);
      if (event === "PASSWORD_RECOVERY") router.replace("/reset/");
    });
    return () => {
      clearTimeout(timer);
      data.subscription.unsubscribe();
    };
  }, [router]);

  useEffect(() => {
    if (ready && !session && !isPublic) router.replace(pathname === "/" ? "/welcome/" : "/login/");
  }, [ready, session, isPublic, router, pathname]);

  // Marketing pages render straight away (and into the static HTML, so search engines see them).
  // Sign-in and reset still wait: a reset link has to be checked before the page knows who you are.
  if (isMarketing) return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
  if (!authConfigured) {
    return <div className="page"><div className="notice error">Sign-in isn&apos;t configured for this site yet.</div></div>;
  }
  if (!ready || (!session && !isPublic)) {
    const waiting = pathname.startsWith("/reset") ? "Checking your reset link…" : "Loading…";
    return <div className="page"><p className="muted">{waiting}</p></div>;
  }
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}
