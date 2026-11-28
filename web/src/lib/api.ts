import { supabase } from "./supabase";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | undefined>;

export async function api<T>(path: string, opts: { method?: string; query?: Query; body?: unknown } = {}): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  const qs = new URLSearchParams();
  const trade = data.session?.user.user_metadata?.trade as string | undefined;
  if (trade) qs.set("trade", trade);
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== "") qs.set(k, String(v));
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}${qs.size ? `?${qs}` : ""}`, {
    method: opts.method ?? "GET",
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Couldn't reach the FirstKnock server. Check your connection and try again.");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") detail = j.detail;
      else if (Array.isArray(j.detail)) detail = j.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {}
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export type Options = {
  territories: Record<string, string[]>;
  metros: string[];
  categories: string[];
  profiles: string[];
  stages: string[];
  trades: { id: string; label: string; emoji: string; sells: string }[];
};

export type PipelineEntry = {
  prospect_id: string;
  name: string;
  category: string;
  stage: string;
  note: string;
  next_follow_up: string | null;
  updated_at?: string;
};

export function addToPipeline(id: string, name: string, category: string, note: string) {
  return api<PipelineEntry>(`/v1/pipeline/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: { name, category, stage: "New lead", note },
  });
}

export function toCsv(rows: Record<string, unknown>[], cols: string[]): string {
  const esc = (v: unknown) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
}

export function downloadCsv(filename: string, csv: string) {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
