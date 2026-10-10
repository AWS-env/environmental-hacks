"""LLM-11: re-embedding or re-running inference on unchanged inputs (static proxy).

Detector semantics version 1.0.0. Flags two shapes in Python source: (1) an embedding call that
embeds a whole corpus read in the same file from a location fixed in the code (document loaders,
directory readers, glob/listdir, `open`, pandas, S3 listings), runs unconditionally, and sits in code
that runs repeatedly (the file also queries the index, or the call is in a Lambda handler,
route/task function or `while True` loop), when the file has no change detection (content hash,
mtime/ETag, LangChain indexing API, CacheBackedEmbeddings, LlamaIndex IngestionPipeline); (2) an
LLM or embedding call in a `for` loop whose request does not depend on the loop. Anything that
cannot be resolved statically is not flagged. Python only; static only.
"""

from __future__ import annotations

import ast
import sys

from . import static
from .llmcalls import call_keywords, llm_calls, resolve
from .obs04 import CONTEXT_PARAMS, EVENT_PARAMS, LAMBDA_HANDLER_NAMES, TEST_PATH
from .static import EvaluationError, Hit, fingerprint  # noqa: F401  (re-exported for the CLI/tests)

CHECK_ID = "LLM-11"
DETECTOR_VERSION = "1.0.0"
NOQA = ("LLM-11", "LLM11")

FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPES = FUNCS + (ast.Lambda, ast.ClassDef)

# --- embedding calls -----------------------------------------------------------------------
FRAMEWORK_ROOTS = frozenset({"llama_index", "gpt_index"})  # plus every `langchain*` package
FROM_METHODS = frozenset({"from_documents", "from_texts", "afrom_documents", "afrom_texts"})
ADD_METHODS = frozenset({"add_documents", "add_texts", "aadd_documents", "aadd_texts"})
EMBED_METHODS = frozenset({"embed_documents", "aembed_documents"})
DATA_KEYWORDS = ("documents", "texts")
LLAMA_VECTOR_INDEXES = ("VectorStoreIndex",)
LEXICAL_RETRIEVERS = ("BM25Retriever", "TFIDFRetriever")

# --- bulk corpus reads ---------------------------------------------------------------------
LOADER_METHODS = frozenset({"load", "load_and_split", "lazy_load", "aload", "alazy_load"})
READER_METHODS = frozenset({"load_data", "aload_data"})
FS_CALLS = frozenset({"glob.glob", "glob.iglob", "os.listdir", "os.walk", "os.scandir", "open", "io.open"})
PATH_METHODS = frozenset({"glob", "rglob", "iterdir"})
S3_LISTINGS = frozenset({"list_objects", "list_objects_v2"})
# A corpus is "unchanged input" only when its location is fixed in the code (not an upload or a parameter).
LOCATION_KEYWORDS = frozenset({
    "path", "file_path", "input_dir", "input_files", "dir_path", "directory", "folder_path", "web_path",
    "web_paths", "url", "urls", "file", "files", "pathname", "top", "path_or_buf", "filepath_or_buffer",
})
FIXED_CALLS = frozenset({
    "os.path.join", "os.path.abspath", "os.path.dirname", "os.path.expanduser", "os.path.realpath", "os.getenv",
    "os.environ.get", "pathlib.Path", "pathlib.PurePath", "str",
})
FIXED_METHODS = frozenset({"joinpath", "resolve", "absolute", "expanduser", "format"})
PATH_ATTRIBUTES = frozenset({"parent", "parents", "stem", "name"})
FIXED_DEPTH = 8
TAINT_METHODS = frozenset({"append", "extend", "insert", "add", "update"})

