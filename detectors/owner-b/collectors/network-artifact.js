'use strict';
const fs=require('node:fs'),n=require('../core/network'),{normalizeHar}=require('../normalization/har'),{validate}=require('../core/contract');
const {readJson}=require('./artifact-json');
/** Local data-only exporter; never loads a module or executes code from the client repository.
 * @param {string} file */
/** @param {any} input @param {any} har @param {any} sidecar */
function attachHar(input,har,sidecar){
 validate(input);n.requireValue(input.check_id==='NET-11'&&input.scope.length===1,'HAR export requires one NET-11 scope');
 n.requireValue(sidecar.repository_id===input.repository_id&&sidecar.commit_sha===input.commit_sha,'HAR source snapshot mismatch');
 n.windowBounds(sidecar.window,3600);
 n.requireValue(['client','auditor','synthetic'].includes(sidecar.origin),'HAR capture origin');
 const data={format:'network-evidence-v1',repository_id:input.repository_id,commit_sha:input.commit_sha,route:sidecar.route,capture:{tool:'chrome-har',version:'1.2',origin:sidecar.origin},acquisition:{status:'complete',...sidecar.window},browser:normalizeHar(har,sidecar)};
 const result={...input,sources:[{source_id:'browser-capture',scope_id:input.scope[0],kind:'artifact',locator:'artifact:sanitized-har-with-reviewed-sidecar',data}]};validate(result);return result;
}
if(require.main===module){
 const [inputFile,harFile,sidecarFile,outputFile]=process.argv.slice(2);n.requireValue(outputFile,'Usage: network-artifact.js input.json capture.har sidecar.json output.json');
 fs.writeFileSync(outputFile,JSON.stringify(attachHar(readJson(inputFile),readJson(harFile),readJson(sidecarFile)),null,2)+'\n');
}
module.exports={readJson,attachHar};
