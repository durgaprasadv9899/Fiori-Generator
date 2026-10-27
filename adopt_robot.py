"""
adopt_robot.py — the project robot: an AI agent that understands a client's adopted project and
carries out change requests in it.

A request becomes a *mission*:

  plan      the robot explores the project with read-only tools, restates the request in its own
            words, asks clarifying questions only where the answer changes the outcome (each with a
            default), writes acceptance criteria and a step plan.
  run       with the user's answers it edits the project through tools (read, search, write, exact
            replace, delete, quick checks). Protected paths from the brief are refused. When it says
            it is done, the full project checks run in the runner; failures go back to the robot,
            which repairs them (up to MAX_REPAIR rounds). Finally it reports each acceptance criterion
            as met / not met with evidence.
  revise    the user's feedback continues the same change set.
  apply     the change set becomes a new project version (the caller does that).

The robot never sees secrets: the brief records where settings come from, never values.
"""

from __future__ import annotations

import difflib
import fnmatch
import json
import os
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Tuple

import adopt_files
import adopt_scan

MAX_STEPS_PLAN = 25
MAX_STEPS_RUN = 120
WRAP_UP = 8                      # steps before the limit at which the AI is told to wrap up
MAX_REPAIR = 2
READ_LINES = 400
NEVER_EDIT = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock")

_MISSIONS: Dict[str, Dict] = {}
_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Mission store (memory + JSON files in the studio data dir)
# ─────────────────────────────────────────────────────────────────────────────
def _dir() -> str:
    import workspace
    d = os.path.join(workspace._default_data_dir(), "missions")
    os.makedirs(d, exist_ok=True)
    return d


def save(m: Dict) -> Dict:
    m["updated_at"] = time.time()
    with _LOCK:
        _MISSIONS[m["id"]] = m
    try:
        with open(os.path.join(_dir(), m["id"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(m, f)
    except OSError:
        pass
    return m


def get(mid: str) -> Optional[Dict]:
    m = _MISSIONS.get(mid)
    if m is None and re.match(r"^[a-f0-9]{16}$", mid or ""):
        try:
            with open(os.path.join(_dir(), mid + ".json"), encoding="utf-8") as f:
                m = json.load(f)
            _MISSIONS[mid] = m
        except (OSError, ValueError):
            return None
    return m


def list_for(project_id: str) -> List[Dict]:
    try:
        for name in os.listdir(_dir()):
            if name.endswith(".json") and name[:-5] not in _MISSIONS:
                get(name[:-5])
    except OSError:
        pass
    ms = [m for m in _MISSIONS.values() if m.get("project_id") == project_id]
    return sorted(ms, key=lambda m: -m.get("created_at", 0))


def mark_interrupted() -> int:
    """At startup: missions that were working when the studio stopped can never finish — mark them failed so
    the user can run them again (plan and partial changes are kept)."""
    n = 0
    try:
        names = [x for x in os.listdir(_dir()) if x.endswith(".json")]
    except OSError:
        return 0
    for name in names:
        m = get(name[:-5])
        if m and m.get("status") in ("working", "understanding"):
            m["status"] = "failed"
            m["error"] = "Interrupted — the studio restarted while the AI assistant was working. Run it again; the plan is kept."
            save(m)
            n += 1
    return n


def summary(m: Dict) -> Dict:
    return {k: m.get(k) for k in ("id", "request", "status", "created_at", "updated_at", "author", "applied_version",
                                  "version_id")} | {"changes": len(m.get("changes") or [])}


def public(m: Dict) -> Dict:
    """The mission as the UI sees it (no internal working state)."""
    return {k: v for k, v in m.items() if not k.startswith("_")}


def new_mission(project_id: str, version_id: str, author: str, request: str) -> Dict:
    return save({"id": uuid.uuid4().hex[:16], "project_id": project_id, "version_id": version_id, "author": author,
                 "created_at": time.time(), "request": request.strip(), "status": "understanding", "restated": "",
                 "questions": [], "answers": {}, "criteria": [], "plan": [], "design": [], "changes": [], "diff": "", "summary": "",
                 "checks": None, "criteria_results": [], "steps": [], "rounds": 0, "feedback": [],
                 "_files": {}, "error": ""})


# ─────────────────────────────────────────────────────────────────────────────
# Workspace the robot edits (base files + change set)
# ─────────────────────────────────────────────────────────────────────────────
class Workspace:
    def __init__(self, base: Dict[str, str], changes: Optional[Dict[str, Optional[str]]] = None,
                 protected: Optional[List[str]] = None, terminal=None):
        self.base = base
        self.changes: Dict[str, Optional[str]] = dict(changes or {})
        self.protected = [p.strip() for p in (protected or []) if p and p.strip()]
        self.terminal = terminal          # runner session (run_command); None = no command execution
        self.commands_run = 0
        self.generated: List[str] = []    # committed build-output folders (rebuilt by the studio, never edited by AI)

    def files(self) -> Dict[str, str]:
        out = dict(self.base)
        for p, c in self.changes.items():
            if c is None:
                out.pop(p, None)
            else:
                out[p] = c
        return out

    def read(self, path: str) -> Optional[str]:
        if path in self.changes:
            return self.changes[path]
        return self.base.get(path)

    def is_protected(self, path: str) -> Optional[str]:
        name = path.split("/")[-1]
        if name in NEVER_EDIT:
            return f"{name} is generated by the package manager — change package.json instead."
        for d in self.generated:
            if path.startswith(d + "/"):
                return (f"{d}/ is the committed BUILD OUTPUT of the frontend — change the frontend sources instead; "
                        "the studio rebuilds this folder after your change.")
        for pat in self.protected:
            folder = pat[:-3] if pat.endswith("/**") else pat[:-1] if pat.endswith("/") else ""
            if (folder and path.startswith(folder.rstrip("/") + "/")) or fnmatch.fnmatch(path, pat):
                if "migrations" in pat and path not in self.base:
                    continue                    # existing migrations are frozen; new ones may be added
                return f"{path} is protected by the project brief ({pat})."
        return None

    def write(self, path: str, content: Optional[str]) -> None:
        if content is not None and self.base.get(path) == content:
            self.changes.pop(path, None)
        elif content is None and path not in self.base:
            self.changes.pop(path, None)
        else:
            self.changes[path] = content


def _norm(path: str) -> str:
    p = (path or "").replace("\\", "/").strip().lstrip("/")
    while p.startswith("./"):
        p = p[2:]
    if ".." in p.split("/") or not p:
        raise ValueError("invalid path")
    return p


def change_list(ws: Workspace) -> List[Dict]:
    out = []
    for p in sorted(ws.changes):
        new, old = ws.changes[p], ws.base.get(p)
        status = "deleted" if new is None else "added" if old is None else "modified"
        a = d = 0
        if not (new and adopt_files.is_binary(new)) and not (old and adopt_files.is_binary(old)):
            for line in difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(), lineterm="", n=0):
                if line.startswith("+") and not line.startswith("+++"):
                    a += 1
                elif line.startswith("-") and not line.startswith("---"):
                    d += 1
        out.append({"path": p, "status": status, "additions": a, "deletions": d})
    return out


def unified_diff(ws: Workspace, limit: int = 400_000) -> str:
    parts = []
    for p in sorted(ws.changes):
        new, old = ws.changes[p], ws.base.get(p)
        if any(p.startswith(d + "/") for d in ws.generated):
            parts.append(f"--- a/{p}\n+++ b/{p}\nRebuilt bundle file (generated by the frontend build — not shown)\n")
            continue
        if (new and adopt_files.is_binary(new)) or (old and adopt_files.is_binary(old)):
            parts.append(f"--- a/{p}\n+++ b/{p}\nBinary file changed\n")
            continue
        parts.append("\n".join(difflib.unified_diff((old or "").splitlines(), (new or "").splitlines(),
                                                    "/dev/null" if old is None else f"a/{p}",
                                                    "/dev/null" if new is None else f"b/{p}", lineterm="", n=3)))
    text = "\n".join(x for x in parts if x)
    return text if len(text) <= limit else text[:limit] + "\n… (diff truncated)"


# ─────────────────────────────────────────────────────────────────────────────
# Quick checks (no execution — fast, run inside the API)
# ─────────────────────────────────────────────────────────────────────────────
def quick_check(ws: Workspace) -> List[str]:
    problems = []
    for p, c in ws.changes.items():
        if c is None or adopt_files.is_binary(c):
            continue
        try:
            if p.endswith(".json") and not re.search(r"(tsconfig|jsconfig|\.eslintrc)[\w.-]*\.json$", p):
                json.loads(c)
            elif p.endswith(".xml"):
                ET.fromstring(c.encode("utf-8"))
            elif p.endswith(".py"):
                compile(c, p, "exec", dont_inherit=True)
            elif p.endswith(".cds"):
                depth = 0
                for ch in re.sub(r"'[^']*'|//[^\n]*|/\*.*?\*/", "", c, flags=re.S):
                    depth += (ch == "{") - (ch == "}")
                    if depth < 0:
                        break
                if depth != 0:
                    problems.append(f"{p}: unbalanced braces")
            elif p.endswith(".properties"):
                for i, line in enumerate(c.splitlines(), 1):
                    s = line.strip()
                    if s and not s.startswith(("#", "!")) and "=" not in s and ":" not in s:
                        problems.append(f"{p}:{i}: not a key=value line")
                        break
        except (ValueError, ET.ParseError, SyntaxError) as e:
            problems.append(f"{p}: {e}")
    return problems


# ─────────────────────────────────────────────────────────────────────────────
# Tools
# ─────────────────────────────────────────────────────────────────────────────
def _schema(props: Dict, required: List[str]) -> Dict:
    return {"json": {"type": "object", "properties": props, "required": required}}


T_LIST = {"name": "list_files", "description": "List project files (paths and sizes). Optional prefix to limit to a folder.",
          "inputSchema": _schema({"prefix": {"type": "string"}}, [])}
T_READ = {"name": "read_file", "description": f"Read a text file with line numbers (max {READ_LINES} lines per call; "
                                             "use start_line/end_line for long files).",
          "inputSchema": _schema({"path": {"type": "string"}, "start_line": {"type": "integer"},
                                  "end_line": {"type": "integer"}}, ["path"])}
T_SEARCH = {"name": "search", "description": "Search file contents with a regular expression (case-insensitive). "
                                            "Optional glob like 'srv/**/*.js'. Returns path:line: text.",
            "inputSchema": _schema({"pattern": {"type": "string"}, "glob": {"type": "string"}}, ["pattern"])}
T_WRITE = {"name": "write_file", "description": "Create a file or replace its whole content. Prefer edit_file for small "
                                               "changes to existing files.",
           "inputSchema": _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])}
