import Parser from "tree-sitter";
import { isLineSuppressed } from "./suppressions.js";

export interface ImportedSymbol {
  /** The local name bound in the module's scope */
  boundName: string;
  /** The module being imported from or directly imported */
  moduleName: string;
  /** The imported attribute if `from x import y`, else undefined */
  importedName?: string;
  /** Was this imported with an alias `as z` */
  alias?: string;
  /** The AST node of the import statement */
  node: Parser.SyntaxNode;
  /** 1-indexed start line */
  startLine: number;
  /** 1-indexed end line */
  endLine: number;
  /** Line snippet */
  snippet: string;
  /** Is this import guarded by TYPE_CHECKING or try-except */
  isGuarded: boolean;
  guardReason?: string;
}

export interface PythonModuleScope {
  filePath: string;
  isInitFile: boolean;
  isConftest: boolean;
  hasWildcardImport: boolean;
  hasDynamicAccess: boolean;
  allExportedSymbols: Set<string>;
  importedSymbols: ImportedSymbol[];
  allReferencedIdentifiers: Set<string>;
  stringLiteralsText: string;
}

/**
 * Extracts the full dotted name text from a dotted_name or identifier node
 */
function getDottedNameText(node: Parser.SyntaxNode): string {
  return node.text;
}

/**
 * Checks if a node is enclosed in an `if TYPE_CHECKING:` or `if typing.TYPE_CHECKING:` block
 */
function isEnclosedInTypeChecking(node: Parser.SyntaxNode): boolean {
  let curr: Parser.SyntaxNode | null = node.parent;
  while (curr) {
    if (curr.type === "if_statement") {
      const condition = curr.childForFieldName("condition");
      if (condition && condition.text.includes("TYPE_CHECKING")) {
        return true;
      }
    }
    curr = curr.parent;
  }
  return false;
}

/**
 * Checks if a node is inside a try-except block that catches ImportError or ModuleNotFoundError
 */
function isEnclosedInTryExceptImportError(node: Parser.SyntaxNode): boolean {
  let curr: Parser.SyntaxNode | null = node.parent;
  while (curr) {
    if (curr.type === "try_statement") {
      for (const child of curr.children) {
        if (child.type === "except_clause") {
          const text = child.text;
          if (
            text.includes("ImportError") ||
            text.includes("ModuleNotFoundError") ||
            text.trim() === "except:"
          ) {
            return true;
          }
        }
      }
    }
    curr = curr.parent;
  }
  return false;
}

/**
 * Extracts symbols exported in `__all__ = [...]` or `__all__ = (...)`
 */
