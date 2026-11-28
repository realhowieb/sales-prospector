"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { supabase } from "@/lib/supabase";
import { usePlan } from "@/lib/plan";
import { saveTrade, TRADES, useTrade } from "@/lib/trades";
import { useSession } from "./AuthGate";

const LINKS = [
  { href: "/", label: "Trigger Leads" },
  { href: "/discover/", label: "Discover" },
  { href: "/pipeline/", label: "My Pipeline" },
  { href: "/alerts/", label: "Weekly email" },
];

export default function Nav() {
  const session = useSession();
  const pathname = usePathname() ?? "/";
  const router = useRouter();
  const trade = useTrade();
  const { pro } = usePlan();
  if (!session) return null;
  return (
    <header className="topbar">
      <Link href="/" className="brand">
        <span aria-hidden>🚪</span> FirstKnock <small>by HS Finest</small>
      </Link>
      <nav>
        {LINKS.map((l) => (
          <Link key={l.href} href={l.href} className={pathname === l.href ? "active" : ""}>
            {l.label}
          </Link>
        ))}
      </nav>
      <div className="account">
        <Link href="/account/" className={pro ? "plan-pill pro" : "plan-pill"}>{pro ? "Pro" : "⭐ Upgrade"}</Link>
        <select className="trade-pick" aria-label="What you sell" title="What you sell — leads are ranked for this"
          value={trade.id}
          onChange={async (e) => {
            try {
              await saveTrade(e.target.value);
              router.refresh();
            } catch (err) {
              alert((err as Error).message);
            }
          }}>
          {TRADES.map((t) => <option key={t.id} value={t.id}>{t.emoji} {t.label}</option>)}
        </select>
        <span className="muted email">{session.user.email}</span>
        <button
          className="ghost"
          onClick={async () => {
            await supabase.auth.signOut();
            router.replace("/login/");
          }}
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
