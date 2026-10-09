'use strict';
const {variable}=require('./local-flow');
const DB_VERSIONS={pdo:'8.2',joomla:'4.4'};
const CACHE_VERSIONS={psr16:'3.0',memcached:'3.2',psr6:'3.0'};
/** @param {any} node */
function method(node) {
  if(node?.kind!=='call'||node.what?.kind!=='propertylookup'||node.what.offset?.kind!=='identifier')return null;
  const receiver=variable(node.what.what);
  return receiver?{receiver,name:node.what.offset.name.toLowerCase(),node,args:node.arguments||[]}:null;
}
/** @param {any} node @param {any} context */
function dbCall(node,context) {
  const m=method(node),model=m&&context.db_apis[m.receiver];
  if(!model||DB_VERSIONS[model.family]!==model.version)return null;
  if(model.family==='pdo' && m.name==='query')return {...m,stage:'execution',sql:m.args[0],api:'PDO::query'};
  if(model.family==='joomla' && m.name==='setquery')return {...m,stage:'setup',sql:m.args[0],api:'JDatabase::setQuery'};
  if(model.family==='joomla' && ['loadobjectlist','loadassoclist','loadresult','loadobject','loadassoc'].includes(m.name))return {...m,stage:'execution',sql:null,api:'JDatabase::'+m.name};
  return null;
}
/** @param {any} node @param {any} context */
function cacheCall(node,context) {
  const m=method(node),model=m&&context.cache_apis[m.receiver];
  if(!model||CACHE_VERSIONS[model.family]!==model.version)return null;
  if(m.name==='get' && ['psr16','memcached'].includes(model.family))return {...m,miss:model.family==='psr16'?'null':'false'};
  if(m.name==='getitem' && model.family==='psr6')return {...m,miss:'isHit'};
  return null;
}
/** @param {any} context */
function validateModels(context) {
  for(const [receiver,model]of Object.entries(context.db_apis))if(!/^[a-zA-Z_]\w*$/.test(receiver)||DB_VERSIONS[model.family]!==model.version)throw new Error('Unsupported DB API/version declaration');
  for(const [receiver,model]of Object.entries(context.cache_apis))if(!/^[a-zA-Z_]\w*$/.test(receiver)||CACHE_VERSIONS[model.family]!==model.version)throw new Error('Unsupported cache API/version declaration');
}
module.exports={method,dbCall,cacheCall,validateModels};
