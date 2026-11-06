// Live state of the workspace database (SAP HANA Cloud on BTP). The free-tier instance stops by
// itself; then every save fails. This polls GET api/health/store (and reacts at once to any API call
// that came back 503 "store_unavailable") and shows a banner above the page until it is back.
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Button, MessageStrip } from "@ui5/webcomponents-react";
import { api, STORE_DOWN_EVENT } from "../../api/client";
import type { StoreHealth } from "../../api/types";

const POLL_UP = 30000;    // healthy: check every 30 s
const POLL_DOWN = 15000;  // down: retry every 15 s so the banner clears soon after the instance starts

interface Ctx { health: StoreHealth | null; recovered: boolean; dismiss: () => void; check: () => void }
const StoreCtx = createContext<Ctx>({ health: null, recovered: false, dismiss: () => undefined, check: () => undefined });
export const useStoreHealth = () => useContext(StoreCtx);

export function StoreStatusProvider({ initial, children }: { initial?: StoreHealth | null; children: React.ReactNode }) {
  const [health, setHealth] = useState<StoreHealth | null>(initial || null);
  const [recovered, setRecovered] = useState(false);
  const wasDown = useRef(false);

  useEffect(() => { if (initial && !health) { setHealth(initial); if (!initial.ok) wasDown.current = true; } }, [initial]); // eslint-disable-line

  const apply = useCallback((h: StoreHealth) => {
    setHealth(h);
    if (!h.ok) { wasDown.current = true; setRecovered(false); }
    else if (wasDown.current) { wasDown.current = false; setRecovered(true); }
  }, []);

  const check = useCallback((fresh = false) => {
    api.get<StoreHealth>(`api/health/store${fresh ? "?fresh=1" : ""}`).then(apply).catch(() => { /* API itself down: other errors show */ });
  }, [apply]);

  const down = health ? !health.ok : false;
  useEffect(() => {
    const t = setInterval(() => check(down), down ? POLL_DOWN : POLL_UP);
    return () => clearInterval(t);
  }, [down, check]);

  useEffect(() => {
    const onDown = (e: Event) => {
      const j = (e as CustomEvent).detail || {};
      apply({ kind: "hana", ok: false, message: j.detail || "", detail: j.cause || "", checked_at: Date.now() / 1000,
              since: health?.ok === false ? health.since : Date.now() / 1000 });
    };
    window.addEventListener(STORE_DOWN_EVENT, onDown);
    return () => window.removeEventListener(STORE_DOWN_EVENT, onDown);
  }, [apply, health]);

  return (
    <StoreCtx.Provider value={{ health, recovered, dismiss: () => setRecovered(false), check: () => check(true) }}>
      {children}
    </StoreCtx.Provider>
  );
}

/** Banner above the page: red while the database is offline, green once after it came back. */
export function StoreBanner() {
  const { health, recovered, dismiss, check } = useStoreHealth();
  const down = health ? !health.ok : false;
  if (down && health) return (
    <div className="ev-store-banner" role="alert">
      <MessageStrip design="Negative" hideCloseButton>
        <b>Workspace database offline.</b> {health.message || "SAP HANA Cloud is not reachable."}
        {health.since ? <span className="ev-store-banner__since"> Since {new Date(health.since * 1000).toLocaleTimeString()}.</span> : null}
        {health.detail && <details className="ev-store-banner__detail"><summary>Technical detail</summary>{health.detail}</details>}
      </MessageStrip>
      <Button design="Transparent" icon="refresh" onClick={check}>Check again</Button>
    </div>
  );
  if (recovered) return (
    <div className="ev-store-banner">
      <MessageStrip design="Positive" onClose={dismiss}>
        <b>SAP HANA Cloud is back online.</b> Reload the page to see the latest data.
      </MessageStrip>
      <Button design="Transparent" icon="refresh" onClick={() => window.location.reload()}>Reload</Button>
    </div>
  );
  return null;
}