# --- signs that the embedding runs repeatedly ----------------------------------------------
QUERY_METHODS = frozenset({
    "as_retriever", "as_query_engine", "as_chat_engine", "similarity_search", "asimilarity_search",
    "similarity_search_with_score", "similarity_search_with_relevance_scores", "similarity_search_by_vector",
    "max_marginal_relevance_search", "amax_marginal_relevance_search", "embed_query", "aembed_query",
    "get_relevant_documents", "aget_relevant_documents",
})
QUERY_EMBEDDINGS = frozenset({"embeddings.create", "invoke_model", "invoke_model_with_response_stream"})
RECURRING_DECORATORS = frozenset({
    "route", "get", "post", "put", "patch", "delete", "websocket", "api_route", "on_event",
    "task", "shared_task", "periodic_task", "scheduled_job",
})

# --- change detection anywhere in the file -------------------------------------------------
HASH_MODULES = frozenset({"hashlib", "xxhash", "mmh3", "blake3"})
HASH_CALLS = frozenset({
    "md5", "sha1", "sha224", "sha256", "sha384", "sha512", "blake2b", "blake2s", "file_digest",
    "crc32", "adler32", "xxh64", "xxh3_64", "xxh128",
})
MTIME_NAMES = frozenset({"getmtime", "st_mtime", "st_mtime_ns"})
S3_VERSION_KEYS = frozenset({"ETag", "LastModified"})
INDEXING_API = frozenset({
    "langchain.indexes.index", "langchain.indexes.aindex",
    "langchain_core.indexing.index", "langchain_core.indexing.aindex",
})
CHANGE_TRACKING_CLASSES = ("RecordManager", "CacheBackedEmbeddings", "IngestionPipeline")
EXISTENCE_CALLS = frozenset({"isdir", "isfile", "is_dir", "is_file", "listdir", "count", "has_collection"})
REFRESH_METHODS = frozenset({"refresh_ref_docs", "arefresh_ref_docs"})

# --- loop-invariant inference --------------------------------------------------------------
CONDITIONAL_IN_LOOP = (
    ast.If, ast.IfExp, ast.Try, getattr(ast, "TryStar", ast.Try), ast.With, ast.AsyncWith, ast.Match, ast.BoolOp,
)
COUNTING_ITERABLES = frozenset({"range", "itertools.count", "itertools.repeat", "itertools.cycle"})
PURE_REQUEST_CALLS = frozenset({"json.dumps"})
READ_ONLY_CALLS = frozenset({"print", "len"})

REFERENCES = (
    "https://blog.langchain.dev/syncing-data-sources-to-vector-stores/",
    "https://reference.langchain.com/python/langchain-core/indexing",
    "https://developers.llamaindex.ai/python/framework/module_guides/loading/ingestion_pipeline/",
    "https://docs.aws.amazon.com/bedrock/latest/userguide/kb-data-source-sync-ingest.html",
    "https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus02-bp02.html",
)
RECOMMENDATION = (
    "Embed only what changed: persist the index and load it instead of rebuilding it at startup, and index "
    "through a change-tracking layer, such as the LangChain indexing API (index() with a RecordManager and "
    "cleanup='incremental'), a LlamaIndex IngestionPipeline with a docstore and cache, an Amazon Bedrock "
    "Knowledge Base with incremental sync, or a content hash per document checked before embedding. Wrap "
    "embeddings in CacheBackedEmbeddings when the same text may be embedded again. Move an LLM call whose "
    "request does not depend on the loop out of the loop."
)
LIMITATION = (
    "Static proxy only: LLM-11 proves that a file embeds a whole corpus it reads itself from a fixed location "
    "on every run with no change detection, or repeats an identical LLM request per loop element, not that "
    "the inputs are unchanged between runs; no tokens or costs are measured. Covered (Python): LangChain "
    "vector store from_documents/from_texts/add_documents/add_texts and embed_documents, LlamaIndex "
    "VectorStoreIndex.from_documents, OpenAI embeddings.create and Bedrock invoke_model with an embedding "
    "model, fed from loaders/readers, glob/listdir/walk, open, pandas.read_*, load_dataset or S3 listings "
    "whose location is a literal, constant, environment variable or path join. A corpus embedding is flagged "
    "only when it is unconditional, its builder is not only called behind an existence check, and the file "
    "queries the index or runs it in a handler, route/task function or while True loop. Not flagged: files "
    "with any content hash, mtime/ETag check, LangChain indexing API, RecordManager, CacheBackedEmbeddings or "
    "LlamaIndex IngestionPipeline/refresh_ref_docs; ingest-only scripts; uploads, parameters and other "
    "variable locations; BM25/TF-IDF retrievers and non-vector LlamaIndex indexes; test files and scopes; "
    "notebooks; Haystack, sentence-transformers and the Cohere SDK. Repeated inference is only judged in for "
    "loops over a collection (not range), outside try/with/if blocks and without break/return."
)


