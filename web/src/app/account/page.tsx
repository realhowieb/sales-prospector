"use client";

import { useEffect, useState } from "react";
import { useSession } from "@/components/AuthGate";
import { api } from "@/lib/api";
import { FREE_LIMITS, PRICES, usePlan } from "@/lib/plan";
import { supabase } from "@/lib/supabase";
import { useTrade } from "@/lib/trades";

const PRO_FEATURES = [
  "Every lead, not just the top 10",
  "Monday email of new leads in your territory",
  "Phone & email lookup for new liquor licenses",
  "Unlimited saved leads in My Pipeline",
  "CSV download for every list",
];

export default function Account() {
  const session = useSession();
  const { pro, status } = usePlan();
  const trade = useTrade();
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "error" | "info"; text: string } | null>(null);

  // Back from Stripe Checkout: the webhook may land a moment later, so refresh the session a few times.
  useEffect(() => {
    if (typeof window === "undefined" || !new URLSearchParams(location.search).has("upgraded")) return;
    setMsg({ kind: "info", text: "Payment received — switching your account to Pro…" });
    let tries = 0;
    const tick = async () => {
      const { data } = await supabase.auth.refreshSession();
      if (data.session?.user.app_metadata?.plan === "pro") {
        setMsg({ kind: "ok", text: "You're on Pro. Thanks! Turn on your weekly email from the Weekly email page." });
        history.replaceState(null, "", "/account/");
      } else if (++tries < 8) {
        setTimeout(tick, 2500);
      } else {
        setMsg({ kind: "info", text: "Payment received. Pro can take a minute to show up — refresh this page shortly." });
      }
    };
    tick();
  }, []);

  async function go(path: string, body?: unknown) {
    setBusy(path);
    setMsg(null);
    try {
      const { url } = await api<{ url: string }>(path, { method: "POST", body });
      location.href = url;
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message });
      setBusy("");
    }
  }

  return (
    <div className="page">
      <h1>Account</h1>
      <p className="lede">{session?.user.email} · {trade.emoji} {trade.label}</p>

      <div className="plans">
        <div className={`panel plan ${pro ? "" : "current"}`}>
          <h2>Free</h2>
          <p className="price">$0</p>
          <ul>
            <li>Top {FREE_LIMITS.leads} leads per search, every source</li>
            <li>Save up to {FREE_LIMITS.pipeline} leads</li>
            <li>Ranked for your trade</li>
          </ul>
          {!pro && <span className="tag">Your plan</span>}
        </div>

        <div className={`panel plan ${pro ? "current" : "featured"}`}>
          <h2>Pro</h2>
          <p className="price">${PRICES.month}<small>/month</small></p>
          <p className="muted" style={{ marginTop: -6 }}>or ${PRICES.year}/year — two months free</p>
          <ul>{PRO_FEATURES.map((f) => <li key={f}>{f}</li>)}</ul>
          {pro ? (
            <>
              <span className="tag hot">Your plan{status !== "active" ? ` · ${status.replace("_", " ")}` : ""}</span>
              <div className="row" style={{ marginTop: 12 }}>
                <button className="ghost" disabled={Boolean(busy)} onClick={() => go("/v1/billing/portal")}>
                  {busy ? "Opening…" : "Manage billing"}
                </button>
              </div>
            </>
          ) : (
            <div className="row" style={{ marginTop: 12 }}>
              <button disabled={Boolean(busy)} onClick={() => go("/v1/billing/checkout", { interval: "month" })}>
                {busy ? "Opening checkout…" : `Upgrade — $${PRICES.month}/mo`}
              </button>
              <button className="ghost" disabled={Boolean(busy)} onClick={() => go("/v1/billing/checkout", { interval: "year" })}>
                ${PRICES.year}/year
              </button>
            </div>
          )}
        </div>
      </div>

      {msg && <div className={`notice ${msg.kind}`}>{msg.text}</div>}
      <p className="muted" style={{ fontSize: "0.85rem" }}>Payments are handled by Stripe. Cancel anytime from Manage billing; you keep Pro until the end of the period.</p>
    </div>
  );
}
