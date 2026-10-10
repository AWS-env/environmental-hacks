'use strict';
// DB-23 Unbounded queries. Static candidate for Python, JavaScript and TypeScript.
// Source: SRC-02 (queries returning an unbounded number of records, typically displayed; fix: pagination). The paper's
// "for display" intent and the table size are not statically visible, so every finding is a candidate.
// Flag: a read that is materialised (Django queryset iterated or list()-ed; SQLAlchemy .all(); Prisma findMany, Sequelize
// findAll, TypeORM find/getMany, Knex/Drizzle awaited select; raw SELECT passed to execute/query) with no limiter.
// Exempt: slices / limit / take / first / one / get / count / exists / aggregate / iterator (streaming), aggregate-only selects,
// lookups on a unique key (context.unique_key_fields), options objects containing a spread (limiter unknown), INSERT ... SELECT.
// LIMITATION: a queryset returned or stored is lazy and may be sliced later; only immediate materialisation is flagged.
// Raw SQL held in a variable and executed elsewhere is not followed.
const orm = require('../../core/orm');

const REF = 'https://taoxie.cs.illinois.edu/publications/icsme20-dbperf.pdf';
const REF_DJANGO = 'https://docs.djangoproject.com/en/stable/topics/db/optimization/';
const DJANGO_CHAIN = new Set(['all', 'filter', 'exclude', 'order_by', 'values', 'values_list', 'select_related', 'prefetch_related', 'annotate', 'distinct', 'only', 'defer', 'using', 'reverse']);
const LIMITERS = new Set(['limit', 'take', 'first', 'one', 'one_or_none', 'fetch', 'fetchmany', 'slice', 'first_or_404', 'scalar', 'scalar_one', 'scalar_one_or_none', 'get', 'count', 'exists', 'paginate']);
const JS_LIMIT_KEYS = new Set(['take', 'limit']);
const JS_DB_RECEIVER = /\b(prisma|prismaClient|db|dbRead|dbWrite|tx|trx|knex|repo|repository|manager|entityManager|em|dataSource|sequelize)\b|(Repository|Repo|Model)\b/;
const {EXEC_METHODS, sqlOf, chainOf} = orm;
// One-off or test-support code rarely sits on a request path: keep the finding, lower the confidence.
const ONE_OFF_PATH = /(^|\/)(management\/commands|migrations?|migration-scripts|scripts?|seeds?|fixtures?|__fixtures__|integration-tests|helpers|demo|cli|create-[a-z-]+-app)(\/|$)|(random_data|populate|seed|fixture)[^/]*$/i;
const AGGREGATE = /\b(count|sum|avg|min|max)\s*\(/i;

function hasUniqueKey(text, fields) {return fields.some(f => new RegExp(`\\b${f}\\s*[=:]`).test(text));}
/** Raw SELECT without any limiter. @returns {boolean} */
// SQL longer than this is not analysed: every regex below stays linear, but a hostile 200 KB string should not cost seconds.
const MAX_SQL_BYTES = 8192;
const KEY_VALUE = String.raw`(?:%s|%\(\w+\)s|\$\d+|\?|:\w+|\d+|@\w+|'[^']*'|"[^"]*"|\$\{[^}]*\})`;
function unboundedSelect(sql, unique) {
  if (sql.length > MAX_SQL_BYTES || Buffer.byteLength(sql) > MAX_SQL_BYTES) return false;
  if (!/^\s*(with\b[\s\S]*?\bselect|select)\b/i.test(sql) || !/\bfrom\b/i.test(sql)) return false;
  if (/\b(limit|fetch\s+(first|next)|top\s+\d+|rownum)\b/i.test(sql)) return false;
  if (/\binsert\s+into\b|\bcreate\s+(or\s+replace\s+)?(temp(orary)?\s+)?(table|view)\b|\bfor\s+update\b/i.test(sql)) return false;
  const head = sql.slice(0, sql.search(/\bfrom\b/i));
  if (AGGREGATE.test(head) && !/\bgroup\s+by\b/i.test(sql)) return false;
  // Unique-key lookup: an equality on a unique field in the WHERE clause, and no OR that could widen it. Searching only the text
  // after the first WHERE keeps this linear (a leading [\s\S]* per WHERE occurrence was quadratic).
  const where = sql.search(/\bwhere\b/i);
  if (where >= 0 && !/\bor\b/i.test(sql)) {
    const tail = sql.slice(where);
    const fields = [...new Set(['pk', 'id', ...unique])].join('|');
    if (new RegExp(String.raw`\b(?:\w+\.)?(?:pk|id)\s*=\s*${KEY_VALUE}\s*(?:$|;|\)|\border\b)`, 'i').test(tail)) return false;
    if (new RegExp(String.raw`\b(?:\w+\.)?(?:${fields})\s*=\s*(?:%s|\$\d+|\?|:\w+|\d+)`, 'i').test(tail)) return false;
  }
  return true;
}

function inspect({source, lang, root, input}) {
  const unique = input.context.unique_key_fields;
  orm.requireValue(Array.isArray(unique) && unique.length > 0 && unique.every(f => /^\w+$/.test(f)), 'Missing/invalid context.unique_key_fields');
  const out = [];
  // Ranking by evidence: reading a whole table (no filter at all) is medium confidence; a filtered read is low (the filter usually scopes it to a parent row).
  const add = (node, api, text, filtered, refs = [REF]) => out.push(orm.finding(`${orm.enclosing(node)}:${api}`,
    `Unbounded read (${text}): no limit, take, pagination or streaming, so every matching row is fetched.`,
    'If this can match many rows (for example a list shown to a user), add pagination or a limit, or stream with iterator()/a cursor. Ignore it for small, bounded reference data.',
    [orm.nodeLines(source, node)], refs, filtered || ONE_OFF_PATH.test(source.locator) ? 'low' : 'medium'));
  const seenSql = new Set();
  const rawSql = (callNode, args) => {
    const first = args?.namedChildren[0];
    if (!first || !['string', 'template_string'].includes(first.type) || seenSql.has(first.id)) return;
    seenSql.add(first.id);
    if (unboundedSelect(sqlOf(first), unique)) add(callNode, 'raw-sql.select', 'a raw SELECT without LIMIT', /\bwhere\b/i.test(sqlOf(first)));
  };
  orm.walk(root, node => {
    if (lang === 'python') {
      // Django: an iterated / list()-ed queryset chain.
      const consider = expr => {
        if (!expr || expr.type !== 'call') return;
        const {methods, root: base} = chainOf(expr, lang);
        if (methods.length && methods.every(m => DJANGO_CHAIN.has(m)) && /\.objects$/.test(base?.text ?? '') && !hasUniqueKey(expr.text, unique.filter(f => f === 'pk' || f === 'id')))
          add(expr, 'django.queryset', 'a Django queryset that is iterated without a slice or iterator()', methods.some(m => m === 'filter' || m === 'exclude'), [REF, REF_DJANGO]);
      };
      if (node.type === 'for_statement') consider(node.childForFieldName('right'));
      else if (node.type === 'for_in_clause') consider(node.childForFieldName('right'));
      else if (node.type === 'call') {
        const fn = node.childForFieldName('function'), args = node.childForFieldName('arguments');
        if (['list', 'tuple', 'set', 'sorted'].includes(fn?.text) && args?.namedChildCount >= 1) consider(args.namedChildren[0]);
        const {methods, root: base} = chainOf(node, lang);
        // SQLAlchemy: a chain ending in .all() without any limiter (Django's own .all() is handled above).
        if (methods.at(-1) === 'all' && !/\.objects$/.test(base?.text ?? '') && !methods.some(m => LIMITERS.has(m))
          && (methods.includes('query') || methods.includes('select') || methods.includes('scalars') || /\.query$|^query$/.test(base?.text ?? ''))
          && !/\bselect\(\s*func\./.test(node.text)) add(node, 'sqlalchemy.all', 'SQLAlchemy .all() without .limit()', methods.some(m => ['filter', 'filter_by', 'where'].includes(m)));
        const method = fn?.type === 'attribute' ? fn.childForFieldName('attribute')?.text : null;
        if (method && EXEC_METHODS.has(method)) rawSql(node, args);
      }
      return;
    }
    if (node.type !== 'call_expression') return;
    const fn = node.childForFieldName('function'), args = node.childForFieldName('arguments');
    const method = fn?.type === 'member_expression' ? fn.childForFieldName('property')?.text : null;
    if (!method) return;
    const receiver = fn.childForFieldName('object')?.text ?? '';
    const optionsOf = () => (args?.namedChildren ?? []).find(a => a.type === 'object');
    const keysOf = obj => (obj?.namedChildren ?? []).map(p => p.type === 'spread_element' ? '...' : p.type === 'pair' ? p.childForFieldName('key')?.text?.replace(/^['"]|['"]$/g, '') : p.type === 'shorthand_property_identifier' ? p.text : null);
    const unboundedOptions = () => {
      // A variable / call / spread argument (findMany(query)) has an unknown limiter: not flagged.
      if ((args?.namedChildren ?? []).some(a => a.type !== 'object')) return false;
      const keys = keysOf(optionsOf());
      if (keys.includes('...') || keys.some(k => JS_LIMIT_KEYS.has(k))) return false;
      const obj = optionsOf();
      const where = obj?.namedChildren.find(p => p.type === 'pair' && p.childForFieldName('key')?.text === 'where');
      const whereKeys = where ? keysOf(where.childForFieldName('value')) : [];
      return !whereKeys.some(k => unique.includes(k));
    };
    const filteredOptions = () => keysOf(optionsOf()).includes('where');
    if (method === 'findMany' || method === 'findAll' || method === 'findAndCountAll') {
      if (unboundedOptions()) add(node, `option.${method}`, `${method}() without take/limit`, filteredOptions());
    } else if ((method === 'find' || method === 'findBy' || method === 'findAndCount') && JS_DB_RECEIVER.test(receiver) && !/^this\.\w*$/.test(receiver) && (args?.namedChildCount === 0 || optionsOf())) {
      if (unboundedOptions()) add(node, 'option.find', 'a repository find() without take/limit', filteredOptions());
    } else if (method === 'getMany' || method === 'getRawMany') {
      const {methods} = chainOf(node, lang);
      if (!methods.some(m => ['take', 'limit', 'getOne'].includes(m))) add(node, 'querybuilder.getMany', `${method}() without take()/limit()`, methods.some(m => m === 'where' || m === 'andWhere'));
    } else if (EXEC_METHODS.has(method)) rawSql(node, args);
    // Knex / Drizzle: the outermost call of an awaited builder chain that selects without a limiter.
    if (node.parent?.type === 'await_expression') {
      const {methods, root: base} = chainOf(node, lang);
      const stops = ['limit', 'first', 'count', 'sum', 'avg', 'min', 'max', 'pluck', 'del', 'delete', 'insert', 'update', 'take', 'countDistinct'];
      // `knex('t').select()` roots at a plain call (methods[0] === 'knex'); `db.select().from()` roots at the identifier `db`.
      if (/^(knex|trx|db|tx)\b/.test(base?.text ?? methods[0] ?? '') && (methods.includes('select') || methods.includes('from'))
        && !methods.some(m => stops.includes(m)) && !hasUniqueKey(node.text, unique.filter(f => f === 'id')))
        add(node, 'builder.select', 'an awaited Knex/Drizzle select without limit()', methods.some(m => /^where/.test(m)));
    }
  });
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => orm.evaluateOrm(input, 'DB-23', inspect), inspect};
