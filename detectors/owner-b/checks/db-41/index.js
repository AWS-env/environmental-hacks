'use strict';
// DB-41 Synchronous DB APIs in async code. Python only (the JS/TS database drivers are Promise based).
// Sources (the taxonomy's SRC-50 asyncio page does not mention databases, see decisions D9): Django "Asynchronous support"
// (calling the ORM from a running event loop raises SynchronousOnlyOperation; wrap in sync_to_async), SQLAlchemy asyncio
// extension (the sync Session / engine must not be used in coroutines), SRC-50 (blocking calls stall every other task).
// Flag: inside an `async def`, (a) a blocking driver call: connect() of a configured sync driver module, or a method call
// (execute/fetch*/commit/rollback/...) on a name bound from one (or from a sync SQLAlchemy Session); (b) a Django ORM call that
// hits the database (get/first/create/save/...) or a plain `for` / list() / len() over a manager queryset.
// Exempt: calls inside sync_to_async / database_sync_to_async / run_in_executor / to_thread / run_sync arguments, nested sync
// `def` bodies, async variants (aget, acreate, async for), AsyncSession / create_async_engine.
// LIMITATION: driver modules come from context.sync_driver_modules; a connection created in another module and passed in is
// not recognised. Every finding is a candidate.
const orm = require('../../core/orm');

const REF = 'https://docs.djangoproject.com/en/stable/topics/async/';
const REF_SQLA = 'https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html';
const REF_ASYNCIO = 'https://docs.python.org/3/library/asyncio-dev.html';
const DJANGO_TERMINAL = new Set(['get', 'first', 'last', 'create', 'update', 'delete', 'save', 'count', 'exists', 'aggregate', 'get_or_create', 'update_or_create', 'bulk_create', 'bulk_update', 'in_bulk', 'latest', 'earliest']);
const DJANGO_CHAIN = new Set(['all', 'filter', 'exclude', 'order_by', 'values', 'values_list', 'select_related', 'prefetch_related', 'annotate', 'distinct', 'only', 'defer', 'using', 'reverse']);
const DRIVER_METHODS = new Set(['execute', 'executemany', 'fetchone', 'fetchall', 'fetchmany', 'commit', 'rollback', 'cursor', 'query', 'add', 'add_all', 'merge', 'flush', 'scalars', 'scalar', 'begin']);
const SQLA_SESSION = new Set(['Session', 'sessionmaker', 'scoped_session']);
const WRAPPERS = new Set(['sync_to_async', 'database_sync_to_async', 'run_in_executor', 'to_thread', 'run_sync', 'run_in_threadpool']);

/** Dotted text of an identifier/attribute chain, or null when it contains calls or subscripts. */
function dotted(node) {
  if (!node) return null;
  if (node.type === 'identifier') return node.text;
  if (node.type === 'attribute') {
    const base = dotted(node.childForFieldName('object'));
    return base ? `${base}.${node.childForFieldName('attribute').text}` : null;
  }
  return null;
}
const nameOf = item => item.type === 'aliased_import' ? item.childForFieldName('name').text : item.text;
const aliasOf = item => item.type === 'aliased_import' ? item.childForFieldName('alias').text : item.text;
/** Sync-driver imports of this file. modules: alias -> module; connects: names that are a driver's connect(); sessions: SQLAlchemy sync Session factories. */
function imports(root, drivers) {
  const modules = new Map(), connects = new Set(), sessions = new Set();
  const isDriver = name => drivers.some(d => name === d || name.startsWith(d + '.'));
  for (const n of root.namedChildren) {
    if (n.type === 'import_statement') {
      for (const item of n.namedChildren) if (isDriver(nameOf(item))) modules.set(aliasOf(item), nameOf(item));
    } else if (n.type === 'import_from_statement') {
      const moduleNode = n.childForFieldName('module_name'), mod = moduleNode?.text ?? '';
      for (const item of n.namedChildren.filter(c => c.id !== moduleNode?.id)) {
        const name = nameOf(item), alias = aliasOf(item);
        if (isDriver(mod) && name === 'connect') connects.add(alias);
        else if (isDriver(`${mod}.${name}`)) modules.set(alias, `${mod}.${name}`);
        if (/^sqlalchemy(\.orm)?$/.test(mod) && SQLA_SESSION.has(name)) sessions.add(alias);
      }
    }
  }
  return {modules, connects, sessions};
}
const isAsyncDef = fn => fn.type === 'function_definition' && fn.children.some(c => c.type === 'async');
/** Methods of a call chain (innermost first) and its base node, for `Model.objects.filter(...).get(...)`. */
function managerChain(expr) {
  let cur = expr;
  const methods = [];
  while (cur?.type === 'call' && cur.childForFieldName('function')?.type === 'attribute') {
    methods.unshift(cur.childForFieldName('function').childForFieldName('attribute').text);
    cur = cur.childForFieldName('function').childForFieldName('object');
  }
  return {methods, base: cur};
}
const isManagerQuery = (expr, extra = []) => {
  if (expr?.type === 'attribute' && /\.objects$/.test(expr.text)) return true;
  const {methods, base} = managerChain(expr);
  return methods.length > 0 && /\.objects$/.test(base?.text ?? '') && methods.every(m => DJANGO_CHAIN.has(m) || extra.includes(m));
};

