"""FE-17: animating layout or paint properties instead of transform/opacity (static CSS)."""
import re

KEY = "FE-17"
LANGUAGE = "web"
DETECTOR_VERSION = "1.0.0"
NOQA = ()
REFS = [
    "https://web.dev/articles/stick-to-compositor-only-properties-and-manage-layer-count",
    "https://web.dev/articles/avoid-large-complex-layouts-and-layout-thrashing",
]
RECOMMENDATION = ("Animate `transform` and `opacity` instead (they can change without layout or paint); for size or "
                  "position changes use `transform: translate()/scale()` (the FLIP technique).")
LIMITATION = ("Static check of CSS files only: SCSS/Less, `<style>` blocks and inline styles are not analysed, "
              "`transition: all` and values set through `var(--custom-property)` cannot be resolved (found on "
              "bootstrap.css: `--bs-progress-bar-transition: width` is missed), range media queries such as "
              "`(width < 576px)` are rewritten to a neutral condition before parsing, and a @keyframes rule is reported even if no element in this file uses "
              "it. The property lists are explicit, not exhaustive. One-off transitions on rarely used elements are still "
              "reported and reduced-motion variants are not special-cased. Layout properties are reported at medium "
              "confidence and paint-only properties at low confidence.")

LAYOUT = {
    "width", "height", "min-width", "max-width", "min-height", "max-height", "top", "right", "bottom", "left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left", "padding", "padding-top",
    "padding-right", "padding-bottom", "padding-left", "border-width", "font-size", "line-height", "flex-basis", "gap",
}
PAINT = {"box-shadow", "background-color", "color", "border-color", "outline"}

_WS = re.compile(r"\s+")


def _confidence(prop):
    return "medium" if prop in LAYOUT else "low" if prop in PAINT else None


def _selector_text(ctx, rule):
    selectors = next((c for c in rule.children if c.type == "selectors"), None)
    return _WS.sub(" ", ctx.text(selectors)).strip() if selectors is not None else "<rule>"


def _block_declarations(ctx, node):
    block = next((c for c in node.children if c.type == "block"), None)
    return [d for d in (block.children if block is not None else []) if d.type == "declaration"]


def _transition_properties(ctx, decl, name):
    """Property names listed by `transition-property` or the `transition` shorthand (all/none skipped)."""
    values = [c for c in decl.children if c.type not in ("property_name", ":", ";", "important", "!")]
    props, expecting = [], True
    for child in values:
        if child.type == ",":
            expecting = True
            continue
        if expecting and child.type == "plain_value":
            props.append(ctx.text(child).lower())
        expecting = False
    return props


def run(ctx):
    out = []
    for node in ctx.walk():
        if node.type == "rule_set":
            selector = _selector_text(ctx, node)
            for decl in _block_declarations(ctx, node):
                name = ctx.text(decl.children[0]).lower()
                if name not in ("transition", "transition-property"):
                    continue
                for prop in _transition_properties(ctx, decl, name):
                    confidence = _confidence(prop)
                    if confidence:
                        out.append(ctx.hit(decl, f"transition:{selector}:{prop}",
                                           f"Transition on `{prop}` animates a {'layout' if confidence == 'medium' else 'paint'} property.",
                                           confidence))
        elif node.type == "keyframes_statement":
            name_node = next((c for c in node.children if c.type == "keyframes_name"), None)
            kf_name = ctx.text(name_node) if name_node is not None else "<anonymous>"
            seen = set()
            for frame in ctx.walk(node):
                if frame.type != "keyframe_block":
                    continue
                for decl in _block_declarations(ctx, frame):
                    prop = ctx.text(decl.children[0]).lower()
                    confidence = _confidence(prop)
                    if confidence and prop not in seen:
                        seen.add(prop)
                        out.append(ctx.hit(decl, f"keyframes:{kf_name}:{prop}",
                                           f"@keyframes `{kf_name}` animates `{prop}`, a {'layout' if confidence == 'medium' else 'paint'} property.",
                                           confidence))
    return out


def accepts(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(".css") and ".min." not in lowered
