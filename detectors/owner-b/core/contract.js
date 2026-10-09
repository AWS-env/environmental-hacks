'use strict';
const crypto = require('node:crypto');
const Ajv = require('ajv/dist/2020').default;
const addFormats = require('ajv-formats').default;
const schema = require('../../../shared/contracts/detector.schema.json');
const checkIds = new Set(require('../../../docs/taxonomy/checks.json').map(c => c.key));
const ajv = new Ajv({strict: false, allErrors: true});
addFormats(ajv);
const shape = ajv.compile(schema);
const identityFields = ['schema_version','repository_id','scan_id','commit_sha','check_id','detector_version','context','scope'];
const units = {energy:'kWh',emissions:'kgCO2e',cpu_time:'seconds',data_transfer:'bytes',requests:'count',tokens:'count',log_volume:'bytes'};

/** @param {unknown} value @returns {string} */
function canonical(value) {
  if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('Numbers must be finite');
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k)+':'+canonical(value[k])).join(',') + '}';
  const result = JSON.stringify(value);
  if (result === undefined) throw new Error('Not a JSON value');
  return result;
}
function requireCondition(condition, message) { if (!condition) throw new Error(message); }
function fingerprint(repository, check, scope, identity) {
  return crypto.createHash('sha256').update(canonical([repository,check,scope,identity]), 'utf8').digest('hex');
}
/** @param {any} payload */
function validate(payload) {
  canonical(payload);
  requireCondition(shape(payload), 'Schema validation failed: '+ajv.errorsText(shape.errors));
  requireCondition(checkIds.has(payload.check_id), 'Unknown taxonomy check_id');
  const scope = new Set(payload.scope);
  if (payload.kind === 'input') {
    requireCondition(new Set(payload.sources.map(s=>s.source_id)).size === payload.sources.length, 'Duplicate source_id');
    requireCondition(payload.sources.every(s=>scope.has(s.scope_id)), 'Source outside requested scope');
    return;
  }
  const evaluated = new Set(payload.coverage.evaluated_scope);
  requireCondition([...evaluated].every(s=>scope.has(s)), 'Evaluated scope exceeds requested scope');
  if (payload.status === 'completed') requireCondition(evaluated.size === scope.size, 'Completed result must evaluate all requested scope');
  else if (payload.status === 'partial') requireCondition(evaluated.size>0 && evaluated.size<scope.size, 'Partial result needs proper nonempty scope subset');
  else requireCondition(!evaluated.size && !payload.findings.length && !payload.measurements.length, 'Unavailable/error cannot certify evidence');
  if (payload.status !== 'completed') requireCondition(payload.coverage.limitations.length, 'Incomplete result must explain why');
  const seen = new Set();
  for (const f of payload.findings) {
    const expected = fingerprint(payload.repository_id,payload.check_id,f.scope_id,f.identity);
    requireCondition(evaluated.has(f.scope_id), 'Finding outside evaluated scope');
    requireCondition(expected === f.fingerprint && !seen.has(expected), 'Incorrect or duplicate fingerprint');
    seen.add(expected);
  }
  const measurements = new Set();
  for (const m of payload.measurements) {
    requireCondition(units[m.metric] === m.unit, 'Metric/unit mismatch');
    const start=Date.parse(m.window.start),end=Date.parse(m.window.end);
    requireCondition(/(Z|[+-]\d\d:\d\d)$/.test(m.window.start) && /(Z|[+-]\d\d:\d\d)$/.test(m.window.end) && start<end, 'Invalid measurement window');
    const key=canonical([m.metric,m.allocation_key,start,end]);
    requireCondition(!measurements.has(key),'Duplicate measurement allocation');
    measurements.add(key);
  }
}
/** Exact evidence validation runs inside the deployed handler before publication/storage.
 * @param {any} input @param {any} result */
function validatePair(input, result) {
  validate(input); validate(result);
  requireCondition(input.kind==='input' && result.kind==='result', 'Expected input/result pair');
  requireCondition(identityFields.every(k=>canonical(input[k])===canonical(result[k])), 'Input/result identity or context mismatch');
  const sources=new Map(input.sources.map(s=>[s.source_id,s]));
  const sourced=new Set(input.sources.map(s=>s.scope_id));
  requireCondition(result.coverage.evaluated_scope.every(s=>sourced.has(s)), 'Evaluated scope has no evidence source');
  for (const f of result.findings) for (const e of f.evidence) {
    const s = sources.get(e.source_id);
    requireCondition(s && s.scope_id===f.scope_id && s.kind===e.kind && s.locator===e.locator, 'Evidence source/locator/scope mismatch');
    if (s.kind==='static') {
      const quote=splitLines(e.value);
      requireCondition(quote.length && canonical(splitLines(s.content).slice(e.line_start-1,e.line_start-1+quote.length))===canonical(quote), 'Evidence does not match source lines');
    } else requireCondition(Object.hasOwn(s.data,e.field) && canonical(s.data[e.field])===canonical(e.value), 'Evidence does not match supplied data field');
  }
  for (const m of result.measurements) for (const id of m.provenance.source_ids) {
    const s=sources.get(id);
    requireCondition(s && result.coverage.evaluated_scope.includes(s.scope_id), 'Measurement source not evaluated');
  }
}
/** Match Python str.splitlines for JSON text (including CR, Unicode separators).
 * @param {string} text */
function splitLines(text) {
  if (!text) return [];
  const lines=text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/);
  if (lines.at(-1)==='') lines.pop();
  return lines;
}
/** @param {any} input */
function envelope(input) {
  const result={kind:'result',status:'unavailable',coverage:{evaluated_scope:[],limitations:[]},findings:[],measurements:[]};
  for (const key of identityFields) result[key]=JSON.parse(canonical(input[key]));
  return result;
}
/** @param {any} previous @param {any} current */
function compare(previous,current) {
  validate(previous); validate(current);
  requireCondition(previous.kind==='result' && current.kind==='result' && previous.repository_id===current.repository_id && previous.check_id===current.check_id,'Comparison requires same repository/check results');
  const compatible=['schema_version','detector_version','context'].every(k=>canonical(previous[k])===canonical(current[k]));
  const comparable=compatible && previous.status==='completed' && current.status==='completed' && canonical([...previous.scope].sort())===canonical([...current.scope].sort());
  const before=new Set(previous.findings.map(f=>f.fingerprint)),after=new Set(current.findings.map(f=>f.fingerprint));
  const gone=[...before].filter(f=>!after.has(f)).sort();
  return {comparable,reason:comparable?'Same detector, context and completed scope':'Incomplete coverage or changed detector/context/scope',persisting:compatible?[...before].filter(f=>after.has(f)).sort():[],new:comparable?[...after].filter(f=>!before.has(f)).sort():[],no_longer_detected:comparable?gone:[],unknown:!compatible?[...before].sort():comparable?[]:gone};
}
module.exports={validate,validatePair,fingerprint,canonical,envelope,splitLines,compare};
