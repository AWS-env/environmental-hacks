'use strict';
// DB-43 Full sequential scan (no usable filter/index). Evidence: a client-produced PostgreSQL EXPLAIN (ANALYZE, FORMAT JSON) plan.
// Source: SRC-05 PostgreSQL "Using EXPLAIN" (a Seq Scan with a WHERE shows the condition as a filter and "Rows Removed by Filter";
// a table of one disk page is normally seq-scanned, which is fine). The row's own wording ("no usable filter/index") is
// approximated by the plan fact: a Seq Scan whose filter discards most of the rows it reads.
// Flag: Seq Scan / Parallel Seq Scan with a Filter where rows examined ((Actual Rows + Rows Removed by Filter) x Actual Loops)
// >= context.min_rows_examined and the removed share >= context.min_removed_ratio (both OUR configured choices, not sourced).
// Exempt: small scans (below min_rows_examined; the source-backed one-page exemption is approximated by rows examined because a
// plan has no page count), scans that keep most rows (the planner is right to read the whole table), scans without a filter.
// LIMITATION: plans from small CI databases misjudge production; plain EXPLAIN (no ANALYZE) is not evaluated.
const plans = require('../../core/plans');

const REF = 'https://www.postgresql.org/docs/current/using-explain.html';

function inspect({source, nodes, input}) {
  const minRows = plans.number(input.context.min_rows_examined, 'min_rows_examined', 1);
  const minRatio = plans.number(input.context.min_removed_ratio, 'min_removed_ratio', 0);
  const out = [];
  const ordinal = new Map();
  for (const {node} of nodes) {
    if (!plans.isSeqScan(node) || node.Filter === undefined) continue;
    const examined = plans.rowsExamined(node), removed = (node['Rows Removed by Filter'] ?? 0) * (node['Actual Loops'] ?? 1);
    const ratio = examined > 0 ? removed / examined : 0;
    if (examined < minRows || ratio < minRatio) continue;
    const rel = plans.relationName(node);
    ordinal.set(rel, (ordinal.get(rel) ?? 0) + 1);
    out.push(plans.finding(`seq-scan:${rel}`,
      `Sequential scan of ${rel} read ${examined.toLocaleString('en-US')} rows and discarded ${removed.toLocaleString('en-US')} (${Math.round(ratio * 100)}%) with the filter ${node.Filter}.`,
      'If this query runs often on a large table, check for an index on the filtered column(s) (or a more selective condition). Verify against production table sizes before adding an index: indexes cost writes and storage.',
      [plans.cite(source, 'sql'), plans.cite(source, 'plan')], [REF], 'medium'));
  }
  return out.sort((a, b) => a.identity.localeCompare(b.identity));
}
module.exports = {evaluate: input => plans.evaluatePlans(input, 'DB-43', inspect), inspect};
