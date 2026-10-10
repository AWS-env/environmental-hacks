'use strict';
/**
 * Owner B EXPLAIN collector for JavaScript / TypeScript projects (Prisma, node-postgres, or any client that can run SQL).
 *
 * Runs INSIDE THE CLIENT'S OWN CI / test run, against the client's own test database. The auditor never connects to a client
 * database and never executes client code; it only reads the artifact this module writes.
 *
 * For every distinct SELECT your tests issue it runs, in a transaction that is always rolled back:
 *   SET LOCAL statement_timeout = <ms>; EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) <your query>
 * EXPLAIN ANALYZE really executes the query, so only plain SELECT / read-only WITH statements are explained; INSERT, UPDATE,
 * DELETE, DDL and data-modifying CTEs never are. Query parameters are NOT stored (the artifact keeps `$1`-style placeholders).
 * Plans from a small CI database can differ from production: findings built from them are candidates.
 *
 * Prisma:   const prisma = new PrismaClient({log: [{emit: 'event', level: 'query'}]});
 *           const collector = new ExplainCollector(); attachPrisma(prisma, collector);
 *           ... run your tests ...; await collector.settle(); collector.writeContractInputs('explain-inputs', {repositoryId, commitSha});
 * pg:       const collector = new ExplainCollector(); await collector.recordWithPg(pool, sql, params);
 */
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const FORMAT = 'explain-plan-v1';
const DETECTOR_VERSION = '1.0.0';
const RULE_VERSION = 'plan-1';
const CHECK_CONTEXTS = { // thresholds are the auditor's configured choices
  'DB-43': {min_rows_examined: 10000, min_removed_ratio: 0.9},
};
const READ_ONLY = /^\s*\(?\s*(select|with)\b/i;
const WRITES = /\b(insert|update|delete|merge|alter|create|drop|truncate|grant|revoke|copy|call|do|vacuum|lock|set)\b|\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b|\binto\b\s+\w/i;
const PLACEHOLDERS = /\$\d+|\?|:\w+|%s/g;
const ROLLBACK = Symbol('rollback');
// EXPLAIN ANALYZE prints the real parameter values inside condition text (Filter: (status = 'refunded')): redact them.
const CONDITION_KEYS = ['Filter', 'Index Cond', 'Recheck Cond', 'Join Filter', 'Hash Cond', 'Merge Cond', 'One-Time Filter', 'TID Cond', 'Order By', 'Heap Fetches Cond'];
const redactText = text => text.replace(/'(?:[^']|'')*'/g, "'?'").replace(/(?<![\w.$"])-?\d+(?:\.\d+)?(?![\w"])/g, '?');
/** Deep copy of an EXPLAIN JSON array with literals removed from condition fields (the plan structure and numbers of rows stay). */
function redactPlan(plan) {
  const clone = JSON.parse(JSON.stringify(plan));
  (function walk(node) {
    for (const key of CONDITION_KEYS) if (typeof node[key] === 'string') node[key] = redactText(node[key]);
    for (const child of node.Plans ?? []) walk(child);
  })(clone[0].Plan);
  return clone;
}

/** Only plain reads: starts with SELECT/WITH and contains no write, DDL, locking or SELECT ... INTO keyword. */
function isExplainable(statement) {
  const text = statement.replace(/'(?:[^']|'')*'/g, "''"); // ignore keywords inside string literals
  return READ_ONLY.test(text) && !WRITES.test(text);
}
const normalise = statement => statement.replace(/\s+/g, ' ').trim();
/** First stack frame outside this module and node_modules: where the query was issued (informational). */
function callerLocator() {
  for (const line of (new Error().stack || '').split('\n').slice(1)) {
    const m = /\(?([^()\s]+):(\d+):\d+\)?$/.exec(line.trim());
    if (m && !m[1].includes('owner-b-explain') && !m[1].includes('node_modules') && !m[1].startsWith('node:')) return `${path.relative(process.cwd(), m[1]).split(path.sep).join('/')}:${m[2]}`;
  }
  return 'unknown';
}

