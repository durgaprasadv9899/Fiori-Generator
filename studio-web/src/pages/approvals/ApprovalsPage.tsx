// Approvals inbox: Pending / Decided / All; decide with a comment (reject requires one).
import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button, Dialog, Icon, MessageStrip, TextArea, Bar } from "@ui5/webcomponents-react";
import { EmptyState, PageHeader, Skeleton, StatusBadge, ago } from "../../components/ui";
import { useApi, fmtDate } from "../../components/shell/useApi";
import { Tabs } from "../../components/shell/Tabs";
import { statusTone } from "../../components/shell/labels";
import { useToast } from "../../components/shell/Toasts";
import { api } from "../../api/client";
import "./approvals.css";

interface Appr {
  id: string; project_id: string; project_name?: string; version_id: string; version_number?: number; env: string; requester: string;
  note?: string; status: string; requested_at?: number; decided_by?: string; decided_at?: number; comment?: string; can_decide?: boolean;
  validation?: { summary: { passed: number; failed: number; not_tested: number; warnings: number }; blocking: string[] } | null;
}

export default function ApprovalsPage() {
  const nav = useNavigate();
  const toast = useToast();
  const [tab, setTab] = useState("pending");
  const { data, error, loading, reload } = useApi<{ approvals: Appr[] }>("api/approvals?status=all", { poll: 30000 });
  const [decide, setDecide] = useState<{ a: Appr; approve: boolean } | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  const all = data?.approvals || [];
  const lists = useMemo(() => ({
    pending: all.filter((a) => a.status === "pending"),
    decided: all.filter((a) => a.status === "approved" || a.status === "rejected"),
    all,
  }), [all]);
  const shown = (lists as any)[tab] as Appr[];
  const mine = lists.pending.filter((a) => a.can_decide).length;

  const submit = async () => {
    if (!decide) return;
    if (!decide.approve && !comment.trim()) { toast("Please explain why the release is rejected.", "error"); return; }
    setBusy(true);
    try {
      await api.post(`api/approvals/${decide.a.id}/decide`, { approve: decide.approve, comment });
      toast(decide.approve ? `Approved ${decide.a.project_name} v${decide.a.version_number} for ${decide.a.env}` : "Release rejected");
      setDecide(null); setComment(""); reload(true);
    } catch (e: any) { toast(e.message, "error"); } finally { setBusy(false); }
  };

  return (
    <div>
      <PageHeader kicker="Governance" title="Release approvals"
        subtitle="Production releases need an approval from a reviewer who neither requested the release nor authored the version."
        actions={<Button icon="refresh" design="Transparent" onClick={() => reload()}>Refresh</Button>} />
      <Tabs id="appr" value={tab} onChange={setTab} tabs={[
        { key: "pending", text: "Pending", icon: "pending", count: lists.pending.length },
        { key: "decided", text: "Decided", icon: "accept", count: lists.decided.length },
        { key: "all", text: "All", icon: "list", count: all.length },
      ]} />
      <div className="ev-tabbody">
        {error && <MessageStrip design="Negative" hideCloseButton>{error}</MessageStrip>}
        {tab === "pending" && mine > 0 && (
          <motion.div className="ev-appr-banner" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}>
            <Icon name="approvals" /> {mine} release{mine > 1 ? "s are" : " is"} waiting for your decision.
          </motion.div>
        )}
        {loading && !data ? <div className="ev-appr-grid">{[0, 1, 2].map((i) => <Skeleton key={i} height={190} radius={16} />)}</div> :
          shown.length === 0 ? (
            <EmptyState icon={tab === "pending" ? "approvals" : "inbox"} title={tab === "pending" ? "Nothing to approve" : "No approvals yet"}
              text={tab === "pending" ? "When someone requests a production release it appears here — with its validation results." : "Decided approvals are listed here for the audit trail."} />
          ) : (
            <div className="ev-appr-grid">
              <AnimatePresence>
                {shown.map((a, i) => (
                  <motion.div key={a.id} className={"ev-appr-card st-" + a.status} layout
                    initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.96 }} transition={{ delay: i * 0.04 }}>
                    <div className="ev-appr-card__top">
                      <div>
                        <div className="ev-appr-card__proj" onClick={() => nav(`/projects/${a.project_id}`)}>{a.project_name || a.project_id}</div>
                        <div className="ev-muted ev-small">Version {a.version_number ?? "?"} → <b>{a.env}</b></div>
                      </div>
                      <StatusBadge tone={statusTone(a.status)}>{a.status}</StatusBadge>
                    </div>
                    <div className="ev-appr-card__who">
                      <span className="ev-mini-av">{a.requester[0]?.toUpperCase()}</span>
                      <span><b>{a.requester}</b> requested <span title={fmtDate(a.requested_at)}>{ago(a.requested_at)}</span></span>
                    </div>
                    {a.note && <div className="ev-appr-card__note">“{a.note}”</div>}
                    <div className="ev-appr-card__val">
                      {a.validation ? (a.validation.blocking?.length ? <StatusBadge tone="bad" icon="error">validation blocked</StatusBadge> : <>
                        <StatusBadge tone="good" icon="shield">{a.validation.summary.passed} passed</StatusBadge>
                        {a.validation.summary.failed > 0 && <StatusBadge tone="bad">{a.validation.summary.failed} failed</StatusBadge>}
                        {a.validation.summary.not_tested > 0 && <StatusBadge>{a.validation.summary.not_tested} not tested</StatusBadge>}
                      </>) : <StatusBadge tone="warn" icon="alert">not validated</StatusBadge>}
                    </div>
                    {a.status !== "pending" && (
                      <div className="ev-appr-card__dec">{a.status === "approved" ? "Approved" : "Rejected"} by <b>{a.decided_by}</b> {ago(a.decided_at)}{a.comment && <> — “{a.comment}”</>}</div>
                    )}
                    <div className="ev-appr-card__acts">
                      <Button design="Transparent" icon="compare" onClick={() => nav(`/projects/${a.project_id}`)}>Review</Button>
                      {a.status === "pending" && a.can_decide && <>
                        <Button design="Negative" icon="decline" onClick={() => { setComment(""); setDecide({ a, approve: false }); }}>Reject</Button>
                        <Button design="Positive" icon="accept" onClick={() => { setComment(""); setDecide({ a, approve: true }); }}>Approve</Button>
                      </>}
                      {a.status === "pending" && !a.can_decide && <span className="ev-muted ev-small">You cannot decide your own request.</span>}
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          )}
      </div>

      <Dialog open={!!decide} onClose={() => setDecide(null)}
        headerText={decide ? `${decide.approve ? "Approve" : "Reject"} ${decide.a.project_name} v${decide.a.version_number} for ${decide.a.env}` : ""}
        footer={<Bar endContent={<>
          <Button design="Transparent" onClick={() => setDecide(null)}>Cancel</Button>
          <Button design={decide?.approve ? "Positive" : "Negative"} disabled={busy} onClick={submit}>{decide?.approve ? "Approve release" : "Reject release"}</Button>
        </>} />}>
        <div style={{ display: "grid", gap: ".6rem", minWidth: "min(460px, 80vw)" }}>
          {decide && !decide.approve && <MessageStrip design="Critical" hideCloseButton>A rejection needs a comment — the requester sees it.</MessageStrip>}
          <TextArea rows={4} placeholder={decide?.approve ? "Optional comment" : "Why is this release rejected?"} value={comment} onInput={(e) => setComment(e.target.value)} />
        </div>
      </Dialog>
    </div>
  );
}
