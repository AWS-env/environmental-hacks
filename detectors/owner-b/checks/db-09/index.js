'use strict';
// DB-09 Inefficient lazy loading (N+1), explicit form. Static candidate for Python, JavaScript and TypeScript.
// Sources: SRC-02 (1 + N queries), SRC-51 (queries "executed in a loop"; select_related/prefetch_related), SRC-52 (N+1 and
// selectinload/joinedload/raiseload), Prisma docs ("don't loop with separate queries"). SRC-52 does not use the word cartesian,
// irrelevant here. DB-40 (the same fact outside a loop) is not reported (decision D12).
// Flag: a database READ call (Django manager get/first/count/..., a queryset used as the iterable of a nested loop, SQLAlchemy
// query/get/execute(select), Prisma/Sequelize/TypeORM find*/count, Knex awaited select, raw SELECT via execute/query) inside a loop
// or forEach/map callback whose receiver or arguments depend on the loop variable (or a name derived from it in the body).
// Higher confidence when the loop itself iterates a query result (the textbook "1 + N" shape).
// Exempt: loops over a literal collection or range() of at most context.max_literal_iterations (our configured choice),
// stepped batch loops, reads that do not depend on the loop variable (that is DB-04, loop-invariant), Prisma findUnique inside a
// map/forEach callback (the Prisma client batches same-tick findUnique calls), cache lookups (non-DB receivers).
// LIMITATION: the implicit lazy-attribute form (`book.author.name` in a loop) needs the model definitions and is NOT covered here;
// loop size and whether an eager option is available are runtime/semantic facts, so every finding is a candidate.
const orm = require('../../core/orm');
const loops = require('../../core/loops');

