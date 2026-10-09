'use strict';

const fs = require('fs');
const phpParser = require('php-parser');

const parser = new (/** @type {any} */ (phpParser))({
  parser: {
    extractDoc: false,
    suppressErrors: true
  },
  ast: {
    withPositions: true
  }
});

// SQL DML keywords that signify direct SQL statements
const SQL_DML_PATTERN = /^\s*(SELECT\b|INSERT\s+INTO\b|INSERT\b|UPDATE\b|DELETE\s+FROM\b|DELETE\b|REPLACE\b|WITH\b)/i;

// Cache lookup method names
const CACHE_METHOD_NAMES = new Set(['get', 'fetch', 'getItem', 'getMultiple', 'has']);

// Non-cache receiver variable/property names to strictly reject (e.g. $request->get(), $params->get())
const NON_CACHE_RECEIVERS = new Set([
  'request', 'req', 'input', 'params', 'param', 'post', 'get', 'session',
  'container', 'config', 'conf', 'settings', 'options', 'form', 'model',
  'user', 'row', 'item', 'data', 'res', 'response', 'bag', 'collection',
  'view', 'validator', 'auth', 'router', 'app', 'env', 'cookie', 'cookies',
  'headers', 'header', 'server', 'files', 'file'
]);

// Procedural cache function names
const CACHE_FUNCTION_NAMES = new Set([
  'cache_get',
  'wp_cache_get',
  'apcu_fetch',
  'apc_fetch',
  'memcache_get',
  'xcache_get'
]);

// Two-stage DB setup methods (Joomla style - binds query without executing it yet)
const DB_SETUP_METHOD_NAMES = new Set([
  'setQuery'
]);

// Database query execution method names
const DB_EXEC_METHOD_NAMES = new Set([
  'query',
  'execute',
  'executeStatement',
  'prepare',
  'get_results',      // WordPress wpdb
  'get_row',
  'get_col',
  'get_var',
  'loadObjectList',   // Joomla JDatabase execution
  'loadAssocList',
  'loadObject',
  'loadAssoc',
  'loadResult',
  'loadRow',
  'loadColumn'
]);

// Procedural DB execution function names
const DB_FUNCTION_NAMES = new Set([
  'mysqli_query',
  'pg_query',
  'mysql_query'
]);

/**
 * Checks if an expression represents a direct SQL string literal or string concatenation.
 *
 * @param {any} expr
 * @returns {string|null}
 */
function isDirectSqlExpression(expr) {
  if (!expr) return null;

  if (expr.kind === 'string') {
    if (typeof expr.value === 'string' && SQL_DML_PATTERN.test(expr.value)) {
      return expr.value.trim();
    }
    return null;
  }

  // String concatenation in PHP is binary operator '.'
  if (expr.kind === 'bin' && expr.type === '.') {
    const leadingStr = getLeadingStringInConcat(expr);
    if (leadingStr && SQL_DML_PATTERN.test(leadingStr)) {
      return leadingStr.trim();
    }
  }

  return null;
}

/**
 * @param {any} binExpr
 * @returns {string|null}
 */
function getLeadingStringInConcat(binExpr) {
  let curr = binExpr;
  while (curr && curr.kind === 'bin' && curr.type === '.') {
    if (curr.left.kind === 'string') {
      return curr.left.value;
    }
    curr = curr.left;
  }
  if (curr && curr.kind === 'string') {
    return curr.value;
  }
  return null;
}

/**
 * Validates whether an AST node is a genuine cache receiver.
 *
 * @param {any} node
 * @returns {boolean}
 */
function isLikelyCacheReceiver(node) {
  if (!node) return false;

  // Variable receiver: $cache, $userCache, $cachePool, $memcached, $redis
  if (node.kind === 'variable') {
    const name = String(node.name).toLowerCase();
    if (NON_CACHE_RECEIVERS.has(name)) {
      return false;
    }
    return /(cache|memcache|redis|pool)/i.test(name);
  }

  // Property lookup: $this->cache, $app->cache
  if (node.kind === 'propertylookup') {
    const propName = node.offset && node.offset.name ? String(node.offset.name).toLowerCase() : '';
    if (NON_CACHE_RECEIVERS.has(propName)) {
      return false;
    }
    return /(cache|memcache|redis|pool)/i.test(propName);
  }

  // Static class call: Cache::get(), \Cache::get()
  if (node.kind === 'staticlookup') {
    const className = node.what && node.what.name ? String(node.what.name).toLowerCase() : '';
    return /(cache|memcache|redis)/i.test(className);
  }

  // Chained call: JFactory::getCache()->get(...)
  if (node.kind === 'call') {
    if (node.what && node.what.kind === 'staticlookup') {
      const cls = node.what.what && node.what.what.name ? String(node.what.what.name).toLowerCase() : '';
      const method = node.what.offset && node.what.offset.name ? String(node.what.offset.name).toLowerCase() : '';
      if (cls === 'jfactory' && method === 'getcache') {
        return true;
      }
    }
  }

  return false;
}

