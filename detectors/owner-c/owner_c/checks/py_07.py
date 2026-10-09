"""PY-07: blocking calls inside `async def` (stall the event loop)."""
import ast

KEY = "PY-07"
DETECTOR_VERSION = "1.0.0"
NOQA = ("ASYNC",)
REFS = ["https://docs.astral.sh/ruff/rules/blocking-http-call-in-async-function/"]
RECOMMENDATION = ("Use an async equivalent (asyncio.sleep, httpx.AsyncClient, an async DB driver or file "
                  "library) or run the call with asyncio.to_thread().")
LIMITATION = ("Static pattern only: calls are matched by name, so blocking calls made through helper functions "
              "are not detected, and the time spent blocked is not measured.")

BLOCKING_CALLS = {
    "time.sleep", "os.system", "urllib.request.urlopen",
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
    "sqlite3.connect", "psycopg2.connect", "pymysql.connect",
}
for _verb in ("get", "post", "put", "delete", "patch", "head", "request"):
    BLOCKING_CALLS.add(f"requests.{_verb}")
    BLOCKING_CALLS.add(f"httpx.{_verb}")


def run(ctx):
    out = []
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = ctx.dotted(node.func)
        if not dotted or not ctx.in_async(node):
            continue
        if dotted in BLOCKING_CALLS:
            out.append(ctx.hit(
                node, f"{ctx.qualname(node)}:{dotted}",
                f"Blocking call {dotted}() inside async def stalls the event loop.", "medium"))
        elif dotted == "open":
            out.append(ctx.hit(
                node, f"{ctx.qualname(node)}:open",
                "Synchronous open() inside async def blocks the event loop on file I/O.", "low"))
    return out