const {PY_DB_RECEIVER, JS_DB_RECEIVER, ONE_OFF_PATH, firstArgString} = loops;
const REF_DJANGO = 'https://docs.djangoproject.com/en/stable/topics/db/optimization/';
const REF_SQLA = 'https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html';
const REF_PAPER = 'https://taoxie.cs.illinois.edu/publications/icsme20-dbperf.pdf';
const DJANGO_READ_TERMINAL = new Set(['get', 'first', 'last', 'count', 'exists', 'aggregate', 'latest', 'earliest', 'in_bulk']);
const DJANGO_CHAIN = new Set(['all', 'filter', 'exclude', 'order_by', 'values', 'values_list', 'select_related', 'prefetch_related', 'annotate', 'distinct', 'only', 'defer', 'using', 'reverse']);
const SQLA_TERMINAL = new Set(['first', 'one', 'one_or_none', 'all', 'scalar', 'scalar_one', 'scalar_one_or_none', 'count']);
const JS_READ = new Set(['findUnique', 'findUniqueOrThrow', 'findFirst', 'findFirstOrThrow', 'findMany', 'count', 'aggregate', 'groupBy', 'findByPk', 'findOne', 'findOneBy', 'findOneOrFail', 'findBy', 'findAll', 'getOne', 'getMany', 'getRawOne']);
// ORM-specific method names that identify a database read on any receiver (Sequelize models are called `Post`, `User`, ...).
const JS_READ_ANY_RECEIVER = new Set(['findUnique', 'findUniqueOrThrow', 'findFirstOrThrow', 'findMany', 'findByPk', 'findAll', 'findOneOrFail', 'findOneBy']);
const PRISMA_BATCHED = new Set(['findUnique', 'findUniqueOrThrow']);
const SELECT_SQL = /^\s*(with\b[\s\S]*?\bselect|select)\b/i;
const QUERYISH = /\.objects\b|\.(findMany|findAll|find|findBy|all|filter|query|fetchall|fetch|execute|getMany)\s*\(|\bselect\s*\(|\bSELECT\b/;

/** Chain of method names (outermost last) and the base node of `a.b(...).c(...)`. */
function chain(expr) {
  let cur = expr;
  const methods = [];
  while (cur && ['call', 'call_expression'].includes(cur.type)) {
    const fn = cur.childForFieldName('function');
    const m = fn?.type === 'attribute' ? fn.childForFieldName('attribute') : fn?.type === 'member_expression' ? fn.childForFieldName('property') : null;
    if (!m) return {methods, base: fn ?? cur};
    methods.unshift(m.text);
    cur = fn.childForFieldName('object');
  }
  return {methods, base: cur};
}
/** True when the call is consumed as an iterable (for-in right side, comprehension source, list()/tuple()/set()/sorted()). */
function consumedAsIterable(node) {
  const p = node.parent;
  if (!p) return false;
  if ((p.type === 'for_statement' || p.type === 'for_in_clause') && p.childForFieldName('right')?.id === node.id) return true;
  return p.type === 'argument_list' && ['list', 'tuple', 'set', 'sorted'].includes(p.parent?.childForFieldName('function')?.text ?? '');
}
/** Classify a call as a per-item read. @returns {string|null} api name */
function readKind(call, lang) {
  const fn = call.childForFieldName('function'), args = call.childForFieldName('arguments');
  if (lang === 'python') {
    if (fn?.type !== 'attribute') return null;
    const {methods, base} = chain(call);
    const last = methods.at(-1), baseText = base?.text ?? '';
    if (/\.objects$/.test(baseText) && methods.every(m => DJANGO_CHAIN.has(m) || DJANGO_READ_TERMINAL.has(m))) {
      if (DJANGO_READ_TERMINAL.has(last)) return `django.${last}`;
      if (consumedAsIterable(call)) return 'django.queryset';
      return null;
    }
    if (PY_DB_RECEIVER.test(baseText) || /\.session$/.test(baseText)) {
      if (methods[0] === 'query' && SQLA_TERMINAL.has(last)) return `sqlalchemy.${last}`;
      if (methods.length === 1 && last === 'get') return 'sqlalchemy.get';
      if (methods.length === 1 && ['execute', 'scalars', 'scalar'].includes(last)) {
        const first = args?.namedChildren[0];
        if (first?.type === 'call' && /^select\(/.test(first.text)) return `sqlalchemy.${last}`;
        if (last === 'execute' && SELECT_SQL.test(firstArgString(args) ?? '')) return 'raw-sql.execute';
      }
    }
    return null;
  }
  if (fn?.type !== 'member_expression') return null;
  const method = fn.childForFieldName('property')?.text, receiver = fn.childForFieldName('object')?.text ?? '';
  if (JS_READ.has(method) && (JS_READ_ANY_RECEIVER.has(method) || JS_DB_RECEIVER.test(receiver))) return `orm.${method}`;
  if (['query', 'execute', 'run', 'raw'].includes(method) && JS_DB_RECEIVER.test(receiver) && SELECT_SQL.test(firstArgString(args) ?? '')) return 'raw-sql.query';
  return null;
}
/** Is the loop's iterable itself a query result (directly or through a name assigned from one in the same function)? */
function iteratesQueryResult(loop) {
  const it = loop.iterable;
  if (!it) return false;
  if (QUERYISH.test(it.text)) return true;
  if (it.type !== 'identifier') return false;
  let scope = loop.header;
  while (scope.parent && !['function_definition', 'function_declaration', 'method_definition', 'arrow_function', 'program', 'module'].includes(scope.type)) scope = scope.parent;
  let found = false;
  orm.walk(scope, n => {
    const target = n.type === 'assignment' ? n.childForFieldName('left') : n.type === 'variable_declarator' ? n.childForFieldName('name') : null;
    const value = n.type === 'assignment' ? n.childForFieldName('right') : n.type === 'variable_declarator' ? n.childForFieldName('value') : null;
    if (target?.text === it.text && value && QUERYISH.test(value.text)) found = true;
  });
  return found;
}
function inspect({source, lang, root, input}) {
  const max = input.context.max_literal_iterations;
  orm.requireValue(Number.isInteger(max) && max >= 0, 'Missing/invalid context.max_literal_iterations');
  const out = [];
  orm.walk(root, node => {
    if (node.type !== (lang === 'python' ? 'call' : 'call_expression')) return;
    let api = readKind(node, lang);
    // Knex / Drizzle: the outermost call of an awaited builder chain that selects.
    if (!api && lang !== 'python' && node.parent?.type === 'await_expression') {
      const {methods, base} = chain(node);
      const rootName = base?.text ?? '';
      if (/^(knex|trx|db|tx)\b/.test(rootName) || /^(knex|trx|db|tx)$/.test(methods[0] ?? '')) if (methods.includes('select') || methods.includes('from')) api = 'builder.select';
    }
    if (!api) return;
    const loop = loops.enclosingLoop(node, lang);
    if (!loop || loops.smallLiteral(loop, max) || loops.steppedLoop(loop)) return;
    if (PRISMA_BATCHED.has(api.replace(/^orm\./, '')) && ['map', 'flatMap', 'forEach'].includes(loop.kind)) return;
    const used = loops.names(node);
    if (![...loops.tainted(loop)].some(x => used.has(x))) return;
    const header = orm.line(source, loop.header.startPosition.row + 1), call = orm.nodeLines(source, node);
    const confidence = ONE_OFF_PATH.test(source.locator) || !iteratesQueryResult(loop) ? 'low' : 'medium';
    out.push(orm.finding(`${orm.enclosing(node)}:${api}-in-${loop.kind}`,
      `Query per item (${api}) inside a ${loop.kind} loop: the classic N+1, one round trip for each of N items plus the query that produced them.`,
      'Fetch the related rows once: Django select_related/prefetch_related or filter(pk__in=...), SQLAlchemy selectinload/joinedload, Prisma include or a single findMany with `in`, then look items up in memory. Ignore it when N is always tiny.',
      header.line_start === call.line_start ? [call] : [header, call], [REF_PAPER, REF_DJANGO, REF_SQLA], confidence));
  });
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => orm.evaluateOrm(input, 'DB-09', inspect), inspect};
