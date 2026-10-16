// Connect (SAP apps): BTP destination → service catalog → metadata, or pasted $metadata;
// several services per app; entity explorer; how the deployed app reaches the backend.
import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import {
  Button, CheckBox, Icon, Input, MessageStrip, Option, Select, TextArea,
} from "@ui5/webcomponents-react";
import { api } from "../../../api/client";
import { Skeleton, StatusBadge } from "../../../components/ui";
import { draftStore, toastBus, useDraft, type SapService, type ServiceSummary } from "../draft";
import { Choice, StepIntro } from "./common";

interface Dest { name: string; url: string; proxy_type: string; authentication: string; reachable: boolean; sap_client?: string }
interface Svc { path: string; name: string; title: string; odata_version: string }

export function ConnectStep() {
  const d = useDraft();
  const nav = useNavigate();
  const [tab, setTab] = useState<"btp" | "paste">("btp");
  const [runtime, setRuntime] = useState<any>(null);
  const [compat, setCompat] = useState<{ ok: boolean; error?: string } | null>(null);
  const [explore, setExplore] = useState(0);

  useEffect(() => { api.get<any>("api/sap/runtime").then(setRuntime).catch(() => setRuntime({ env: "unknown", description: "" })); }, []);
  useEffect(() => {
    if (d.services.length < 2) { setCompat(null); return; }
    api.post<any>("api/sap/check-services", { services: d.services.map(({ edmx, service_url, title }) => ({ edmx, service_url, title })) })
      .then((r) => setCompat({ ok: !!r.ok, error: r.error })).catch((e) => setCompat({ ok: false, error: e.message }));
  }, [d.services]);

  const addService = (s: SapService, dest?: Dest) => {
    if (d.services.some((x) => x.service_url.replace(/\/+$/, "") === s.service_url.replace(/\/+$/, ""))) {
      toastBus.push("This service is already part of the app.", "info");
      return;
    }
    const first = d.services.length === 0;
    draftStore.update((x) => ({
      services: [...x.services, s], design: null, generated: null, validation: null,
      destination: first && dest ? { use_existing: true, name: dest.name, on_premise: dest.proxy_type.toLowerCase() === "onpremise", auth_type: "NoAuthentication" } : x.destination,
    }));
    toastBus.push(`${s.title} loaded — ${s.summary?.entity_sets.length ?? "?"} entity sets`, "good");
  };
  const remove = (i: number) => draftStore.update((x) => ({ services: x.services.filter((_, j) => j !== i), design: null, generated: null, validation: null }));

  return (
    <div className="ev-step-page">
      <StepIntro icon="connected" title="Connect your SAP service"
        text="The AI designs only with entity sets, fields and actions that really exist in the service, so the generated app works against your backend from the first run. Nothing is written to the backend at design time." />

      {runtime && (
        <div className="ev-runtime">
          <Icon name={runtime.env === "connectivity" ? "cloud" : runtime.env === "bas" ? "it-host" : "laptop"} />
          <span>{runtime.description || "Runtime unknown"}</span>
          {runtime.cf_target && <StatusBadge tone="info">{runtime.cf_target.space}</StatusBadge>}
        </div>
      )}

      <div className="ev-connect">
        <div className="ev-connect__main">
          <Choice value={tab} onChange={setTab} options={[{ key: "btp", label: "BTP destination", icon: "chain-link" }, { key: "paste", label: "Paste $metadata", icon: "paste" }]} />
          <AnimatePresence mode="wait">
            <motion.div key={tab} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
              {tab === "btp" ? <BtpBrowser onLoaded={addService} /> : <PasteMetadata onLoaded={addService} />}
            </motion.div>
          </AnimatePresence>
        </div>

        <aside className="ev-connect__side">
          <div className="ev-panel">
            <div className="ev-panel__title"><Icon name="chain-link" /> Services in this app <span className="ev-muted">{d.services.length}</span></div>
            {d.services.length === 0 ? (
              <div className="ev-muted ev-pad">No service yet. Load one from a destination or paste its $metadata.</div>
            ) : (
              <div className="ev-svclist">
                <AnimatePresence initial={false}>
                  {d.services.map((s, i) => (
                    <motion.div key={s.service_url} className={"ev-svc" + (explore === i ? " is-on" : "")} layout initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
                      onClick={() => setExplore(i)}>
                      <span className="ev-svc__icon"><Icon name="connected" /></span>
                      <div className="ev-svc__text">
                        <b>{s.title}</b>
                        <code>{s.service_url}</code>
                        <span className="ev-svc__badges">
                          {i === 0 ? <StatusBadge tone="brand">main</StatusBadge> : <StatusBadge tone="neutral">additional</StatusBadge>}
                          {s.summary && <StatusBadge tone="info">OData V{s.summary.odata_version[0]}</StatusBadge>}
                          {s.summary && <StatusBadge tone="neutral">{s.summary.entity_sets.length} sets</StatusBadge>}
                        </span>
                      </div>
                      <Button design="Transparent" icon="decline" tooltip="Remove" onClick={(e) => { e.stopPropagation(); remove(i); }} />
                    </motion.div>
                  ))}
                </AnimatePresence>
                {compat && (compat.ok
                  ? <MessageStrip design="Positive" hideCloseButton>The services can be combined — one app with linked pages per service.</MessageStrip>
                  : <MessageStrip design="Negative" hideCloseButton>{compat.error || "These services cannot be combined."}</MessageStrip>)}
              </div>
            )}
          </div>
          <BackendConnection />
        </aside>
      </div>

      {d.services[explore] && <EntityExplorer service={d.services[explore]} />}

      <div className="ev-cta-row">
        <Button design="Emphasized" endIcon="navigation-right-arrow" disabled={!d.services.length || (compat !== null && !compat.ok)} onClick={() => nav("/studio/describe")}>
          Describe the app
        </Button>
      </div>
    </div>
  );
}

