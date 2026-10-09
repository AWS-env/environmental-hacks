"""FE-03: `<img>` without explicit dimensions (static, HTML and JSX).

"Sized" follows Lighthouse's `unsized-images` audit: width+height, width+aspect-ratio or height+aspect-ratio.
"""
import re

KEY = "FE-03"
LANGUAGE = "web"
DETECTOR_VERSION = "1.0.0"
NOQA = ()
SUFFIXES = (".html", ".htm", ".jsx", ".tsx", ".js")
REFS = [
    "https://web.dev/articles/browser-level-image-lazy-loading",
    "https://raw.githubusercontent.com/GoogleChrome/lighthouse/main/core/audits/unsized-images.js",
]
RECOMMENDATION = ("Set both `width` and `height` on the image (or one of them plus a CSS `aspect-ratio`) so the browser "
                  "reserves its space before it loads; this avoids layout shifts and re-layout work.")
LIMITATION = ("Static check: an external or computed stylesheet can size an image, so images with a `class` or `id` are "
              "reported at low confidence. Template placeholders and JSX expression values count as present, and a JSX "
              "element with spread props (`{...props}`) is not flagged because the props may carry the dimensions. "
              "Lighthouse's rendered-box exemptions (0x0, hidden parents) cannot be evaluated statically. Responsive "
              "layouts may deliberately size images in CSS only.")

_NON_NEG_INT = re.compile(r"\s*\d+")  # parseInt(value, 10) >= 0 and no leading '+': digits first
_PLACEHOLDER = re.compile(r"\{\{|\{%|<%|\$\{")
_OUT_OF_FLOW = re.compile(r"position\s*:\s*(fixed|absolute)", re.I)
_CSS_UNSET = {"auto", "initial", "unset", "inherit", ""}


def _css_props(style: str) -> dict:
    props = {}
    for part in style.split(";"):
        if ":" in part:
            name, _, value = part.partition(":")
            props[name.strip().lower()] = value.strip().lower()
    return props


def _valid_attr(value) -> bool:
    if value is None:
        return False
    return bool(_PLACEHOLDER.search(value)) or bool(_NON_NEG_INT.match(value))


def _sized(attrs: dict) -> bool:
    style = _css_props(attrs.get("style") or "")
    width = _valid_attr(attrs.get("width")) or style.get("width", "auto") not in _CSS_UNSET
    height = _valid_attr(attrs.get("height")) or style.get("height", "auto") not in _CSS_UNSET
    ratio = style.get("aspect-ratio", "auto") not in _CSS_UNSET
    return (width and height) or (width and ratio) or (height and ratio)


def _exempt(attrs: dict) -> bool:
    src = (attrs.get("src") or "").strip().lower()
    if src.startswith("data:image/svg+xml"):
        return True
    return bool(_OUT_OF_FLOW.search(attrs.get("style") or ""))


def _anchor(src) -> str:
    return f"img:{src}" if src else "img:<no-src>"


def _judge(ctx, node, attrs, src, summary_src):
    if _exempt(attrs) or _sized(attrs):
        return None
    low = "class" in attrs or "id" in attrs
    return ctx.hit(node, _anchor(src), f"Image `{summary_src}` has no explicit width and height.",
                   "low" if low else "medium")


def _html_hits(ctx):
    out = []
    for node in ctx.walk():
        if node.type not in ("start_tag", "self_closing_tag"):
            continue
        name = next((c for c in node.children if c.type == "tag_name"), None)
        if name is None or ctx.text(name).lower() != "img":
            continue
        attrs = dict(ctx.attributes(node))
        hit = _judge(ctx, node, attrs, attrs.get("src"), attrs.get("src") or "<no src>")
        if hit:
            out.append(hit)
    return out


def _jsx_attrs(ctx, tag):
    """(attrs dict, has_spread) for a jsx opening/self-closing element. Expression values count as present."""
    attrs, spread = {}, False
    for child in tag.children:
        if child.type == "jsx_attribute":
            name = next((c for c in child.children if c.type == "property_identifier"), None)
            if name is None:
                continue
            value = next((c for c in child.children if c.type in ("string", "jsx_expression")), None)
            if value is None:
                attrs[ctx.text(name)] = ""
            elif value.type == "string":
                attrs[ctx.text(name)] = ctx.text(value)[1:-1]
            else:
                attrs[ctx.text(name)] = "{" + ctx.text(value)[1:-1].strip() + "}"
        elif child.type == "jsx_expression" and any(c.type == "spread_element" for c in child.children):
            spread = True
    return attrs, spread


def _jsx_hits(ctx):
    out = []
    for node in ctx.walk():
        if node.type not in ("jsx_self_closing_element", "jsx_opening_element"):
            continue
        name = next((c for c in node.children if c.type == "identifier"), None)
        if name is None or ctx.text(name) != "img":
            continue
        attrs, spread = _jsx_attrs(ctx, node)
        if spread:
            continue
        # JSX expression values are treated as present dimensions; a bare `{x}` for style is not parsed.
        src = attrs.get("src")
        for dim in ("width", "height"):
            if attrs.get(dim, "").startswith("{"):
                attrs[dim] = "0"
        hit = _judge(ctx, node, attrs, src, src or "<no src>")
        if hit:
            out.append(hit)
    return out


def run(ctx):
    return _html_hits(ctx) if ctx.kind == "html" else _jsx_hits(ctx)


def accepts(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(SUFFIXES) and ".min." not in lowered