function inspect({source, lang, root, input}) {
  orm.requireValue(lang === 'python', 'DB-41 applies to Python files only');
  const drivers = input.context.sync_driver_modules;
  orm.requireValue(Array.isArray(drivers) && drivers.length > 0 && drivers.every(d => /^[\w.]+$/.test(d)), 'Missing/invalid context.sync_driver_modules');
  const {modules, connects, sessions} = imports(root, drivers);
  const usesDjango = /\b(from|import)\s+django\b/.test(root.text);
  const isConnect = callee => !!callee && (connects.has(callee) || (modules.has(callee.split('.')[0]) && callee.endsWith('.connect')));
  /** A value expression that evaluates to a sync connection / session / cursor, or a method result on one. */
  const fromSync = (value, taint) => {
    let v = value;
    for (let guard = 0; v && guard < 20; guard++) {
      if (v.type === 'call') {
        const fn = v.childForFieldName('function'), callee = dotted(fn);
        if (isConnect(callee) || (callee && sessions.has(callee))) return true;
        v = fn;
      } else if (v.type === 'attribute') v = v.childForFieldName('object');
      else return v.type === 'identifier' && taint.has(v.text);
    }
    return false;
  };
  const bindTaint = (scope, taint) => {
    for (let pass = 0; pass < 3; pass++) orm.walk(scope, n => {
      let target = null, value = null;
      if (n.type === 'assignment') {target = n.childForFieldName('left'); value = n.childForFieldName('right');}
      else if (n.type === 'as_pattern') {value = n.namedChildren[0]; target = n.childForFieldName('alias');}
      if (target?.type === 'as_pattern_target') target = target.namedChildren[0];
      if (target?.type === 'identifier' && value && fromSync(value, taint)) taint.add(target.text);
    });
  };
  const moduleTaint = new Set();
  bindTaint(root, moduleTaint);
  const out = [];
  const add = (node, api, text, refs) => out.push(orm.finding(`${orm.enclosing(node)}:${api}`,
    `Blocking database call in a coroutine (${text}): it stalls the event loop, so every other task waits for the database.`,
    'Use the async variant (Django aget/acreate/async for, an async driver such as asyncpg or psycopg AsyncConnection, SQLAlchemy AsyncSession) or run the call with sync_to_async / asyncio.to_thread.',
    [orm.nodeLines(source, node)], refs, 'medium'));
  const wrapped = (node, fn) => {
    for (let n = node.parent; n && n.id !== fn.id; n = n.parent)
      if (n.type === 'call' && WRAPPERS.has((dotted(n.childForFieldName('function')) ?? '').split('.').at(-1))) return true;
    return false;
  };
  orm.walk(root, fn => {
    if (!isAsyncDef(fn)) return;
    const taint = new Set(moduleTaint);
    bindTaint(fn, taint);
    const visit = node => {
      // Nested sync defs / classes run when called, not when defined: not part of this coroutine.
      if (node.id !== fn.id && ['function_definition', 'class_definition'].includes(node.type)) return;
      if (node.type === 'call' && !wrapped(node, fn)) {
        const fnNode = node.childForFieldName('function'), callee = dotted(fnNode);
        if (isConnect(callee)) add(node, 'sync-driver.connect', `${callee}()`, [REF_ASYNCIO, REF_SQLA]);
        else if (fnNode?.type === 'attribute') {
          const method = fnNode.childForFieldName('attribute').text, base = dotted(fnNode.childForFieldName('object'));
          if (DRIVER_METHODS.has(method) && fromSync(fnNode.childForFieldName('object'), taint)) add(node, `sync-driver.${method}`, `${base ?? 'connection'}.${method}()`, [REF_ASYNCIO, REF_SQLA]);
          else if (usesDjango && DJANGO_TERMINAL.has(method) && isManagerQuery(fnNode.childForFieldName('object'))) add(node, `django.${method}`, `Django ORM .${method}()`, [REF]);
        } else if (usesDjango && ['list', 'tuple', 'set', 'sorted', 'len'].includes(callee ?? '') && isManagerQuery(node.childForFieldName('arguments')?.namedChildren[0]))
          add(node, `django.${callee}`, `${callee}() over a queryset`, [REF]);
      } else if (node.type === 'for_statement' && usesDjango && !node.children.some(c => c.type === 'async') && !wrapped(node, fn)
        && isManagerQuery(node.childForFieldName('right'))) add(node.childForFieldName('right'), 'django.for', 'a plain for over a queryset (use async for)', [REF]);
      for (const c of node.namedChildren) visit(c);
    };
    visit(fn);
  });
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => orm.evaluateOrm(input, 'DB-41', inspect), inspect};
