'use strict';
// DB-14 Unnecessary column retrieval. Static candidate for Python, JavaScript and TypeScript.
// Sources: SRC-02 (retrieving more columns than needed), SRC-51 Django optimization (values()/only()/defer()).
// Flag, first pass: a raw SQL string passed to execute/query/raw/fetch* whose SELECT list is `*` or `t.*`, and an explicit
// Knex-style `.select('*')` / `.select('t.*')`. sqlfluff AM04 is the reference rule for the SQL form.
// Exempt: COUNT(*), EXISTS (SELECT *), INSERT / CREATE ... SELECT *, `SELECT * FROM (subquery)` wrappers, tables listed in
// context.select_star_allowed_tables (small config tables the project accepts).
// NOT covered (second pass, needs use analysis): ORM defaults that fetch every column (Prisma/TypeORM/Django without select/only).
// LIMITATION: "unnecessary" depends on what the caller reads; the table's column count is unknown, so every finding is a candidate.
const orm = require('../../core/orm');

const REF = 'https://docs.sqlfluff.com/en/stable/reference/rules.html';
const REF_PAPER = 'https://taoxie.cs.illinois.edu/publications/icsme20-dbperf.pdf';
const STAR_SELECT = /\bselect\s+(?:distinct\s+(?:on\s*\([^)]*\)\s*)?)?((?:\w+\.)?\*)\s*(?=,|\bfrom\b)/gi;
const ONE_OFF_PATH = /(^|\/)(management\/commands|migrations?|migration-scripts|scripts?|seeds?|fixtures?|__fixtures__|integration-tests|helpers|demo|cli)(\/|$)/i;

/** Indices of `select *` occurrences in a SQL string that are not exempt. @returns {{table:string|null}[]} */
function starSelects(sql) {
  const hits = [];
  STAR_SELECT.lastIndex = 0;
  for (const m of sql.matchAll(STAR_SELECT)) {
    const before = sql.slice(0, m.index), after = sql.slice(m.index + m[0].length);
    if (/\bexists\s*\(\s*$/i.test(before)) continue;
    if (/\binsert\s+into\b[^;]*$/i.test(before) || /\bcreate\s+(or\s+replace\s+)?(temp(orary)?\s+)?(table|view|materialized\s+view)\b[^;]*$/i.test(before)) continue;
    // First FROM after the star (the select list may carry more columns); a placeholder table ({t}, %s, $1) still counts as a table.
    const from = /\bfrom\s+(\(|[\w"`.\[\]{}$%]+)/i.exec(after);
    if (!from || from[1] === '(') continue;
    hits.push({table: from[1].replace(/["`\[\]]/g, '').toLowerCase()});
  }
  return hits;
}
function inspect({source, lang, root, input}) {
  const allowed = input.context.select_star_allowed_tables;
  orm.requireValue(Array.isArray(allowed) && allowed.every(t => typeof t === 'string'), 'Missing/invalid context.select_star_allowed_tables');
  const allow = new Set(allowed.map(t => t.toLowerCase()));
  const out = [];
  const seen = new Set();
  const add = (node, api, text) => out.push(orm.finding(`${orm.enclosing(node)}:${api}`,
    `${text}: every column of the row is fetched, including ones the caller may never read.`,
    'List only the columns this code reads (or use values()/only() in Django, select in Prisma). Keep SELECT * where the table is tiny or every column is used.',
    [orm.nodeLines(source, node)], [REF, REF_PAPER], ONE_OFF_PATH.test(source.locator) ? 'low' : 'medium'));
  orm.walk(root, node => {
    const callType = lang === 'python' ? 'call' : 'call_expression', memberType = lang === 'python' ? 'attribute' : 'member_expression';
    if (node.type !== callType) return;
    const fn = node.childForFieldName('function'), args = node.childForFieldName('arguments');
    const method = fn?.type === memberType ? fn.childForFieldName(lang === 'python' ? 'attribute' : 'property')?.text : null;
    if (!method) return;
    if (orm.EXEC_METHODS.has(method)) {
      const first = args?.namedChildren[0];
      if (!first || !['string', 'template_string'].includes(first.type) || seen.has(first.id)) return;
      seen.add(first.id);
      for (const hit of starSelects(orm.sqlOf(first))) if (!allow.has(hit.table)) add(node, 'raw-sql.select-star', `Raw SQL \`SELECT *\` from ${hit.table}`);
    } else if (lang !== 'python' && method === 'select') {
      const literals = (args?.namedChildren ?? []).filter(a => a.type === 'string').map(a => orm.sqlOf(a));
      if (literals.some(t => t === '*' || /^\w+\.\*$/.test(t))) add(node, 'builder.select-star', 'Query-builder select(\'*\')');
    }
  });
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => orm.evaluateOrm(input, 'DB-14', inspect), inspect};
