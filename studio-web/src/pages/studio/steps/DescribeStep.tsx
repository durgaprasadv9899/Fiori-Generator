// Describe: the requirement in plain language (+ clarifying questions for new apps).
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useNavigate } from "react-router-dom";
import { Button, Icon, Input, TextArea } from "@ui5/webcomponents-react";
import { api } from "../../../api/client";
import { useMe } from "../../../app/MeProvider";
import { draftStore, sourceOf, useDraft } from "../draft";
import { useStudioJobs } from "../flow";
import { JobError, JobProgress, StepIntro } from "./common";

const TIPS_NEW = ["Who uses it and why", "Main records and their fields", "Line items (header / items)", "Approval steps & roles", "Calculations (totals, days…)", "Rules (must / only when…)", "Pages: dashboard, board, calendar…"];
const TIPS_SAP = ["Which pages (list, board, calendar, dashboard)", "Calculations shown as columns", "Highlights (overdue in red…)", "Rules & defaults on create", "Actions (approve, release…)", "Languages"];

export function DescribeStep() {
  const d = useDraft();
  const nav = useNavigate();
  const { me } = useMe();
  const { start, jobOf, busy } = useStudioJobs();
  const [examples, setExamples] = useState<Record<string, string>>({});
  const [text, setText] = useState(d.requirement);
  const isNew = d.mode === "standalone";

  useEffect(() => {
    api.get<any>("api/examples").then((r) => setExamples((isNew ? r.new_app : r.sap || r.new_app) || {})).catch(() => undefined);
  }, [isNew]);
  useEffect(() => setText(d.requirement), [d.requirement]);
  const commit = (v: string) => draftStore.update({ requirement: v });

  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  const quality = words < 12 ? { t: "Add more detail", tone: "bad" } : words < 35 ? { t: "Good start", tone: "warn" } : { t: "Detailed brief", tone: "good" };

  const ask = () => {
    commit(text);
    start("questions", "api/jobs/questions", { requirement: text }, (r) => {
      draftStore.update({ questions: r.questions || [], answers: {} });
    });
  };
  const designModel = () => {
    commit(text);
    start("domain", "api/jobs/domain", { requirement: text, answers: d.answers }, (r) => {
      draftStore.update({ domain: r.domain, domainNotes: r.notes || [], domainSummary: r.summary || "", domainSource: r.source || "", design: null, generated: null, validation: null });
      // take the user to the result only if they are still in the studio
      if (window.location.hash.startsWith("#/studio")) window.location.hash = "#/studio/model";
    }, "Data model ready");
  };
  const designScreens = () => {
    commit(text);
    start("design", "api/jobs/design", { requirement: text, source: sourceOf({ ...d, requirement: text }), floorplan: d.floorplan },
      (r) => draftStore.update({ design: r, chat: [], generated: null, validation: null }), "Screens designed");
    nav("/studio/screens");
  };

  const qJob = jobOf("questions");
  const dJob = jobOf("domain");
  const setAnswer = (q: string, a: string) => draftStore.update((x) => ({ answers: { ...x.answers, [q]: a } }));

  return (
    <div className="ev-step-page">
      <StepIntro icon="write-new-document" title="Describe the application"
        text={isNew
          ? "Write it the way you would brief a developer. The AI turns it into a data model, a business process with approval steps, and screens. You review each part before anything is generated."
          : "Describe what users should see and do. The AI designs the screens only with entity sets, fields and actions that exist in the connected service."} />

      <div className="ev-describe">
        <div className="ev-describe__main">
          {Object.keys(examples).length > 0 && (
            <div className="ev-examples">
              <span className="ev-label">Start from an example</span>
              <div className="ev-chips">
                {Object.entries(examples).map(([k, v]) => (
                  <motion.button key={k} className="ev-chip" whileHover={{ y: -2 }} whileTap={{ scale: 0.97 }}
                    onClick={() => { setText(v); commit(v); }}>
                    <Icon name="lightbulb" />{k}
                  </motion.button>
                ))}
              </div>
            </div>
          )}
          <div className="ev-prompt">
            <TextArea className="ev-prompt__area" value={text} rows={9} growing growingMaxRows={18}
              placeholder={isNew ? "e.g. A purchase request application with multiple line items, manager approval with a reason on rejection, totals calculated from the items, and finance reporting by department and month."
                : "e.g. A sales order desk: a dashboard with open orders and net value by sales organisation, an order list with tabs per status that highlights overdue orders, release/reject approval for orders in approval…"}
              onInput={(e) => setText((e.target as any).value)} onChange={(e) => commit((e.target as any).value)} />
            <div className="ev-prompt__foot">
              <span className={`ev-quality ev-quality--${quality.tone}`}><span />{quality.t}</span>
              <span className="ev-prompt__count">{words} words · {text.length} characters</span>
            </div>
          </div>

          <div className="ev-actions">
            {isNew && (
              <Button icon="question-mark" disabled={!text.trim() || busy("questions") || !me?.ai.available} onClick={ask}
                tooltip={me?.ai.available ? "The AI asks about anything unclear" : "Needs an AI model"}>
                {busy("questions") ? "Reviewing…" : "Check the requirement"}
              </Button>
            )}
            <Button design="Emphasized" icon={isNew ? "org-chart" : "screen-split-three"} disabled={!text.trim() || busy("domain") || busy("design")}
              onClick={isNew ? designModel : designScreens}>
              {isNew ? (busy("domain") ? "Designing data model…" : "Design data model") : "Design screens"}
            </Button>
            {!me?.ai.available && <span className="ev-muted"><Icon name="message-warning" /> No AI model — the rule-based designer is used.</span>}
          </div>
          <JobProgress job={qJob} title="Reviewing the requirement" />
          <JobProgress job={dJob} title="Designing the data model and business process" />
          <JobError kind="questions" />
          <JobError kind="domain" />

          <AnimatePresence>
            {isNew && d.questions.length > 0 && (
              <motion.div className="ev-questions" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <div className="ev-questions__head"><Icon name="discussion-2" /><b>The AI needs a few details</b><span className="ev-muted">Optional — unanswered questions are decided by the AI.</span></div>
                {d.questions.map((q, i) => (
                  <motion.div key={q.id} className="ev-q" initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.06 }}>
                    <div className="ev-q__text"><span className="ev-q__num">{i + 1}</span>{q.question}</div>
                    {q.why && <div className="ev-q__why">{q.why}</div>}
                    {q.options.length > 0 && (
                      <div className="ev-chips">
                        {q.options.map((o) => (
                          <button key={o} className={"ev-chip ev-chip--opt" + (d.answers[q.question] === o ? " is-on" : "")}
                            onClick={() => setAnswer(q.question, d.answers[q.question] === o ? "" : o)}>
                            {d.answers[q.question] === o && <Icon name="accept" />}{o}
                          </button>
                        ))}
                      </div>
                    )}
                    <Input className="ev-q__input" placeholder={q.options.length ? "…or your own answer" : "Your answer"}
                      value={q.options.includes(d.answers[q.question]) ? "" : d.answers[q.question] || ""}
                      onInput={(e) => setAnswer(q.question, (e.target as any).value)} />
                  </motion.div>
                ))}
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <aside className="ev-describe__side">
          <div className="ev-tipcard">
            <div className="ev-tipcard__title"><Icon name="hint" /> What makes a good brief</div>
            <ul>{(isNew ? TIPS_NEW : TIPS_SAP).map((t) => <li key={t}><Icon name="accept" />{t}</li>)}</ul>
          </div>
          <div className="ev-tipcard ev-tipcard--soft">
            <div className="ev-tipcard__title"><Icon name="process" /> What happens next</div>
            <ol className="ev-next">
              {(isNew ? ["Data model & process are designed", "You review them visually", "AI designs the screens", "Try the app with sample data", "Generate, validate, deploy"]
                : ["AI designs the screens", "Coverage of every requirement", "Try the app with mock data", "Generate, validate, deploy"]).map((t) => <li key={t}>{t}</li>)}
            </ol>
          </div>
        </aside>
      </div>
    </div>
  );
}