function BtpBrowser({ onLoaded }: { onLoaded: (s: SapService, dest: Dest) => void }) {
  const [dests, setDests] = useState<Dest[] | null>(null);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [dest, setDest] = useState<Dest | null>(null);
  const [svcs, setSvcs] = useState<Svc[] | null>(null);
  const [warn, setWarn] = useState<string[]>([]);
  const [sq, setSq] = useState("");
  const [typed, setTyped] = useState("");
  const [loading, setLoading] = useState("");

  const loadDests = () => {
    setErr(""); setDests(null);
    api.get<any>("api/sap/destinations").then((r) => setDests(r.destinations || [])).catch((e) => { setErr(e.message); setDests([]); });
  };
  useEffect(loadDests, []);
  const pick = (dd: Dest) => {
    setDest(dd); setSvcs(null); setWarn([]); setSq("");
    if (!dd.reachable) return;
    setLoading("services");
    api.post<any>("api/sap/services", { destination: dd.name })
      .then((r) => { setSvcs(r.services || []); setWarn(r.warnings || []); })
      .catch((e) => { toastBus.push(e.message, "bad"); setSvcs([]); }).finally(() => setLoading(""));
  };
  const load = (path: string, title?: string) => {
    if (!dest) return;
    setLoading(path);
    api.post<any>("api/sap/metadata", { destination: dest.name, path, service_url: path })
      .then((r) => onLoaded({ edmx: r.edmx, service_url: r.service_url || path, title: title || path.replace(/\/+$/, "").split("/").pop() || path, summary: r.summary, destination: dest.name }, dest))
      .catch((e) => toastBus.push(`Metadata: ${e.message}`, "bad")).finally(() => setLoading(""));
  };

  const fd = (dests || []).filter((x) => !q || (x.name + x.url).toLowerCase().includes(q.toLowerCase()));
  const fs = (svcs || []).filter((x) => !sq || (x.name + x.title + x.path).toLowerCase().includes(sq.toLowerCase()));
  return (
    <div className="ev-btp">
      {err && <MessageStrip design="Critical" hideCloseButton className="ev-gap-bottom">{err} — you can still paste the $metadata.</MessageStrip>}
      <div className="ev-btp__cols">
        <div className="ev-btp__col">
          <div className="ev-btp__head">
            <b>1 · Backend system</b>
            <Button design="Transparent" icon="refresh" tooltip="Reload" onClick={loadDests} />
          </div>
          <Input className="ev-search" icon={<Icon name="search" />} placeholder="Search destinations" value={q} onInput={(e) => setQ((e.target as any).value)} />
          <div className="ev-destlist">
            {dests === null ? [0, 1, 2].map((i) => <Skeleton key={i} height={58} radius={12} />) : fd.length === 0 ? (
              <div className="ev-muted ev-pad">No HTTP destinations visible.</div>
            ) : fd.map((x, i) => (
              <motion.button key={x.name} className={"ev-dest" + (dest?.name === x.name ? " is-on" : "") + (x.reachable ? "" : " is-off")}
                onClick={() => pick(x)} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.03 }}>
                <span className="ev-dest__icon"><Icon name={x.proxy_type.toLowerCase() === "onpremise" ? "building" : "cloud"} /></span>
                <span className="ev-dest__text">
                  <b>{x.name}</b>
                  <span className="ev-dest__url">{x.url}{x.sap_client ? ` · client ${x.sap_client}` : ""}</span>
                  <span className="ev-svc__badges">
                    <StatusBadge tone={x.proxy_type.toLowerCase() === "onpremise" ? "warn" : "info"}>{x.proxy_type}</StatusBadge>
                    <StatusBadge tone="neutral">{x.authentication}</StatusBadge>
                    {!x.reachable && <StatusBadge tone="bad" icon="locked">not reachable here</StatusBadge>}
                  </span>
                </span>
              </motion.button>
            ))}
          </div>
        </div>
        <div className="ev-btp__col">
          <div className="ev-btp__head"><b>2 · OData service</b>{svcs && <span className="ev-muted">{svcs.length} published</span>}</div>
          {!dest ? <div className="ev-placeholder ev-placeholder--sm"><Icon name="arrow-left" /><span>Pick a backend system.</span></div>
            : !dest.reachable ? <MessageStrip design="Critical" hideCloseButton>On-premise destinations are reachable only from SAP BTP (connectivity service) or SAP Business Application Studio. Use “Paste $metadata” here.</MessageStrip>
            : loading === "services" ? <div className="ev-destlist">{[0, 1, 2, 3].map((i) => <Skeleton key={i} height={46} radius={10} />)}</div>
            : (
              <>
                <Input className="ev-search" icon={<Icon name="search" />} placeholder="Search services" value={sq} onInput={(e) => setSq((e.target as any).value)} />
                {warn.map((w, i) => <div key={i} className="ev-muted ev-small"><Icon name="message-warning" /> {w}</div>)}
                <div className="ev-destlist ev-destlist--svc">
                  {fs.slice(0, 200).map((s) => (
                    <button key={s.path} className="ev-svcrow" onClick={() => load(s.path, s.name)} disabled={!!loading}>
                      <span className="ev-svcrow__text"><b>{s.name}</b><span className="ev-muted">{s.title}</span></span>
                      <StatusBadge tone="info">V{(s.odata_version || "2")[0]}</StatusBadge>
                      {loading === s.path ? <span className="ev-jobbox__spinner" /> : <Icon name="add" />}
                    </button>
                  ))}
                </div>
                <div className="ev-typed">
                  <Input placeholder="…or a service path not in the catalog, e.g. /sap/opu/odata/sap/ZMY_SRV/" value={typed} onInput={(e) => setTyped((e.target as any).value)} />
                  <Button disabled={!typed.trim() || !!loading} onClick={() => load(typed.trim())}>Load</Button>
                </div>
              </>
            )}
        </div>
      </div>
    </div>
  );
}

