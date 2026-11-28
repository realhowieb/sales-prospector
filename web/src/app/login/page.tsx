"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useSession } from "@/components/AuthGate";
import { DEFAULT_TRADE, TRADES } from "@/lib/trades";

type Mode = "signin" | "signup" | "reset";

export default function Login() {
  const session = useSession();
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [trade, setTrade] = useState<string>(DEFAULT_TRADE);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    if (session) router.replace("/");
  }, [session, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else if (mode === "signup") {
        if (password.length < 8) throw new Error("Use at least 8 characters for your password.");
        const { data, error } = await supabase.auth.signUp({ email, password, options: { emailRedirectTo: `${location.origin}/`, data: { trade } } });
        if (error) throw error;
        if (!data.session) setMsg({ kind: "ok", text: "Account created. Check your email to confirm it, then sign in." });
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: `${location.origin}/reset/` });
        if (error) throw error;
        setMsg({ kind: "ok", text: "If that email has an account, a reset link is on its way." });
      }
    } catch (err) {
      setMsg({ kind: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth">
      <h1>🚪 FirstKnock</h1>
      <p className="muted" style={{ textAlign: "center", marginTop: 0 }}>Be the first to knock on new businesses.</p>
      <form className="panel" onSubmit={submit}>
        <div className="switch">
          {(["signin", "signup", "reset"] as Mode[]).map((m) => (
            <button type="button" key={m} className={mode === m ? "" : "ghost"} onClick={() => { setMode(m); setMsg(null); }}>
              {m === "signin" ? "Sign in" : m === "signup" ? "Sign up" : "Reset"}
            </button>
          ))}
        </div>
        <label>
          Email
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        {mode !== "reset" && (
          <label>
            Password
            <input type="password" autoComplete={mode === "signin" ? "current-password" : "new-password"} required
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
        )}
        {mode === "signup" && (
          <label>
            What do you sell?
            <select value={trade} onChange={(e) => setTrade(e.target.value)}>
              {TRADES.map((t) => <option key={t.id} value={t.id}>{t.emoji} {t.label}</option>)}
            </select>
          </label>
        )}
        <button type="submit" disabled={busy}>
          {busy ? "Working…" : mode === "signin" ? "Sign in" : mode === "signup" ? "Create account" : "Send reset link"}
        </button>
        {msg && <div className={`notice ${msg.kind}`}>{msg.text}</div>}
      </form>
    </div>
  );
}
