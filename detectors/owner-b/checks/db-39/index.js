'use strict';
// DB-39 Offset pagination (Skip/Take) on large tables. Static candidate for Python, JavaScript and TypeScript.
// Sources: SRC-06 Use The Index, Luke (offset counts every row up to the requested page; the seek method skips them).
// The taxonomy's EXPLAIN method is not used here: this is the static presence of an offset on a read query.
// Exempt: a literal offset below context.min_literal_offset (our configured choice, not a sourced threshold), seek pagination.
// LIMITATION: whether the table is large or the page deep is a runtime fact, so every finding is a candidate.
// KNOWN FALSE NEGATIVES (measured on real repos, see shreyas/docs/owner-b-decisions.md): options passed through variables or
// wrapper methods (e.g. Medusa listAndCount(filters, config)), SQL assembled from fragments, receivers whose queryset origin is
// outside the function. A wider callee list was tried and reverted: it only duplicated repository-level findings and flagged
// HTTP/tRPC client calls that are not database queries.
const orm = require('../../core/orm');

const REF = 'https://use-the-index-luke.com/sql/partial-results/fetch-next-page';
const PRISMA_REF = 'https://www.prisma.io/docs/orm/prisma-client/queries/pagination';
const QUERYSET = /\.(objects|filter|exclude|all|order_by|values|values_list|only|defer|annotate|select_related|prefetch_related|distinct)\b/;
const FIND_METHODS = new Set(['findMany', 'findFirst', 'findAll', 'findAndCountAll', 'find', 'findAndCount', 'paginate']);
const PLACEHOLDER = String.raw`%s|%\(\w+\)s|\$\d+|\?|:\w+|@\w+|\$\{[^}]*\}|\{[^}]*\}`;
const OFFSET_SQL = new RegExp(String.raw`\bOFFSET\s+(\d+|${PLACEHOLDER})`, 'gi');
const LIMIT_COMMA = new RegExp(String.raw`\bLIMIT\s+(\d+|${PLACEHOLDER})\s*,\s*(?:\d+|${PLACEHOLDER})`, 'gi');

/** @returns {{literal:number|null}} */
function classify(text) {return /^\d+$/.test(text) ? {literal: Number(text)} : {literal: null};}
function flagged(literal, min) {return literal === null || literal >= min;}
function describe(literal) {return literal === null ? 'a non-literal offset' : `a literal offset of ${literal}`;}

/** A slice receiver is a queryset when its own text carries a queryset chain, or it is a name assigned from one in the same function (not wrapped in list()/tuple()). */
function isQueryset(value, at) {
  if (QUERYSET.test(value.text)) return true;
  if (value.type !== 'identifier') return false;
  let scope = at;
  while (scope.parent && !['function_definition', 'module'].includes(scope.type)) scope = scope.parent;
  let found = false;
  orm.walk(scope, n => {
    if (n.type !== 'assignment' || n.childForFieldName('left')?.text !== value.text) return;
    const rhs = n.childForFieldName('right')?.text ?? '';
    if (QUERYSET.test(rhs) && !/^(list|tuple|set|sorted)\(/.test(rhs)) found = true;
  });
  return found;
}
function inspect({source, lang, root, input}) {
  const min = input.context.min_literal_offset;
  orm.requireValue(Number.isInteger(min) && min >= 1, 'Missing/invalid context.min_literal_offset');
  const out = [];
  const add = (node, api, literal, text, confidence = 'medium', refs = [REF]) => {
    if (!flagged(literal, min)) return;
    out.push(orm.finding(`${orm.enclosing(node)}:${api}`,
      `Offset pagination via ${text} with ${describe(literal)}: the database reads and discards every skipped row, so deep pages cost grows with the offset.`,
      'If pages can go deep on a large table, consider keyset (seek) pagination: filter on the last seen sort key instead of skipping rows. Check table size and the deepest page before changing it.',
      [orm.nodeLines(source, node)], refs, confidence));
  };
  const sqlText = (node, text) => {
    if (!/\bselect\b/i.test(text) || !/\bfrom\b/i.test(text)) return;
    for (const re of [OFFSET_SQL, LIMIT_COMMA]) {
      re.lastIndex = 0;
      for (const m of text.matchAll(re)) add(node, 'raw-sql.offset', classify(m[1]).literal, 'a raw SQL OFFSET');
    }
  };
  orm.walk(root, node => {
    if (lang === 'python') {
      if (node.type === 'string') sqlText(node, node.text);
      else if (node.type === 'call') {
        const fn = node.childForFieldName('function'), args = node.childForFieldName('arguments');
        if (fn?.type === 'attribute' && fn.childForFieldName('attribute')?.text === 'offset' && args?.namedChildCount === 1)
          add(node, 'sqlalchemy.offset', orm.integerLiteral(args.namedChildren[0]), '.offset()');
        else if (fn?.type === 'identifier' && fn.text === 'Paginator' && /django\.core\.paginator/.test(root.text))
          add(node, 'django.Paginator', null, 'Django Paginator (LIMIT/OFFSET by page number)', 'low');
      } else if (node.type === 'subscript') {
        const value = node.childForFieldName('value'), slice = node.childForFieldName('subscript');
        if (slice?.type === 'slice' && value && isQueryset(value, node) && slice.child(0)?.type !== ':')
          add(node, 'django.slice', orm.integerLiteral(slice.child(0)), 'a queryset slice');
      }
      return;
    }
    if (['string', 'template_string'].includes(node.type)) sqlText(node, node.text);
    else if (node.type === 'call_expression') {
      const fn = node.childForFieldName('function'), args = node.childForFieldName('arguments');
      const method = fn?.type === 'member_expression' ? fn.childForFieldName('property')?.text : null;
      if ((method === 'offset' || method === 'skip') && args?.namedChildCount === 1)
        add(node, method === 'offset' ? 'builder.offset' : 'builder.skip', orm.integerLiteral(args.namedChildren[0]), `.${method}()`, method === 'skip' ? 'low' : 'medium');
      else if (method && FIND_METHODS.has(method)) {
        for (const arg of args?.namedChildren ?? []) {
          if (arg.type !== 'object') continue;
          const keyOf = prop => prop.type === 'pair' ? prop.childForFieldName('key')?.text?.replace(/^['"]|['"]$/g, '')
            : prop.type === 'shorthand_property_identifier' ? prop.text : null;
          for (const prop of arg.namedChildren) {
            const key = keyOf(prop);
            if (key !== 'skip' && key !== 'offset') continue;
            const value = prop.type === 'pair' ? prop.childForFieldName('value') : null;
            add(node, `option.${key}`, value ? orm.integerLiteral(value) : null, `the \`${key}\` option of ${method}()`, 'medium', key === 'skip' ? [REF, PRISMA_REF] : [REF]);
          }
        }
      }
    }
  });
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => orm.evaluateOrm(input, 'DB-39', inspect), inspect};
