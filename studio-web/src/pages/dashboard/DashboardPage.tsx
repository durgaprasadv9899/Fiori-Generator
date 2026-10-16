// Dashboard: simple colourful header + quick actions + quick actions, KPIs, activity, charts, recent projects, running jobs, onboarding.
import { motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button, Icon, MessageStrip, BusyIndicator } from "@ui5/webcomponents-react";
import { Card, EmptyState, KpiTile, Skeleton, StatusBadge, ago } from "../../components/ui";
import { BarChart, Donut } from "../../components/shell/charts";
import { useApi } from "../../components/shell/useApi";
import { actionInfo, MODE_INFO } from "../../components/shell/labels";
import { useMe } from "../../app/MeProvider";
import { useJobs } from "../../jobs/JobProvider";
import type { Project } from "../../api/types";
import { ImportDialog } from "../../components/shell/ImportDialog";
import { useState } from "react";
import "./dashboard.css";

interface Dash {
  kpis: { projects: number; versions: number; deployments: number; deploy_success_rate: number | null;
    validated_versions: number; pending_approvals: number; apps_live?: number };
  activity: { at: number; user: string; action: string; project_id: string; project_name?: string; detail?: any }[];
  recent_projects: Project[];
  running_jobs: number;
  by_mode: Record<string, number>;
  versions_per_day: { day: string; count: number }[];
}


