"""Parse context for JavaScript / TypeScript (tree-sitter). Source is parsed only, never executed."""
from __future__ import annotations

import re

import tree_sitter as ts
import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts

from owner_c.common import EVIDENCE_MAX_LINES, Candidate, Hit
from owner_c.langs import ParseError

FUNCTION_TYPES = {
    "function_declaration", "function_expression", "function", "arrow_function", "method_definition",
    "generator_function", "generator_function_declaration",
}
LOOP_TYPES = {"for_statement", "for_in_statement", "while_statement", "do_statement"}
ITERATION_METHODS = {"forEach", "map", "filter", "reduce", "reduceRight", "some", "every", "flatMap", "find",
                     "findIndex", "findLast", "findLastIndex"}

_LANGUAGES = {}


def _language(path: str):
    kind = "tsx" if path.endswith(".tsx") else "ts" if path.endswith((".ts", ".mts", ".cts")) else "js"
    if kind not in _LANGUAGES:
        raw = {"js": tsjs.language, "ts": tsts.language_typescript, "tsx": tsts.language_tsx}[kind]()
        _LANGUAGES[kind] = ts.Language(raw)
    return _LANGUAGES[kind]


_DISABLE = re.compile(r"eslint-disable(?P<scope>-next-line|-line)?(?P<rules>[^*\n]*)")


