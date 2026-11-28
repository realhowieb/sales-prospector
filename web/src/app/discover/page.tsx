"use client";

import { useEffect, useState } from "react";
import LeadTable, { ExtLink, ScoreBar, type Column } from "@/components/LeadTable";
import { api, type Options } from "@/lib/api";

type Row = Record<string, unknown>;

const cols: Column<Row>[] = [
  { key: "score", label: "Score", render: (r) => <ScoreBar value={Number(r.score)} /> },
  { key: "name", label: "Business", className: "name" },
  { key: "category", label: "Category", className: "nowrap" },
  { key: "phone", label: "Phone", className: "nowrap" },
  { key: "website", label: "Website", render: (r) => <ExtLink href={r.website as string} /> },
  { key: "address", label: "Address", className: "nowrap" },
  { key: "insight", label: "Why call", className: "wrap" },
];

export default function Discover() {
  const [opts, setOpts] = useState<Options | null>(null);
  const [metro, setMetro] = useState("");
  const [area, setArea] = useState("");
  const [cats, setCats] = useState<string[]>([]);
  const [profile, setProfile] = useState("");
  const [rows, setRows] = useState<Row[] | null>(null);
  const [label, setLabel] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Options>("/v1/options")
      .then((o) => {
        setOpts(o);
        setMetro(o.metros[0] ?? "");
        setCats(o.categories.slice(0, 3));
        // Empty = let the API pick the profile for the rep's trade.
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  async function run() {
    setLoading(true);
    setError("");
    setRows(null);
    try {
      const res = await api<{ area: string; prospects: Row[] }>("/v1/discover", {
        query: { metro, area, categories: cats.join(","), profile },
      });
      setRows(res.prospects);
      setLabel(res.area);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <h1>Discover</h1>
      <p className="lede">Every business of a given type in an area, scored for how winnable it is for what you sell.</p>
      <div className="panel">
        <div className="controls">
          <label>
            Metro
            <select value={metro} onChange={(e) => setMetro(e.target.value)}>
              {opts?.metros.map((m) => <option key={m}>{m}</option>)}
            </select>
          </label>
          <label>
            …or any city / neighborhood
            <input value={area} placeholder="Redwood City, CA" onChange={(e) => setArea(e.target.value)} />
          </label>
          <label>
            What do you sell?
            <select value={profile} onChange={(e) => setProfile(e.target.value)}>
              <option value="">Match my trade</option>
              {opts?.profiles.map((p) => <option key={p}>{p}</option>)}
            </select>
          </label>
        </div>
        <div style={{ marginTop: 12 }}>
          <div className="muted" style={{ fontSize: "0.85rem", fontWeight: 600, marginBottom: 6 }}>Categories</div>
          <div className="chips">
            {opts?.categories.map((c) => (
              <button key={c} className={`chip ${cats.includes(c) ? "on" : ""}`}
                onClick={() => setCats(cats.includes(c) ? cats.filter((x) => x !== c) : [...cats, c])}>
                {c}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <button disabled={!cats.length || loading} onClick={run}>{loading ? "Searching…" : "Search"}</button>
        {area && <span className="muted">Searching “{area}” instead of the metro.</span>}
      </div>
      {loading && <div className="notice info">Searching OpenStreetMap — can take up to a minute when the free map servers are busy…</div>}
      {error && <div className="notice error">{error}</div>}
      {rows && (
        <>
          <div className="stats">
            <div className="stat"><b>{rows.length}</b><span>Businesses in {label}</span></div>
            <div className="stat"><b>{rows.filter((r) => Number(r.score) >= 70).length}</b><span>Hot (score 70+)</span></div>
            <div className="stat"><b>{rows.filter((r) => r.phone).length}</b><span>With phone</span></div>
          </div>
          <LeadTable rows={rows} columns={cols} csvName="discover.csv" rowId={(r) => String(r.id)}
            toPipeline={(r) => ({
              name: String(r.name), category: String(r.category),
              note: [r.address, r.phone, r.website].filter(Boolean).join(" · "),
            })} />
        </>
      )}
    </div>
  );
}
