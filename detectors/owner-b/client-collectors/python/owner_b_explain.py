"""Owner B EXPLAIN collector for Python projects (SQLAlchemy, Django, or any DB-API 2.0 PostgreSQL driver).

Runs INSIDE THE CLIENT'S OWN CI / test run, against the client's own test database. The auditor never connects to a client
database and never executes client code; it only reads the artifact this module writes.

What it does for every distinct SELECT your tests issue:
    BEGIN; SET LOCAL statement_timeout = <ms>; EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) <your query>; ROLLBACK;
EXPLAIN ANALYZE really executes the query, so only plain SELECT / read-only WITH statements are explained; INSERT, UPDATE, DELETE,
DDL and data-modifying CTEs are never explained. Query parameters are NOT stored (the artifact keeps the statement with its
placeholders). Plans from a small CI database can differ from production: findings built from them are candidates.

Usage (SQLAlchemy):
    collector = ExplainCollector()
    install_sqlalchemy(engine, collector)          # before the test run
    ... run your tests ...
    collector.write_contract_inputs("explain-inputs/", repository_id="github:me/app", commit_sha=os.environ["GITHUB_SHA"])

Usage (Django):  with install_django(collector): ... run code ...      (or collector.attach_django(connection))
Usage (any driver): collector.record(cursor_factory, statement, params, locator)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import traceback
from contextlib import contextmanager
from pathlib import Path

FORMAT = "explain-plan-v1"
DETECTOR_VERSION = "1.0.0"
RULE_VERSION = "plan-1"
CHECK_CONTEXTS = {  # thresholds are the auditor's configured choices (see shreyas/docs/owner-b-decisions.md)
    "DB-43": {"min_rows_examined": 10000, "min_removed_ratio": 0.9},
    "DB-45": {"min_sort_space_kb": 0},
    "DB-46": {"min_inner_loops": 100, "min_rows_examined": 10000},
}
_READ_ONLY = re.compile(r"^\s*\(?\s*(select|with)\b", re.IGNORECASE)
_WRITES = re.compile(r"\b(insert|update|delete|merge|alter|create|drop|truncate|grant|revoke|copy|call|do|vacuum|lock|set)\b|\bfor\s+(update|share|no\s+key\s+update|key\s+share)\b|\binto\b\s+\w", re.IGNORECASE)
_PLACEHOLDERS = re.compile(r"%\(\w+\)s|%s|\$\d+|\?|:\w+")
# EXPLAIN ANALYZE prints the real parameter values inside condition text (Filter: (status = 'refunded')): redact them.
_CONDITION_KEYS = ("Filter", "Index Cond", "Recheck Cond", "Join Filter", "Hash Cond", "Merge Cond", "One-Time Filter", "TID Cond", "Order By", "Heap Fetches Cond")
_QUOTED = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"(?<![\w.$\"])-?\d+(?:\.\d+)?(?![\w\"])")


def _whole_number(text: str):
    value = float(text)
    return int(value) if value.is_integer() else value


def redact_plan(plan: list) -> list:
    """Deep copy of an EXPLAIN JSON array with literals removed from condition fields (plan structure and row counts stay).

    Integral floats (4890.00 in PostgreSQL's JSON) become ints: the Node detectors cannot tell 4890.0 from 4890, and the
    contract's evidence check compares canonical JSON, so the Python reference validator would otherwise reject the evidence.
    """
    clone = json.loads(json.dumps(plan), parse_float=_whole_number)

    def walk(node: dict) -> None:
        for key in _CONDITION_KEYS:
            if isinstance(node.get(key), str):
                node[key] = _NUMBER.sub("?", _QUOTED.sub("'?'", node[key]))
        for child in node.get("Plans", []):
            walk(child)

    walk(clone[0]["Plan"])
    return clone


def is_explainable(statement: str) -> bool:
    """Only plain reads: starts with SELECT/WITH and contains no write, DDL, locking or SELECT ... INTO keyword."""
    text = re.sub(r"'(?:[^']|'')*'", "''", statement)  # ignore keywords inside string literals
    return bool(_READ_ONLY.match(text)) and not _WRITES.search(text)


def normalise(statement: str) -> str:
    return re.sub(r"\s+", " ", statement).strip()


def _caller_locator() -> str:
    """First stack frame outside this module and the database libraries: where the query was issued (informational)."""
    skip = ("owner_b_explain", os.sep + "sqlalchemy" + os.sep, os.sep + "django" + os.sep + "db", os.sep + "psycopg", "site-packages")
    for frame in reversed(traceback.extract_stack()):
        if not any(part in frame.filename for part in skip):
            return f"{os.path.relpath(frame.filename)}:{frame.lineno}".replace(os.sep, "/")
    return "unknown"


class ExplainCollector:
    def __init__(self, dialect: str = "postgresql-16", max_queries: int = 200, statement_timeout_ms: int = 5000):
        self.dialect, self.max_queries, self.timeout = dialect, max_queries, statement_timeout_ms
        self.queries: dict[str, dict] = {}
        self.skipped = {"not_read_only": 0, "duplicate": 0, "limit": 0, "failed": 0}
        self._busy = threading.local()  # EXPLAIN issues queries of its own; never explain those

    # -- core ---------------------------------------------------------------------------------------------------
    def record(self, connection_factory, statement: str, params=None, locator: str | None = None) -> bool:
        """EXPLAIN one statement on a separate DB-API connection, in a transaction that is always rolled back."""
        if getattr(self._busy, "on", False):
            return False
        sql = normalise(statement)
        if not is_explainable(sql):
            self.skipped["not_read_only"] += 1
            return False
        key = hashlib.sha256(_PLACEHOLDERS.sub("?", sql).encode()).hexdigest()[:16]
        if key in self.queries:
            self.skipped["duplicate"] += 1
            return False
        if len(self.queries) >= self.max_queries:
            self.skipped["limit"] += 1
            return False
        self._busy.on = True
        conn = None
        try:
            conn = connection_factory()
            cur = conn.cursor()
            cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout)}")
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, params)
            row = cur.fetchone()[0]
            plan = redact_plan(row if isinstance(row, list) else json.loads(row))
            self.queries[key] = {"query_id": key, "locator": locator or _caller_locator(), "sql": sql, "plan": plan}
            return True
        except Exception:  # a query that cannot be explained (bad params, timeout) is skipped, never fatal to the test run
            self.skipped["failed"] += 1
            return False
        finally:
            try:
                if conn is not None:
                    conn.rollback()
                    conn.close()
            finally:
                self._busy.on = False

    # -- output -------------------------------------------------------------------------------------------------
    def artifact(self, query: dict) -> dict:
        analyzed = all(n.get("Actual Loops") is not None for n in _nodes(query["plan"][0]["Plan"]))
        return {"format": FORMAT, "acquisition": {"status": "complete"}, "dialect": self.dialect, "analyzed": analyzed,
                "locator": query["locator"], "sql": query["sql"], "plan": query["plan"]}

    def contract_inputs(self, repository_id: str, commit_sha: str, scan_id: str | None = None) -> list[dict]:
        """One contract v1 input per plan check, one artifact source per query (scope `query:<id>`)."""
        if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
            raise ValueError("commit_sha must be the full lowercase 40-character git SHA")
        sources = [{"source_id": f"plan:{q['query_id']}", "scope_id": f"query:{q['query_id']}", "kind": "artifact",
                    "locator": q["locator"], "data": self.artifact(q)} for q in self.queries.values()]
        scan = scan_id or "explain-" + hashlib.sha256(json.dumps(sorted(self.queries)).encode()).hexdigest()[:12]
        return [{"schema_version": "1.0", "kind": "input", "repository_id": repository_id, "scan_id": scan, "commit_sha": commit_sha,
                 "check_id": check, "detector_version": DETECTOR_VERSION,
                 "context": {"rule_version": RULE_VERSION, "mode": "candidate", **ctx},
                 "scope": [s["scope_id"] for s in sources], "sources": sources}
                for check, ctx in CHECK_CONTEXTS.items() if sources]

    def write_contract_inputs(self, directory: str, repository_id: str, commit_sha: str, scan_id: str | None = None) -> list[Path]:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for payload in self.contract_inputs(repository_id, commit_sha, scan_id):
            path = out / f"{payload['check_id'].lower()}-input.json"
            path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
            paths.append(path)
        return paths

    # -- integrations -------------------------------------------------------------------------------------------
    def attach_sqlalchemy(self, engine) -> None:
        from sqlalchemy import event  # imported lazily: SQLAlchemy is optional

        @event.listens_for(engine, "before_cursor_execute")
        def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ARG001
            if not executemany:
                self.record(engine.raw_connection, statement, parameters)

    def attach_django(self, connection) -> "contextlib.AbstractContextManager":
        """Context manager: explains every SELECT Django issues on `connection` while active (separate DB-API connection)."""
        def factory():
            import psycopg2  # the explain runs on its own connection to the same database
            s = connection.settings_dict
            return psycopg2.connect(dbname=s["NAME"], user=s["USER"], password=s["PASSWORD"], host=s["HOST"] or "localhost", port=s["PORT"] or 5432)

        def wrapper(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if not many:
                self.record(factory, sql, params)
            return result
        return connection.execute_wrapper(wrapper)


def _nodes(node):
    yield node
    for child in node.get("Plans", []):
        yield from _nodes(child)


def install_sqlalchemy(engine, collector: ExplainCollector) -> ExplainCollector:
    collector.attach_sqlalchemy(engine)
    return collector


@contextmanager
def install_django(collector: ExplainCollector, alias: str = "default"):
    from django.db import connections
    with collector.attach_django(connections[alias]):
        yield collector