def _own(scope):
    """Nodes in `scope`, excluding the bodies of nested functions and classes."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _scope_of(ctx, node):
    return next((a for a in ctx.ancestors(node) if isinstance(a, SCOPES)), ctx.tree)


def _framework(dotted):
    root = (dotted or "").split(".")[0]
    return root.startswith("langchain") or root in FRAMEWORK_ROOTS


def _name_of(ctx, func):
    """Last part of a callee: `DirectoryLoader` for `DirectoryLoader(...)`, `load` for `x.load`."""
    dotted = ctx.dotted(func)
    if dotted:
        return dotted.rsplit(".", 1)[-1]
    return func.attr if isinstance(func, ast.Attribute) else None


def _data_argument(call, keywords):
    """The first positional argument, or the first of `keywords` passed."""
    if call.args:
        return call.args[0]
    return next((call_kw.value for call_kw in call.keywords if call_kw.arg in keywords), None)


def _imports(ctx, root):
    return any(path.split(".")[0] == root for path in ctx.aliases.values())


class _Scan:
    """Embedding calls, corpus reads and corpus-derived names in one file."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.llm = list(llm_calls(ctx))
        self.bedrock_embeddings = {id(call.node) for call in self.llm if self._bedrock_embedding(call)}
        self._taint = {}
        self.grown = {
            node.func.value.id for node in ast.walk(ctx.tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in TAINT_METHODS and isinstance(node.func.value, ast.Name)
        }

    def _bedrock_embedding(self, call):
        """Bedrock invoke_model whose modelId mentions `embed` (literal, constant or env default)."""
        if call.provider != "bedrock" or not call.api.startswith("invoke_model"):
            return False
        model = (call_keywords(self.ctx, call.node) or {}).get("modelId")
        if model is None:
            return False
        nodes = (model, resolve(self.ctx, model))
        return any("embed" in ast.unparse(node).lower() for node in nodes if node is not None)

    def sink(self, call):
        """(label, data node) for an embedding call, else None."""
        ctx = self.ctx
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
            return None
        func, method = call.func, call.func.attr
        if method in FROM_METHODS:
            receiver = ctx.dotted(func.value) or ""
            name = receiver.rsplit(".", 1)[-1]
            if receiver.split(".")[0] in FRAMEWORK_ROOTS:
                embeds = name.endswith(LLAMA_VECTOR_INDEXES)  # Summary/Keyword/Tree indexes do not embed
            else:
                embeds = _framework(receiver) and not name.endswith(LEXICAL_RETRIEVERS)
            return (f"{name}.{method}", _data_argument(call, DATA_KEYWORDS)) if embeds else None
        if method in ADD_METHODS | EMBED_METHODS:
            built = resolve(ctx, func.value)
            if isinstance(built, ast.Call) and _framework(ctx.dotted(built.func)):
                return method, _data_argument(call, DATA_KEYWORDS)
            return None
        if id(call) in self.bedrock_embeddings:
            return method, (call_keywords(ctx, call) or {}).get("body")
        openai_v1 = method == "create" and isinstance(func.value, ast.Attribute) and func.value.attr == "embeddings"
        if (openai_v1 and _imports(ctx, "openai")) or ctx.dotted(func) == "openai.Embedding.create":
            return "embeddings.create", (call_keywords(ctx, call) or {}).get("input")
        return None

    def fixed(self, node, depth=0):
        """True if `node` is a location fixed in the code: literals, constants, env lookups, path joins."""
        ctx = self.ctx
        if node is None or depth > FIXED_DEPTH:
            return False
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, ast.Name):
            if node.id == "__file__" or node.id in ctx.aliases:
                return True
            if node.id in self.grown:
                return False  # `texts = []` filled with `texts.append(...)`
            value = resolve(ctx, node)
            return value is not None and not isinstance(value, ast.Name) and self.fixed(value, depth + 1)
        if isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                return node.value.id in ctx.aliases  # `config.DATA_DIR`, `os.sep`; not `self.path`
            return node.attr in PATH_ATTRIBUTES and self.fixed(node.value, depth + 1)
        if isinstance(node, ast.Call):
            dotted = ctx.dotted(node.func) or ""
            method = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if dotted not in FIXED_CALLS and not (method in FIXED_METHODS and self.fixed(node.func.value, depth + 1)):
                return False
            return all(self.fixed(arg, depth + 1) for arg in node.args + [kw.value for kw in node.keywords])
        children = {
            ast.JoinedStr: lambda n: n.values, ast.FormattedValue: lambda n: [n.value],
            ast.BinOp: lambda n: [n.left, n.right], ast.List: lambda n: n.elts, ast.Tuple: lambda n: n.elts,
            ast.Subscript: lambda n: [n.value, n.slice],
        }.get(type(node))
        return children is not None and all(self.fixed(child, depth + 1) for child in children(node))

    def _fixed_location(self, call):
        """True if a loader/reader/listing call reads a location fixed in the code."""
        if call.args:
            return self.fixed(call.args[0])
        location = [kw.value for kw in call.keywords if kw.arg in LOCATION_KEYWORDS]
        if location:
            return self.fixed(location[0])
        return all(self.fixed(kw.value) for kw in call.keywords if kw.arg is not None)

    def corpus_read(self, node):
        """Label of a bulk read of a fixed corpus location (`DirectoryLoader.load`, `glob.glob`, ...), else None."""
        ctx = self.ctx
        if not isinstance(node, ast.Call):
            return None
        dotted = ctx.dotted(node.func) or ""
        if dotted in FS_CALLS or dotted == "datasets.load_dataset" or dotted.startswith("pandas.read_"):
            return dotted if self._fixed_location(node) else None
        if not isinstance(node.func, ast.Attribute):
            return None
        method = node.func.attr
        if method in LOADER_METHODS | READER_METHODS:
            built = resolve(ctx, node.func.value)
            name = _name_of(ctx, built.func) if isinstance(built, ast.Call) else None
            if name and (name.endswith("Loader") or (method in READER_METHODS and name.endswith("Reader"))):
                load_args = node.args + [kw.value for kw in node.keywords]  # `load_data(texts=request_texts)`
                fixed = self._fixed_location(built) and all(self.fixed(arg) for arg in load_args)
                return f"{name}.{method}" if fixed else None
            return None
        receiver = node.func.value
        if method in PATH_METHODS and not (isinstance(receiver, ast.Name) and receiver.id == "glob"):
            return f"Path.{method}" if self.fixed(receiver) and self._fixed_location(node) else None
        if method in S3_LISTINGS:
            keywords = call_keywords(ctx, node) or {}
            return method if "Bucket" in keywords and all(self.fixed(v) for v in keywords.values()) else None
        return None

    def derived(self, node, tainted):
        """Label of the corpus read `node` is computed from, else None; the earliest read wins."""
        direct = None
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load) and sub.id in tainted:
                return tainted[sub.id]  # `open(path)` with `path` from glob.glob() reports glob.glob
            direct = direct or self.corpus_read(sub)
        return direct

    def tainted(self, scope):
        """{name: corpus-read label} for names in `scope` computed from a bulk corpus read."""
        if id(scope) in self._taint:
            return self._taint[id(scope)]
        own = list(_own(scope))
        tainted = {}
        if scope is not self.ctx.tree:
            local = {n.id for n in own if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                local |= {arg.arg for arg in ast.walk(scope.args) if isinstance(arg, ast.arg)}
            inherited = self.tainted(_scope_of(self.ctx, scope))
            tainted = {name: label for name, label in inherited.items() if name not in local}
        changed = True
        while changed:
            changed = False
            for node in own:
                pairs = []
                if isinstance(node, ast.Assign):
                    pairs = [(target, node.value) for target in node.targets]
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value is not None:
                    pairs = [(node.target, node.value)]
                elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    pairs = [(node.target, node.iter)]
                elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                    pairs = [(node.optional_vars, node.context_expr)]
                elif (
                    isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in TAINT_METHODS and isinstance(node.func.value, ast.Name)
                ):
                    pairs = [(node.func.value, arg) for arg in node.args]
                for target, value in pairs:
                    label = self.derived(value, tainted)
                    if not label:
                        continue
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name) and name.id not in tainted:
                            tainted[name.id] = label
                            changed = True
        self._taint[id(scope)] = tainted
        return tainted


