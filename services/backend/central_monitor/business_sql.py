"""Read-only SQL validation for Business Monitor queries (spec section 6.2).

Validation is done on a real parse tree per dialect (sqlglot), never on keyword lists alone, and the
worker executes the *normalized* SQL generated from that tree, so the text the database receives is the text
that was validated (no parser differential). This is defence in depth: the client database login must still
be read-only with SELECT only on the approved views/tables, which remains the primary protection.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIALECTS = {"sqlserver": "tsql", "postgres": "postgres"}
MAX_QUERY_CHARS = 20000
PARAM_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")
_PARAM_REF = re.compile(r"(?<![@\w])@([A-Za-z_]\w{0,62})")
_PLACEHOLDER = re.compile(r"__p_([A-Za-z_]\w{0,62})")

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.Declare, exp.Set, exp.Copy, exp.TruncateTable, exp.Transaction, exp.Commit, exp.Rollback, exp.Use,
    exp.Into, exp.Lock, exp.Kill, exp.Grant,
)
# Side effects, external access, file/OS access, locking and sleeping. Names are lower-case.
FORBIDDEN_FUNCTIONS = {
    "openrowset", "openquery", "opendatasource", "openjson_file", "xp_cmdshell", "sp_executesql",
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "waitfor", "dblink", "dblink_exec", "dblink_connect",
    "lo_import", "lo_export", "lo_unlink", "lo_create", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile",
    "set_config", "nextval", "setval", "currval", "txid_current", "pg_advisory_lock", "pg_advisory_xact_lock",
    "pg_try_advisory_lock", "query_to_xml", "table_to_xml", "cursor_to_xml", "copy",
}
FORBIDDEN_PREFIXES = ("xp_", "sp_", "pg_advisory", "lo_", "dblink")


class SqlRejected(ValueError):
    """The query is not an approved read-only SELECT."""


@dataclass(frozen=True)
class ValidatedQuery:
    dialect: str
    normalized_sql: str
    sha256: str
    parameters: tuple[str, ...]


def _function_names(tree: exp.Expression) -> set[str]:
    names: set[str] = set()
    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            names.add(str(node.name).lower())
        elif isinstance(node, exp.Func):
            names.add(type(node).__name__.lower())
            if node.name:
                names.add(str(node.name).lower())
    return names


def validate_query(sql: str, dialect: str, declared_parameters: dict[str, object] | None = None) -> ValidatedQuery:
    """Return the normalized, hashed query, or raise SqlRejected with a user-safe reason."""
    if dialect not in DIALECTS:
        raise SqlRejected("Unsupported database dialect")
    declared = declared_parameters or {}
    if not isinstance(sql, str) or not sql.strip():
        raise SqlRejected("Query is empty")
    if len(sql) > MAX_QUERY_CHARS:
        raise SqlRejected("Query is too long")
    if "@@" in sql:
        raise SqlRejected("System variables are not allowed")
    if "\x00" in sql:
        raise SqlRejected("Invalid character in query")
    for name in declared:
        if not isinstance(name, str) or not PARAM_NAME.fullmatch(name):
            raise SqlRejected("Invalid parameter name")
    used = set(_PARAM_REF.findall(sql))
    if used != set(declared):
        raise SqlRejected("Query parameters and declared parameters differ")
    read = DIALECTS[dialect]
    prepared = _PARAM_REF.sub(lambda m: f"__p_{m.group(1)}", sql)
    try:
        statements = sqlglot.parse(prepared, read=read)
    except SqlglotError as error:
        raise SqlRejected("Query could not be parsed for this dialect") from error
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise SqlRejected("Exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation)):
        raise SqlRejected("Only SELECT statements are allowed")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SqlRejected("Statement contains a forbidden operation")
        if isinstance(node, exp.Table) and (node.name.startswith(("#", "@")) or isinstance(node.this, exp.Parameter)
                                            or (isinstance(node.this, exp.Identifier) and node.this.args.get("temporary"))):
            raise SqlRejected("Temporary tables and table variables are not allowed")
        if isinstance(node, exp.Table) and len(node.parts) > 3:
            raise SqlRejected("Linked-server (four-part) names are not allowed")
    for name in _function_names(tree):
        if name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_PREFIXES):
            raise SqlRejected(f"Function {name} is not allowed")
    normalized = tree.sql(dialect=read)
    normalized = _PLACEHOLDER.sub(lambda m: f"@{m.group(1)}", normalized)
    return ValidatedQuery(dialect, normalized, hashlib.sha256(normalized.encode()).hexdigest(), tuple(sorted(used)))
