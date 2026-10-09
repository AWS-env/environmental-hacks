import Parser from "tree-sitter";
import Python from "tree-sitter-python";

let pythonParserInstance: Parser | null = null;

export function getPythonParser(): Parser {
  if (!pythonParserInstance) {
    const parser = new Parser();
    parser.setLanguage(Python);
    pythonParserInstance = parser;
  }
  return pythonParserInstance;
}

export interface ParsedPythonFile {
  path: string;
  content: string;
  tree: Parser.Tree;
  rootNode: Parser.SyntaxNode;
  hasSyntaxError: boolean;
}

export function parsePythonSource(filePath: string, content: string): ParsedPythonFile {
  const parser = getPythonParser();
  const tree = parser.parse(content);
  const rootNode = tree.rootNode;
  const hasSyntaxError = Boolean(rootNode.hasError);

  return {
    path: filePath,
    content,
    tree,
    rootNode,
    hasSyntaxError,
  };
}
