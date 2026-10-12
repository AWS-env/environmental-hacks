/**
 * A small line highlighter for code evidence. It only colours what it can recognise on one line
 * (comments, strings, numbers, keywords, YAML keys), so a miss leaves plain text rather than wrong colours.
 */

export type TokenKind = "plain" | "keyword" | "string" | "number" | "comment";
export interface Token { kind: TokenKind; text: string }

const EXTENSIONS: Record<string, string> = {
  py: "python", js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
  ts: "typescript", tsx: "typescript", go: "go", java: "java", kt: "kotlin", rb: "ruby", rs: "rust",
  cs: "csharp", php: "php", yml: "yaml", yaml: "yaml", json: "json", tf: "hcl", sh: "bash", bash: "bash",
  toml: "toml", sql: "sql",
};

/** Language name for a file path ("" when unknown); also used as the fence hint in the agent prompt. */
export function languageOf(path: string | null): string {
  const name = (path ?? "").split("/").pop()?.toLowerCase() ?? "";
  if (name === "dockerfile" || name.startsWith("dockerfile.")) return "dockerfile";
  const ext = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1) : "";
  return EXTENSIONS[ext] ?? "";
}

const words = (list: string) => new Set(list.split(" "));
const C_LIKE = "if else for while do switch case break continue return function class new this super extends import export from default const let var try catch finally throw typeof instanceof in of async await yield null undefined true false void static public private protected interface type enum implements";
const KEYWORDS: Record<string, Set<string>> = {
  python: words("def class return if elif else for while in not and or is import from as with try except finally raise pass break continue lambda yield async await global nonlocal assert del None True False self"),
  javascript: words(C_LIKE),
  typescript: words(`${C_LIKE} readonly declare namespace keyof as satisfies`),
  go: words("func package import return if else for range switch case default break continue go defer select chan map struct interface type var const nil true false make new"),
  java: words(`${C_LIKE} final void int long boolean double float char byte short package throws synchronized`),
  kotlin: words("fun val var class object interface return if else when for while in is as try catch finally throw null true false import package override private public internal suspend"),
  ruby: words("def end class module return if elsif else unless for while do in yield begin rescue ensure raise nil true false self require"),
  rust: words("fn let mut pub struct enum impl trait use mod return if else for while loop match in as ref move async await self Self true false const static"),
  csharp: words(`${C_LIKE} using namespace void int string bool var readonly override virtual`),
  php: words("function class return if else elseif foreach for while as new echo public private protected static use namespace null true false"),
  bash: words("if then else elif fi for in do done while case esac function return export local echo exit"),
  dockerfile: words("FROM RUN CMD COPY ADD ENV ARG WORKDIR EXPOSE ENTRYPOINT USER LABEL VOLUME AS"),
  hcl: words("resource data variable output module provider locals terraform true false null for in if"),
  sql: words("SELECT FROM WHERE JOIN LEFT RIGHT INNER OUTER ON AND OR NOT IN IS NULL GROUP BY ORDER LIMIT INSERT INTO VALUES UPDATE SET DELETE AS COUNT DISTINCT select from where join on and or not in is null group by order limit insert into values update set delete as count distinct"),
  yaml: words("true false null yes no on off"),
  json: words("true false null"),
  toml: words("true false"),
};

const HASH = new Set(["python", "ruby", "yaml", "bash", "dockerfile", "hcl", "toml"]);
const SLASH = new Set(["javascript", "typescript", "go", "java", "kotlin", "rust", "csharp", "php", "hcl"]);
const STRING = String.raw`"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\])*'?|` + "`(?:\\\\.|[^`\\\\])*`?";
const patterns = new Map<string, RegExp>();

function patternFor(lang: string): RegExp {
  let re = patterns.get(lang);
  if (!re) {
    const comment = [HASH.has(lang) && "#.*", SLASH.has(lang) && String.raw`\/\/.*|\/\*.*?(?:\*\/|$)`, lang === "sql" && "--.*"]
      .filter(Boolean).join("|");
    const key = lang === "yaml" ? String.raw`(?<=^\s*(?:-\s+)?)[\w.\-/]+(?=\s*:(?:\s|$))|` : "";
    re = new RegExp(`(${comment || "(?!)"})|(${key}${STRING})|(\\b\\d[\\d_]*(?:\\.\\d+)?\\b)|([A-Za-z_][\\w]*)`, "g");
    patterns.set(lang, re);
  }
  re.lastIndex = 0;
  return re;
}

export function highlightLine(line: string, lang: string): Token[] {
  if (!lang || !line) return [{ kind: "plain", text: line }];
  const keywords = KEYWORDS[lang];
  const tokens: Token[] = [];
  const re = patternFor(lang);
  let last = 0;
  for (let m = re.exec(line); m; m = re.exec(line)) {
    if (m.index > last) tokens.push({ kind: "plain", text: line.slice(last, m.index) });
    const [text, comment, str, num] = m;
    // YAML keys arrive in the string group; colour them like keywords.
    const kind: TokenKind = comment ? "comment"
      : str ? (lang === "yaml" && !/^["'`]/.test(str) ? "keyword" : "string")
      : num ? "number"
      : keywords?.has(text) ? "keyword" : "plain";
    tokens.push({ kind, text });
    last = m.index + text.length;
  }
  if (last < line.length) tokens.push({ kind: "plain", text: line.slice(last) });
  return tokens;
}