function extractAllExports(rootNode: Parser.SyntaxNode): Set<string> {
  const exports = new Set<string>();

  function visit(node: Parser.SyntaxNode) {
    if (node.type === "assignment") {
      const left = node.childForFieldName("left");
      const right = node.childForFieldName("right");
      if (left && left.text === "__all__" && right) {
        // Collect string literals inside right
        for (const child of right.descendantsOfType("string")) {
          // Remove string quotes
          const raw = child.text.replace(/^['"]+|['"]+$/g, "");
          if (raw) {
            exports.add(raw);
          }
        }
      }
    }
    for (const child of node.namedChildren) {
      visit(child);
    }
  }

  visit(rootNode);
  return exports;
}

/**
 * Analyze Python file AST for imported symbols and all scope usages
 */
export function analyzePythonScope(
  rootNode: Parser.SyntaxNode,
  filePath: string,
  sourceLines: string[]
): PythonModuleScope {
  const normalizedPath = filePath.replace(/\\/g, "/");
  const isInitFile = normalizedPath.endsWith("/__init__.py") || normalizedPath === "__init__.py";
  const isConftest = normalizedPath.endsWith("/conftest.py") || normalizedPath === "conftest.py";

  let hasWildcardImport = false;
  let hasDynamicAccess = false;
  const importedSymbols: ImportedSymbol[] = [];
  const allReferencedIdentifiers = new Set<string>();
  const stringLiteralsList: string[] = [];
  const importNodes = new Set<Parser.SyntaxNode>();

  const allExportedSymbols = extractAllExports(rootNode);

  // First pass: find imports and identify import statement nodes
  function collectImports(node: Parser.SyntaxNode) {
    if (node.type === "import_statement") {
      importNodes.add(node);
      const startLine = node.startPosition.row + 1;
      const endLine = node.endPosition.row + 1;
      const snippet = sourceLines[node.startPosition.row]?.trim() || node.text;

      const suppression = isLineSuppressed(sourceLines[node.startPosition.row] || "");
      if (suppression.isSuppressed) {
        return;
      }

      const inTypeChecking = isEnclosedInTypeChecking(node);
      const inTryExcept = isEnclosedInTryExceptImportError(node);

      // Process import names: e.g. import a, b as c
      for (const child of node.namedChildren) {
        if (child.type === "dotted_name") {
          const moduleName = child.text;
          const boundName = moduleName.split(".")[0];
          importedSymbols.push({
            boundName,
            moduleName,
            node,
            startLine,
            endLine,
            snippet,
            isGuarded: inTypeChecking || inTryExcept,
            guardReason: inTypeChecking ? "TYPE_CHECKING" : inTryExcept ? "try-except" : undefined,
          });
        } else if (child.type === "aliased_import") {
          const nameNode = child.childForFieldName("name");
          const aliasNode = child.childForFieldName("alias");
          if (nameNode && aliasNode) {
            const moduleName = nameNode.text;
            const alias = aliasNode.text;
            importedSymbols.push({
              boundName: alias,
              moduleName,
              alias,
              node,
              startLine,
              endLine,
              snippet,
              isGuarded: inTypeChecking || inTryExcept,
              guardReason: inTypeChecking ? "TYPE_CHECKING" : inTryExcept ? "try-except" : undefined,
            });
          }
        }
      }
    } else if (node.type === "import_from_statement") {
      importNodes.add(node);
      const startLine = node.startPosition.row + 1;
      const endLine = node.endPosition.row + 1;
      const snippet = sourceLines[node.startPosition.row]?.trim() || node.text;

      const suppression = isLineSuppressed(sourceLines[node.startPosition.row] || "");
      if (suppression.isSuppressed) {
        return;
      }

      const moduleNode = node.childForFieldName("module_name");
      const moduleName = moduleNode ? moduleNode.text : "";

      // Guard: __future__ imports are language feature toggles
      if (moduleName === "__future__") {
        return;
      }

      const inTypeChecking = isEnclosedInTypeChecking(node);
      const inTryExcept = isEnclosedInTryExceptImportError(node);

      // Check for wildcard import `from x import *`
      if (node.text.includes("import *")) {
        hasWildcardImport = true;
        return;
      }

      // Find imported symbols in `from x import a, b as c`
      for (const child of node.namedChildren) {
        if (child === moduleNode) continue;

        if (child.type === "dotted_name" || child.type === "identifier") {
          const importedName = child.text;
          importedSymbols.push({
            boundName: importedName,
            moduleName,
            importedName,
            node,
            startLine,
            endLine,
            snippet,
            isGuarded: inTypeChecking || inTryExcept,
            guardReason: inTypeChecking ? "TYPE_CHECKING" : inTryExcept ? "try-except" : undefined,
          });
        } else if (child.type === "aliased_import") {
          const nameNode = child.childForFieldName("name");
          const aliasNode = child.childForFieldName("alias");
          if (nameNode && aliasNode) {
            importedSymbols.push({
              boundName: aliasNode.text,
              moduleName,
              importedName: nameNode.text,
              alias: aliasNode.text,
              node,
              startLine,
              endLine,
              snippet,
              isGuarded: inTypeChecking || inTryExcept,
              guardReason: inTypeChecking ? "TYPE_CHECKING" : inTryExcept ? "try-except" : undefined,
            });
          }
        }
      }
    }

    for (const child of node.namedChildren) {
      collectImports(child);
    }
  }

  collectImports(rootNode);

  // Helper: check if a node is part of an import definition itself
  function isInsideImport(node: Parser.SyntaxNode): boolean {
    let curr: Parser.SyntaxNode | null = node;
    while (curr) {
      if (importNodes.has(curr)) {
        return true;
      }
      curr = curr.parent;
    }
    return false;
  }

  // Second pass: collect all referenced identifiers outside import statements
  function collectReferences(node: Parser.SyntaxNode) {
    // Check for dynamic access patterns
    if (node.type === "call") {
      const func = node.childForFieldName("function");
      if (func) {
        const text = func.text;
        if (
          text === "globals" ||
          text === "locals" ||
          text === "eval" ||
          text === "exec" ||
          text === "getattr" ||
          text === "importlib.import_module" ||
          text.includes("getattr")
        ) {
          hasDynamicAccess = true;
        }
      }
    }

    if (node.type === "string") {
      stringLiteralsList.push(node.text);
    }

    if (node.type === "identifier") {
      if (!isInsideImport(node)) {
        allReferencedIdentifiers.add(node.text);
      }
    }

    for (const child of node.namedChildren) {
      collectReferences(child);
    }
  }

  collectReferences(rootNode);

  return {
    filePath,
    isInitFile,
    isConftest,
    hasWildcardImport,
    hasDynamicAccess,
    allExportedSymbols,
    importedSymbols,
    allReferencedIdentifiers,
    stringLiteralsText: stringLiteralsList.join(" "),
  };
}