# --- 1. whole corpus re-embedded on every run ----------------------------------------------


def _changes_tracked(ctx):
    """True if the file shows any change detection: hashing, mtime/ETag, indexing API, caches."""
    for path in ctx.aliases.values():
        if path.split(".")[0] in HASH_MODULES or path in INDEXING_API:
            return True
        if path.rsplit(".", 1)[-1].endswith(CHANGE_TRACKING_CLASSES):
            return True
    for node in ast.walk(ctx.tree):
        if isinstance(node, ast.Call):
            name = _name_of(ctx, node.func) or ""
            if name in HASH_CALLS or name in REFRESH_METHODS:
                return True
        elif isinstance(node, ast.Attribute) and node.attr in MTIME_NAMES:
            return True
        elif isinstance(node, ast.Name) and node.id in MTIME_NAMES:
            return True
        elif isinstance(node, ast.Constant) and node.value in S3_VERSION_KEYS:
            return True
    return False


def _is_main_guard(test):
    return (
        isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__"
        and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in test.comparators)
    )


def _trivial(test, tainted):
    """`if not docs:` / `if len(chunks) == 0:`: an emptiness test on the corpus is not change detection."""
    if _is_main_guard(test):
        return True
    names = [n for n in ast.walk(test) if isinstance(n, ast.Name)]
    if any(isinstance(n, (ast.Attribute, ast.Subscript, ast.Await)) for n in ast.walk(test)):
        return False
    return any(n.id in tainted for n in names) and all(n.id in tainted or n.id in ("len", "bool") for n in names)