class JsCtx:
    def __init__(self, path: str, source: str):
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self._bytes = source.encode("utf-8")
        tree = ts.Parser(_language(path)).parse(self._bytes)
        if tree.root_node.has_error:
            raise ParseError("syntax errors in file")
        self.tree = tree
        self.root = tree.root_node
        self._bindings = None

    # ---- text and navigation -------------------------------------------------
    def text(self, node) -> str:
        return self._bytes[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def walk(self, node=None):
        stack = [node or self.root]
        while stack:
            current = stack.pop()
            yield current
            stack.extend(reversed(current.children))

    def ancestors(self, node):
        node = node.parent
        while node is not None:
            yield node
            node = node.parent

    def field(self, node, name):
        return node.child_by_field_name(name)

    def call_parts(self, call):
        """(object text or '', property/function text) for a call_expression, or (None, None)."""
        fn = self.field(call, "function")
        if fn is None:
            return None, None
        if fn.type == "member_expression":
            obj, prop = self.field(fn, "object"), self.field(fn, "property")
            return (self.text(obj) if obj else ""), (self.text(prop) if prop else "")
        return "", self.text(fn)

    def dotted_callee(self, call) -> str:
        obj, prop = self.call_parts(call)
        if prop is None:
            return ""
        return f"{obj}.{prop}" if obj else prop

    # ---- imports -------------------------------------------------------------
    def string_value(self, node) -> str | None:
        if node is None or node.type != "string":
            return None
        return self.text(node)[1:-1]

    def bindings(self) -> dict:
        """local name -> (module, member or None) from `import` and `require`, `node:` prefix removed."""
        if self._bindings is not None:
            return self._bindings
        out = {}

        def module_of(text):
            return text[5:] if text.startswith("node:") else text

        for node in self.walk():
            if node.type == "import_statement":
                source = module_of(self.string_value(self.field(node, "source")) or "")
                for child in node.children:
                    if child.type != "import_clause":
                        continue
                    for part in child.children:
                        if part.type == "identifier":
                            out[self.text(part)] = (source, None)
                        elif part.type == "namespace_import":
                            ident = next((c for c in part.children if c.type == "identifier"), None)
                            if ident is not None:
                                out[self.text(ident)] = (source, None)
                        elif part.type == "named_imports":
                            for spec in part.children:
                                if spec.type == "import_specifier":
                                    name, alias = self.field(spec, "name"), self.field(spec, "alias")
                                    local = alias or name
                                    out[self.text(local)] = (source, self.text(name))
            elif node.type == "variable_declarator":
                value = self.field(node, "value")
                if value is None or value.type != "call_expression" or self.text(self.field(value, "function")) != "require":
                    continue
                args = self.field(value, "arguments")
                first = next((c for c in args.children if c.type == "string"), None) if args is not None else None
                source = module_of(self.string_value(first) or "")
                target = self.field(node, "name")
                if target is None:
                    continue
                if target.type == "identifier":
                    out[self.text(target)] = (source, None)
                elif target.type == "object_pattern":
                    for prop in target.children:
                        if prop.type == "shorthand_property_identifier_pattern":
                            out[self.text(prop)] = (source, self.text(prop))
                        elif prop.type == "pair_pattern":
                            key, val = self.field(prop, "key"), self.field(prop, "value")
                            if key is not None and val is not None:
                                out[self.text(val)] = (source, self.text(key))
        self._bindings = out
        return out

    def resolve_call(self, call) -> tuple[str, str] | None:
        """(module, function) for calls like fs.readFileSync(...) or readFileSync(...) via imports; else None."""
        obj, prop = self.call_parts(call)
        if prop is None:
            return None
        binding = self.bindings().get(obj if obj else prop)
        if binding is None:
            return None
        module, member = binding
        if obj:  # namespace/default import: fs.readFileSync
            return module, prop
        return module, member or prop

    # ---- structure -----------------------------------------------------------
    def enclosing_function(self, node):
        for a in self.ancestors(node):
            if a.type in FUNCTION_TYPES:
                return a
        return None

    def function_start_line(self, fn) -> int:
        return fn.start_point[0] + 1

    def _function_name(self, fn) -> str:
        name = self.field(fn, "name")
        if name is not None:
            return self.text(name)
        parent = fn.parent
        if parent is not None and parent.type == "variable_declarator":
            ident = self.field(parent, "name")
            if ident is not None:
                return self.text(ident)
        if parent is not None and parent.type in ("pair", "public_field_definition", "field_definition"):
            key = self.field(parent, "key") or self.field(parent, "name")
            if key is not None:
                return self.text(key)
        if parent is not None and parent.type == "assignment_expression":
            left = self.field(parent, "left")
            if left is not None:
                return self.text(left)
        return "<anonymous>"

    def qualname(self, node) -> str:
        names = []
        for a in self.ancestors(node):
            if a.type in FUNCTION_TYPES:
                names.append(self._function_name(a))
            elif a.type in ("class_declaration", "class"):
                ident = self.field(a, "name")
                names.append(self.text(ident) if ident else "<class>")
        return ".".join(reversed(names)) or "<module>"

    def in_loop(self, node, include_callbacks: bool = True) -> bool:
        """True inside a loop of the same function; callbacks of .map/.forEach/... count as loops."""
        for a in self.ancestors(node):
            if a.type in LOOP_TYPES:
                return True
            if a.type in FUNCTION_TYPES:
                if not include_callbacks:
                    return False
                call = a.parent.parent if a.parent is not None and a.parent.type == "arguments" else None
                if call is None or call.type != "call_expression":
                    return False
                _obj, prop = self.call_parts(call)
                return prop in ITERATION_METHODS  # an iteration callback runs once per element
        return False

    # ---- lines, evidence, suppression ---------------------------------------
    def line_of(self, node) -> int:
        return node.start_point[0] + 1

    def end_line_of(self, node) -> int:
        return node.end_point[0] + 1

    def evidence_lines(self, node):
        start = self.line_of(node)
        end = min(self.end_line_of(node), start + EVIDENCE_MAX_LINES - 1)
        return start, "\n".join(self.lines[start - 1:end])

    def _disables(self, text: str, scope: str, codes: tuple) -> bool:
        for m in _DISABLE.finditer(text):
            if (m.group("scope") or "") != scope:
                continue
            rules = [r.strip() for r in m.group("rules").replace("--", ",").split(",") if r.strip()]
            if not rules or any(code in rules for code in codes):
                return True
        return False

    def is_suppressed(self, codes: tuple, line: int) -> bool:
        if not 0 < line <= len(self.lines):
            return False
        if self._disables(self.lines[line - 1], "-line", codes):
            return True
        if line >= 2 and self._disables(self.lines[line - 2], "-next-line", codes):
            return True
        head = "\n".join(self.lines[:line - 1])
        return any(self._disables(m.group(0), "", codes) and "eslint-enable" not in head
                   for m in re.finditer(r"/\*\s*eslint-disable[^*]*\*/", head))

    # ---- builders ------------------------------------------------------------
    def hit(self, node, anchor, summary, confidence) -> Hit:
        return Hit(node, anchor, summary, confidence)

    def candidate(self, node, anchor, detail) -> Candidate:
        fn = self.enclosing_function(node)
        meta = self.function_start_line(fn) if fn is not None else None
        return Candidate(node, anchor, detail, self.line_of(node), self.end_line_of(node), meta)

    def args(self, call) -> list:
        """Named argument nodes of a call or new expression."""
        node = self.field(call, "arguments")
        return [c for c in node.children if c.is_named and c.type != "comment"] if node is not None else []
