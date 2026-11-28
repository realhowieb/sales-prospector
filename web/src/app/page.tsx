"use client";

import { useEffect, useState } from "react";
import LeadTable, { ExtLink, ScoreBar, type Column } from "@/components/LeadTable";
import { api, type Options } from "@/lib/api";

type Row = Record<string, unknown>;
type Source = "permits" | "licenses" | "news" | "map";

const SOURCES: { id: Source; label: string; blurb: string }[] = [
  {
    id: "permits",
    label: "🏗️ Build-outs & move-ins",
    blurb:
      "Commercial building permits: tenant improvements (TI) and Use & Occupancy (U&O) certificates. A company files these weeks before it moves in — while it is still choosing alarm, camera and access-control vendors. Palo Alto for now; more cities as their data becomes available.",
  },
  {
    id: "licenses",
    label: "🍷 Liquor licenses",
    blurb:
      "Pending liquor-license applications and new licenses from the California ABC daily file. A pending license means a restaurant, bar, store or hotel is building out or getting a new owner.",
  },
  {
    id: "news",
    label: "📰 Openings, leases & moves",
    blurb: "Local news from the last few weeks about businesses opening, signing leases or moving in your territory.",
  },
  {
    id: "map",
    label: "🗺️ New on the map",
    blurb:
      "Businesses recently added to OpenStreetMap — offices, clinics, salons, gyms, shops. Volunteers add listings, so some appear weeks after opening.",
  },
];

