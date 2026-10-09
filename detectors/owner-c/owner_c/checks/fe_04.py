"""FE-04: a JavaScript lazy-load library used for images instead of native `loading="lazy"` (static)."""
import os

KEY = "FE-04"
LANGUAGE = "web"
DETECTOR_VERSION = "1.0.0"
NOQA = ()
SUFFIXES = (".html", ".htm", ".jsx", ".tsx", ".js")
REFS = [
    "https://web.dev/articles/browser-level-image-lazy-loading",
    "https://caniuse.com/loading-lazy-attr",
]
RECOMMENDATION = ("Use the browser's native `loading=\"lazy\"` on `<img>` (and `<iframe>`) instead of a JavaScript "
                  "lazy-load library; keep the library only when you must support browsers without the attribute or "
                  "need finer thresholds or CSS background images.")
LIMITATION = ("Static check with an explicit list of four libraries (lazysizes, vanilla-lazyload, lozad, react-lazyload); "
              "other lazy-load libraries are not detected and import statements are not analysed. A library may be kept "
              "on purpose for legacy browsers, custom thresholds or CSS background images (native `loading` works only "
              "on <img> and <iframe>), so a dependency is reported at low confidence and library markup on an <img> "
              "at medium. No existing linter or audit flags this pattern.")

LIBRARIES = ("lazysizes", "vanilla-lazyload", "lozad", "react-lazyload")
_CLASS_TOKENS = {"lazyload", "lozad", "lazy"}
_SECTIONS = ("dependencies", "devDependencies")


def _package_hits(ctx):
    out = []
    for section in _SECTIONS:
        block = ctx.data.get(section)
        if not isinstance(block, dict):
            continue
        for name in LIBRARIES:
            if name in block:
                out.append(ctx.hit(ctx.dependency_line(section, name), f"dep:{name}",
                                   f"`{name}` is a JavaScript image lazy-loading library; native loading=\"lazy\" may replace it.",
                                   "low"))
    return sorted(out, key=lambda h: h.node.lineno)


def _flag(ctx, node, attrs, class_attr):
    tokens = set((attrs.get(class_attr) or "").split())
    data_src = attrs.get("data-src")
    if data_src is None or not tokens & _CLASS_TOKENS:
        return None
    if (attrs.get("loading") or "").lower() == "lazy":
        return None  # already native; the library is progressive enhancement
    return ctx.hit(node, f"markup:img:{data_src}",
                   f"Image `{data_src}` is lazy-loaded by a JavaScript library (class + data-src).", "medium")


def _html_hits(ctx):
    out = []
    for node in ctx.walk():
        if node.type not in ("start_tag", "self_closing_tag"):
            continue
        name = next((c for c in node.children if c.type == "tag_name"), None)
        if name is None or ctx.text(name).lower() != "img":
            continue
        hit = _flag(ctx, node, dict(ctx.attributes(node)), "class")
        if hit:
            out.append(hit)
    return out


def _jsx_hits(ctx):
    out = []
    for node in ctx.walk():
        if node.type not in ("jsx_self_closing_element", "jsx_opening_element"):
            continue
        name = next((c for c in node.children if c.type == "identifier"), None)
        if name is None or ctx.text(name) != "img":
            continue
        attrs = {}
        for child in node.children:
            if child.type != "jsx_attribute":
                continue
            key = next((c for c in child.children if c.type == "property_identifier"), None)
            value = next((c for c in child.children if c.type == "string"), None)
            if key is not None:
                attrs[ctx.text(key)] = ctx.text(value)[1:-1] if value is not None else ""
        hit = _flag(ctx, node, attrs, "className")
        if hit:
            out.append(hit)
    return out


def run(ctx):
    if ctx.kind == "package":
        return _package_hits(ctx)
    return _html_hits(ctx) if ctx.kind == "html" else _jsx_hits(ctx)


def accepts(path: str) -> bool:
    lowered = path.lower()
    return (lowered.endswith(SUFFIXES) and ".min." not in lowered) or os.path.basename(lowered) == "package.json"
