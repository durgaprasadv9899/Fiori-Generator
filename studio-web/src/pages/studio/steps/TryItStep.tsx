// Try it: the designed app as a clickable prototype with mock data, in a device frame.
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Icon } from "@ui5/webcomponents-react";
import { api } from "../../../api/client";
import { DeviceFrame, type Device } from "../../../components/designers/DeviceFrame";
import { Skeleton } from "../../../components/ui";
import { draftTitle, sourceOf, useDraft } from "../draft";
import { Choice, StepIntro } from "./common";

export function TryItStep() {
  const d = useDraft();
  const nav = useNavigate();
  const [device, setDevice] = useState<Device>("desktop");
  const [html, setHtml] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(() => {
    if (!d.design) return;
    setLoading(true); setErr("");
    api.text("api/preview", { blueprint: d.design.blueprint, source: sourceOf(d) })
      .then(setHtml).catch((e) => setErr(e.message)).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d.design]);
  useEffect(load, [load]);

  const openWindow = () => {
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    window.open(url, "_blank", "noopener");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };

  return (
    <div className="ev-step-page">
      <StepIntro icon="play" title="Try the app before it exists"
        text="Click through the designed screens with sample data: navigate, search, filter, open records, edit, create and run actions — approval actions change the status like the real process would."
        aside={<Choice value={device} onChange={setDevice} options={[
          { key: "desktop", label: "Desktop", icon: "laptop" }, { key: "tablet", label: "Tablet", icon: "ipad" }, { key: "phone", label: "Phone", icon: "iphone" }]} />} />

      <div className="ev-mockbar">
        <span className="ev-mockbar__tag"><Icon name="lab" /> MOCK DATA</span>
        <span>{d.mode === "standalone"
          ? "Sample records from the generated backend's seed data, kept in your browser. Nothing is saved — the deployed app uses its own CAP service and database."
          : "Generated sample records — nothing is read from or sent to your SAP system. The deployed app reads the live service."}</span>
        <span className="ev-tabs__spacer" />
        <Button design="Transparent" icon="refresh" onClick={load} disabled={loading}>Reset data</Button>
        <Button design="Transparent" icon="action" onClick={openWindow} disabled={!html}>Open in new window</Button>
      </div>

      {err ? (
        <div className="ev-placeholder ev-placeholder--err"><Icon name="error" /><span>The preview could not be built: {err}</span><Button onClick={load}>Retry</Button></div>
      ) : loading && !html ? (
        <div className="ev-preview-skel"><Skeleton height={36} /><Skeleton height={420} /></div>
      ) : (
        <DeviceFrame device={device} html={html} title={draftTitle(d)} />
      )}

      <div className="ev-cta-row">
        <Button design="Transparent" icon="edit" onClick={() => nav("/studio/screens")}>Change the design</Button>
        <Button design="Emphasized" endIcon="navigation-right-arrow" onClick={() => nav("/studio/generate")}>Generate the application</Button>
      </div>
    </div>
  );
}
