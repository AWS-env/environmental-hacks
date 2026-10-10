import Parser from "tree-sitter";

/**
 * Nodes whose text is only partly covered by child nodes: tree-sitter-python gives a string with escape
 * sequences a `string_content` whose children are just the escapes, so the plain text between them is not a
 * leaf. They are compared by their whole text instead of by children.
 */
const WHOLE_TEXT_TYPES = new Set(["string_content", "format_specifier"]);

/** Leaf tokens, so `a[ i ]` matches `a[i]`, comments are ignored and string text is never lost. */
export function leafTokens(node: Parser.SyntaxNode | null): string[] {
  if (!node) return [];
  if (node.type === "comment") return [];
  if (node.childCount === 0 || WHOLE_TEXT_TYPES.has(node.type)) return [node.text];
  return node.children.flatMap(leafTokens);
}

/** True when both nodes spell the same code apart from whitespace and comments. */
export function sameTokens(a: Parser.SyntaxNode | null, b: Parser.SyntaxNode | null): boolean {
  const ta = leafTokens(a);
  const tb = leafTokens(b);
  return ta.length === tb.length && ta.every((t, i) => t === tb[i]);
}
