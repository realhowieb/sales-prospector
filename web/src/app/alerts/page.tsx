"use client";

import { useEffect, useState } from "react";
import { api, type Options } from "@/lib/api";
import Link from "next/link";
import { usePlan } from "@/lib/plan";
import { useTrade } from "@/lib/trades";

type Digest = {
  active: boolean;
  territory: string;
  sources: string[];
  last_sent_at: string | null;
  email: string;
  sources_available: Record<string, string>;
};

export default function WeeklyEmail() {
  const trade = useTrade();
  const { pro } = usePlan();
  const [d, setD] = useState<Digest | null>(null);
  const [territories, setTerritories] = useState<string[]>([]);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "error" | "info"; text: string } | null>(null);

  useEffect(() => {
    Promise.all([api<Options>("/v1/options"), api<Digest>("/v1/digest")])
      .then(([o, dg]) => {
        const names = Object.keys(o.territories);
        setTerritories(names);
        if (!names.includes(dg.territory)) setCustom(dg.territory);
        setD(dg);
      })
      .catch((e) => setMsg({ kind: "error", text: (e as Error).message }));
  }, []);

  if (!d) {
    return (
      <div className="page">
        <h1>Weekly email</h1>
        {msg ? <div className={`notice ${msg.kind}`}>{msg.text}</div> : <p className="muted">Loading…</p>}
      </div>
    );
  }

  const isCustom = !territories.includes(d.territory);
  const territory = isCustom ? custom : d.territory;
  const body = { active: d.active, territory, sources: d.sources };

  async function save(next: Partial<Digest> = {}) {
    const payload = { ...body, ...next, territory: next.territory ?? territory };
    setBusy("save");
    setMsg(null);
    try {
      const saved = await api<Partial<Digest>>("/v1/digest", { method: "PUT", body: payload });
      setD((prev) => (prev ? { ...prev, ...saved } : prev));
      setMsg({ kind: "ok", text: payload.active ? "Saved. Your next email goes out Monday morning." : "Saved. Weekly email is off." });
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  async function sendTest() {
    setBusy("test");
    setMsg({ kind: "info", text: "Gathering this week's leads and sending a test (up to a minute)…" });
    try {
      const r = await api<{ sent_to: string; leads: number; unavailable: string[] }>("/v1/digest/test", { method: "POST", body: { ...body, trade: trade.id } });
      const skipped = r.unavailable.length ? ` (${r.unavailable.map((s) => d!.sources_available[s]).join(", ")} didn't respond)` : "";
      setMsg({ kind: "ok", text: `Test sent to ${r.sent_to} with ${r.leads} leads${skipped}. Check your inbox.` });
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  }

  const toggleSource = (s: string) =>
    setD({ ...d, sources: d.sources.includes(s) ? d.sources.filter((x) => x !== s) : [...d.sources, s] });

  return (
    <div className="page">
      <h1>Weekly email</h1>
      <p className="lede">Every Monday morning: the new leads in your territory that you haven&apos;t been sent before, ranked for {trade.emoji} {trade.label}.</p>

      {!pro && (
        <div className="notice info upsell">The weekly email is part of Pro. <Link href="/account/">Upgrade</Link> to turn it on.</div>
      )}
      <div className="panel" style={pro ? undefined : { opacity: 0.6, pointerEvents: "none" }}>
        <label className="check-label" style={{ fontSize: "1.05rem" }}>
          <input type="checkbox" checked={d.active} onChange={(e) => setD({ ...d, active: e.target.checked })} />
          <b>Send me the weekly email</b> <span className="muted">to {d.email}</span>
        </label>

        <div className="controls" style={{ marginTop: 14 }}>
          <label className="wide">
            Territory
            <select value={isCustom ? "custom" : d.territory}
              onChange={(e) => setD({ ...d, territory: e.target.value === "custom" ? custom || "custom" : e.target.value })}>
              {territories.map((t) => <option key={t}>{t}</option>)}
              <option value="custom">Custom cities…</option>
            </select>
          </label>
          {isCustom && (
            <label className="wide">
              California cities (comma-separated)
              <input value={custom} placeholder="Palo Alto, Menlo Park" onChange={(e) => setCustom(e.target.value)} />
            </label>
          )}
        </div>

        <div style={{ marginTop: 14 }}>
          <div className="muted" style={{ marginBottom: 6 }}>Include</div>
          {Object.entries(d.sources_available).map(([id, label]) => (
            <label key={id} className="check-label" style={{ marginRight: 18 }}>
              <input type="checkbox" checked={d.sources.includes(id)} onChange={() => toggleSource(id)} /> {label}
            </label>
          ))}
        </div>

        <div className="row" style={{ marginTop: 16 }}>
          <button disabled={Boolean(busy) || (isCustom && !custom.trim())} onClick={() => save()}>
            {busy === "save" ? "Saving…" : "Save"}
          </button>
          <button className="ghost" disabled={Boolean(busy) || !d.sources.length || !territory.trim()} onClick={sendTest}>
            {busy === "test" ? "Sending…" : "✉️ Send me a test now"}
          </button>
        </div>
        {d.last_sent_at && <p className="muted" style={{ marginBottom: 0 }}>Last sent {new Date(d.last_sent_at).toLocaleString()}.</p>}
      </div>

      {msg && <div className={`notice ${msg.kind}`}>{msg.text}</div>}
    </div>
  );
}
