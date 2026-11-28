"use client";

import { useEffect, useMemo, useState } from "react";
import { api, downloadCsv, toCsv, type Options, type PipelineEntry } from "@/lib/api";

export default function Pipeline() {
  const [entries, setEntries] = useState<PipelineEntry[] | null>(null);
  const [stages, setStages] = useState<string[]>(["New lead", "Contacted", "Qualified", "Won", "Passed"]);
  const [stageFilter, setStageFilter] = useState("All");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState<string>("");

  useEffect(() => {
    api<Options>("/v1/options").then((o) => setStages(o.stages)).catch(() => {});
    api<{ entries: PipelineEntry[] }>("/v1/pipeline")
      .then((r) => setEntries(r.entries))
      .catch((e) => setError((e as Error).message));
  }, []);

  const shown = useMemo(
    () => (entries ?? []).filter((e) => stageFilter === "All" || e.stage === stageFilter),
    [entries, stageFilter],
  );

  async function save(entry: PipelineEntry) {
    setSaving(entry.prospect_id);
    setError("");
    try {
      const saved = await api<PipelineEntry>(`/v1/pipeline/${encodeURIComponent(entry.prospect_id)}`, {
        method: "PUT",
        body: { name: entry.name, category: entry.category, stage: entry.stage, note: entry.note, next_follow_up: entry.next_follow_up || null },
      });
      setEntries((prev) => (prev ?? []).map((e) => (e.prospect_id === saved.prospect_id ? { ...e, ...saved } : e)));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving("");
    }
  }

  async function remove(id: string) {
    if (!confirm("Remove this lead from your pipeline?")) return;
    try {
      await api(`/v1/pipeline/${encodeURIComponent(id)}`, { method: "DELETE" });
      setEntries((prev) => (prev ?? []).filter((e) => e.prospect_id !== id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const update = (id: string, patch: Partial<PipelineEntry>) =>
    setEntries((prev) => (prev ?? []).map((e) => (e.prospect_id === id ? { ...e, ...patch } : e)));

  return (
    <div className="page">
      <h1>My Pipeline</h1>
      <p className="lede">Leads you&apos;ve saved. Saved to your account, so they&apos;re here on any device.</p>
      {error && <div className="notice error">{error}</div>}
      {!entries && !error && <p className="muted">Loading…</p>}
      {entries && (
        <>
          <div className="stats">
            {stages.map((s) => (
              <div className="stat" key={s}><b>{entries.filter((e) => e.stage === s).length}</b><span>{s}</span></div>
            ))}
          </div>
          <div className="row" style={{ marginBottom: 12 }}>
            <label>
              Show
              <select value={stageFilter} onChange={(e) => setStageFilter(e.target.value)}>
                <option>All</option>
                {stages.map((s) => <option key={s}>{s}</option>)}
              </select>
            </label>
            <span style={{ flex: 1 }} />
            <button className="ghost" disabled={!entries.length}
              onClick={() => downloadCsv("pipeline.csv", toCsv(entries as unknown as Record<string, unknown>[], ["name", "category", "stage", "next_follow_up", "note", "prospect_id"]))}>
              Download CSV
            </button>
          </div>
          {!entries.length && <div className="notice info">No saved leads yet. Tick leads on Trigger Leads or Discover and click “Add to pipeline”.</div>}
          <div className="pipeline-grid">
            {shown.map((e) => (
              <div className="card" key={e.prospect_id}>
                <header>
                  <b>{e.name || e.prospect_id}</b>
                  <span className="tag">{e.category}</span>
                  <span style={{ flex: 1 }} />
                  <span className="muted" style={{ fontSize: "0.8rem" }}>{e.updated_at ? `Updated ${e.updated_at.slice(0, 10)}` : ""}</span>
                </header>
                <div className="row">
                  <label>
                    Stage
                    <select value={e.stage} onChange={(ev) => update(e.prospect_id, { stage: ev.target.value })}>
                      {stages.map((s) => <option key={s}>{s}</option>)}
                    </select>
                  </label>
                  <label>
                    Next follow-up
                    <input type="date" value={e.next_follow_up ?? ""} onChange={(ev) => update(e.prospect_id, { next_follow_up: ev.target.value || null })} />
                  </label>
                </div>
                <textarea value={e.note ?? ""} onChange={(ev) => update(e.prospect_id, { note: ev.target.value })} aria-label="Notes" />
                <div className="row">
                  <button disabled={saving === e.prospect_id} onClick={() => save(e)}>{saving === e.prospect_id ? "Saving…" : "Save"}</button>
                  <button className="ghost" onClick={() => remove(e.prospect_id)}>Remove</button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
