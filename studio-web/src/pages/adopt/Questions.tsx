// "Fill the gaps" questions — one renderer for the wizard (step 3) and the project's Brief tab.
import React, { useState } from "react";
import { Button, Icon, Input, Option, Select } from "@ui5/webcomponents-react";
import type { Answers, Question } from "../../api/types";
import "./adopt.css";

export function defaultAnswers(questions: Question[]): Answers {
  return Object.fromEntries(questions.map((q) => [q.id, q.value ?? (q.kind === "checks" ? [] : "")]));
}

const TITLES: Record<string, string> = {
  purpose: "What does this app do?", entry: "Start app", preview_data: "Preview runs with", env: "Settings and their sources",
  commands: "Build and test commands", run: "Run commands", protected: "Never changed by AI", rules: "Team rules",
  deploy_target: "Deploy target",
};

/** Questions for a stored brief when the server sends no question definitions: one field per answer. */
export function questionsFromBrief(brief: Answers): Question[] {
  return Object.entries(brief).map(([id, value]) => ({
    id, value, why: "",
    title: TITLES[id] || (id.startsWith("dest:") ? `Backend connection ${id.slice(5)}` : id.replace(/_/g, " ")),
    kind: Array.isArray(value) ? "checks" : ["commands", "run", "env"].includes(id) ? "code"
      : String(value).length > 80 || ["purpose", "rules"].includes(id) ? "textarea" : "text",
    options: Array.isArray(value) ? value.map((v) => ({ value: v, label: v, checked: true })) : undefined,
  }));
}

export function QuestionList({ questions, answers, onChange, disabled }: {
  questions: Question[]; answers: Answers; onChange: (a: Answers) => void; disabled?: boolean;
}) {
  const set = (id: string, v: string | string[]) => onChange({ ...answers, [id]: v });
  return (
    <div className="ev-form">
      {questions.map((q) => (
        <FormRow key={q.id} label={q.title} hint={q.why} guess={q.guess}>
          <QuestionInput q={q} value={answers[q.id]} onChange={(v) => set(q.id, v)} disabled={disabled} />
        </FormRow>
      ))}
    </div>
  );
}

/** One aligned form row: label (+ one-line hint) on the left, the field on the right. */
export function FormRow({ label, hint, guess, htmlFor, children }: {
  label: string; hint?: string; guess?: string; htmlFor?: string; children: React.ReactNode;
}) {
  return (
    <div className="ev-frow">
      <div className="ev-frow__l">
        {htmlFor ? <label htmlFor={htmlFor}><b>{label}</b></label> : <b>{label}</b>}
        {hint && <small>{hint}</small>}
        {guess && <span className="ev-q__guess"><Icon name="ai" />{guess}</span>}
      </div>
      <div className="ev-frow__v">{children}</div>
    </div>
  );
}

/** One aligned fact row: label on the left, value on the right. */
export function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="ev-fact"><span>{label}</span><div>{children}</div></div>;
}

function QuestionInput({ q, value, onChange, disabled }: { q: Question; value: any; onChange: (v: any) => void; disabled?: boolean }) {
  const id = `q-${q.id.replace(/[^\w-]/g, "_")}`;
  switch (q.kind) {
    case "textarea":
    case "code":
      return <textarea id={id} aria-label={q.title} className={"ev-textarea" + (q.kind === "code" ? " is-code" : "")} disabled={disabled}
        value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} spellCheck={q.kind !== "code"}
        rows={Math.min(12, Math.max(3, String(value ?? "").split("\n").length + 1))} />;
    case "select":
      return (
        <Select id={id} disabled={disabled} onChange={(e) => onChange((e.detail.selectedOption as any).value)}>
          {(q.options || []).map((o) => <Option key={o.value} value={o.value} selected={o.value === value}>{o.label}</Option>)}
        </Select>
      );
    case "radio":
      return (
        <div className="ev-radios" role="radiogroup" aria-label={q.title}>
          {(q.options || []).map((o) => (
            <label key={o.value} className={"ev-radio" + (o.value === value ? " is-on" : "")}>
              <input type="radio" name={id} checked={o.value === value} disabled={disabled} onChange={() => onChange(o.value)} />
              <span>{o.label}{o.hint && <small>{o.hint}</small>}</span>
            </label>
          ))}
        </div>
      );
    case "checks":
      return <Checks q={q} value={Array.isArray(value) ? value : []} onChange={onChange} disabled={disabled} />;
    default:
      return <Input id={id} disabled={disabled} value={String(value ?? "")} onInput={(e) => onChange(e.target.value)} />;
  }
}

function Checks({ q, value, onChange, disabled }: { q: Question; value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const [extra, setExtra] = useState("");
  const opts = [...(q.options || [])];
  value.forEach((v) => { if (!opts.some((o) => o.value === v)) opts.push({ value: v, label: v, hint: "added by you" }); });
  const toggle = (v: string) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  const add = () => {
    const v = extra.trim();
    if (v && !value.includes(v)) onChange([...value, v]);
    setExtra("");
  };
  return (
    <div className="ev-checks">
      {opts.length === 0 && <span className="ev-muted ev-small">Nothing suggested — add paths below if needed.</span>}
      {opts.map((o) => (
        <label key={o.value} className="ev-check">
          <input type="checkbox" checked={value.includes(o.value)} disabled={disabled} onChange={() => toggle(o.value)} />
          <span className="ev-mono">{o.label}</span>{o.hint && <small>· {o.hint}</small>}
        </label>
      ))}
      <div className="ev-row" style={{ marginTop: ".3rem" }}>
        <Input placeholder="Add a path or pattern, e.g. srv/legacy/**" value={extra} disabled={disabled}
          onInput={(e) => setExtra(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") add(); }} style={{ flex: 1, minWidth: 200 }} />
        <Button icon="add" design="Transparent" disabled={disabled || !extra.trim()} onClick={add}>Add</Button>
      </div>
    </div>
  );
}
