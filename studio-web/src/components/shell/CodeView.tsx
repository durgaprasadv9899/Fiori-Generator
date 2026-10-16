// Read-only code viewer with line numbers and simple, dependency-free syntax highlighting.
import { useMemo } from "react";

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function highlight(line: string, lang: string): string {
  let s = esc(line);
  if (lang === "xml" || lang === "html") {
    s = s.replace(/(&lt;\/?)([\w:.-]+)/g, '$1<span class="tk-tag">$2</span>')
      .replace(/([\w:.-]+)=(&quot;|")(.*?)\2/g, '<span class="tk-attr">$1</span>=<span class="tk-str">"$3"</span>')
      .replace(/(&lt;!--.*?--&gt;)/g, '<span class="tk-com">$1</span>');
    return s;
  }
  if (lang === "properties" || lang === "yaml") {
    if (/^\s*#/.test(line)) return `<span class="tk-com">${s}</span>`;
    return s.replace(/^(\s*[\w.\-/]+)(\s*[:=])/, '<span class="tk-key">$1</span>$2');
  }
  // js / json / cds / css
  return s
    .replace(/(\/\/.*$)/, '<span class="tk-com">$1</span>')
    .replace(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`[^`]*`)/g, '<span class="tk-str">$1</span>')
    .replace(/\b(function|return|const|let|var|if|else|for|while|new|this|true|false|null|undefined|import|export|from|async|await|entity|service|using|namespace|key|type|extends|on|as|projection|action|define|aspect|association|composition|of|many|to|one)\b/g,
      '<span class="tk-kw">$1</span>')
    .replace(/\b(\d+(?:\.\d+)?)\b/g, '<span class="tk-num">$1</span>');
}

export function langOf(path: string): string {
  const ext = (path.split(".").pop() || "").toLowerCase();
  return ({ js: "js", ts: "js", json: "json", xml: "xml", html: "html", cds: "cds", css: "css", yaml: "yaml",
    yml: "yaml", properties: "properties", md: "md", csv: "csv" } as Record<string, string>)[ext] || "text";
}

export function CodeView({ text, path = "", maxHeight = 520 }: { text: string; path?: string; maxHeight?: number | string }) {
  const lang = langOf(path);
  const lines = useMemo(() => (text || "").split("\n").slice(0, 4000), [text]);
  return (
    <div className="ev-code" style={{ maxHeight }}>
      <table>
        <tbody>
          {lines.map((l, i) => (
            <tr key={i}>
              <td className="ev-code__ln">{i + 1}</td>
              <td className="ev-code__src" dangerouslySetInnerHTML={{ __html: lang === "text" || lang === "md" || lang === "csv" ? esc(l) || " " : highlight(l, lang) || " " }} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
