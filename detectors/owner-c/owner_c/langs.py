"""Per-language behaviour: which files a check accepts and how its parse context is built.

A check module declares `LANGUAGE` ("python" by default, "javascript", or "config") and may override
`accepts(path)`. The connector uses this to scope each check, the runner to parse the right way.
"""
from __future__ import annotations

import os

JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")


class ParseError(ValueError):
    """A file could not be parsed reliably, so it is not evaluated (never reported as clean)."""


def _is_generated_js(path: str) -> bool:
    name = os.path.basename(path).lower()
    return name.endswith((".min.js", ".bundle.js", ".d.ts", ".d.mts", ".d.cts")) or ".min." in name


def accepts(module, path: str) -> bool:
    custom = getattr(module, "accepts", None)
    if custom is not None:
        return custom(path)
    language = getattr(module, "LANGUAGE", "python")
    if language == "python":
        return path.endswith(".py")
    if language == "javascript":
        return path.endswith(JS_SUFFIXES) and not _is_generated_js(path)
    return False


def make_ctx(module, path: str, source: str):
    language = getattr(module, "LANGUAGE", "python")
    if language == "web":  # HTML/CSS with tree-sitter, package.json with json, JSX/TSX/JS with the JS grammars
        lowered = path.lower()
        if lowered.endswith((".html", ".htm")):
            from owner_c.web.ctx import HtmlCtx

            return HtmlCtx(path, source)
        if lowered.endswith(".css"):
            from owner_c.web.ctx import CssCtx

            return CssCtx(path, source)
        if os.path.basename(lowered) == "package.json":
            from owner_c.web.ctx import PackageCtx

            return PackageCtx(path, source)
        from owner_c.js.ctx import JsCtx

        ctx = JsCtx(path, source)
        ctx.kind = "jsx"
        return ctx
    if language == "javascript":
        from owner_c.js.ctx import JsCtx

        return JsCtx(path, source)
    if language == "config":
        from owner_c.config.ctx import ConfigCtx

        return ConfigCtx(path, source)
    from owner_c.common import Ctx

    return Ctx(path, source)