def _exits(statement):
    for node in [statement, *_own(statement)]:
        if isinstance(node, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
            if ast.unparse(node.func) in ("sys.exit", "exit", "quit"):
                return True
    return False


def _guarded(ctx, call, scope, tainted, existence_only=False):
    """True if `call` only runs under a condition (an existence check, a fallback, an early exit).

    Inside the embedding's own scope any non-trivial condition counts. At a call site of the builder
    (`existence_only`), only existence checks and `except` fallbacks count, so `if request.method ==
    "POST": build()` still rebuilds on every request.
    """
    def counts(test):
        return _checks_existence(test) if existence_only else not _trivial(test, tainted)

    child = call
    for ancestor in ctx.ancestors(call):
        if isinstance(ancestor, (ast.If, ast.IfExp)) and counts(ancestor.test):
            return True
        if isinstance(ancestor, ast.ExceptHandler):
            return True
        if not existence_only and isinstance(ancestor, (ast.Match, ast.BoolOp)):
            return True
        if not existence_only and isinstance(ancestor, ast.While) and not (
            isinstance(ancestor.test, ast.Constant) and ancestor.test.value is True
        ):
            return True
        for field in ("body", "orelse", "finalbody"):
            statements = getattr(ancestor, field, None)
            if isinstance(statements, list) and child in statements:
                for earlier in statements[:statements.index(child)]:
                    if isinstance(earlier, ast.If) and counts(earlier.test) and _exits(earlier):
                        return True
        if ancestor is scope:
            return False
        child = ancestor
    return False


def _checks_existence(test):
    """`os.path.exists(DIR)`, `Path(p).is_dir()`, `index_exists()`, `collection.count()`: an existing index."""
    for node in ast.walk(test):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if "exist" in name.lower() or name in EXISTENCE_CALLS:
                return True
    return False


def _queries(ctx, scan, corpus_sinks):
    """True if the file also reads from an index: retrieval calls or an embedding of a non-corpus input."""
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in QUERY_METHODS:
            return True
        found = scan.sink(node) if id(node) not in corpus_sinks else None
        if found and found[0] in QUERY_EMBEDDINGS and found[1] is not None:
            if not scan.derived(found[1], scan.tainted(_scope_of(ctx, node))):
                return True  # a query embedding
    return False


def _decorator_name(ctx, decorator):
    return _name_of(ctx, decorator.func if isinstance(decorator, ast.Call) else decorator)


def _entry_point(ctx, call):
    """Description of the recurring entry point `call` runs in, else None."""
    for ancestor in ctx.ancestors(call):
        if isinstance(ancestor, ast.While) and isinstance(ancestor.test, ast.Constant) and ancestor.test.value is True:
            return "a while True loop"
        if isinstance(ancestor, FUNCS):
            params = [a.arg for a in ancestor.args.posonlyargs + ancestor.args.args]
            if len(params) >= 2 and params[1] in CONTEXT_PARAMS and (
                ancestor.name in LAMBDA_HANDLER_NAMES or params[0] in EVENT_PARAMS
            ):
                return f"the Lambda handler {ancestor.name}()"
            for decorator in ancestor.decorator_list:
                if _decorator_name(ctx, decorator) in RECURRING_DECORATORS:
                    return f"{ancestor.name}(), decorated with @{ast.unparse(decorator)}"
    return None


def _only_called_guarded(ctx, scan, function):
    """True if every in-file call of `function` (`f()`, `self.f()`) sits behind an existence check or fallback."""
    if not isinstance(function, FUNCS):
        return False
    sites = [
        node for node in ast.walk(ctx.tree)
        if isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) and node.func.id == function.name
            or isinstance(node.func, ast.Attribute) and node.func.attr == function.name
            and isinstance(node.func.value, ast.Name) and node.func.value.id in ("self", "cls")
        )
    ]
    return bool(sites) and all(
        _guarded(ctx, site, _scope_of(ctx, site), {}, existence_only=True) for site in sites
    )