/**
 * Checks if a call or expression is a cache lookup.
 *
 * @param {any} expr
 * @returns {{ type: string, name: string, apiDesc: string, node: any, assignedVar?: string | null } | null}
 */
function inspectCacheLookup(expr) {
  if (!expr) return null;

  if (expr.kind === 'call') {
    // Method call: $receiver->get(...)
    if (expr.what && expr.what.kind === 'propertylookup') {
      const receiver = expr.what.what;
      const methodName = expr.what.offset && expr.what.offset.name;

      if (typeof methodName === 'string' && CACHE_METHOD_NAMES.has(methodName)) {
        if (isLikelyCacheReceiver(receiver)) {
          const receiverName = receiver.kind === 'variable' ? `$${receiver.name}` : '$cache';
          return {
            type: 'method',
            name: methodName,
            apiDesc: `${receiverName}->${methodName}()`,
            node: expr
          };
        }
      }
    }

    // Static call: Cache::get(...)
    if (expr.what && expr.what.kind === 'staticlookup') {
      const className = expr.what.what && expr.what.what.name;
      const methodName = expr.what.offset && expr.what.offset.name;
      if (typeof methodName === 'string' && CACHE_METHOD_NAMES.has(methodName)) {
        if (isLikelyCacheReceiver(expr.what)) {
          return {
            type: 'static',
            name: methodName,
            apiDesc: `${className}::${methodName}()`,
            node: expr
          };
        }
      }
    }

    // Procedural function call: wp_cache_get(...), apcu_fetch(...)
    if (expr.what && expr.what.kind === 'identifier') {
      const funcName = expr.what.name;
      if (typeof funcName === 'string' && CACHE_FUNCTION_NAMES.has(funcName.toLowerCase())) {
        return {
          type: 'function',
          name: funcName,
          apiDesc: `${funcName}()`,
          node: expr
        };
      }
    }
  }

  // Nested in assignment: $val = $cache->get(...)
  if (expr.kind === 'assign') {
    const nested = inspectCacheLookup(expr.right);
    if (nested) {
      return {
        ...nested,
        assignedVar: expr.left && expr.left.kind === 'variable' ? expr.left.name : null
      };
    }
  }

  return null;
}

/**
 * Evaluates whether an if-statement's test condition represents a cache-HIT test
 * or a cache-MISS test.
 *
 * Hit polarity: condition is truthy when cache has data.
 * Miss polarity: condition is truthy when cache missed (e.g. !$cached, $cached === false, $cached === null).
 *
 * @param {any} testNode
 * @param {string|null} _cacheVarName
 * @returns {'hit' | 'miss'}
 */
function evaluateCacheConditionPolarity(testNode, _cacheVarName) {
  if (!testNode) return 'hit';

  // Negation: !$cached, !$cache->get(...), !empty($cached)
  if (testNode.kind === 'unary' && (testNode.type === '!' || testNode.type === 'not')) {
    return 'miss';
  }

  // empty($cached)
  if (testNode.kind === 'empty') {
    return 'miss';
  }

  // Binary comparisons: $cached === false, $cached === null, $cached == false
  if (testNode.kind === 'bin') {
    const op = testNode.type;
    const isFalseOrNull = (node) => {
      if (!node) return false;
      if (node.kind === 'boolean' && node.value === false) return true;
      if (node.kind === 'null') return true;
      return false;
    };

    // If checking equality to false or null: === false, == false, === null
    if (op === '===' || op === '==' || op === 'equal') {
      if (isFalseOrNull(testNode.left) || isFalseOrNull(testNode.right)) {
        return 'miss';
      }
    }

    // If checking inequality to false or null: !== false, != false, !== null
    if (op === '!==' || op === '!=' || op === 'notequal') {
      if (isFalseOrNull(testNode.left) || isFalseOrNull(testNode.right)) {
        return 'hit';
      }
    }
  }

  return 'hit';
}

