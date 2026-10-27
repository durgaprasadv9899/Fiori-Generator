// Live previews of an adopted project: one "main" preview (a version or an AI change) and an optional "base"
// preview of the original version for a before / after comparison. Each preview remembers what it shows, so
// Restart restarts the same thing and the screen always says which version is on it.
import { useState } from "react";
import { Button, Icon, MessageStrip, SegmentedButton, SegmentedButtonItem } from "@ui5/webcomponents-react";
import { LiveTimeline, StatusBadge } from "../../components/ui";
import { DeviceFrame, type Device } from "../../components/designers/DeviceFrame";
import { api } from "../../api/client";
import type { AdoptProjectInfo, PreviewInfo, PreviewSource, VersionMeta } from "../../api/types";
import { useTrackedJob } from "./useTrackedJob";

export type PreviewBody = { version_id?: string; mission_id?: string };
type Slot = "main" | "base";

const ss = {
  get<T>(k: string, d: T): T { try { const v = sessionStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k: string, v: unknown) { try { if (v === null || v === undefined || v === "") sessionStorage.removeItem(k); else sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
};

/** State + start/stop of one preview slot of a project. */
export function usePreviewSlot(pid: string, slot: Slot) {
  const key = slot === "main" ? `evoke.adopt.preview.${pid}` : `evoke.adopt.preview-base.${pid}`;
  const pendKey = `${key}.pending`;
  const [preview, setState] = useState<PreviewInfo | null>(() => ss.get(key, null));
  const [err, setErr] = useState("");
  const [path, setPathState] = useState<string>("");
  const set = (p: PreviewInfo | null) => { setState(p); ss.set(key, p); if (!p) setPathState(""); };
  const job = useTrackedJob<PreviewInfo>(slot === "main" ? `adopt-preview.${pid}` : `adopt-preview-base.${pid}`, (r) => {
    const src = r.source || ss.get<PreviewSource | null>(pendKey, null) || undefined;
    ss.set(pendKey, null);
    setPathState("");
    set({ ...r, source: src });
    setErr("");
  }, (e) => { setErr(e); ss.set(pendKey, null); });

  const start = async (body: PreviewBody, source: PreviewSource) => {
    setErr("");
    ss.set(pendKey, source);
    try { await job.start("api/jobs/adopt-preview", { ...body, slot }); } catch (e: any) { setErr(e.message); }
  };
  const stop = async () => {
    if (preview) { try { await api.post(`api/adopt/preview/${preview.preview_id}/stop`, {}); } catch { /* already gone */ } }
    set(null);
    setErr("");
  };
  /** The URL the frame shows: the entry, or a page of the app the user picked. */
  const src = preview ? (path ? previewOrigin(preview.url) + path : preview.url) : "";
  return { preview, set, err, job, running: job.running, start, stop, src, path, setPath: setPathState };
}
export type PreviewSlot = ReturnType<typeof usePreviewSlot>;

export function previewOrigin(url: string): string {
  try { return new URL(url).origin; } catch { return ""; }
}

/** What a preview shows, in words, plus whether it is the latest. */
export function describeSource(src: PreviewSource | undefined, info: AdoptProjectInfo, versions: VersionMeta[]) {
  if (!src) return { title: "A preview started earlier", detail: "The version it shows is not known — restart to be sure.", tone: "warn" as const, latest: false };
  if (src.kind === "mission") {
    const m = info.missions.find((x) => x.id === src.id);
    const req = (m?.request || src.label || "").trim();
    const short = req.length > 90 ? req.slice(0, 90) + "…" : req;
    return m?.applied_version
      ? { title: `AI change — “${short}”`, detail: `Applied as version ${m.applied_version.number}.`, tone: "good" as const, latest: m.applied_version.id === info.version.id }
      : { title: `AI change — “${short}”`, detail: "Not applied yet — this is the changed code, before you save it.", tone: "brand" as const, latest: false };
  }
  const n = versions.find((v) => v.id === src.id)?.number ?? (src.id === info.version.id ? info.version.number : undefined);
  const isLatest = src.id === info.version.id;
  return {
    title: `Version ${n ?? "?"}`,
    detail: isLatest ? "The latest saved version." : `An older version — the latest is version ${info.version.number}.`,
    tone: isLatest ? "good" as const : "warn" as const, latest: isLatest,
  };
}

export function versionSource(info: AdoptProjectInfo, vid?: string, versions: VersionMeta[] = []): PreviewSource {
  const id = vid || info.version.id;
  const n = versions.find((v) => v.id === id)?.number ?? (id === info.version.id ? info.version.number : "?");
  return { kind: "version", id, label: `Version ${n}` };
}

/** The saved version just before `vid`, if any. */
export function previousVersion(versions: VersionMeta[], vid: string): VersionMeta | undefined {
  const cur = versions.find((v) => v.id === vid);
  if (!cur) return undefined;
  return versions.filter((v) => v.number < cur.number).sort((a, b) => b.number - a.number)[0];
}

export function bodyOf(src: PreviewSource): PreviewBody {
  return src.kind === "mission" ? { mission_id: src.id } : { version_id: src.id };
}

/** The strip that says which version is on screen. */
export function SourceStrip({ preview, info, versions, onLatest }: {
  preview: PreviewInfo; info: AdoptProjectInfo; versions: VersionMeta[]; onLatest: () => void;
}) {
  const d = describeSource(preview.source, info, versions);
  return (
    <div className={`ev-srcstrip ev-srcstrip--${d.tone}`} role="status">
      <span className="ev-srcstrip__icon"><Icon name={preview.source?.kind === "mission" ? "ai" : "history"} /></span>
      <div className="ev-srcstrip__txt"><span>Showing</span><b>{d.title}</b><small>{d.detail}</small></div>
      {!d.latest && <Button design="Transparent" icon="synchronize" onClick={onLatest}>Show latest version</Button>}
    </div>
  );
}

export function DeviceSwitch({ device, setDevice }: { device: Device; setDevice: (d: Device) => void }) {
  return (
    <SegmentedButton accessibleName="Device" onSelectionChange={(e) => setDevice(((e.detail.selectedItems[0] as any)?.dataset?.device || "desktop") as Device)}>
      {(["desktop", "tablet", "phone"] as Device[]).map((d) => (
        <SegmentedButtonItem key={d} data-device={d} selected={device === d} icon={d === "desktop" ? "laptop" : d === "tablet" ? "ipad" : "iphone"}
          tooltip={d}>{""}</SegmentedButtonItem>
      ))}
    </SegmentedButton>
  );
}

export function Warnings({ warnings }: { warnings?: string[] }) {
  if (!warnings?.length) return null;
  return (
    <MessageStrip design="Critical" hideCloseButton className="ev-pvwarn">
      The preview reported {warnings.length === 1 ? "a problem" : `${warnings.length} problems`}: {warnings.slice(0, 3).join(" · ")}
      {warnings.length > 3 ? " …" : ""}. Open the Log for details.
    </MessageStrip>
  );
}

export function StartingPanel({ messages, compact }: { messages: string[]; compact?: boolean }) {
  return (
    <div className={"ev-pvstart" + (compact ? " is-compact" : "")}>
      <div className="ev-pvstart__head"><span className="ev-pvstart__spin" /><div><b>Starting the application</b>
        <small>Installing dependencies, building and starting it in the sandbox — the first start takes longest.</small></div></div>
      <LiveTimeline messages={messages.length ? messages.slice(-8) : ["Starting…"]} running />
    </div>
  );
}

/** Before / after: the original version and the AI change side by side. */
export function CompareView({ base, main, versions, device, onClose }: {
  base: PreviewSlot; main: PreviewSlot; versions: VersionMeta[]; device: Device; onClose: () => void;
}) {
  const bsrc = base.preview?.source;
  const bn = bsrc ? (bsrc.number ?? versions.find((v) => v.id === bsrc.id)?.number) : undefined;
  const msrc = main.preview?.source;
  const mn = msrc?.kind === "version" ? (msrc.number ?? versions.find((v) => v.id === msrc.id)?.number) : undefined;
  const side = (label: string, tone: "neutral" | "brand", s: PreviewSlot) => (
    <div className="ev-cmp__side">
      <div className="ev-cmp__label"><StatusBadge tone={tone}>{label}</StatusBadge>
        {s.preview && <a className="ev-linkbtn" href={s.src} target="_blank" rel="noreferrer">Open ↗</a>}</div>
      {s.running ? <StartingPanel compact messages={s.job.job?.messages || []} /> :
        s.err ? <MessageStrip design="Negative" hideCloseButton>{s.err}</MessageStrip> :
          s.preview ? <DeviceFrame device={device} src={s.src} title={s.preview.entry} height={560} /> :
            <div className="ev-muted ev-small">Not running.</div>}
    </div>
  );
  return (
    <div className="ev-cmp">
      <div className="ev-cmp__head">
        <Icon name="compare" /><b>Before and after</b>
        <span className="ev-muted ev-small">Same pages, side by side — the left one as it was, the right one with the change.</span>
        <span className="ev-spacer" />
        <Button design="Transparent" icon="decline" onClick={onClose}>Close comparison</Button>
      </div>
      <div className="ev-cmp__grid">
        {side(bn ? `Before · version ${bn}` : "Before · original version", "neutral", base)}
        {side(msrc?.kind === "version" ? `After · version ${mn ?? "?"}` : "After · AI change", "brand", main)}
      </div>
    </div>
  );
}
