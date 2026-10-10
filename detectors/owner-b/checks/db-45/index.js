'use strict';
// DB-45 Sort spilling to disk / large external sort. Evidence: a client-produced PostgreSQL EXPLAIN (ANALYZE, FORMAT JSON) plan.
// Source: SRC-05 PostgreSQL "Using EXPLAIN" (the Sort node reports the method used, "in particular, whether in-memory or
// on-disk", and the memory or disk space needed). SRC-06 (index-ordered output can omit the sort) motivates the recommendation.
// Flag: a Sort node whose `Sort Space Type` is `Disk` and `Sort Space Used` >= context.min_sort_space_kb (OUR configured floor,
// 0 means any disk sort).
// Exempt: in-memory sorts (quicksort, top-N heapsort).
// LIMITATION: needs ANALYZE (plain EXPLAIN has no Sort Method, so it is reported as not evaluated); whether a disk sort matters
// depends on how often the query runs and on the server's work_mem, which the plan does not carry.
const plans = require('../../core/plans');

const REF = 'https://www.postgresql.org/docs/current/using-explain.html';
const REF_INDEX = 'https://use-the-index-luke.com/sql/sorting-grouping/indexed-order-by';

function inspect({source, nodes, input}) {
  const minKb = plans.number(input.context.min_sort_space_kb, 'min_sort_space_kb', 0);
  const out = [];
  for (const {node} of nodes) {
    if (node['Node Type'] !== 'Sort' || node['Sort Space Type'] !== 'Disk') continue;
    const kb = node['Sort Space Used'] ?? 0;
    if (kb < minKb) continue;
    const keys = (node['Sort Key'] ?? []).join(', ') || 'unknown key';
    out.push(plans.finding(`sort-disk:${keys}`,
      `Sort on (${keys}) spilled to disk (${node['Sort Method'] ?? 'external sort'}, ${kb.toLocaleString('en-US')} kB): the rows did not fit in work_mem.`,
      'Reduce the rows being sorted (filter earlier, select fewer columns), provide an index that returns rows already in order, or raise work_mem for this query if the server has memory to spare.',
      [plans.cite(source, 'sql'), plans.cite(source, 'plan')], [REF, REF_INDEX], 'medium'));
  }
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => plans.evaluatePlans(input, 'DB-45', inspect), inspect};