/**
 * Checks if a call is a DB setup call (like Joomla $db->setQuery($sql)).
 *
 * @param {any} node
 * @param {string} targetVarName
 * @returns {{ method: string, apiDesc: string, receiverVar: string|null, node: any } | null}
 */
function inspectDbSetup(node, targetVarName) {
  if (!node) return null;

  if (node.kind === 'call') {
    if (node.what && node.what.kind === 'propertylookup') {
      const methodName = node.what.offset && node.what.offset.name;
      if (typeof methodName === 'string' && DB_SETUP_METHOD_NAMES.has(methodName)) {
        if (callUsesVariable(node.arguments, targetVarName)) {
          const receiver = node.what.what;
          const receiverVar = receiver && receiver.kind === 'variable' ? receiver.name : null;
          return {
            method: methodName,
            apiDesc: `$${receiverVar || 'db'}->${methodName}($${targetVarName})`,
            receiverVar,
            node
          };
        }
      }
    }
  }

  if (node.kind === 'assign') {
    return inspectDbSetup(node.right, targetVarName);
  }

  return null;
}

/**
 * Checks if a node is a direct database execution call using targetVarName,
 * OR an execution call on a configured DB receiver (e.g. $db->loadObjectList()).
 *
 * @param {any} node
 * @param {string} targetVarName
 * @param {string|null} setupReceiverVar
 * @returns {{ method: string, apiDesc: string, node: any } | null}
 */
function inspectDbExecution(node, targetVarName, setupReceiverVar = null) {
  if (!node) return null;

  if (node.kind === 'call') {
    if (node.what && node.what.kind === 'propertylookup') {
      const methodName = node.what.offset && node.what.offset.name;
      const receiver = node.what.what;
      const receiverVar = receiver && receiver.kind === 'variable' ? receiver.name : null;

      if (typeof methodName === 'string' && DB_EXEC_METHOD_NAMES.has(methodName)) {
        // Direct query execution passing SQL variable: $db->query($sql), $pdo->query($sql)
        if (callUsesVariable(node.arguments, targetVarName)) {
          return {
            method: methodName,
            apiDesc: `$${receiverVar || 'db'}->${methodName}($${targetVarName})`,
            node
          };
        }
        // Two-stage execution after setQuery: $db->loadObjectList(), $db->execute()
        if (setupReceiverVar && receiverVar === setupReceiverVar) {
          return {
            method: methodName,
            apiDesc: `$${receiverVar}->${methodName}()`,
            node
          };
        }
      }
    }

    // Procedural DB call: mysqli_query($conn, $sql)
    if (node.what && node.what.kind === 'identifier') {
      const funcName = node.what.name;
      if (typeof funcName === 'string' && DB_FUNCTION_NAMES.has(funcName.toLowerCase())) {
        if (callUsesVariable(node.arguments, targetVarName)) {
          return {
            method: funcName,
            apiDesc: `${funcName}(..., $${targetVarName})`,
            node
          };
        }
      }
    }
  }

  // Assignment where right-hand side is DB execution: $res = $db->query($sql)
  if (node.kind === 'assign') {
    return inspectDbExecution(node.right, targetVarName, setupReceiverVar);
  }

  return null;
}

function callUsesVariable(args, varName) {
  if (!Array.isArray(args)) return false;
  return args.some(arg => isMatchingVariable(arg, varName));
}

function isMatchingVariable(node, varName) {
  if (!node) return false;
  return node.kind === 'variable' && node.name === varName;
}

/**
 * Checks if a block unconditionally exits the function scope without running the query.
 *
 * @param {any} bodyNode
 * @param {string} targetVarName
 * @returns {{ exitsScope: boolean, exitLine: number | null } | null}
 */
function inspectBypassBlock(bodyNode, targetVarName) {
  if (!bodyNode) return null;

  const stmts = bodyNode.kind === 'block' ? (bodyNode.children || []) : [bodyNode];
  let exitsScope = false;
  let exitLine = null;
  let usesSqlInExit = false;

  for (const s of stmts) {
    if (hasDbExecutionInSubtree(s, targetVarName)) {
      usesSqlInExit = true;
      break;
    }
    if (s.kind === 'return' || s.kind === 'throw' || s.kind === 'exit') {
      exitsScope = true;
      exitLine = s.loc?.start?.line ?? null;
      if (s.expr && isMatchingVariable(s.expr, targetVarName)) {
        usesSqlInExit = true;
      }
      break;
    }
  }

  if (exitsScope && !usesSqlInExit) {
    return { exitsScope: true, exitLine };
  }
  return null;
}

