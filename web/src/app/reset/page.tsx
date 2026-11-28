"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Session } from "@supabase/supabase-js";
import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { useSession } from "@/components/AuthGate";

export default function Reset() {
  const gateSession = useSession();
  const [linkSession, setLinkSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(false);
  const session = linkSession ?? gateSession;
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [linkProblem, setLinkProblem] = useState("");
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(`${location.hash.replace(/^#/, "")}&${location.search.replace(/^\?/, "")}`);
    // A good link carries a one-time sign-in in the URL. Use it directly instead of relying on the
    // background check, so a slow or failed background step can't leave you stuck.
    const access = params.get("access_token");
    const refresh = params.get("refresh_token");
    if (access && refresh) {
      setChecking(true);
      supabase.auth
        .setSession({ access_token: access, refresh_token: refresh })
        .then(({ data, error: err }) => {
          if (err || !data.session) {
            setLinkProblem(`That reset link couldn't be used${err ? ` (${err.message})` : ""}. Send yourself a new one below.`);
          } else {
            setLinkSession(data.session);
          }
        })
        .catch((err: Error) => setLinkProblem(`That reset link couldn't be used (${err.message}). Send yourself a new one below.`))
        .finally(() => {
          setChecking(false);
          history.replaceState(null, "", "/reset/"); // never leave the sign-in token in the address bar
        });
      return;
    }
    // Supabase sends expired / already-used links back here with the reason in the URL.
    const code = params.get("error_code") || params.get("error");
    if (!code) return;
    setLinkProblem(
      code === "otp_expired"
        ? "That reset link has expired or was already used. Each new reset email replaces the last one, and links last an hour."
        : params.get("error_description")?.replace(/\+/g, " ") || "That reset link didn't work.",
    );
    history.replaceState(null, "", "/reset/");
  }, []);

  async function resend(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const { error: err } = await supabase.auth.resetPasswordForEmail(email.trim(), { redirectTo: `${location.origin}/reset/` });
    if (err) return setError(err.message);
    setSent(email.trim());
  }

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

  if (checking && !session) {
    return <div className="auth"><h1>🔑 Set a new password</h1><p className="muted">Checking your reset link…</p></div>;
  }
  if (!session) {
    return (
      <div className="auth">
        <h1>🔑 Set a new password</h1>
        <form className="panel" onSubmit={resend}>
          {linkProblem ? (
            <div className="notice error" style={{ marginTop: 0 }}>{linkProblem}</div>
          ) : (
            <p style={{ marginTop: 0 }}>Open this page from the reset link in your email. Links expire after an hour.</p>
          )}
          {sent ? (
            <div className="notice ok">
              New link sent to {sent}. Open the <b>newest</b> email from FirstKnock and click Reset password (check spam too).
            </div>
          ) : (
            <>
              <label>
                Email
                <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
              </label>
              <button type="submit">Send a new reset link</button>
            </>
          )}
          {error && <div className="notice error">{error}</div>}
          <Link href="/login/">Back to sign in</Link>
        </form>
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