const money = (v: unknown) =>
  typeof v === "number" ? `$${v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}K` : v}` : "";
const hotSignal = (s: unknown) => /^(Move|Build)/.test(String(s));

const permitCols: Column<Row>[] = [
  { key: "Score", label: "Score", render: (r) => <ScoreBar value={Number(r.Score)} /> },
  { key: "Opened", label: "Filed", className: "nowrap" },
  { key: "Signal", label: "Signal", render: (r) => <span className={`tag ${hotSignal(r.Signal) ? "hot" : ""}`}>{String(r.Signal)}</span> },
  { key: "Business", label: "Business", className: "name", render: (r) => (r.Business ? String(r.Business) : <span className="muted">See description</span>) },
  { key: "Address", label: "Address", className: "nowrap", render: (r) => <ExtLink href={r.Map as string} label={String(r.Address)} /> },
  { key: "Value", label: "Job value", className: "nowrap", render: (r) => money(r.Value) },
  { key: "Status", label: "Status", className: "nowrap" },
  {
    key: "Description", label: "What they're doing", className: "wrap",
    render: (r) => {
      const d = String(r.Description);
      return <span title={d}>{d.length > 160 ? `${d.slice(0, 160)}…` : d}</span>;
    },
  },
  { key: "Contractor", label: "Contractor" },
  { key: "Note", label: "Note", render: (r) => (r.Note ? <span className="tag">{String(r.Note)}</span> : null) },
  { key: "Permit #", label: "Permit #", className: "nowrap" },
];

const licenseCols: Column<Row>[] = [
  { key: "Score", label: "Score", render: (r) => <ScoreBar value={Number(r.Score)} /> },
  { key: "Business", label: "Business", className: "name" },
  { key: "Person to ask for", label: "Ask for" },
  { key: "Phone", label: "Phone", className: "nowrap" },
  { key: "Email", label: "Email", render: (r) => (r.Email ? <a href={`mailto:${r.Email}`}>{String(r.Email)}</a> : null) },
  { key: "Website", label: "Website", render: (r) => <ExtLink href={r.Website as string} /> },
  { key: "Address", label: "Address", className: "nowrap" },
  { key: "City", label: "City", className: "nowrap" },
  { key: "Type", label: "Type" },
  { key: "Trigger", label: "Trigger", render: (r) => <span className={`tag ${String(r.Trigger).startsWith("Pending") ? "hot" : ""}`}>{String(r.Trigger)}</span> },
  { key: "Owner", label: "Owner" },
  { key: "ABC record", label: "Owners", render: (r) => <ExtLink href={r["ABC record"] as string} label="ABC record" /> },
];

const newsCols: Column<Row>[] = [
  { key: "Score", label: "Score", render: (r) => <ScoreBar value={Number(r.Score)} /> },
  { key: "Date", label: "Date", className: "nowrap" },
  { key: "Signal", label: "Signal", render: (r) => <span className="tag hot">{String(r.Signal)}</span> },
  { key: "City", label: "City", className: "nowrap" },
  { key: "Headline", label: "Headline", className: "wrap", render: (r) => <ExtLink href={r.Link as string} label={String(r.Headline)} /> },
  { key: "Source", label: "Source" },
];

const mapCols: Column<Row>[] = [
  { key: "Score", label: "Score", render: (r) => <ScoreBar value={Number(r.Score)} /> },
  { key: "Added", label: "Added", className: "nowrap" },
  { key: "Business", label: "Business", className: "name" },
  { key: "Kind", label: "Kind" },
  { key: "Address", label: "Address", className: "nowrap" },
  { key: "Phone", label: "Phone", className: "nowrap" },
  { key: "Email", label: "Email", render: (r) => (r.Email ? <a href={`mailto:${r.Email}`}>{String(r.Email)}</a> : null) },
  { key: "Website", label: "Website", render: (r) => <ExtLink href={r.Website as string} /> },
  { key: "Note", label: "Note", render: (r) => (r.Note ? <span className="tag">{String(r.Note)}</span> : null) },
  { key: "Map", label: "Map", render: (r) => <ExtLink href={r.Map as string} label="View" /> },
];

const DAYS: Record<Source, number[]> = { permits: [30, 60, 90, 180, 365], licenses: [90, 180, 365], news: [7, 14, 30, 60], map: [30, 60, 90, 180] };
const DEFAULT_DAYS: Record<Source, number> = { permits: 90, licenses: 180, news: 30, map: 60 };

export default function TriggerLeads() {
  const [opts, setOpts] = useState<Options | null>(null);
  const [territory, setTerritory] = useState("");
  const [custom, setCustom] = useState("");
  const [source, setSource] = useState<Source>("permits");
  const [days, setDays] = useState<Record<Source, number>>(DEFAULT_DAYS);
  const [hideChains, setHideChains] = useState(true);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [withContacts, setWithContacts] = useState(false);
  const [notCovered, setNotCovered] = useState<string[]>([]);
  const [loading, setLoading] = useState<string>("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Options>("/v1/options")
      .then((o) => {
        setOpts(o);
        setTerritory(Object.keys(o.territories)[0] ?? "custom");
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  const cities = territory === "custom" ? custom : territory;
  const cityList = territory === "custom" ? custom.split(",").map((c) => c.trim()).filter(Boolean) : opts?.territories[territory] ?? [];

  async function run(contacts = false) {
    setError("");
    setLoading(
      contacts
        ? "Matching public listings and reading business websites (1–2 minutes)…"
        : source === "permits"
          ? "Reading the city's permit records…"
          : source === "map"
          ? "Searching the map for newly added businesses (up to a minute)…"
          : source === "news"
            ? "Searching local news…"
            : "Downloading today's California ABC license file…",
    );
    if (!contacts) setRows(null);
    try {
      const query = { cities, days: days[source], hide_chains: hideChains };
      const path =
        source === "permits" ? "/v1/leads/permits" : source === "licenses" ? (contacts ? "/v1/leads/licenses/contacts" : "/v1/leads/licenses") : source === "news" ? "/v1/leads/news" : "/v1/leads/new-on-map";
      const res = await api<{ leads: Row[]; not_covered?: string[] }>(path, { method: contacts ? "POST" : "GET", query });
      setRows(res.leads);
      setNotCovered(res.not_covered ?? []);
      setWithContacts(contacts || source !== "licenses");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading("");
    }
  }

  const current = SOURCES.find((s) => s.id === source)!;
  const count = rows?.length ?? 0;

  return (
    <div className="page">
      <h1>Trigger Leads</h1>
      <p className="lede">Businesses opening, moving or changing hands — the moment they buy alarms, cameras, access control and services.</p>

      <div className="panel">
        <div className="controls">
          <label className="wide">
            Territory
            <select value={territory} onChange={(e) => { setTerritory(e.target.value); setRows(null); }}>
              {Object.keys(opts?.territories ?? {}).map((t) => (
                <option key={t}>{t}</option>
              ))}
              <option value="custom">Custom cities…</option>
            </select>
          </label>
          {territory === "custom" && (
            <label className="wide">
              California cities (comma-separated)
              <input value={custom} placeholder="Fresno, Clovis" onChange={(e) => setCustom(e.target.value)} />
            </label>
          )}
          <label>
            Look back
            <select value={days[source]} onChange={(e) => setDays({ ...days, [source]: Number(e.target.value) })}>
              {DAYS[source].map((d) => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          </label>
          <label className="check-label">
            <input type="checkbox" checked={hideChains} onChange={(e) => setHideChains(e.target.checked)} /> Hide chains
          </label>
        </div>
        {territory !== "custom" && <div className="cities">{cityList.join(", ")}</div>}
      </div>

      <div className="tabs" role="tablist">
        {SOURCES.map((s) => (
          <button key={s.id} role="tab" aria-selected={source === s.id} className={source === s.id ? "on" : ""}
            onClick={() => { setSource(s.id); setRows(null); setError(""); }}>
            {s.label}
          </button>
        ))}
      </div>
      <p className="muted">{current.blurb}</p>

      <div className="row">
        <button disabled={!cities || Boolean(loading)} onClick={() => run(false)}>Find leads</button>
        {source === "licenses" && rows && !withContacts && (
          <button className="ghost" disabled={Boolean(loading)} onClick={() => run(true)}>📞 Look up phone &amp; email</button>
        )}
      </div>

      {loading && <div className="notice info">{loading}</div>}
      {error && <div className="notice error">{error}</div>}

      {rows && (
        <>
          <div className="stats">
            <div className="stat"><b>{count}</b><span>{source === "news" ? "Stories" : "Leads"}</span></div>
            {source === "licenses" && (
              <div className="stat"><b>{rows.filter((r) => String(r.Trigger).startsWith("Pending")).length}</b><span>Pending (opening / new owner)</span></div>
            )}
            {source === "permits" && (
              <div className="stat"><b>{rows.filter((r) => String(r.Signal).startsWith("Move")).length}</b><span>Move-ins (U&amp;O)</span></div>
            )}
            {source !== "news" && source !== "permits" && (
              <div className="stat"><b>{withContacts ? rows.filter((r) => r.Phone || r.Email).length : "—"}</b><span>With phone or email</span></div>
            )}
            {source === "news" && (
              <div className="stat"><b>{rows.filter((r) => r.Signal !== "Opening").length}</b><span>Leases &amp; moves</span></div>
            )}
          </div>
          {source === "permits" && notCovered.length > 0 && (
            <div className="notice info">
              Permit data covers {cityList.filter((c) => !notCovered.includes(c)).join(", ") || "none of these cities"} so far. Not yet: {notCovered.join(", ")}.
            </div>
          )}
          {count === 0 ? (
            <div className="notice info">Nothing found for these cities. Try a longer look-back.</div>
          ) : source === "permits" ? (
            <LeadTable rows={rows} columns={permitCols} csvName="build_outs.csv"
              rowId={(r) => `permit-${r["Permit #"]}`}
              toPipeline={(r) => ({
                name: String(r.Business || r.Address), category: String(r.Signal),
                note: [
                  `${r.Signal} filed ${r.Opened}`, `${r.Address}, ${r.City}`, r.Value ? `Job ${money(r.Value)}` : "",
                  r.Contractor ? `Contractor: ${r.Contractor}` : "", `Permit ${r["Permit #"]}`, String(r.Description).slice(0, 300),
                ].filter(Boolean).join(" · "),
              })} />
          ) : source === "licenses" ? (
            <LeadTable rows={rows} columns={licenseCols} csvName="license_leads.csv"
              rowId={(r) => `abc-${r["ABC file #"]}`}
              toPipeline={(r) => ({
                name: String(r.Business), category: String(r.Type),
                note: [r.Trigger, `${r.Address}, ${r.City}`, r.Phone, r.Email, `Owner: ${r["Person to ask for"] || r.Owner}`].filter(Boolean).join(" · "),
              })} />
          ) : source === "news" ? (
            <LeadTable rows={rows} columns={newsCols} csvName="news_leads.csv"
              rowId={(r) => `news-${hash(String(r.Link))}`}
              toPipeline={(r) => ({ name: String(r.Headline).slice(0, 80), category: String(r.Signal), note: `${r.Signal} · ${r.City} · ${r.Date} · ${r.Link}` })} />
          ) : (
            <LeadTable rows={rows} columns={mapCols} csvName="new_on_map.csv"
              rowId={(r) => `osm-${r["OSM id"]}`}
              toPipeline={(r) => ({
                name: String(r.Business), category: String(r.Kind),
                note: [`New on map ${r.Added}`, r.Address, r.Phone, r.Email].filter(Boolean).join(" · "),
              })} />
          )}
        </>
      )}
    </div>
  );
}

function hash(s: string): string {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}
