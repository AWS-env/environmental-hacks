'use strict';
// DB-13 Inefficient updating (row-by-row). Static candidate for Python, JavaScript and TypeScript.
// Sources: SRC-02 (N separate queries to update N records), SRC-51 Django optimization (bulk_create/bulk_update/update()
// preferred; caveat: bulk methods do not call save()/delete() of individual instances, so per-row signals/overrides may be needed).
// Flag: a database write call inside a loop (for / comprehension / forEach-map-reduce callback) whose receiver or argument
// depends on the loop variable (or a name derived from it inside the loop body).
// Exempt: bulk APIs (bulk_create/bulk_update/executemany/createMany/updateMany/insertMany/bulkCreate/add_all), loops over a
// literal collection or range() of at most context.max_literal_iterations items (our configured choice, not sourced),
// writes that do not depend on the loop variable, loops stepping by more than one (`i += batchSize`, range(0, n, step)) which
// already write one batch per iteration.
// LIMITATION: per-row transaction or signal semantics may legitimately forbid batching; loop size is a runtime fact, so every
// finding is a candidate. Receivers are recognised by name (session/db/prisma/repo...), so renamed handles are missed.
const orm = require('../../core/orm');
const loops = require('../../core/loops');

const {PY_DB_RECEIVER, JS_DB_RECEIVER, ONE_OFF_PATH, DML, firstArgString} = loops;
const REF = 'https://docs.djangoproject.com/en/stable/ref/models/querysets/#bulk-create';
const REF_DOC = 'https://docs.djangoproject.com/en/stable/topics/db/optimization/';
const JS_WRITE = new Set(['create', 'update', 'upsert', 'delete', 'save', 'insert', 'remove', 'destroy', 'softRemove', 'del']);
const PY_ORM_WRITE = new Set(['create', 'update', 'delete', 'update_or_create', 'get_or_create']);
// TypeORM / MikroORM managers and repositories: create() only builds an in-memory entity, the write is save/insert/flush.
const JS_INMEMORY_CREATE = /\b(repo|repository|manager|entityManager|em|dataSource)\b|(Repository|Repo)\b/;

/** Classify a call as a per-row write. @returns {{api:string, confidence:string}|null} */
function writeKind(call, lang) {
  const fn = call.childForFieldName('function'), args = call.childForFieldName('arguments');
  if (lang === 'python') {
    if (fn?.type !== 'attribute') return null;
    const method = fn.childForFieldName('attribute')?.text, receiver = fn.childForFieldName('object')?.text ?? '';
    if (PY_ORM_WRITE.has(method) && (/\.objects\b/.test(receiver) || (['update', 'delete'].includes(method) && /\.(filter|exclude|all)\(/.test(receiver))))
      return {api: `django.${method}`, confidence: method.endsWith('_or_create') ? 'low' : 'medium'};
    if (method === 'save' && !(args?.namedChildren ?? []).some(a => a.type !== 'keyword_argument')) return {api: 'django.save', confidence: 'low'};
    if (['add', 'merge', 'delete'].includes(method) && PY_DB_RECEIVER.test(receiver)) return {api: `session.${method}`, confidence: 'medium'};
    if (method === 'execute' && PY_DB_RECEIVER.test(receiver) && DML.test(firstArgString(args) ?? '')) return {api: 'raw-sql.execute', confidence: 'medium'};
    return null;
  }
  if (fn?.type !== 'member_expression') return null;
  const method = fn.childForFieldName('property')?.text, receiver = fn.childForFieldName('object')?.text ?? '';
  if (JS_WRITE.has(method) && JS_DB_RECEIVER.test(receiver) && !(method === 'create' && JS_INMEMORY_CREATE.test(receiver)))
    return {api: `orm.${method}`, confidence: 'medium'};
  if (['query', 'execute', 'run', 'raw'].includes(method) && JS_DB_RECEIVER.test(receiver) && DML.test(firstArgString(args) ?? '')) return {api: 'raw-sql.query', confidence: 'medium'};
  return null;
}
function inspect({source, lang, root, input}) {
  const max = input.context.max_literal_iterations;
  orm.requireValue(Number.isInteger(max) && max >= 0, 'Missing/invalid context.max_literal_iterations');
  const out = [];
  orm.walk(root, node => {
    if (node.type !== (lang === 'python' ? 'call' : 'call_expression')) return;
    const kind = writeKind(node, lang);
    if (!kind) return;
    const loop = loops.enclosingLoop(node, lang);
    if (!loop || loops.smallLiteral(loop, max) || loops.steppedLoop(loop)) return;
    const used = loops.names(node);
    if (![...loops.tainted(loop)].some(x => used.has(x))) return;
    const header = orm.line(source, loop.header.startPosition.row + 1), call = orm.nodeLines(source, node);
    out.push(orm.finding(`${orm.enclosing(node)}:${kind.api}-in-${loop.kind}`,
      `Row-by-row write (${kind.api}) inside a ${loop.kind} loop: one database round trip per item instead of one batched statement.`,
      'If the loop runs over many items, batch the writes (bulk_create/bulk_update or update() in Django, add_all/executemany in SQLAlchemy, createMany/updateMany in Prisma, insert([...]) in Knex). Keep per-row writes where save() overrides, signals or per-row transactions are required.',
      header.line_start === call.line_start ? [call] : [header, call], [REF_DOC, REF], ONE_OFF_PATH.test(source.locator) ? 'low' : kind.confidence));
  });
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => orm.evaluateOrm(input, 'DB-13', inspect), inspect};