function PasteMetadata({ onLoaded }: { onLoaded: (s: SapService) => void }) {
  const [path, setPath] = useState("");
  const [edmx, setEdmx] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const go = () => {
    setBusy(true); setErr("");
    api.post<any>("api/sap/metadata", { edmx, service_url: path })
      .then((r) => { onLoaded({ edmx: r.edmx || edmx, service_url: r.service_url || path, title: path.replace(/\/+$/, "").split("/").pop() || "Service", summary: r.summary }); setEdmx(""); })
      .catch((e) => setErr(e.message)).finally(() => setBusy(false));
  };
  return (
    <div className="ev-paste">
      <div className="ev-field">
        <label className="ev-label">Service path the deployed app will call</label>
        <Input value={path} placeholder="/sap/opu/odata/sap/API_SALES_ORDER_SRV/" onInput={(e) => setPath((e.target as any).value)} />
      </div>
      <div className="ev-field">
        <label className="ev-label">$metadata XML</label>
        <TextArea value={edmx} rows={10} placeholder={'<?xml version="1.0" encoding="utf-8"?><edmx:Edmx …'} onInput={(e) => setEdmx((e.target as any).value)} />
        <span className="ev-muted">Open &lt;service&gt;/$metadata in the browser, copy everything and paste it here.</span>
      </div>
      {err && <MessageStrip design="Negative" hideCloseButton>{err}</MessageStrip>}
      <div className="ev-actions"><Button design="Emphasized" icon="upload" disabled={!path.trim() || !edmx.trim() || busy} onClick={go}>{busy ? "Reading…" : "Load metadata"}</Button></div>
    </div>
  );
}