T_EDIT = {"name": "edit_file", "description": "Replace an exact text snippet in a file. old_text must match exactly once "
                                             "(include enough surrounding lines) unless replace_all is true.",
          "inputSchema": _schema({"path": {"type": "string"}, "old_text": {"type": "string"},
                                  "new_text": {"type": "string"}, "replace_all": {"type": "boolean"}},
                                 ["path", "old_text", "new_text"])}
T_APPEND = {"name": "append_file", "description": "Append text to the end of a file you are writing in parts. Use it for "
                                                 "big files: write_file the first part (max ~250 lines), then append_file "
                                                 "the following parts in order.",
            "inputSchema": _schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])}
T_DELETE = {"name": "delete_file", "description": "Delete a file.", "inputSchema": _schema({"path": {"type": "string"}}, ["path"])}
T_QUICK = {"name": "quick_check", "description": "Fast syntax checks of the files you changed (JSON, XML, CDS braces, "
                                                "Python, .properties). The full build/test run happens automatically "
                                                "after you finish.",
           "inputSchema": _schema({}, [])}
MAX_COMMANDS = 16
T_RUN = {"name": "run_command",
         "description": "Run a shell command in a persistent sandbox copy of the project (your current changes are "
                        "synced first; working directory = project root; use `cd sub && cmd` for sub-folders). Use it to "
                        "install dependencies (npm install / pip install -r requirements.txt) and to run the tests or "
                        "builds that cover your change (npm test, npx jest <file>, npm run build, python -m pytest -q). "
                        "The first install can take 1-2 minutes; later commands reuse it. Long output is shortened to "
                        f"the important part. Use plain commands — no pipes to tail/head/grep (the sandbox may run "
                        f"Windows or Linux). Max {MAX_COMMANDS} commands per task.",
         "inputSchema": _schema({"command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, ["command"])}
T_PLAN = {"name": "submit_plan", "description": "Submit your understanding and plan (ends the planning phase).",
          "inputSchema": _schema({
              "restated": {"type": "string", "description": "The request in your own words, in business terms, 2-4 sentences."},
              "questions": {"type": "array", "description": "Only questions whose answer changes what you build. Max 4. "
                                                            "Each needs a sensible default.",
                            "items": {"type": "object", "properties": {
                                "id": {"type": "string"}, "question": {"type": "string"},
                                "options": {"type": "array", "items": {"type": "string"}},
                                "default": {"type": "string"}}, "required": ["id", "question", "default"]}},
              "criteria": {"type": "array", "items": {"type": "string"},
                           "description": "Testable acceptance criteria (3-8)."},
              "plan": {"type": "array", "items": {"type": "object", "properties": {
                  "step": {"type": "string"}, "files": {"type": "array", "items": {"type": "string"}}},
                  "required": ["step", "files"]}},
              "design": {"type": "array", "description": "For requests that create or reshape screens / UI / an app: the "
                                                         "proposed design, one entry per screen. Empty otherwise.",
                         "items": {"type": "object", "properties": {
                             "screen": {"type": "string", "description": "Screen name, e.g. 'Expenses list'"},
                             "type": {"type": "string", "description": "Floorplan / page type, e.g. 'List Report', 'Dashboard'"},
                             "purpose": {"type": "string", "description": "Who uses it for what, one sentence"},
                             "layout": {"type": "string", "description": "Layout in one sentence, e.g. 'KPI header, filter bar, table'"},
                             "sections": {"type": "array", "items": {"type": "string"},
                                          "description": "Main sections / components, in order"}},
                             "required": ["screen", "type", "purpose", "layout", "sections"]}}},
              ["restated", "questions", "criteria", "plan"])}
T_FINISH = {"name": "finish", "description": "Call when the change is complete. Report every acceptance criterion.",
            "inputSchema": _schema({
                "summary": {"type": "string", "description": "What you changed and why, for the reviewer (short paragraphs)."},
                "criteria_results": {"type": "array", "items": {"type": "object", "properties": {
                    "criterion": {"type": "string"}, "met": {"type": "boolean"}, "evidence": {"type": "string"}},
                    "required": ["criterion", "met", "evidence"]}}},
                ["summary", "criteria_results"])}

PLAN_TOOLS = [T_LIST, T_READ, T_SEARCH, T_PLAN]
RUN_TOOLS = [T_LIST, T_READ, T_SEARCH, T_WRITE, T_APPEND, T_EDIT, T_DELETE, T_QUICK, T_RUN, T_FINISH]


def _exec_tool(name: str, args: Dict, ws: Workspace, step: Callable[[str, str, bool], None]) -> Tuple[str, bool]:
    """Returns (text result, ok)."""
    files = ws.files()
    if name == "list_files":
        pre = (args.get("prefix") or "").lstrip("/")
        paths = [p for p in sorted(files) if p.startswith(pre)]
        step("read", f"Listing {pre or 'project'} ({len(paths)} files)", True)
        lines = [f"{p}  ({len(files[p])} chars{' , binary' if adopt_files.is_binary(files[p]) else ''})" for p in paths[:600]]
        return "\n".join(lines) + (f"\n… {len(paths) - 600} more" if len(paths) > 600 else "") or "(no files)", True
    if name == "read_file":
        p = _norm(args.get("path", ""))
        c = ws.read(p)
        if c is None:
            near = [x for x in files if x.endswith(p.split("/")[-1])][:5]
            return f"File not found: {p}" + (f". Did you mean: {', '.join(near)}" if near else ""), False
        if adopt_files.is_binary(c):
            return f"{p} is a binary file.", False
        lines = c.splitlines()
        s = max(1, int(args.get("start_line") or 1))
        e = min(len(lines), int(args.get("end_line") or (s + READ_LINES - 1)), s + READ_LINES - 1)
        step("read", f"Reading {p}" + (f" (lines {s}-{e})" if s > 1 or e < len(lines) else ""), True)
        body = "\n".join(f"{i:5}| {lines[i - 1]}" for i in range(s, e + 1))
        more = f"\n… file has {len(lines)} lines; read more with start_line={e + 1}" if e < len(lines) else ""
        return (body or "(empty file)") + more, True
    if name == "search":
        pat = args.get("pattern") or ""
        try:
            rx = re.compile(pat, re.I)
        except re.error as e:
            return f"Invalid regular expression: {e}", False
        glob = args.get("glob") or ""
        hits = []
        for p in sorted(files):
            c = files[p]
            if adopt_files.is_binary(c) or (glob and not fnmatch.fnmatch(p, glob)) or p.endswith(NEVER_EDIT):
                continue
            for i, line in enumerate(c.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{p}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 120:
                        break
            if len(hits) >= 120:
                break
        step("search", f"Searching “{pat[:60]}”" + (f" in {glob}" if glob else "") + f" — {len(hits)} hit(s)", True)
        return "\n".join(hits) or "No matches.", True
    if name in ("write_file", "append_file", "edit_file", "delete_file"):
        p = _norm(args.get("path", ""))
        why = ws.is_protected(p)
        if why:
            step("edit", f"Refused to change {p} (protected)", False)
            return f"Refused: {why}", False
        old = ws.read(p)
        if name == "delete_file":
            if old is None:
                return f"File not found: {p}", False
            ws.write(p, None)
            step("edit", f"Deleted {p}", True)
            return f"Deleted {p}.", True
        if name == "write_file":
            content = args.get("content")
            if content is None:
                return "content is required", False
            ws.write(p, content)
            step("edit", f"{'Created' if old is None else 'Rewrote'} {p} ({len(content.splitlines())} lines)", True)
            return f"Wrote {p} ({len(content.splitlines())} lines).", True
        if name == "append_file":
            content = args.get("content") or ""
            if old is None:
                return f"File not found: {p}. Start it with write_file.", False
            if adopt_files.is_binary(old):
                return f"{p} is binary.", False
            joined = old + ("" if old.endswith("\n") or not old else "\n") + content
            ws.write(p, joined)
            step("edit", f"Appended to {p} (now {len(joined.splitlines())} lines)", True)
            return f"Appended; {p} now has {len(joined.splitlines())} lines.", True
        if old is None:
            return f"File not found: {p}. Use write_file to create it.", False
        if adopt_files.is_binary(old):
            return f"{p} is binary.", False
        o, n = args.get("old_text") or "", args.get("new_text") or ""
        count = old.count(o) if o else 0
        if count == 0:
            # tolerate line-ending / trailing whitespace differences
            o2 = re.sub(r"[ \t]+\n", "\n", o.replace("\r\n", "\n"))
            old2 = re.sub(r"[ \t]+\n", "\n", old.replace("\r\n", "\n"))
            if o2 and old2.count(o2) == 1:
                old, o, count = old2, o2, 1
        if count == 0:
            return "old_text was not found in the file. Read the file again and copy the exact text.", False
        if count > 1 and not args.get("replace_all"):
            return f"old_text occurs {count} times — include more surrounding lines, or set replace_all.", False
        new = old.replace(o, n) if args.get("replace_all") else old.replace(o, n, 1)
        ws.write(p, new)
        added = max(0, n.count("\n") - o.count("\n"))
        step("edit", f"Edited {p}" + (f" (+{added} lines)" if added else ""), True)
        return f"Edited {p}.", True
    if name == "quick_check":
        probs = quick_check(ws)
        step("check", "Quick checks: " + ("all fine" if not probs else f"{len(probs)} problem(s)"), not probs)
        return "No problems found." if not probs else "\n".join(probs), True
    if name == "run_command":
        cmd = (args.get("command") or "").strip()
        if not cmd:
            return "command is required", False
        if ws.terminal is None:
            return "Command execution is not available here; the full checks run after you finish.", False
        if ws.commands_run >= MAX_COMMANDS:
            return f"Command limit ({MAX_COMMANDS}) reached — finish, the full checks run automatically.", False
        ws.commands_run += 1
        step("run", f"$ {cmd[:160]}", True)
        try:
            r = ws.terminal.exec(cmd, ws.changes, float(args.get("timeout_seconds") or 300))
        except Exception as e:  # noqa: BLE001
            step("run", f"Command could not run: {str(e)[:200]}", False)
            return f"The sandbox could not run the command: {e}", False
        ok = r.get("code") == 0
        step("run", f"exit {r.get('code')} after {r.get('seconds', 0)}s", ok)
        return f"exit code {r.get('code')}\n{r.get('output', '')}", True
    return f"Unknown tool {name}", False


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────
def _context(files: Dict[str, str], analysis: Dict, brief: Dict, history: List[Dict]) -> str:
    analysis = adopt_scan.enrich(files, analysis)
    u = analysis.get("understanding") or {}
    parts = [adopt_scan.overview_text(analysis)]
    if u.get("summary") or brief.get("purpose"):
        parts.append("BUSINESS CONTEXT\n" + (brief.get("purpose") or u.get("summary") or ""))
    for k in ("business_objects", "roles", "processes", "integrations"):
        if u.get(k):
            parts.append(f"{k.replace('_', ' ').title()}: " + "; ".join(u[k]))
    if brief.get("rules"):
        parts.append("TEAM RULES (follow them)\n" + brief["rules"])
    prot = brief.get("protected") or []
    if prot:
        parts.append("PROTECTED PATHS (never change): " + ", ".join(prot))
    if brief.get("commands"):
        parts.append("BUILD / TEST COMMANDS (run after you finish)\n" + brief["commands"])
    if history:
        parts.append("EARLIER CHANGES IN THIS PROJECT\n" + "\n".join(
            f"- {h.get('request', '')[:200]} → {h.get('summary', '')[:300]}" for h in history[-8:]))
    tree = sorted(p for p in files if not p.endswith(NEVER_EDIT))
    parts.append(f"FILES ({len(tree)})\n" + "\n".join(tree[:900]) + (f"\n… {len(tree) - 900} more (use list_files)" if len(tree) > 900 else ""))
    return "\n\n".join(parts)


_SYS_COMMON = """You are the Evoke AI assistant: a senior full-stack engineer and UX designer (SAP CAP, Fiori Elements, SAPUI5,
React/Angular/Vue, Node.js, TypeScript, Python) who has just joined the client's team. You work ONLY through
the tools. You make focused, production-quality changes that fit the existing code: same structure,
naming, style, libraries and i18n conventions. You never invent APIs of the project — read the code first.
You never touch secrets; settings come from the environment."""

_SYS_PLAN = _SYS_COMMON + """
PHASE: understand and plan. Explore the relevant files with list_files / read_file / search (be efficient:
5-12 tool calls). Then call submit_plan once with:
- restated: the request in business terms,
- questions: ONLY when the answer really changes what you build (max 4, each with a default the team
  would most likely pick); none when the request is clear,
- criteria: testable acceptance criteria,
- plan: concrete steps with the files each step changes or creates (for UI work: the files the UI MAP lists
  for the affected pages — the components / templates / styles that actually render them),
- design: when the request creates or reshapes screens, the UI or a new app, DESIGN it first as an
  experienced UX designer would for this business scenario — one entry per screen (type, purpose, layout,
  sections in order). Follow SAP Fiori design guidelines for SAP UIs.
When the request concerns screens / the UI / a new app and does not say which type of application it should
be, include a question with id "app_type" ("What type of application should this be?") with options that fit
the project's stack; the first option keeps the current type.
Do not change files in this phase."""

_SYS_RUN = _SYS_COMMON + """
PHASE: implement. Carry out the plan with the tools.
- Read a file before editing it; prefer edit_file with exact snippets; write_file for new files.
- Keep every tool call small: at most ~250 lines of content per call. For a big new or rewritten file,
  write_file the first part, then append_file the rest part by part (answers that are too long are cut off).
- Keep every layer consistent (e.g. CAP: CDS model, service projection, annotations, handlers, i18n, seed CSV;
  UI5: view, controller, i18n of all languages present; React/Angular: component, types, API client, tests).
- UI changes: the UI MAP in the context lists every page URL and the files that render it (SPA components,
  server templates and their CSS, UI5 views). Change exactly those files so the change is VISIBLE on those
  pages — a UI request is not done until the files that draw the screen are changed. Server-rendered pages
  (Jinja / Django / EJS templates) are changed in the template and its stylesheet, SPA screens in their
  components and styles. Never edit built output (dist/, build/, a committed static/dist) — change the
  sources; the studio rebuilds committed bundles itself.
- Add or update tests when the project has tests.
- Run quick_check after editing.
- Verify for real with run_command: install dependencies once, then run the tests / build that cover your
  change, read the output and fix what fails before you finish. Do not add dependencies whose peer
  requirements conflict with the framework versions the project uses (check package.json first).
- If a failure is pre-existing (it fails without your change too), leave it and say so in the summary.
- When done, call finish with a reviewer summary and every acceptance criterion marked met/not met with evidence.
  Be honest: if something could not be done, say so."""


# ─────────────────────────────────────────────────────────────────────────────
# Agent loop
# ─────────────────────────────────────────────────────────────────────────────
def _trim(messages: List[Dict], budget: int = 450_000) -> None:
    """Keep the conversation under budget: shorten the oldest tool results first."""
    def size():
        return sum(len(json.dumps(m, default=str)) for m in messages)
    i = 1
    while size() > budget and i < len(messages) - 4:
        for part in messages[i].get("content") or []:
            tr = part.get("toolResult")
            if tr:
                for c in tr.get("content") or []:
                    if len(c.get("text", "")) > 400:
                        c["text"] = c["text"][:300] + "\n[… trimmed to save context; read the file again if needed]"
        i += 1


def _salvage(use: Dict, ws: Workspace, step) -> Optional[str]:
    """A write_file / append_file cut off at the answer limit: apply its complete lines and tell the robot where
    to continue, instead of throwing minutes of generated content away."""
    raw = (use.get("input") or {}).get("__partial_json")
    if not raw or use.get("name") not in ("write_file", "append_file"):
        return None
    pm = re.search(r'"path"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    cm = re.search(r'"content"\s*:\s*"', raw)
    if not pm or not cm:
        return None
    body = raw[cm.end():]
    cut = body.rfind("\\n")                      # keep whole lines only
    if cut < 0:
        return None
    try:
        text = json.loads('"' + body[:cut + 2] + '"')
        path = json.loads('"' + pm.group(1) + '"')
    except ValueError:
        return None
    lines = text.split("\n")
    if len(text.strip()) < 40:
        return None
    use["input"]["path"] = path
    result, ok = _exec_tool(use["name"], {"path": path, "content": text}, ws, step)
    if not ok:
        return None
    last = next((l for l in reversed(lines) if l.strip()), "")[-160:]
    total = len((ws.read(_norm(path)) or "").splitlines())
    return (f"Your {use['name']} for {path} was cut off at the answer limit. The complete lines were SAVED "
            f"({total} lines in the file now). Continue with append_file for the rest, starting right after this last "
            f"saved line:\n{last}\nSend at most ~250 lines per call.")


def _converse_with_heartbeat(converse, system, messages, tools, say, every: float = 30.0, tool_choice=None):
    """One model call; while it runs (long answers take minutes) report that the robot is still working."""
    box: Dict = {"chars": 0}

    def progress(n):
        box["chars"] = n

    def call():
        try:
            try:
                kw = {"tool_choice": tool_choice} if tool_choice else {}
                box["r"] = converse(system, messages, tools, on_progress=progress, **kw)
            except TypeError:                              # a converse without progress reporting
                box["r"] = converse(system, messages, tools)
        except Exception as e:  # noqa: BLE001
            box["e"] = e
    t = threading.Thread(target=call, daemon=True)
    t0 = time.time()
    t.start()
    while t.is_alive():
        t.join(every)
        if t.is_alive():
            say(f"⏳ Still writing… ({int(time.time() - t0)} s, ~{box['chars'] // 4:,} tokens so far)")
    if "e" in box:
        raise box["e"]
    return box["r"]


def _loop(converse, system: str, messages: List[Dict], tools: List[Dict], ws: Workspace, stop_tool: str,
          max_steps: int, step: Callable[[str, str, bool], None], say: Callable[[str], None]) -> Dict:
    """Run tool calls until `stop_tool` is called; returns its input."""
    cutoffs = 0
    for n in range(max_steps):
        _trim(messages)
        r = _converse_with_heartbeat(converse, system, messages, tools, say)
        msg = r["output"]["message"]
        messages.append({"role": "assistant", "content": msg["content"]})
        uses = [c["toolUse"] for c in msg["content"] if "toolUse" in c]
        for c in msg["content"]:
            if c.get("text", "").strip():
                t = c["text"].strip()
                step("think", t[:400], True)
        if r.get("stopReason") == "max_tokens":
            hint = ("Your answer was cut off because it was too long, so it was NOT applied. Send at most ~250 lines per "
                    "tool call: write_file the first part of the file, then append_file the remaining parts one call "
                    "at a time.")
            results, saved = [], False
            for u in uses:
                note = _salvage(u, ws, step)
                if note:
                    saved = True
                    u["input"] = {"path": u["input"].get("path", ""), "content": "(cut off — the complete lines were saved)"}
                results.append({"toolResult": {"toolUseId": u["toolUseId"], "status": "success" if note else "error",
                                               "content": [{"text": note or hint}]}})
            if saved:
                cutoffs = 0
                step("info", "That answer was too long — kept the complete part and continues from there", True)
            else:
                cutoffs += 1
                step("info", "That answer was too long and was cut off — continuing in smaller parts", False)
                if cutoffs >= 3:
                    raise RuntimeError("The AI answers were cut off 3 times in a row (the change is too large for "
                                       "one step). Split the request into smaller requests, e.g. one screen at a time.")
            for u in uses:                         # never send the raw partial JSON back to the model
                if "__partial_json" in (u.get("input") or {}):
                    u["input"] = {"note": "cut off"}
            messages.append({"role": "user", "content": results or [{"text": hint}]})
            continue
        cutoffs = 0
        if not uses:
            messages.append({"role": "user", "content": [{"text": f"Continue using the tools. Call {stop_tool} when done."}]})
            continue
        results = []
        final = None
        for u in uses:
            if u["name"] == stop_tool:
                final = u["input"] or {}
                results.append({"toolResult": {"toolUseId": u["toolUseId"], "content": [{"text": "ok"}]}})
                continue
            try:
                text, ok = _exec_tool(u["name"], u.get("input") or {}, ws, step)
            except Exception as e:  # noqa: BLE001
                text, ok = f"Tool error: {e}", False
            results.append({"toolResult": {"toolUseId": u["toolUseId"], "status": "success" if ok else "error",
                                           "content": [{"text": text[:60_000]}]}})
        if max_steps - n - 1 == WRAP_UP:
            results.append({"text": f"Only {WRAP_UP} steps are left. Complete the most important remaining work now "
                                    f"(no more exploring) and call {stop_tool}."})
        messages.append({"role": "user", "content": results})
        if final is not None:
            return final
    return _out_of_steps(converse, system, messages, tools, ws, stop_tool, max_steps, step, say)


def _out_of_steps(converse, system, messages, tools, ws, stop_tool, max_steps, step, say) -> Dict:
    """The step budget is used up: make the model report what it did (it must call `stop_tool`), and never throw
    away the files already changed."""
    step("info", f"Reached the limit of {max_steps} steps — asking the AI to wrap up with what is done", False)
    messages[-1]["content"].append({"text": f"The step limit is reached. Call {stop_tool} NOW: report what is done "
                                            "and list anything that is still missing."})
    try:
        r = _converse_with_heartbeat(converse, system, messages, tools, say,
                                     tool_choice={"type": "tool", "name": stop_tool})
        use = next((c["toolUse"] for c in r["output"]["message"]["content"]
                    if "toolUse" in c and c["toolUse"]["name"] == stop_tool), None)
        if use is not None:
            return dict(use["input"] or {}, _limit=True)
    except Exception as e:  # noqa: BLE001
        say(f"Wrap-up call failed: {e}")
    if stop_tool == "finish" and ws.changes:
        return {"summary": f"Stopped after {max_steps} steps before the AI reported back; the changes made so far "
                           "are kept. Review them, then use Revise (e.g. \"continue\") to complete the rest.",
                "criteria_results": [], "_limit": True}
    raise RuntimeError(f"The AI assistant did not finish within {max_steps} steps. Split the request into smaller "
                       "requests (e.g. one screen at a time).")


def _stepper(m: Dict, say: Callable[[str], None]):
    icons = {"think": "💭", "read": "📖", "search": "🔎", "edit": "✏️", "check": "🧪", "run": "▶", "ask": "❓", "info": "•"}

    def step(kind: str, text: str, ok: bool = True):
        m["steps"].append({"at": time.time(), "kind": kind, "text": text, "ok": ok})
        del m["steps"][:-400]
        say(f"{icons.get(kind, '•')} {text if kind != 'think' else text[:220]}")
    return step


_UI_WORDS = re.compile(r"\b(ui|ux|screens?|(re)?design\w*|layout|look and feel|dashboards?|frontend|front-end|"
                       r"user interface|interface|theme|styl(e|ing)|professional|modern|navigation|menu|wizard|"
                       r"list report|object page|fiori|tiles?|charts?|new (app|application|page|view)|landing page)\b",
                       re.I)


def is_ui_request(request: str) -> bool:
    return bool(_UI_WORDS.search(request or ""))


def app_type_question(analysis: Dict) -> Dict:
    """The application type, with options that fit the project's stack."""
    kinds = {m.get("kind") for m in analysis.get("modules") or []}
    ui = next((m for m in analysis.get("modules") or [] if m.get("role") == "ui"), None)
    current = f"Keep the current type ({ui['framework']})" if ui else "Keep the current type"
    if kinds & {"cap-srv", "cap-db", "fe", "ovp", "ui5"}:
        opts = ["SAP Fiori Elements — List Report and Object Page", "SAP Fiori Elements — Worklist",
                "SAP Fiori Elements — Overview Page / analytical", "SAPUI5 freestyle — custom layout",
                "Master–detail (split screen)", "Dashboard with KPIs and charts", "Guided form / wizard"]
    else:
        opts = ["Admin dashboard", "Data table with detail pages (CRUD)", "Master–detail (split screen)",
                "Analytics dashboard with charts", "Guided form / wizard", "Portal / landing page"]
    return {"id": "app_type", "question": "What type of application should this be?", "options": [current] + opts,
            "default": current}


def _design_text(m: Dict) -> str:
    if not m.get("design"):
        return ""
    app_type = (m.get("answers") or {}).get("app_type") or next(
        (q.get("default") for q in m.get("questions") or [] if q.get("id") == "app_type"), "")
    lines = ["\n\nAPPROVED DESIGN" + (f" — application type: {app_type}" if app_type else "")
             + " (build exactly this; if the chosen application type differs from the design, adapt the design to it)"]
    for d in m["design"]:
        lines.append(f"- {d.get('screen')} [{d.get('type')}]: {d.get('purpose')} Layout: {d.get('layout')}. "
                     f"Sections: {', '.join(d.get('sections') or [])}")
    return "\n".join(lines)


def plan(m: Dict, files: Dict[str, str], analysis: Dict, brief: Dict, history: List[Dict], converse,
         say: Callable[[str], None] = lambda x: None) -> Dict:
    step = _stepper(m, say)
    step("info", "Understanding the request and exploring the project…")
    ws = Workspace(files, {}, brief.get("protected"))
    msgs = [{"role": "user", "content": [{"text": _context(files, analysis, brief, history)
                                          + "\n\nCHANGE REQUEST\n" + m["request"]}]}]
    out = _loop(converse, _SYS_PLAN, msgs, PLAN_TOOLS, ws, "submit_plan", MAX_STEPS_PLAN, step, say)
    m["restated"] = out.get("restated", "")
    m["questions"] = [q for q in (out.get("questions") or []) if q.get("question")][:4]
    for i, q in enumerate(m["questions"]):
        q["id"] = q.get("id") or f"q{i + 1}"
    m["criteria"] = [c for c in (out.get("criteria") or []) if c][:10]
    m["plan"] = out.get("plan") or []
    m["design"] = [d for d in (out.get("design") or []) if isinstance(d, dict) and d.get("screen")][:12]
    if is_ui_request(m["request"]) and not any(q["id"] == "app_type" for q in m["questions"]):
        m["questions"].insert(0, app_type_question(analysis))       # always ask which type of application
    m["status"] = "needs_input" if m["questions"] else "planned"
    step("info", "Plan ready" + (f" — {len(m['questions'])} question(s) for you" if m["questions"] else ""))
    return save(m)


def _run_rounds(m: Dict, ws: Workspace, msgs: List[Dict], converse, analysis: Dict, brief: Dict,
                checks_fn: Optional[Callable[[Dict[str, str], Callable], Dict]], say, step, rebuild_fn=None) -> Dict:
    try:
        return _rounds(m, ws, msgs, converse, checks_fn, say, step, analysis, rebuild_fn)
    finally:
        if ws.terminal is not None:
            try:
                ws.terminal.close()
            except Exception:  # noqa: BLE001
                pass


def _mark_preexisting(report: Dict, baseline: set) -> Dict:
    """Checks that also fail on the original version are not caused by the change: shown as warnings,
    never blocking."""
    if not baseline:
        return report
    checks = []
    for c in report.get("checks", []):
        if c["status"] == "failed" and c["name"] in baseline:
            c = dict(c, status="warning", critical=False,
                     detail="Already failing before this change (pre-existing) — not caused by the AI change.\n" + (c.get("detail") or ""))
        checks.append(c)
    summary = {k: sum(1 for c in checks if c["status"] == s) for k, s in
               (("passed", "passed"), ("failed", "failed"), ("warnings", "warning"), ("not_tested", "not_tested"))}
    out = dict(report, checks=checks, summary=summary)
    out["blocking"] = [c["name"] for c in checks if c.get("critical") and c["status"] == "failed"]
    return out


_UI_EXT = (".tsx", ".jsx", ".vue", ".css", ".scss", ".less", ".html", ".htm", ".ejs", ".pug", ".hbs", ".njk", ".jinja",
           ".jinja2", ".view.xml", ".fragment.xml", ".svelte")


def _ui_files_touched(ws: Workspace, analysis: Dict) -> List[str]:
    """Changed files that render something the user sees (from the UI map, or by their kind)."""
    mapped = {f for u in analysis.get("ui_map") or [] for f in u.get("files") or []}
    return [p for p in ws.changes if not any(p.startswith(d + "/") for d in ws.generated)
            and (p in mapped or p.endswith(_UI_EXT) or "/templates/" in "/" + p or "/components/" in "/" + p
                 or "/pages/" in "/" + p or "/views/" in "/" + p)]


def _rebuild_committed(ws: Workspace, analysis: Dict, rebuild_fn, say, step) -> None:
    """A committed UI bundle (e.g. React built into the backend's static/dist) is rebuilt from the changed sources,
    so the deployed app shows the change — not only the preview."""
    outs = []
    for b in analysis.get("build_outputs") or []:
        if not b.get("committed"):
            continue
        src = b.get("module_path") or ""
        if any(p.startswith(src + ("" if src else "src/")) and not p.startswith(b["out_dir"] + "/") for p in ws.changes):
            outs.append(b)
    if not outs or not rebuild_fn:
        return
    step("check", "Rebuilding the committed UI bundle (" + ", ".join(b["out_dir"] + "/" for b in outs)
         + ") so the deployed app shows the change…")
    try:
        r = rebuild_fn(ws.files(), outs, say)
    except Exception as e:  # noqa: BLE001
        step("check", f"Could not rebuild the UI bundle: {str(e)[:300]} — rebuild it before deploying", False)
        return
    for e in r.get("errors") or []:
        step("check", "Could not rebuild: " + e[:300], False)
    new = r.get("files") or {}
    for d in r.get("out_dirs") or []:
        for p in [p for p in ws.files() if p.startswith(d + "/") and p not in new]:
            if _HASHED.search(p):                               # stale hashed assets of the old build
                ws.write(p, None)
    for p, c in new.items():
        ws.write(p, c)
    if r.get("out_dirs"):
        step("check", f"Rebuilt {', '.join(d + '/' for d in r['out_dirs'])} ({len(new)} files) — the new UI is in the change", True)


_HASHED = re.compile(r"[.-][A-Za-z0-9_-]{8,}\.(js|mjs|css|map)$|/assets/")


def _rounds(m: Dict, ws: Workspace, msgs: List[Dict], converse, checks_fn, say, step, analysis: Optional[Dict] = None,
            rebuild_fn=None) -> Dict:
    analysis = analysis or {}
    final = _loop(converse, _SYS_RUN, msgs, RUN_TOOLS, ws, "finish", MAX_STEPS_RUN, step, say)
    if is_ui_request(m["request"]) and ws.changes and not _ui_files_touched(ws, analysis):
        # a UI request whose change touches no file that renders a page would leave the screens exactly as they were
        step("info", "None of the changed files renders a page — asking the AI to change the screens themselves", False)
        msgs.append({"role": "user", "content": [{"text": (
            "Your change does not touch ANY file that renders what the user sees, so the UI would look exactly as "
            "before. Use the UI MAP: change the files that render the affected pages (components / templates / "
            "styles / views) so the requested UI change is visible, then call finish again.")}]})
        final = _loop(converse, _SYS_RUN, msgs, RUN_TOOLS, ws, "finish", 40, step, say)
    m["summary"] = final.get("summary", "")
    m["criteria_results"] = final.get("criteria_results") or []
    if final.get("_limit"):
        m["summary"] = ("⚠ The AI reached its step limit, so this change may be incomplete — check the criteria "
                        "below and use Revise (e.g. \"continue with the remaining items\") to finish it.\n\n" + m["summary"])
    m["_files"] = ws.changes
    save(m)
    baseline: Optional[set] = None
    while checks_fn and ws.changes:
        step("check", "Running the project's build and test commands in the sandbox…")
        report = checks_fn(ws.files(), say)
        failed = [c for c in report.get("checks", []) if c["status"] == "failed"]
        if failed and baseline is None:
            step("check", "Some checks fail — running them on the original version to see what was already failing…")
            base_rep = checks_fn(ws.base, say)
            baseline = {c["name"] for c in base_rep.get("checks", []) if c["status"] == "failed"}
            if baseline:
                step("check", "Already failing before this change: " + ", ".join(sorted(baseline))[:300], True)
        report = _mark_preexisting(report, baseline or set())
        m["checks"] = report
        failed = [c for c in report.get("checks", []) if c["status"] == "failed"]
        step("check", f"Checks: {report['summary']['passed']} passed · {report['summary']['failed']} failed · "
                      f"{report['summary']['not_tested']} not tested", not failed)
        if not failed or m["rounds"] >= MAX_REPAIR:
            break
        m["rounds"] += 1
        step("info", f"Repairing the failures (round {m['rounds']} of {MAX_REPAIR})…")
        ws.commands_run = max(0, ws.commands_run - 6)          # a few more commands for the repair
        detail = "\n\n".join(f"### {c['name']}\n{c['detail'][:4500]}" for c in failed[:6])
        msgs.append({"role": "user", "content": [{"text": "The full checks FAILED after your change:\n\n" + detail +
                                                  "\n\nFind the cause (it may be pre-existing — then say so and leave it), "
                                                  "fix what your change broke, and call finish again."}]})
        final = _loop(converse, _SYS_RUN, msgs, RUN_TOOLS, ws, "finish", 40, step, say)
        m["summary"] = final.get("summary", m["summary"])
        m["criteria_results"] = final.get("criteria_results") or m["criteria_results"]
    blocking = (m.get("checks") or {}).get("blocking") or []
    if ws.changes and not blocking:
        _rebuild_committed(ws, analysis, rebuild_fn, say, step)
    m["_generated"] = ws.generated
    m["_files"] = ws.changes
    m["changes"] = change_list(ws)
    m["diff"] = unified_diff(ws)
    m["status"] = "ready" if ws.changes and not blocking else "failed"
    if not ws.changes:
        m["error"] = "The AI assistant finished without changing any file."
    elif blocking:
        m["error"] = ("Checks still fail after the repair attempts: " + ", ".join(blocking[:4])
                      + ". Revise with guidance, or fix it in your own tools and import a new version.")
        step("info", "Stopped: checks still fail — the change is kept for your review", False)
    return save(m)


def _workspace(files: Dict[str, str], m: Dict, analysis: Dict, brief: Dict, terminal) -> Workspace:
    ws = Workspace(files, m.get("_files") or {}, brief.get("protected"), terminal)
    ws.generated = [b["out_dir"] for b in analysis.get("build_outputs") or [] if b.get("committed") and b.get("out_dir")]
    return ws


def run(m: Dict, files: Dict[str, str], analysis: Dict, brief: Dict, history: List[Dict], converse,
        checks_fn=None, say: Callable[[str], None] = lambda x: None, terminal=None, rebuild_fn=None) -> Dict:
    step = _stepper(m, say)
    m["status"] = "working"
    m["error"] = ""
    save(m)
    analysis = adopt_scan.enrich(files, analysis)
    ws = _workspace(files, m, analysis, brief, terminal)
    qa = "\n".join(f"- {q['question']} → {m['answers'].get(q['id']) or q.get('default', '')}" for q in m["questions"])
    task = (f"CHANGE REQUEST\n{m['request']}\n\nYOUR UNDERSTANDING\n{m['restated']}\n\n"
            + (f"ANSWERS FROM THE TEAM\n{qa}\n\n" if qa else "")
            + "ACCEPTANCE CRITERIA\n" + "\n".join(f"- {c}" for c in m["criteria"]) + "\n\nPLAN\n"
            + "\n".join(f"{i + 1}. {p.get('step')} ({', '.join(p.get('files') or [])})" for i, p in enumerate(m["plan"]))
            + _design_text(m))
    msgs = [{"role": "user", "content": [{"text": _context(files, analysis, brief, history) + "\n\n" + task}]}]
    step("info", "Implementing the plan…")
    try:
        return _run_rounds(m, ws, msgs, converse, analysis, brief, checks_fn, say, step, rebuild_fn)
    except Exception as e:  # noqa: BLE001
        m["status"] = "failed"
        m["error"] = str(e)[:800]
        m["_files"] = ws.changes
        m["changes"] = change_list(ws)
        m["diff"] = unified_diff(ws)
        save(m)
        raise


def revise(m: Dict, feedback: str, files: Dict[str, str], analysis: Dict, brief: Dict, history: List[Dict], converse,
           checks_fn=None, say: Callable[[str], None] = lambda x: None, terminal=None, rebuild_fn=None) -> Dict:
    step = _stepper(m, say)
    m["feedback"].append({"at": time.time(), "text": feedback})
    m["status"] = "working"
    m["rounds"] = 0
    m["error"] = ""
    save(m)
    analysis = adopt_scan.enrich(files, analysis)
    ws = _workspace(files, m, analysis, brief, terminal)
    changed = "\n".join(f"- {c['status']} {c['path']} (+{c['additions']} −{c['deletions']})" for c in change_list(ws))
    task = (f"CHANGE REQUEST\n{m['request']}\n\nACCEPTANCE CRITERIA\n" + "\n".join(f"- {c}" for c in m["criteria"])
            + f"\n\nYOU ALREADY CHANGED (current state is in the files)\n{changed}\n\nYOUR EARLIER SUMMARY\n{m['summary']}"
            + f"\n\nREVIEWER FEEDBACK — address it\n{feedback}")
    msgs = [{"role": "user", "content": [{"text": _context(ws.files(), analysis, brief, history) + "\n\n" + task}]}]
    step("info", "Revising the change with your feedback…")
    try:
        return _run_rounds(m, ws, msgs, converse, analysis, brief, checks_fn, say, step, rebuild_fn)
    except Exception as e:  # noqa: BLE001
        m["status"] = "failed"
        m["error"] = str(e)[:800]
        save(m)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# Suggestions
# ─────────────────────────────────────────────────────────────────────────────
_SUGGEST_SYS = """You are a senior SAP BTP / full-stack consultant reviewing a client project the team just adopted.
Propose the 6 most valuable next changes the client would likely ask for or benefit from — mix business
features that fit the scenario with quality items (security, tests, i18n, accessibility, performance,
upgrades) where the findings show a need. Each must be concrete enough to hand to an engineer.
Answer ONLY with JSON: {"suggestions": [{"title": "short", "request": "the change request, 1-3 sentences",
"why": "one sentence", "kind": "feature|quality|security|upgrade"}]}"""


def suggest(files: Dict[str, str], analysis: Dict, brief: Dict, ai_fn) -> List[Dict]:
    u = analysis.get("understanding") or {}
    text = (adopt_scan.overview_text(analysis) + "\n\nPurpose: " + (brief.get("purpose") or u.get("summary") or "")
            + "\nFindings:\n" + "\n".join(f"- [{f['severity']}] {f['text']}" for f in analysis.get("findings") or [])
            + "\n\nKEY FILES\n" + adopt_scan.key_files(files, analysis, 40_000))
    raw = ai_fn(_SUGGEST_SYS, text)
    mm = re.search(r"\{.*\}", raw, re.S)
    data = json.loads(mm.group(0)) if mm else {}
    return [s for s in data.get("suggestions") or [] if s.get("request")][:8]
