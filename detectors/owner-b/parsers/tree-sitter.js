'use strict';
// Syntax-tree parsing for Python / JavaScript / TypeScript sources via WASM tree-sitter.
// Source text is only parsed, never imported or executed. The grammars are vendored in ../grammars
// (see its README); web-tree-sitter is pinned to the version those binaries load with.
const fs = require('node:fs');
const path = require('node:path');
const Parser = require('web-tree-sitter');

const MAX_SOURCE_BYTES = 256 * 1024;
const PARSE_TIMEOUT_MICROS = 5_000_000;
const GRAMMARS = {python: 'tree-sitter-python.wasm', javascript: 'tree-sitter-javascript.wasm', typescript: 'tree-sitter-typescript.wasm', tsx: 'tree-sitter-tsx.wasm'};
/** @type {Record<string,'python'|'javascript'|'typescript'|'tsx'>} */
const EXTENSIONS = {'.py': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript', '.ts': 'typescript', '.mts': 'typescript', '.cts': 'typescript', '.tsx': 'tsx'};

/** @param {string} locator @returns {'python'|'javascript'|'typescript'|'tsx'|null} */
function languageOf(locator) {
  const m = /\.[A-Za-z0-9]+$/.exec(locator);
  return (m && EXTENSIONS[m[0].toLowerCase()]) || null;
}
// Source layout (parsers/ -> ../grammars) and esbuild bundle layout (grammars/ next to the bundle).
function firstExisting(candidates, what) {
  const hit = candidates.find(p => fs.existsSync(p));
  if (!hit) throw new Error(`${what} not found; looked in ${candidates.join(', ')}`);
  return hit;
}
function grammarPath(file) {
  const dirs = [process.env.OWNER_B_GRAMMARS, path.join(__dirname, '..', 'grammars'), path.join(__dirname, 'grammars')].filter(Boolean);
  return firstExisting(dirs.map(d => path.join(d, file)), 'Grammar ' + file);
}
function runtimeWasm() {
  const candidates = [path.join(__dirname, 'tree-sitter.wasm'), path.join(__dirname, '..', 'tree-sitter.wasm')];
  try {candidates.push(require.resolve('web-tree-sitter/tree-sitter.wasm'));} catch { /* bundled layout */ }
  return firstExisting(candidates, 'web-tree-sitter runtime');
}

let ready = null;
const languages = new Map();
function init() {
  ready ??= Parser.init({locateFile: () => runtimeWasm()});
  return ready;
}
async function language(name) {
  await init();
  if (!languages.has(name)) languages.set(name, Parser.Language.load(grammarPath(GRAMMARS[name])));
  return languages.get(name);
}
/** Parse one source. A tree with any syntax error is rejected: an unparsed file must never look clean.
 * @param {'python'|'javascript'|'typescript'|'tsx'} lang @param {string} source */
async function parse(lang, source) {
  if (!GRAMMARS[lang]) throw new Error('Unsupported language ' + lang);
  if (Buffer.byteLength(source) > MAX_SOURCE_BYTES) throw new Error('Source exceeds the 256 KiB bound');
  const lng = await language(lang);
  const parser = new Parser();
  try {
    parser.setLanguage(lng);
    parser.setTimeoutMicros(PARSE_TIMEOUT_MICROS);
    const tree = parser.parse(source);
    if (!tree) throw new Error('Parse timed out');
    if (tree.rootNode.hasError()) {tree.delete(); throw new Error('Syntax error: file not evaluated');}
    return tree;
  } finally {parser.delete();}
}
module.exports = {parse, languageOf, MAX_SOURCE_BYTES};