function BackendConnection() {
  const d = useDraft();
  const dst = d.destination;
  const set = (patch: Partial<typeof dst>) => draftStore.update((x) => ({ destination: { ...x.destination, ...patch }, generated: null }));
  return (
    <div className="ev-panel">
      <div className="ev-panel__title"><Icon name="cloud" /> Deployed app → backend</div>
      <CheckBox text="Use an existing BTP destination" checked={dst.use_existing} onChange={(e) => set({ use_existing: (e.target as any).checked })} />
      {dst.use_existing ? (
        <div className="ev-field-row">
          <Input value={dst.name} placeholder="EvokeSD4" onInput={(e) => set({ name: (e.target as any).value })} />
          <CheckBox text="On-premise (Cloud Connector)" checked={dst.on_premise} onChange={(e) => set({ on_premise: (e.target as any).checked })} />
        </div>
      ) : (
        <div className="ev-field">
          <label className="ev-label">Authentication of the new destination</label>
          <Select value={dst.auth_type} onChange={(e) => set({ auth_type: (e.target as any).value })}>
            {["NoAuthentication", "BasicAuthentication", "OAuth2ClientCredentials", "PrincipalPropagation"].map((a) => <Option key={a} value={a}>{a}</Option>)}
          </Select>
          <span className="ev-muted">Credentials are entered only in the deploy console and never stored.</span>
        </div>
      )}
    </div>
  );
}

function EntityExplorer({ service }: { service: SapService }) {
  const s: ServiceSummary | undefined = service.summary;
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const sets = useMemo(() => (s?.entity_sets || []).filter((e) => !q || (e.name + e.label).toLowerCase().includes(q.toLowerCase())), [s, q]);
  useEffect(() => setSel(0), [service.service_url]);
  if (!s) return null;
  const es = sets[sel];
  const ops = (s.operations || []).filter((o) => !es || !o.bound_type || o.bound_type === es.name || es.name.startsWith(o.bound_type));
  return (
    <div className="ev-explorer">
      <div className="ev-section-title"><Icon name="database" /> Entity explorer · {service.title}
        <span className="ev-muted">{s.entity_sets.length} entity sets · {s.operations.length} operations</span></div>
      <div className="ev-explorer__grid">
        <div className="ev-explorer__list">
          <Input className="ev-search" icon={<Icon name="search" />} placeholder="Search entity sets" value={q} onInput={(e) => { setQ((e.target as any).value); setSel(0); }} />
          <div className="ev-explorer__items">
            {sets.map((e, i) => (
              <button key={e.name} className={"ev-exitem" + (i === sel ? " is-on" : "")} onClick={() => setSel(i)}>
                <b>{e.label || e.name}</b><span className="ev-muted">{e.name} · {e.field_count} fields</span>
              </button>
            ))}
          </div>
        </div>
        {es && (
          <motion.div key={es.name} className="ev-explorer__detail" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
            <div className="ev-explorer__dhead">
              <h3>{es.label || es.name}</h3>
              <span className="ev-svc__badges">
                <StatusBadge tone={es.creatable ? "good" : "neutral"}>{es.creatable ? "create" : "no create"}</StatusBadge>
                <StatusBadge tone={es.updatable ? "good" : "neutral"}>{es.updatable ? "update" : "read-only"}</StatusBadge>
                <StatusBadge tone={es.deletable ? "good" : "neutral"}>{es.deletable ? "delete" : "no delete"}</StatusBadge>
                <StatusBadge tone="info" icon="key">{(es.keys || []).join(", ")}</StatusBadge>
              </span>
            </div>
            <div className="ev-table-wrap ev-table-wrap--scroll">
              <table className="ev-table">
                <thead><tr><th>Field</th><th>Name</th><th>Type</th></tr></thead>
                <tbody>{(es.fields || []).map((f) => (
                  <tr key={f.name}><td>{f.label}{es.keys?.includes(f.name) && <Icon name="key" className="ev-keyico" />}</td><td className="ev-mono">{f.name}</td><td className="ev-muted">{f.type.replace("Edm.", "")}</td></tr>
                ))}</tbody>
              </table>
            </div>
            {(es.navigations?.length > 0 || ops.length > 0) && (
              <div className="ev-explorer__extra">
                {es.navigations?.length > 0 && <div><b>Navigations</b><div className="ev-chips">{es.navigations.map((n) => <span key={n.name} className="ev-chip ev-chip--static"><Icon name={n.to_many ? "list" : "chain-link"} />{n.name} → {n.target}</span>)}</div></div>}
                {ops.length > 0 && <div><b>Operations</b><div className="ev-chips">{ops.slice(0, 20).map((o) => <span key={o.name} className="ev-chip ev-chip--static"><Icon name="action" />{o.label || o.name}</span>)}</div></div>}
              </div>
            )}
          </motion.div>
        )}
      </div>
    </div>
  );
}
