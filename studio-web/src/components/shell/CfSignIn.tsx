// Sign in to Cloud Foundry with a one-time SSO passcode — shown wherever the studio needs a CF session
// (import / adopt from Cloud Foundry) and has none. The session belongs to the user; no password is entered.
import { useEffect, useState } from "react";
import { Button, Input, MessageStrip } from "@ui5/webcomponents-react";
import { api, ApiError } from "../../api/client";

/** True when an API error means "the studio has no Cloud Foundry session". */
export const isNoCfSession = (e: unknown) => e instanceof ApiError && e.status === 409 && /session|sign/i.test(e.message);

export function CfPasscodeSignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [url, setUrl] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api.get<{ passcode_url?: string }>("api/cf/session").then((s) => setUrl(s.passcode_url || "")).catch(() => undefined); }, []);
  const login = async () => {
    setBusy(true); setErr("");
    try { await api.post("api/cf/login", { passcode: code.trim() }); setCode(""); onSignedIn(); }
    catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="ev-cfsign">
      <div className="ev-cfsign__t">Sign in to Cloud Foundry</div>
      <div className="ev-cfsign__s">Needed once to list and download the apps in your space. The session is yours; no password is stored.</div>
      <ol>
        <li>Open {url ? <a href={url} target="_blank" rel="noreferrer">{url.replace(/^https:\/\//, "")}</a> : "the Cloud Foundry passcode page"} and sign in with your BTP user.</li>
        <li>Copy the <b>Temporary Authentication Code</b> and paste it below. It works once.</li>
      </ol>
      <div className="ev-row">
        <Input type="Password" placeholder="Passcode" value={code} onInput={(e) => setCode(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && code.trim()) login(); }} style={{ flex: 1, minWidth: 220 }} />
        <Button design="Emphasized" icon="log" disabled={!code.trim() || busy} onClick={login}>{busy ? "Signing in…" : "Sign in"}</Button>
      </div>
      {err && <MessageStrip design="Negative" hideCloseButton>{err}</MessageStrip>}
    </div>
  );
}