function hasDbExecutionInSubtree(node, targetVarName) {
  if (!node) return false;
  if (inspectDbExecution(node, targetVarName)) return true;
  if (inspectDbSetup(node, targetVarName)) return true;
  if (node.expression && (inspectDbExecution(node.expression, targetVarName) || inspectDbSetup(node.expression, targetVarName))) {
    return true;
  }

  for (const key of Object.keys(node)) {
    if (key === 'loc') continue;
    const val = node[key];
    if (Array.isArray(val)) {
      for (const item of val) {
        if (item && typeof item === 'object' && hasDbExecutionInSubtree(item, targetVarName)) {
          return true;
        }
      }
    } else if (val && typeof val === 'object') {
      if (hasDbExecutionInSubtree(val, targetVarName)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Checks if a statement reassigns the target variable.
 *
 * @param {any} stmt
 * @param {string} targetVarName
 * @returns {boolean}
 */
function isVariableReassigned(stmt, targetVarName) {
  if (!stmt) return false;

  let assignNode = null;
  if (stmt.kind === 'expressionstatement' && stmt.expression && stmt.expression.kind === 'assign') {
    assignNode = stmt.expression;
  } else if (stmt.kind === 'assign') {
    assignNode = stmt;
  }

  if (assignNode && assignNode.left && assignNode.left.kind === 'variable') {
    return assignNode.left.name === targetVarName;
  }
  return false;
}

/**
 * Checks if a variable escapes to non-DB calls or unknown helpers.
 *
 * @param {any} node
 * @param {string} targetVarName
 * @returns {boolean}
 */
function variableEscapesInNode(node, targetVarName) {
  if (!node) return false;

  if (node.kind === 'call') {
    const isDb = inspectDbExecution(node, targetVarName) || inspectDbSetup(node, targetVarName);
    if (!isDb && callUsesVariable(node.arguments, targetVarName)) {
      return true;
    }
  }

  for (const key of Object.keys(node)) {
    if (key === 'loc') continue;
    const val = node[key];
    if (Array.isArray(val)) {
      for (const item of val) {
        if (item && typeof item === 'object' && variableEscapesInNode(item, targetVarName)) {
          return true;
        }
      }
    } else if (val && typeof val === 'object') {
      if (variableEscapesInNode(val, targetVarName)) {
        return true;
      }
    }
  }
  return false;
}

/**
 * Scans a sequential list of statements inside a function, method, or file scope.
 *
 * @param {any[]} statements
 * @param {string} filePath
 * @param {string[]} sourceLines
 * @returns {import('./types').Finding[]}
 */
function scanStatements(statements, filePath, sourceLines) {
  const findings = [];
  if (!Array.isArray(statements)) return findings;

  for (let i = 0; i < statements.length; i++) {
    const stmt = statements[i];
    let assignNode = null;

    if (stmt.kind === 'expressionstatement' && stmt.expression && stmt.expression.kind === 'assign') {
      assignNode = stmt.expression;
    } else if (stmt.kind === 'assign') {
      assignNode = stmt;
    }

    if (!assignNode || !assignNode.left || assignNode.left.kind !== 'variable') {
      continue;
    }

    const varName = assignNode.left.name;
    const sqlPreview = isDirectSqlExpression(assignNode.right);
    if (!sqlPreview) {
      continue;
    }

    const constructionLine = assignNode.loc?.start?.line || (stmt.loc?.start?.line) || 1;
    const constructionCol = assignNode.loc?.start?.column || 1;

    let cacheCheckInfo = null;
    let cacheCheckIndex = -1;
    let escaped = false;
    let reassigned = false;

    // Scan downstream statements
    for (let j = i + 1; j < statements.length; j++) {
      const nextStmt = statements[j];

      // Check for SQL variable reassignment
      if (isVariableReassigned(nextStmt, varName)) {
        reassigned = true;
        break;
      }

      // Check if SQL variable escaped
      if (variableEscapesInNode(nextStmt, varName)) {
        escaped = true;
        break;
      }

      // Two-step cache check: $cached = $cache->get(...); if (...)
      let cacheLookup = null;
      if (nextStmt.kind === 'expressionstatement' && nextStmt.expression) {
        cacheLookup = inspectCacheLookup(nextStmt.expression);
      } else if (nextStmt.kind === 'assign') {
        cacheLookup = inspectCacheLookup(nextStmt);
      }

      if (cacheLookup) {
        if (j + 1 < statements.length && statements[j + 1].kind === 'if') {
          const ifStmt = statements[j + 1];
          const polarity = evaluateCacheConditionPolarity(ifStmt.test, cacheLookup.assignedVar || null);

          // Only consider cache-HIT bypasses
          if (polarity === 'hit') {
            const bypass = inspectBypassBlock(ifStmt.body, varName);
            if (bypass) {
              cacheCheckInfo = {
                line: nextStmt.loc?.start?.line || constructionLine,
                col: nextStmt.loc?.start?.column || 1,
                apiDesc: cacheLookup.apiDesc,
                bypassExitLine: bypass.exitLine
              };
              cacheCheckIndex = j + 1;
              break;
            }
          }
        }
      }

      // Direct cache check inside if: if ($cached = $cache->get(...))
      if (nextStmt.kind === 'if') {
        const directLookup = inspectCacheLookup(nextStmt.test);
        if (directLookup) {
          const polarity = evaluateCacheConditionPolarity(nextStmt.test, directLookup.assignedVar || null);

          if (polarity === 'hit') {
            const bypass = inspectBypassBlock(nextStmt.body, varName);
            if (bypass) {
              cacheCheckInfo = {
                line: nextStmt.loc?.start?.line || constructionLine,
                col: nextStmt.loc?.start?.column || 1,
                apiDesc: directLookup.apiDesc,
                bypassExitLine: bypass.exitLine
              };
              cacheCheckIndex = j;
              break;
            }
          }
        }
      }
    }

    if (reassigned || escaped || !cacheCheckInfo || cacheCheckIndex === -1) {
      continue;
    }

    // Look for DB execution or two-stage setup + execution on cache-miss path
    let dbSetupInfo = null;
    let dbExecInfo = null;

    for (let k = cacheCheckIndex + 1; k < statements.length; k++) {
      const missStmt = statements[k];

      // Check if variable is reassigned before reaching DB execution
      if (isVariableReassigned(missStmt, varName)) {
        reassigned = true;
        break;
      }

      const missExpr = missStmt.kind === 'expressionstatement' ? missStmt.expression : missStmt;

      // Check for two-stage setup: $db->setQuery($query)
      if (!dbSetupInfo) {
        const setup = inspectDbSetup(missExpr, varName);
        if (setup) {
          dbSetupInfo = {
            line: missStmt.loc?.start?.line || cacheCheckInfo.line + 1,
            col: missStmt.loc?.start?.column || 1,
            apiDesc: setup.apiDesc,
            receiverVar: setup.receiverVar
          };
          continue;
        }
      }

      // Check for execution call (either passing SQL directly, or calling $db->loadObjectList() after setQuery)
      const exec = inspectDbExecution(missExpr, varName, dbSetupInfo ? dbSetupInfo.receiverVar : null);
      if (exec) {
        dbExecInfo = {
          line: missStmt.loc?.start?.line || cacheCheckInfo.line + 1,
          col: missStmt.loc?.start?.column || 1,
          apiDesc: exec.apiDesc
        };
        break;
      }
    }

    if (reassigned || !dbExecInfo) {
      continue;
    }

    const getSnippet = (line) => {
      if (sourceLines && sourceLines[line - 1]) {
        return sourceLines[line - 1].trim();
      }
      return undefined;
    };

    /** @type {import('./types').FindingLocations} */
    const locations = {
      construction: {
        line: constructionLine,
        column: constructionCol,
        snippet: getSnippet(constructionLine)
      },
      cache_check: {
        line: cacheCheckInfo.line,
        column: cacheCheckInfo.col,
        snippet: getSnippet(cacheCheckInfo.line)
      },
      db_execution: {
        line: dbExecInfo.line,
        column: dbExecInfo.col,
        snippet: getSnippet(dbExecInfo.line)
      }
    };

    if (dbSetupInfo) {
      locations.db_setup = {
        line: dbSetupInfo.line,
        column: dbSetupInfo.col,
        snippet: getSnippet(dbSetupInfo.line)
      };
    }

    /** @type {import('./types').FindingEvidence} */
    const evidence = {
      query_variable: `$${varName}`,
      sql_preview: sqlPreview.length > 60 ? sqlPreview.slice(0, 57) + '...' : sqlPreview,
      cache_api: cacheCheckInfo.apiDesc,
      db_api: dbExecInfo.apiDesc,
      construction_line: constructionLine,
      cache_check_line: cacheCheckInfo.line,
      db_execution_line: dbExecInfo.line
    };

    if (dbSetupInfo) {
      evidence.setup_api = dbSetupInfo.apiDesc;
      evidence.setup_line = dbSetupInfo.line;
    }

    const setupNotice = dbSetupInfo
      ? ` Query setup occurred on line ${dbSetupInfo.line} (${dbSetupInfo.apiDesc}) before execution call on line ${dbExecInfo.line} (${dbExecInfo.apiDesc}).`
      : '';

    findings.push(/** @type {import('./types').Finding} */ ({
      check_id: 'DB-34',
      rule_id: 'R1',
      file_path: filePath,
      line_number: constructionLine,
      column_number: constructionCol,
      locations,
      bypass_explanation: `SQL query string ($${varName}) is constructed on line ${constructionLine} before cache lookup on line ${cacheCheckInfo.line}. When the cache hits, execution returns on line ${cacheCheckInfo.bypassExitLine} without executing the query, wasting query-construction CPU cycles.${setupNotice}`,
      affected_resource: 'CPU',
      supported_language: 'PHP (direct database access)',
      supported_apis: ['PDO', 'mysqli', 'Joomla JDatabase', 'WordPress wpdb', 'PSR-6/PSR-16 Cache'],
      confidence: 'High',
      limitations: 'Static AST analysis is bounded to local function/method scope. Dynamic variable aliases, cross-function cache delegation, or custom cache abstraction helpers are excluded to prevent false positives.',
      recommendation: 'Check cache first; construct SQL only on the miss path.',
      reference: 'Shao et al., Database-Access Performance Antipatterns in Database-Backed Web Applications (ICSME 2020), AP-34 & Figure 11',
      evidence
    }));
  }

  return findings;
}

/**
 * Traverse the AST to find all scopes (functions, class methods, closures, top-level).
 *
 * @param {any} ast
 * @param {string} filePath
 * @param {string[]} sourceLines
 * @returns {import('./types').Finding[]}
 */
function scanAst(ast, filePath, sourceLines) {
  const allFindings = [];

  function walk(node) {
    if (!node) return;

    if (node.kind === 'function' || node.kind === 'method' || node.kind === 'closure') {
      if (node.body && node.body.children) {
        const found = scanStatements(node.body.children, filePath, sourceLines);
        allFindings.push(...found);
      }
    } else if (node.kind === 'arrowfunc') {
      if (node.body && node.body.children) {
        const found = scanStatements(node.body.children, filePath, sourceLines);
        allFindings.push(...found);
      }
    }

    for (const key of Object.keys(node)) {
      if (key === 'loc') continue;
      const child = node[key];
      if (Array.isArray(child)) {
        for (const c of child) {
          if (c && typeof c === 'object') {
            walk(c);
          }
        }
      } else if (child && typeof child === 'object') {
        walk(child);
      }
    }
  }

  if (ast.children && Array.isArray(ast.children)) {
    const topLevelStatements = ast.children.filter((c) => c.kind !== 'function' && c.kind !== 'class');
    if (topLevelStatements.length > 0) {
      const topFound = scanStatements(topLevelStatements, filePath, sourceLines);
      allFindings.push(...topFound);
    }
  }

  walk(ast);

  return allFindings;
}

/**
 * Pure scanner function: takes source code string and an optional file path,
 * returns an array of findings.
 *
 * @param {string} sourceText - Raw PHP source code
 * @param {string} [filePath='unknown.php'] - Target file path
 * @returns {import('./types').Finding[]}
 */
function scanSource(sourceText, filePath = 'unknown.php') {
  if (typeof sourceText !== 'string' || sourceText.trim() === '') {
    return [];
  }

  let ast;
  try {
    ast = parser.parseCode(sourceText, filePath);
  } catch {
    return [];
  }

  if (!ast) {
    return [];
  }

  const sourceLines = sourceText.split(/\r?\n/);
  return scanAst(ast, filePath, sourceLines);
}

/**
 * Synchronous scanner for a file path. Read-only.
 *
 * @param {string} filePath - Path to PHP file
 * @returns {import('./types').Finding[]}
 */
function scanFile(filePath) {
  if (!fs.existsSync(filePath)) {
    return [];
  }
  const content = fs.readFileSync(filePath, 'utf-8');
  return scanSource(content, filePath);
}

module.exports = {
  scanSource,
  scanFile
};
