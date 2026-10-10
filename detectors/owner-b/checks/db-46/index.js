'use strict';
// DB-46 Nested-loop inner child executed per outer row. Evidence: a client-produced PostgreSQL EXPLAIN (ANALYZE, FORMAT JSON) plan.
// Sources: SRC-05 PostgreSQL "Using EXPLAIN" (a nested-loop join runs its inner child once for each row from the outer child;
// `loops` is the number of executions; reported times and rows are per-execution averages), SRC-06 (nested loops perform well when
// the driving query returns few rows and the inner side is indexed).
// Flag: a Nested Loop whose INNER child is itself a Seq Scan / Parallel Seq Scan executed `Actual Loops` >= context.min_inner_loops
// times and examining >= context.min_rows_examined rows in total (both OUR configured choices, not sourced).
// Exempt: inner Index / Bitmap scans (the efficient shape), Materialize / Memoize wrappers (the scan below them runs once), small
// outer sets (loops below the floor).
// Real plans showed the planner flipping join order and inserting Materialize / Bitmap nodes, which is why the rule looks at the
// inner child node itself and not at "any nested loop".
// LIMITATION: needs ANALYZE for the real loop count; plans from small CI databases misjudge production.
const plans = require('../../core/plans');

const REF = 'https://www.postgresql.org/docs/current/using-explain.html';
const REF_LOOPS = 'https://use-the-index-luke.com/sql/join/nested-loops-join-n1-problem';

function inspect({source, nodes, input}) {
  const minLoops = plans.number(input.context.min_inner_loops, 'min_inner_loops', 1);
  const minRows = plans.number(input.context.min_rows_examined, 'min_rows_examined', 1);
  const out = [];
  for (const {node, parent} of nodes) {
    if (!plans.isSeqScan(node) || node['Parent Relationship'] !== 'Inner' || parent?.['Node Type'] !== 'Nested Loop') continue;
    const loops = node['Actual Loops'] ?? 1, examined = plans.rowsExamined(node);
    if (loops < minLoops || examined < minRows) continue;
    const rel = plans.relationName(node);
    out.push(plans.finding(`nested-loop-inner-seq-scan:${rel}`,
      `Nested loop re-scans ${rel} sequentially ${loops.toLocaleString('en-US')} times (${examined.toLocaleString('en-US')} rows examined in total): the inner side has no usable index for the join condition.`,
      'Index the join column on the inner table, or let the planner choose a hash/merge join (refresh statistics, check that the join columns have the same type). Verify against production data sizes first.',
      [plans.cite(source, 'sql'), plans.cite(source, 'plan')], [REF, REF_LOOPS], 'medium'));
  }
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => plans.evaluatePlans(input, 'DB-46', inspect), inspect};
