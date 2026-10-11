'use strict';
// Committed cases for the static ORM checks. kind: positive | negative | exception | missing | malformed | boundary.
// `identities` lists the expected finding identities (semantic anchors, no line numbers); `status` the coverage status.
const cases = {
  'DB-39': [
    {id: 1, kind: 'positive', name: 'Django queryset slice from a request-derived page', path: 'app/views.py',
      content: 'def article_page(page):\n    return Article.objects.filter(live=True)[page * 10:page * 10 + 10]\n', identities: ['article_page:django.slice']},
    {id: 2, kind: 'positive', name: 'SQLAlchemy .offset() with a variable', path: 'app/repo.py',
      content: 'class Repo:\n    def page(self, n):\n        return session.query(User).order_by(User.id).offset(n * 20).limit(20).all()\n', identities: ['Repo.page:sqlalchemy.offset']},
    {id: 3, kind: 'positive', name: 'Prisma findMany skip with a variable', path: 'src/users.ts',
      content: 'export async function listUsers(page: number) {\n  return prisma.user.findMany({ skip: page * 10, take: 10 });\n}\n', identities: ['listUsers:option.skip']},
    {id: 4, kind: 'positive', name: 'Sequelize findAll offset and Knex .offset()', path: 'src/repo.js',
      content: 'async function a(n) { return Post.findAll({ offset: n, limit: 10 }); }\nfunction b(n) { return knex("posts").offset(n).limit(10); }\n', identities: ['a:option.offset', 'b:builder.offset']},
    {id: 5, kind: 'positive', name: 'raw SQL OFFSET placeholder (python) and LIMIT a,b (ts)', path: 'src/raw.py',
      content: 'def f(cur, off):\n    cur.execute("SELECT id FROM t ORDER BY id LIMIT 10 OFFSET %s", (off,))\n', identities: ['f:raw-sql.offset']},
    {id: 6, kind: 'positive', name: 'two raw queries in one function get stable ordinals', path: 'src/two.ts',
      content: 'async function f(db, o) {\n  await db.query("SELECT a FROM t OFFSET $1", [o]);\n  await db.query("SELECT b FROM t OFFSET $1", [o]);\n}\n', identities: ['f:raw-sql.offset', 'f:raw-sql.offset#2']},
    {id: 7, kind: 'negative', name: 'seek pagination has no offset', path: 'src/seek.ts',
      content: 'export const next = (last: number) => prisma.user.findMany({ where: { id: { gt: last } }, orderBy: { id: "asc" }, take: 10 });\n', identities: []},
    {id: 8, kind: 'negative', name: 'plain list slice is not a queryset', path: 'app/util.py',
      content: 'def head(items, n):\n    return items[n:n + 10]\n', identities: []},
    {id: 9, kind: 'negative', name: 'SQL without OFFSET', path: 'app/q.py',
      content: 'Q = "SELECT id FROM t ORDER BY id LIMIT 10"\n', identities: []},
    {id: 10, kind: 'exception', name: 'skip: 0 and a small literal offset are exempt', path: 'src/first.ts',
      content: 'export const a = () => prisma.user.findMany({ skip: 0, take: 5 });\nexport const b = () => knex("t").offset(5);\n', identities: []},
    {id: 11, kind: 'boundary', name: 'literal offset exactly at the minimum is flagged', path: 'src/edge.js',
      content: 'function f() { return knex("t").offset(100); }\n', identities: ['f:builder.offset']},
    {id: 12, kind: 'boundary', name: 'literal offset one below the minimum is exempt', path: 'src/edge2.js',
      content: 'function f() { return knex("t").offset(99); }\n', identities: []},
    {id: 13, kind: 'missing', name: 'no source for the requested scope is unavailable, never clean', path: 'src/none.ts', content: null, identities: [], status: 'unavailable'},
    {id: 14, kind: 'malformed', name: 'syntax error file is not evaluated', path: 'app/bad.py', content: 'def f(:\n    pass\n', identities: [], status: 'unavailable'},
    {id: 15, kind: 'malformed', name: 'unsupported language is not evaluated', path: 'app/Thing.php', content: '<?php echo 1;', identities: [], status: 'unavailable'},
    {id: 16, kind: 'positive', name: 'Django queryset held in a variable then sliced', path: 'app/paged.py',
      content: 'def page(request, n):\n    qs = Item.objects.filter(live=True).order_by("id")\n    return qs[n * 25:(n + 1) * 25]\n', identities: ['page:django.slice']},
    {id: 17, kind: 'negative', name: 'slicing a materialised list(qs) is not a queryset offset', path: 'app/listed.py',
      content: 'def page(n):\n    rows = list(Item.objects.all())\n    return rows[n:n + 10]\n', identities: []},
    {id: 18, kind: 'boundary', name: 'KNOWN LIMIT: skip inside a wrapper-method options object (listAndCount) is not recognised', path: 'src/svc.ts',
      content: 'export async function list(svc, page: number) {\n  return svc.listAndCount({}, { skip: page * 20, take: 20 });\n}\n', identities: []},
    {id: 19, kind: 'negative', name: 'bare {offset, limit} response metadata object is not a query', path: 'src/resp.ts',
      content: 'export function respond(items, offset: number) {\n  return res.json({ items, offset, limit: 10 });\n}\n', identities: []},
    {id: 20, kind: 'negative', name: 'offset on a non-find callee is ignored', path: 'src/meta.ts',
      content: 'export const x = (o: number) => build({ offset: o });\n', identities: []},
  ],
};
// Per-check context (every evaluation-affecting threshold is explicit; there are no silent defaults).
const contexts = {
  'DB-39': {min_literal_offset: 100},
};
/** Contract-v1 input for one case. */
function buildInput(check, c, ctx = {}) {
  const scope = 'file:' + c.path;
  return {schema_version: '1.0', kind: 'input', repository_id: 'github:test/orm', scan_id: 'scan-1', commit_sha: 'a'.repeat(40), check_id: check, detector_version: '1.0.0',
    context: {rule_version: 'orm-1', mode: 'candidate', ...contexts[check], ...c.ctx, ...ctx}, scope: [scope],
    sources: c.content === null ? [] : [{source_id: 'src-1', scope_id: scope, kind: 'static', locator: c.path, content: c.content}]};
}
module.exports = {cases, contexts, buildInput};
