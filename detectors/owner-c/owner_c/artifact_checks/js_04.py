"""JS-04: synchronous fs / child_process / crypto / zlib calls inside functions, confirmed by a V8 CPU profile."""
from owner_c.js.profile import confirm_cpu

KEY = "JS-04"
LANGUAGE = "javascript"
DETECTOR_VERSION = "1.0.0"
PROFILER = "node-cpu"
SETTINGS = {"min_time_share": (0, 1)}  # (exclusive minimum, inclusive maximum)
NOQA = ("n/no-sync", "node/no-sync", "no-sync")
REFS = [
    "https://github.com/eslint-community/eslint-plugin-n/blob/master/docs/rules/no-sync.md",
    "https://nodejs.org/en/learn/asynchronous-work/dont-block-the-event-loop",
]
RECOMMENDATION = ("Use the async variant (fs.promises.*, util.promisify(crypto.pbkdf2), child_process.execFile with a "
                  "callback) or move the work to a worker thread so the event loop stays free.")
LIMITATION = ("Needs a client-produced V8 CPU profile (`node --no-opt --cpu-prof`). Calls at module top level are "
              "ignored (start-up work), and a function that only runs at start-up can still match if the profile covers it.")

SYNC_CALLS = {
    "fs": {"readFileSync", "writeFileSync", "appendFileSync", "readdirSync", "statSync", "lstatSync", "existsSync",
           "mkdirSync", "rmSync", "rmdirSync", "unlinkSync", "copyFileSync", "renameSync", "accessSync", "readlinkSync",
           "realpathSync", "mkdtempSync", "openSync", "readSync", "writeSync", "closeSync", "fstatSync", "cpSync",
           "truncateSync", "symlinkSync", "linkSync", "chmodSync", "chownSync", "utimesSync", "opendirSync"},
    "child_process": {"execSync", "execFileSync", "spawnSync"},
    "crypto": {"pbkdf2Sync", "scryptSync", "randomFillSync", "generateKeyPairSync", "hkdfSync"},
    "zlib": {"gzipSync", "gunzipSync", "deflateSync", "inflateSync", "brotliCompressSync", "brotliDecompressSync",
             "deflateRawSync", "inflateRawSync", "unzipSync"},
}


def find(ctx) -> list:
    out = []
    for node in ctx.walk():
        if node.type != "call_expression" or ctx.enclosing_function(node) is None:
            continue
        resolved = ctx.resolve_call(node)
        if resolved is None or resolved[1] not in SYNC_CALLS.get(resolved[0], ()):
            continue
        out.append(ctx.candidate(node, f"{ctx.qualname(node)}:{resolved[0]}.{resolved[1]}",
                                 f"{resolved[0]}.{resolved[1]}() blocks the event loop"))
    return out


def confirm(candidate, data, settings):
    return confirm_cpu(candidate, data, settings, "blocking call")