export default function DashboardPage() {
  const canCreate = useMe().can("project.create");
  const nav = useNavigate();
  const { me } = useMe();
  const { jobs } = useJobs();
  const { data, error, loading, reload } = useApi<Dash>("api/dashboard", { poll: 30000 });
  const k = data?.kpis;
  const running = jobs.filter((j) => j.status === "running");
  const isNew = !!data && (k?.projects || 0) === 0;
  const [importing, setImporting] = useState(false);
  const [actFilter, setActFilter] = useState("all");
  const [actMore, setActMore] = useState(false);
  const acts = (data?.activity || []).filter((a) => actFilter === "all" || (ACT_FILTERS[actFilter] || /./).test(a.action));

  const days = (data?.versions_per_day || []).map((d) => ({
    label: new Date(d.day + "T00:00:00").toLocaleDateString(undefined, { day: "2-digit", month: "short" }), value: d.count }));
  const modes = Object.entries(data?.by_mode || {}).filter(([, v]) => v > 0)
    .map(([m, v]) => ({ label: MODE_INFO[m]?.label || m, value: v }));

  return (
    <div className="ev-dash">
      <motion.section className="ev-hero3 v-blue" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
        <span className="ev-shard s1" /><span className="ev-shard s2" /><span className="ev-shard s3" /><span className="ev-shard s4" /><span className="ev-hero3__shine" />
        <div className="ev-hero3__text">
          <div className="ev-hero3__eyebrow">Evoke Fiori App Studio</div>
          <h1>Create SAP Fiori applications with <span className="ev-ai-word">AI<Icon name="ai" className="ev-ai-word__spark" /></span></h1>
          <p>Describe the business requirement — the studio designs, builds, validates and deploys the application on SAP BTP.</p>
        </div>
        {canCreate && (
          <div className="ev-hero3__actions">
            <button className="ev-hbtn is-primary" onClick={() => nav("/studio")}><Icon name="add-document" />New application</button>
            <button className="ev-hbtn" onClick={() => nav("/studio?mode=sap")}><Icon name="connected" />Connect SAP service</button>
            <button className="ev-hbtn" onClick={() => setImporting(true)}><Icon name="upload" />Import project</button>
          </div>
        )}
      </motion.section>
      <ImportDialog open={importing} onClose={() => setImporting(false)} />

      <div className="ev-content">
        {error && (
          <MessageStrip design="Negative" className="ev-mb" onClose={() => reload()}>
            Dashboard data could not be loaded: {error}
          </MessageStrip>
        )}

        <div className="ev-kpis">
          {loading && !data ? Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} height={104} radius={14} />) : (
            <>
              <KpiTile label="Projects" value={k?.projects || 0} icon="folder-blank" hint="apps in the workspace" onClick={() => nav("/projects")} delay={0} />
              <KpiTile label="Versions" value={k?.versions || 0} icon="save" hint="generated and saved" delay={0.05} />
              <KpiTile label="Validated" value={k?.validated_versions || 0} icon="shield" tone="good" hint="versions with a validation run" delay={0.1} />
              <KpiTile label="Deployments" value={k?.deployments || 0} icon="paper-plane" tone="neutral" hint={`${k?.apps_live ?? 0} app(s) live`} delay={0.15} />
              <KpiTile label="Deploy success" value={k?.deploy_success_rate ?? 0} suffix="%" icon="status-positive"
                tone={(k?.deploy_success_rate ?? 100) >= 80 ? "good" : "warn"} hint={k?.deploy_success_rate == null ? "no deployments yet" : "of all deployments"} delay={0.2} />
              <KpiTile label="Pending approvals" value={k?.pending_approvals || 0} icon="approvals" tone={(k?.pending_approvals || 0) > 0 ? "warn" : "neutral"}
                hint="production releases waiting" onClick={() => nav("/approvals")} delay={0.25} />
            </>
          )}
        </div>

        {isNew && <Onboarding />}

        <div className="ev-dash__cols">
          <div className="ev-col">
            <Card title="Activity" subtitle="Latest events in your workspace" icon="activity-2" className="ev-dash__activity" delay={0.1}
              actions={<select className="ev-actsel" value={actFilter} onChange={(e) => { setActFilter(e.target.value); setActMore(false); }}>
                <option value="all">All activity</option><option value="build">Generated &amp; saved</option>
                <option value="deploy">Deployments</option><option value="approval">Approvals</option>
                <option value="validate">Validations</option><option value="project">Project changes</option>
                <option value="access">Users &amp; sign-ins</option></select>}>
              {loading && !data ? <SkelRows n={5} /> : acts.length === 0 ? (
                <EmptyState icon="activity-2" title={actFilter === "all" ? "No activity yet" : "Nothing in this category"} text="Generations, validations, approvals and deployments are listed here." />
              ) : (
                <>
                <motion.div className="ev-feed is-compact" layout>
                  {acts.slice(0, actMore ? 20 : 5).map((a, i) => {
                    const info = actionInfo(a.action);
                    return (
                      <motion.div key={`${a.at}-${i}`} className="ev-feed__item" layout initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: i < 5 ? 0.03 * i : 0 }} onClick={() => a.project_id && nav(`/projects/${a.project_id}`)}>
                        <span className={`ev-feed__icon tone-${info.tone}`}><Icon name={info.icon} /></span>
                        <div className="ev-feed__body">
                          <div className="ev-feed__line"><b>{shortUser(a.user)}</b> {info.text}{a.project_name && <> · <span className="ev-feed__proj">{a.project_name}</span></>}</div>
                        </div>
                        <span className="ev-feed__when" title={new Date(a.at * 1000).toLocaleString()}>{ago(a.at)}</span>
                      </motion.div>
                    );
                  })}
                </motion.div>
                {acts.length > 5 && (
                  <button className="ev-feed__more" onClick={() => setActMore(!actMore)}>
                    <Icon name={actMore ? "slim-arrow-up" : "slim-arrow-down"} />
                    {actMore ? "Show less" : `Show ${Math.min(acts.length, 20) - 5} more`}
                  </button>
                )}
                </>
              )}
            </Card>
            <Card title="Recent projects" icon="folder-blank" delay={0.2} className="ev-col__fill"
              actions={<Button design="Transparent" endIcon="navigation-right-arrow" onClick={() => nav("/projects")}>All projects</Button>}>
              {loading && !data ? <SkelRows n={3} /> : (data?.recent_projects || []).length === 0 ? (
                <EmptyState icon="folder-blank" title="No projects yet" text="Your first generated application appears here."
                  action={<Button design="Emphasized" icon="add-document" onClick={() => nav("/studio")}>Create an application</Button>} />
              ) : (
                <div className="ev-recent">
                  {(data?.recent_projects || []).slice(0, 6).map((p, i) => {
                    const m = MODE_INFO[p.mode] || MODE_INFO.import;
                    return (
                      <motion.div key={p.id} className="ev-recent__item" onClick={() => nav(`/projects/${p.id}`)}
                        initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.04 * i }} whileHover={{ y: -3 }}>
                        <div className="ev-recent__icon"><Icon name={m.icon} /></div>
                        <div className="ev-recent__name">{p.name}</div>
                        <div className="ev-recent__meta"><StatusBadge tone={m.tone}>{m.label}</StatusBadge><span>v{p.latest_version || 0}</span></div>
                        <div className="ev-muted ev-small">updated {ago(p.updated_at)}</div>
                      </motion.div>
                    );
                  })}
                </div>
              )}
            </Card>
          </div>
          <div className="ev-col">
            <Card title="Versions per day" subtitle="Last 14 days" icon="bar-chart" delay={0.15}>
              {loading && !data ? <Skeleton height={170} /> : <BarChart data={days.length ? days : emptyDays()} />}
            </Card>
            <Card title="Apps by type" icon="donut-chart" delay={0.2}>
              {modes.length ? <Donut data={modes} /> : <div className="ev-muted ev-small">No apps yet.</div>}
            </Card>
            <Card title="Running jobs" icon="activity-2" delay={0.25} className="ev-col__fill">
              {running.length === 0 ? (
                <div className="ev-idle"><Icon name="sys-enter-2" /> All quiet — AI designs, generations, validations and deployments show their live progress here.</div>
              ) : running.map((j) => (
                <div key={j.id} className="ev-runjob">
                  <BusyIndicator active size="S" />
                  <div className="ev-runjob__body">
                    <div className="ev-runjob__t">{j.label}<span>{Math.round(j.seconds)}s</span></div>
                    <div className="ev-muted ev-small">{j.messages[j.messages.length - 1] || "Working…"}</div>
                    <div className="ev-job__bar"><span /></div>
                  </div>
                </div>
              ))}
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

