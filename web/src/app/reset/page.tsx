"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { supabase } from "@/lib/supabase";
import { useSession } from "@/components/AuthGate";

export default function Reset() {
  const session = useSession();
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (password.length < 8) return setError("Use at least 8 characters.");
    if (password !== confirm) return setError("The two passwords don't match.");
    setBusy(true);
    const { error: err } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (err) return setError(err.message);
    router.replace("/");
  }

  if (!session) {
    return (
      <div className="auth">
        <h1>🔑 Set a new password</h1>
        <div className="panel">
          <p>Open this page from the reset link in your email. Links expire after an hour.</p>
          <Link href="/login/">Request a new reset link →</Link>
        </div>
      </div>
    );
  }
  return (
    <div className="auth">
      <h1>🔑 Set a new password</h1>
      <form className="panel" onSubmit={submit}>
        <p className="muted" style={{ margin: 0 }}>For {session.user.email}</p>
        <label>
          New password
          <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label>
          Confirm new password
          <input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Set new password"}</button>
        {error && <div className="notice error">{error}</div>}
      </form>
    </div>
  );
}
