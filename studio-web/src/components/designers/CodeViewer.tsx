// Read-only code viewer with line numbers and lightweight syntax highlighting
// (json, xml/html, js/ts, cds, yaml, properties) — no external highlighter.
import React, { useMemo } from "react";
import "./designers.css";

type Tok = { t: string; c?: string };
const KW_JS = /^(const|let|var|function|return|if|else|for|while|new|this|class|extends|import|from|export|default|async|await|try|catch|throw|true|false|null|undefined|typeof|switch|case|break|sap)$/;
const KW_CDS = /^(namespace|using|entity|service|type|aspect|key|as|projection|on|to|many|one|Composition|Association|of|action|function|returns|annotate|with|extend|define|context|select|from|where|virtual|default|not|null|enum|cuid|managed)$/;

function rx(lang: string): RegExp {
  switch (lang) {
    case "json": return /("(?:\\.|[^"\\])*"(?=\s*:))|("(?:\\.|[^"\\])*")|(-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)|(\btrue\b|\bfalse\b|\bnull\b)/gi;
    case "xml": return /(<!--[\s\S]*?-->)|(<\/?[\w:.-]+)|([\w:.-]+(?==))|("[^"]*"|'[^']*')|(\/?>)/g;
    case "yaml": return /(#.*$)|(^\s*-?\s*[\w.$"'/-]+(?=\s*:))|("[^"]*"|'[^']*')|(\b\d+(?:\.\d+)?\b)/gm;
    case "properties": return /(^\s*#.*$)|(^[^=\n]+(?==))/gm;
    default: return /(\/\/.*$|\/\*[\s\S]*?\*\/)|("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_$][\w$]*\b)/gm;
  }
}

function tokenize(code: string, lang: string): Tok[] {
  const re = rx(lang);
  const out: Tok[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(code))) {
    if (m[0] === "") { re.lastIndex++; continue; }
    if (m.index > last) out.push({ t: code.slice(last, m.index) });
    const g = m.findIndex((x, i) => i > 0 && x !== undefined);
    let c = "";
    if (lang === "json") c = ["", "key", "str", "num", "kw"][g];
    else if (lang === "xml") c = ["", "com", "tag", "attr", "str", "tag"][g];
    else if (lang === "yaml") c = ["", "com", "key", "str", "num"][g];
    else if (lang === "properties") c = ["", "com", "key"][g];
    else {
      c = ["", "com", "str", "num", ""][g];
      if (g === 4) c = (lang === "cds" ? KW_CDS : KW_JS).test(m[0]) ? "kw" : "";
    }
    out.push({ t: m[0], c });
    last = m.index + m[0].length;
  }
  if (last < code.length) out.push({ t: code.slice(last) });
  return out;
}

export function langOf(path: string): string {
  const ext = (path.split(".").pop() || "").toLowerCase();
  return ({ json: "json", xml: "xml", html: "xml", yaml: "yaml", yml: "yaml", properties: "properties", js: "js",
    ts: "js", cds: "cds", mtaext: "yaml", md: "text", css: "js" } as Record<string, string>)[ext] || "text";
}

export function CodeViewer({ code, lang, maxHeight = 520 }: { code: string; lang: string; maxHeight?: number }) {
  const lines = useMemo(() => {
    const text = code.length > 400_000 ? code.slice(0, 400_000) + "\n… (truncated)" : code;
    const toks = lang === "text" ? [{ t: text }] : tokenize(text, lang);
    // split tokens into lines
    const rows: Tok[][] = [[]];
    toks.forEach((tk) => {
      const parts = tk.t.split("\n");
      parts.forEach((p, i) => {
        if (i > 0) rows.push([]);
        if (p) rows[rows.length - 1].push({ t: p, c: tk.c });
      });
    });
    return rows;
  }, [code, lang]);
  return (
    <div className="ev-code" style={{ maxHeight }}>
      <table><tbody>
        {lines.map((row, i) => (
          <tr key={i}>
            <td className="ev-code__ln">{i + 1}</td>
            <td className="ev-code__line">{row.length ? row.map((tk, j) => tk.c ? <span key={j} className={`tk-${tk.c}`}>{tk.t}</span> : <React.Fragment key={j}>{tk.t}</React.Fragment>) : "\u00a0"}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  );
}