def _reembedding(ctx, scan):
    if _changes_tracked(ctx):
        return
    candidates = []
    for node in ast.walk(ctx.tree):
        found = scan.sink(node)
        if found is None or found[1] is None:
            continue
        label, data = found
        scope = _scope_of(ctx, node)
        tainted = scan.tainted(scope)
        source = scan.derived(data, tainted)
        if source and not _guarded(ctx, node, scope, tainted) and not _only_called_guarded(ctx, scan, scope):
            candidates.append((node, label, source))
    if not candidates:
        return
    queried = _queries(ctx, scan, {id(node) for node, _, _ in candidates})
    for node, label, source in candidates:
        entry = _entry_point(ctx, node)
        if entry:
            why = f"it runs in {entry}"
        elif queried:
            why = "the same file queries the index, so every process start rebuilds it"
        else:
            continue  # an ingest-only script may run once
        yield Hit(
            node=node,
            anchor=f"{ctx.qualname(node)}:reembed:{label}",
            summary=(
                f"{label}() embeds the whole corpus read by {source}() on every run ({why}), with no content "
                "hash, record manager or existence check in the file: unchanged documents are embedded and "
                "billed again each time."
            ),
            confidence="medium",
        )


# --- 2. the same inference request re-sent in a loop ---------------------------------------