function detailText(d: any): string {
  if (!d || typeof d !== "object") return "";
  if (d.version) return ` · version ${d.version}`;
  if (d.env) return ` · ${d.env}`;
  return "";
}

function emptyDays() {
  return Array.from({ length: 14 }).map((_, i) => {
    const dt = new Date(Date.now() - (13 - i) * 86400000);
    return { label: dt.toLocaleDateString(undefined, { day: "2-digit", month: "short" }), value: 0 };
  });
}

function SkelRows({ n }: { n: number }) {
  return <div style={{ display: "grid", gap: ".7rem" }}>{Array.from({ length: n }).map((_, i) => <Skeleton key={i} height={34} />)}</div>;
}

function Onboarding() {
  const nav = useNavigate();
  const steps = [
    { icon: "add-document", t: "Create your first application", s: "Describe it in plain language — the AI designs the data model, process and screens.", to: "/studio" },
    { icon: "shield", t: "Validate it", s: "Build and run the generated project in a sandbox: UI5 build, CAP backend, browser smoke test.", to: "/projects" },
    { icon: "paper-plane", t: "Deploy to SAP BTP", s: "Release to development; production needs an approved, validated version.", to: "/projects" },
  ];
  return (
    <Card title="Get started in three steps" subtitle="Welcome to the Evoke Fiori App Studio" icon="lead" className="ev-onb" delay={0.05}>
      <div className="ev-onb__steps">
        {steps.map((s, i) => (
          <motion.div key={s.t} className="ev-onb__step" onClick={() => nav(s.to)} whileHover={{ y: -3 }}
            initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + i * 0.08 }}>
            <span className="ev-onb__num">{i + 1}</span>
            <Icon name={s.icon} className="ev-onb__icon" />
            <div className="ev-onb__t">{s.t}</div>
            <div className="ev-muted ev-small">{s.s}</div>
          </motion.div>
        ))}
      </div>
    </Card>
  );
}



const ACT_FILTERS: Record<string, RegExp> = {
  build: /^(app\.generate|version\.save)/, deploy: /^deploy\./, approval: /^approval\./, validate: /validat/,
  project: /^(project\.|member\.|version\.(delete|restore))/, access: /^(sign-in|user\.)/,
};
function shortUser(u: string): string {
  return (u || "").split("@")[0];
}
