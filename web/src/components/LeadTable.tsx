"use client";

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { addToPipeline, downloadCsv, toCsv } from "@/lib/api";
import { usePlan } from "@/lib/plan";

export type Column<T> = { key: string; label: string; render?: (row: T) => ReactNode; className?: string };

type Props<T> = {
  rows: T[];
  columns: Column<T>[];
  rowId: (row: T) => string;
  toPipeline: (row: T) => { name: string; category: string; note: string };
  csvName: string;
  total?: number; // how many the search found (free plans see the top few)
  limited?: boolean;
};

export function ScoreBar({ value }: { value: number }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <span className="score" title={`Score ${v}`}>
      <span className="bar"><span style={{ width: `${v}%` }} /></span>
      <b>{v}</b>
    </span>
  );
}

export function ExtLink({ href, label }: { href?: string; label?: string }) {
  if (!href) return null;
  const url = /^https?:\/\//.test(href) ? href : `https://${href}`;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer">
      {label ?? url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
    </a>
  );
}

export default function LeadTable<T extends Record<string, unknown>>({ rows, columns, rowId, toPipeline, csvName, total, limited }: Props<T>) {
  const { pro } = usePlan();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [filter, setFilter] = useState("");

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((r) => Object.values(r).some((v) => typeof v === "string" && v.toLowerCase().includes(q)));
  }, [rows, filter]);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function addPicked() {
    setBusy(true);
    setMsg(null);
    const chosen = rows.filter((r) => picked.has(rowId(r)));
    try {
      for (const r of chosen) {
        const p = toPipeline(r);
        await addToPipeline(rowId(r), p.name, p.category, p.note);
      }
      setAdded((prev) => new Set([...prev, ...chosen.map(rowId)]));
      setPicked(new Set());
      setMsg({ kind: "ok", text: `Added ${chosen.length} to My Pipeline.` });
    } catch (e) {
      setMsg({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="table-wrap">
      <div className="table-tools">
        <input className="search" placeholder="Filter results…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <span className="muted">{shown.length} shown</span>
        <span className="spacer" />
        <button disabled={!picked.size || busy} onClick={addPicked}>
          {busy ? "Adding…" : `Add ${picked.size || ""} to pipeline`}
        </button>
        {pro ? (
          <button className="ghost" onClick={() => downloadCsv(csvName, toCsv(shown, columns.map((c) => c.key)))}>
            Download CSV
          </button>
        ) : (
          <Link className="button ghost" href="/account/" title="CSV download is a Pro feature">Download CSV · Pro</Link>
        )}
      </div>
      {msg && <div className={`notice ${msg.kind}`}>{msg.text}</div>}
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th className="check" aria-label="Select" />
              {columns.map((c) => (
                <th key={c.key} className={c.className}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const id = rowId(r);
              return (
                <tr key={id} className={picked.has(id) ? "picked" : ""}>
                  <td className="check">
                    {added.has(id) ? (
                      <span title="In your pipeline">✓</span>
                    ) : (
                      <input type="checkbox" checked={picked.has(id)} onChange={() => toggle(id)} aria-label="Select lead" />
                    )}
                  </td>
                  {columns.map((c) => (
                    <td key={c.key} className={c.className}>
                      {c.render ? c.render(r) : String(r[c.key] ?? "")}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {limited && (
        <div className="notice info upsell">
          Showing the top {rows.length} of {total ?? rows.length} leads. <Link href="/account/">Upgrade to Pro</Link> to see all of them, plus the weekly email, phone &amp; email lookup and CSV.
        </div>
      )}
    </div>
  );
}
