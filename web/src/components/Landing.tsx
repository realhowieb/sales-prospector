import Link from "next/link";
import { FREE_LIMITS, PRICES } from "@/lib/planData";
import { TRADE_COPY, type TradeCopy } from "@/lib/tradeCopy";

const SOURCES = [
  { emoji: "🏗️", title: "Build-outs & move-ins", body: "Commercial permits for tenant improvements and new occupancy — filed weeks before a company moves in. Palo Alto, San Jose and San Francisco." },
  { emoji: "🍷", title: "Liquor licenses", body: "Pending and brand-new licenses from the California ABC daily file: restaurants, bars, markets and hotels opening or changing owners." },
  { emoji: "📰", title: "Openings, leases & moves", body: "Local news about businesses opening, signing leases and relocating in your cities." },
  { emoji: "🗺️", title: "New on the map", body: "Offices, clinics, shops and gyms newly added to the map in your territory." },
];

export default function Landing({ trade }: { trade?: TradeCopy }) {
  const signup = `/login/?signup=1${trade ? `&trade=${trade.id}` : ""}`;
  return (
    <div className="landing">
      <header className="lp-top">
        <Link href="/welcome/" className="brand"><span aria-hidden>🚪</span> FirstKnock</Link>
        <nav>
          <a href="#pricing">Pricing</a>
          <Link href="/login/">Sign in</Link>
          <Link className="button" href={signup}>Start free</Link>
        </nav>
      </header>

      <section className="lp-hero">
        {trade && <p className="lp-kicker">{trade.emoji} For {trade.label} reps</p>}
        <h1>{trade ? trade.headline : "Be the first to knock on new businesses."}</h1>
        <p className="lp-sub">
          {trade
            ? trade.sub
            : "FirstKnock finds businesses that are opening, moving or building out — from permits, liquor licenses, local news and the map — and ranks them for what you sell."}
        </p>
        <div className="row">
          <Link className="button lp-cta" href={signup}>Start free — no card</Link>
          <a className="button ghost" href="#how">How it works</a>
        </div>
      </section>

      {trade && (
        <section className="lp-section">
          <h2>What you&apos;ll see</h2>
          <div className="lp-grid">
            <div className="panel">
              <h3>Best leads for {trade.label}</h3>
              <ul>{trade.bestLeads.map((b) => <li key={b}>{b}</li>)}</ul>
            </div>
            {trade.pitches.map((p) => (
              <div className="panel" key={p.lead}>
                <p className="muted lp-small">{p.lead}</p>
                <p className="lp-quote">“{p.say}”</p>
                <p className="muted lp-small">Suggested opener, written for your trade</p>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="lp-section" id="how">
        <h2>Four signals, one list</h2>
        <div className="lp-grid">
          {SOURCES.map((s) => (
            <div className="panel" key={s.title}>
              <h3>{s.emoji} {s.title}</h3>
              <p className="muted">{s.body}</p>
            </div>
          ))}
        </div>
        <ol className="lp-steps">
          <li><b>Pick your territory and trade.</b> Leads are scored for what you sell.</li>
          <li><b>Find leads</b> — with the address, job value, contractor and a suggested opener.</li>
          <li><b>Save them to your pipeline</b> and get new ones by email every Monday.</li>
        </ol>
      </section>

      {!trade && (
        <section className="lp-section">
          <h2>Built for reps who sell to new businesses</h2>
          <div className="lp-trades">
            {TRADE_COPY.map((t) => (
              <Link key={t.id} href={`/for/${t.id}/`} className="panel lp-trade">
                <span className="lp-emoji">{t.emoji}</span> {t.label}
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className="lp-section" id="pricing">
        <h2>Pricing</h2>
        <div className="plans">
          <div className="panel plan">
            <h3>Free</h3>
            <p className="price">$0</p>
            <ul>
              <li>Top {FREE_LIMITS.leads} leads per search, every source</li>
              <li>Save up to {FREE_LIMITS.pipeline} leads</li>
              <li>Ranked for your trade</li>
            </ul>
            <Link className="button ghost" href={signup}>Start free</Link>
          </div>
          <div className="panel plan featured">
            <h3>Pro</h3>
            <p className="price">${PRICES.month}<small>/month</small></p>
            <p className="muted" style={{ marginTop: -6 }}>or ${PRICES.year}/year — two months free</p>
            <ul>
              <li>Every lead, not just the top {FREE_LIMITS.leads}</li>
              <li>Monday email of new leads</li>
              <li>Phone &amp; email lookup</li>
              <li>Unlimited pipeline and CSV export</li>
            </ul>
            <Link className="button" href={signup}>Start free, upgrade anytime</Link>
          </div>
        </div>
      </section>

      <footer className="lp-foot muted">
        FirstKnock by HS Finest · Data from public permit records, the California ABC, local news and OpenStreetMap.
      </footer>
    </div>
  );
}