class ExplainCollector {
  /** @param {{dialect?: string, maxQueries?: number, statementTimeoutMs?: number}} [options] */
  constructor(options = {}) {
    this.dialect = options.dialect ?? 'postgresql-16';
    this.maxQueries = options.maxQueries ?? 200;
    this.timeoutMs = options.statementTimeoutMs ?? 5000;
    this.queries = new Map();
    this.skipped = {not_read_only: 0, duplicate: 0, limit: 0, failed: 0};
    this.pending = new Set();
  }
  /**
   * @param {(explainSql: string, params: unknown[]) => Promise<unknown>} explain runs the EXPLAIN inside a rolled-back
   *   transaction (with SET LOCAL statement_timeout applied) and resolves with the EXPLAIN JSON (array)
   */
  async record(explain, statement, params = [], locator = callerLocator()) {
    const sql = normalise(statement);
    if (sql.toUpperCase().startsWith('EXPLAIN') || !isExplainable(sql)) {this.skipped.not_read_only++; return false;}
    const key = crypto.createHash('sha256').update(sql.replace(PLACEHOLDERS, '?')).digest('hex').slice(0, 16);
    if (this.queries.has(key)) {this.skipped.duplicate++; return false;}
    if (this.queries.size >= this.maxQueries) {this.skipped.limit++; return false;}
    this.queries.set(key, null); // reserve: concurrent identical queries explain once
    try {
      const raw = await explain(`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ${statement}`, params);
      const plan = redactPlan(typeof raw === 'string' ? JSON.parse(raw) : raw);
      this.queries.set(key, {query_id: key, locator, sql, plan});
      return true;
    } catch {
      this.queries.delete(key);
      this.skipped.failed++; // a query that cannot be explained (bad params, timeout) never breaks the test run
      return false;
    }
  }
  /** node-postgres: pool or client. Runs in its own transaction on a dedicated connection and always rolls back. */
  async recordWithPg(pool, statement, params = [], locator = callerLocator()) {
    return this.record(async (explainSql, p) => {
      const client = typeof pool.connect === 'function' && pool.totalCount !== undefined ? await pool.connect() : pool;
      try {
        await client.query('BEGIN');
        await client.query(`SET LOCAL statement_timeout = ${Math.trunc(this.timeoutMs)}`);
        const res = await client.query(explainSql, p);
        return res.rows[0]['QUERY PLAN'];
      } finally {
        await client.query('ROLLBACK').catch(() => {});
        if (client !== pool) client.release();
      }
    }, statement, params, locator);
  }
  /** Resolves once every explain started through attachPrisma has finished. */
  async settle() {await Promise.allSettled([...this.pending]);}

  artifact(query) {
    const nodes = [];
    (function walk(n) {nodes.push(n); for (const c of n.Plans ?? []) walk(c);})(query.plan[0].Plan);
    return {format: FORMAT, acquisition: {status: 'complete'}, dialect: this.dialect, analyzed: nodes.every(n => n['Actual Loops'] !== undefined),
      locator: query.locator, sql: query.sql, plan: query.plan};
  }
  /** One contract v1 input per plan check, one artifact source per query (scope `query:<id>`).
   * @param {{repositoryId: string, commitSha: string, scanId?: string}} options */
  contractInputs({repositoryId, commitSha, scanId}) {
    if (!/^[0-9a-f]{40}$/.test(commitSha)) throw new Error('commitSha must be the full lowercase 40-character git SHA');
    const queries = [...this.queries.values()].filter(Boolean);
    const sources = queries.map(q => ({source_id: `plan:${q.query_id}`, scope_id: `query:${q.query_id}`, kind: 'artifact', locator: q.locator, data: this.artifact(q)}));
    const scan = scanId ?? 'explain-' + crypto.createHash('sha256').update(JSON.stringify(queries.map(q => q.query_id).sort())).digest('hex').slice(0, 12);
    if (!sources.length) return [];
    return Object.entries(CHECK_CONTEXTS).map(([check, ctx]) => ({schema_version: '1.0', kind: 'input', repository_id: repositoryId, scan_id: scan, commit_sha: commitSha,
      check_id: check, detector_version: DETECTOR_VERSION, context: {rule_version: RULE_VERSION, mode: 'candidate', ...ctx}, scope: sources.map(s => s.scope_id), sources}));
  }
  /** @param {string} directory @param {{repositoryId: string, commitSha: string, scanId?: string}} options */
  writeContractInputs(directory, options) {
    fs.mkdirSync(directory, {recursive: true});
    return this.contractInputs(options).map(input => {
      const file = path.join(directory, `${input.check_id.toLowerCase()}-input.json`);
      fs.writeFileSync(file, JSON.stringify(input, null, 1));
      return file;
    });
  }
}

/**
 * Prisma: logs query events and explains each SELECT through prisma.$queryRawUnsafe inside an interactive transaction that is
 * rolled back. The client must be created with `log: [{emit: 'event', level: 'query'}]`.
 * @param {any} prisma @param {ExplainCollector} collector
 */
function attachPrisma(prisma, collector) {
  prisma.$on('query', event => {
    const params = (() => {try {return JSON.parse(event.params);} catch {return [];}})();
    const job = collector.record(async (explainSql, p) => {
      let plan;
      try {
        await prisma.$transaction(async tx => {
          await tx.$executeRawUnsafe(`SET LOCAL statement_timeout = ${Math.trunc(collector.timeoutMs)}`);
          const rows = await tx.$queryRawUnsafe(explainSql, ...p);
          plan = rows[0]['QUERY PLAN'];
          throw ROLLBACK; // always roll back
        });
      } catch (e) {if (e !== ROLLBACK) throw e;}
      return plan;
    }, event.query, params, callerLocator());
    collector.pending.add(job);
    job.finally(() => collector.pending.delete(job));
  });
  return collector;
}
module.exports = {ExplainCollector, attachPrisma, isExplainable, redactPlan, FORMAT, CHECK_CONTEXTS};
