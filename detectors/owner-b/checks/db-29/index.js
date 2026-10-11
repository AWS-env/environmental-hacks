'use strict';
// DB-29 Joining unused tables. Static candidate for Python, JavaScript and TypeScript, raw SQL only.
// Sources: SRC-02 (AP-29: a join added although its table is not needed); sqlfluff ST11 (structure.unused_join) is the reference rule.
// Flag: in a single SELECT passed to execute/query/raw/fetch*, a LEFT JOIN whose table (alias) is not referenced anywhere outside
// its own ON clause: not in the select list, WHERE, GROUP BY, HAVING, ORDER BY or another join's ON.
// Exempt (not provable, so not flagged): inner joins (they filter rows), RIGHT/FULL joins, any unqualified column or bare `*` in the
// query (could belong to the joined table), nested subqueries / CTEs / UNION (correlated references), SQL that does not parse.
// LIMITATION: an unused LEFT JOIN can still change the row count when it matches several rows, so removing it is a review decision
// ("flag only"). ORM-built joins (Django select_related, Prisma include) are not examined.
const orm = require('../../core/orm');
const {Parser} = require('node-sql-parser');

const REF = 'https://docs.sqlfluff.com/en/stable/reference/rules.html';
const REF_PAPER = 'https://taoxie.cs.illinois.edu/publications/icsme20-dbperf.pdf';
const DIALECTS = {postgresql: 'Postgresql', mysql: 'MySQL', sqlite: 'SQLite'};
const MAX_SQL_BYTES = 8192;
const ONE_OFF_PATH = /(^|\/)(management\/commands|migrations?|migration-scripts|scripts?|seeds?|fixtures?|__fixtures__|integration-tests|helpers|demo|cli)(\/|$)/i;
const parser = new Parser();

/** Replace driver placeholders and string-interpolation holes so the SQL parses: values become 1, identifiers become x. */
function sanitize(sql) {
  return sql
    .replace(/(=|<|>|\(|,|\bAND\b|\bOR\b|\bIN\b|\bLIKE\b|\bLIMIT\b|\bOFFSET\b)\s*(\$\{[^}]*\}|\{[^}]*\})/gi, '$1 1')
    .replace(/\$\{[^}]*\}|\{[^}]*\}/g, 'x')
    .replace(/%\(\w+\)s|%s|\$\d+|\?/g, '1')
    .replace(/(^|[^:]):[A-Za-z_]\w*/g, '$11');
}
/** Tables that are referenced outside their own ON clause, or null when the query cannot be analysed safely. */
function analyse(sql, dialects) {
  /** The library's public AST union omits dialect fields, so it is treated as untyped. @type {any} */
  let ast = null;
  for (const d of dialects) {
    try {ast = parser.astify(sanitize(sql), {database: DIALECTS[d]}); break;} catch {ast = null;}
  }
  if (Array.isArray(ast)) ast = ast.length === 1 ? ast[0] : null;
  if (!ast || ast.type !== 'select' || ast.with || ast._next || ast.union || !Array.isArray(ast.from)) return null;
  const joins = ast.from.filter(f => typeof f.join === 'string' && /^LEFT\b/i.test(f.join) && f.table && !f.expr);
  if (!joins.length) return null;
  let ambiguous = false;
  /** References to `alias` anywhere in the statement except inside the ON clause of `own`. */
  const referencesTo = (alias, own) => {
    let count = 0;
    const visit = node => {
      if (!node || typeof node !== 'object') return;
      if (Array.isArray(node)) {for (const n of node) visit(n); return;}
      if (node.type === 'select') {ambiguous = true; return;}
      if (node.type === 'column_ref') {
        const table = typeof node.table === 'string' ? node.table : node.table?.value ?? null;
        if (table === null) ambiguous = true; // unqualified column or bare *: could belong to the joined table
        else if (table === alias) count++;
        return;
      }
      for (const v of Object.values(node)) visit(v);
    };
    // Walk the children of the statement itself (the top-level SELECT is not a nested subquery).
    for (const v of Object.values({...ast, from: ast.from.map(f => (f === own ? {...f, on: null} : f))})) visit(v);
    return count;
  };
  const unused = joins.filter(j => referencesTo(j.as || j.table, j) === 0);
  return ambiguous ? null : unused.map(j => ({alias: j.as || j.table, table: j.table}));
}
function inspect({source, lang, root, input}) {
  const configured = input.context.sql_dialects;
  orm.requireValue(Array.isArray(configured) && configured.length > 0 && configured.every(d => DIALECTS[d]), 'Missing/invalid context.sql_dialects');
  const out = [];
  const seen = new Set();
  const callType = lang === 'python' ? 'call' : 'call_expression', memberType = lang === 'python' ? 'attribute' : 'member_expression';
  orm.walk(root, node => {
    if (node.type !== callType) return;
    const fn = node.childForFieldName('function'), args = node.childForFieldName('arguments');
    const method = fn?.type === memberType ? fn.childForFieldName(lang === 'python' ? 'attribute' : 'property')?.text : null;
    if (!method || !orm.EXEC_METHODS.has(method)) return;
    const first = args?.namedChildren[0];
    if (!first || !['string', 'template_string'].includes(first.type) || seen.has(first.id)) return;
    seen.add(first.id);
    const sql = orm.sqlOf(first);
    if (Buffer.byteLength(sql) > MAX_SQL_BYTES || !/^\s*select\b/i.test(sql)) return;
    let unused;
    try {unused = analyse(sql, configured);} catch {return;}
    for (const u of unused ?? []) out.push(orm.finding(`${orm.enclosing(node)}:raw-sql.unused-join:${u.alias}`,
      `LEFT JOIN of ${u.table}${u.alias === u.table ? '' : ` (${u.alias})`} is never referenced outside its own ON clause, so the query pays for a join whose columns it does not use.`,
      'If nothing needs this table, drop the join. Check first that it matches at most one row per outer row, otherwise removing it changes the number of rows returned.',
      [orm.nodeLines(source, node)], [REF, REF_PAPER], ONE_OFF_PATH.test(source.locator) ? 'low' : 'medium'));
  });
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => orm.evaluateOrm(input, 'DB-29', inspect), inspect, analyse};