def _base_name(node):
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _variant_names(ctx, loop, call):
    """Names a loop iteration may change: targets, stores, mutated receivers, arguments of other calls."""
    inside = {id(n) for n in ast.walk(call)}
    names = {n.id for n in ast.walk(loop.target) if isinstance(n, ast.Name)}
    for statement in loop.body + loop.orelse:
        for node in ast.walk(statement):
            if id(node) in inside:
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            elif isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(_base_name(node))
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    names.add(_base_name(node.func.value))
                if (ctx.dotted(node.func) or "") not in READ_ONLY_CALLS:
                    for arg in node.args + [kw.value for kw in node.keywords]:
                        names.update(n.id for n in ast.walk(arg) if isinstance(n, ast.Name))
    names.discard(None)
    return names


def _invariant_request(ctx, call, variant):
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(kw.arg is None for kw in call.keywords):
        return False
    for value in call.args + [kw.value for kw in call.keywords]:
        for node in ast.walk(value):
            if isinstance(node, ast.Call) and (ctx.dotted(node.func) or "") not in PURE_REQUEST_CALLS:
                return False
            if isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr, ast.Lambda)):
                return False
            if isinstance(node, ast.Name) and node.id in variant:
                return False
    return True


def _repeat_loop(ctx, call):
    """The `for` loop over a collection that repeats `call` unconditionally, else None."""
    loop = ctx.enclosing_loop(call)
    if not isinstance(loop, (ast.For, ast.AsyncFor)):
        return None
    for ancestor in ctx.ancestors(call):
        if ancestor is loop:
            break
        if isinstance(ancestor, CONDITIONAL_IN_LOOP):
            return None
    iterable = loop.iter.args[0] if (
        isinstance(loop.iter, ast.Call) and ctx.dotted(loop.iter.func) == "enumerate" and loop.iter.args
    ) else loop.iter
    if isinstance(iterable, ast.Call) and (ctx.dotted(iterable.func) or "") in COUNTING_ITERABLES:
        return None
    for statement in loop.body:
        for node in [statement, *_own(statement)]:
            if isinstance(node, ast.Return):
                return None
            if isinstance(node, ast.Break) and ctx.enclosing_loop(node) is loop:
                return None
    return loop


def _loop_invariant(ctx, scan, flagged):
    calls = [(call.node, call.api, "low" if call.evidence == "chain" else "medium") for call in scan.llm]
    for node in ast.walk(ctx.tree):
        found = scan.sink(node) if isinstance(node, ast.Call) else None
        if found and found[0] == "embeddings.create":
            calls.append((node, found[0], "medium"))
    for call, api, confidence in calls:
        if id(call) in flagged:
            continue
        loop = _repeat_loop(ctx, call)
        if loop is None or not _invariant_request(ctx, call, _variant_names(ctx, loop, call)):
            continue
        header = f"for {ast.unparse(loop.target)} in {ast.unparse(loop.iter)}"
        if len(header) > 80:
            header = header[:77] + "..."
        yield Hit(
            node=call,
            anchor=f"{ctx.qualname(call)}:loop-invariant:{api}",
            summary=(
                f"{api}() sends the same request on every iteration of `{header}`: no argument depends on "
                "the loop, so identical inference is re-run once per element instead of once."
            ),
            confidence=confidence,
        )


def _in_test(ctx, node):
    """True inside a `test*` function or `Test*` class."""
    return any(
        isinstance(scope, FUNCS) and scope.name.startswith("test")
        or isinstance(scope, ast.ClassDef) and scope.name.startswith("Test")
        for scope in ctx.ancestors(node)
    )


def run(ctx):
    if TEST_PATH.search(ctx.path):
        return []
    scan = _Scan(ctx)
    hits = list(_reembedding(ctx, scan))
    hits += _loop_invariant(ctx, scan, {id(hit.node) for hit in hits})
    return [hit for hit in hits if not _in_test(ctx, hit.node)]


def evaluate(payload):
    """Evaluate a contract v1 input payload and return a contract v1 result payload."""
    return static.evaluate_static(payload, sys.modules[__name__])
