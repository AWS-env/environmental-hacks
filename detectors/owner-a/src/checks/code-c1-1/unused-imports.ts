import {
  Finding,
  generateFingerprint,
  CostTier,
  Severity,
  Confidence,
} from "../../core/finding.js";
import { PythonModuleScope } from "../../core/python-scope.js";
import importCostData from "./import-cost.json" with { type: "json" };

const heavySet = new Set(importCostData.heavyModules);
const lightSet = new Set(importCostData.lightModules);

export function detectUnusedImports(scope: PythonModuleScope): Finding[] {
  // Never flag intentional re-exports in __init__.py or test fixtures in conftest.py
  if (scope.isInitFile || scope.isConftest) {
    return [];
  }

  const findings: Finding[] = [];

  for (const sym of scope.importedSymbols) {
    // False positive guard: guarded by TYPE_CHECKING or try/except ImportError
    if (sym.isGuarded) {
      continue;
    }

    // False positive guard: symbol exported in __all__
    if (scope.allExportedSymbols.has(sym.boundName)) {
      continue;
    }

    // False positive guard: referenced elsewhere in the module
    if (scope.allReferencedIdentifiers.has(sym.boundName)) {
      continue;
    }

    // False positive guard: forward-referenced in type string annotations (e.g. "pd.DataFrame")
    if (scope.stringLiteralsText.includes(sym.boundName)) {
      continue;
    }

    const rootModule = sym.moduleName.split(".")[0];
    const isHeavy = heavySet.has(rootModule);
    const isLight = lightSet.has(rootModule);

    const costTier: CostTier = isHeavy ? "heavy" : isLight ? "light" : "unknown";
    const severity: Severity = isHeavy ? "high" : "low";

    let confidence: Confidence = "high";
    const limitations: string[] = [
      "Static single-pass AST scope analysis without runtime execution.",
    ];

    if (scope.hasDynamicAccess) {
      confidence = "medium";
      limitations.push(
        "File contains dynamic evaluation or reflection (globals/locals/eval/exec/getattr); symbol may be referenced dynamically."
      );
    }

    if (scope.hasWildcardImport) {
      confidence = "medium";
      limitations.push(
        "File contains wildcard import ('from ... import *'); namespace may have overlapping references."
      );
    }

    const why = isHeavy
      ? `Unused import of heavy module '${rootModule}' executes top-level package initialization, allocating memory and burning CPU cycles on startup without being referenced.`
      : `Unused import '${sym.boundName}' from module '${sym.moduleName}' loads unnecessary code into process memory on startup.`;

    const fingerprint = generateFingerprint(
      "CODE-C1.1",
      "unused-import",
      scope.filePath,
      sym.boundName
    );

    const agentPrompt = `In ${scope.filePath}:${sym.startLine}, remove unused import '${sym.boundName}' (line: "${sym.snippet}") to eliminate avoidable startup compute and memory overhead.`;

    findings.push({
      check: "CODE-C1.1",
      kind: "unused-import",
      fingerprint,
      location: {
        path: scope.filePath,
        startLine: sym.startLine,
        endLine: sym.endLine,
      },
      evidence: {
        snippet: sym.snippet,
        symbol: sym.boundName,
        module: sym.moduleName,
        costTier,
      },
      why,
      severity,
      confidence,
      limitations,
      evidenceTier: "static",
      impact: {
        quantified: false,
        reason:
          "Static analysis identifies unnecessary module initialization on load; exact energy savings depend on execution frequency and module size.",
      },
      references: [
        {
          id: "SRC-01",
          title: "Watts This Smell: An Empirical Study on Energy Smells in Python Software",
          url: "https://arxiv.org/abs/2604.04809",
        },
      ],
      agentPrompt,
      detector: {
        id: "owner-a-static-scan",
        version: "0.1.0",
      },
    });
  }

  return findings;
}
