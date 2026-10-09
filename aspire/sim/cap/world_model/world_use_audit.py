"""Bounded, opt-in audit of whether a judgment-world policy used what it asked.

Two selected condition-C policies called ``world.update`` and never consumed a
world answer. The API/outer-recovery repair is separate and already accepted;
this module is only the *feedback* mechanism for the next version. It reads the
frozen policy source together with that trial's ordinary
``judgment_world/events.jsonl`` trace and says which of three things the evidence
supports:

``logging_only``
    No ``query`` call site at all, or every site's answer is discarded, printed,
    or read only in a branch whose body neither acts nor changes control flow.
``supported_use_candidate``
    A direct dependency from a query's return value into a motion-API argument or
    into a branch/loop condition whose body acts — *and* a query was recorded at
    that exact call site during the ordinary trial.
``inconclusive``
    The dependency runs through Python this small reader does not model (helper
    boundaries, containers, comprehensions, dynamic attribute access, a value
    bound in one code block and read in a later one), a consumer candidate has no
    corroborating recorded query, the recorded query's block-local line matches
    more than one call site, or a block did not parse.

It is deliberately a limited reader, not a dataflow framework and not a general
taint tracker. It defines no task predicate, no threshold, no per-action gate and
no minimum query count, and it never treats a print statement, a model
self-report, or a forced-output counterfactual as evidence. A corroborated
candidate is **not** a proof that the consumer statement executed, that the
world's judgment was right, or that task success improved — see ``LIMITS``.
Python it cannot follow is reported as inconclusive rather than pushed into
either verdict.

Mechanism status stays separate from protocol completion and simulator success:
callers report it beside the task result and never gate on it.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

# --- opt-in identity -------------------------------------------------------

REVISION = "r1"
PROFILE = "judgment"
CONDITION = "C"
ARTIFACT = "world_use_audit.json"
MAX_REPORTED_SITES = 24

NO_QUERY = "no_query"
LOGGING_ONLY = "logging_only"
SUPPORTED = "supported_use_candidate"
INCONCLUSIVE = "inconclusive"
IDENTITY_MISMATCH = "identity_mismatch"
STATUSES = (NO_QUERY, LOGGING_ONLY, SUPPORTED, INCONCLUSIVE, IDENTITY_MISMATCH)

# One corroborated use candidate anywhere in the frozen pair's executions is
# reported as supported; otherwise the most cautious observed status is reported.
AGGREGATE_ORDER = (SUPPORTED, IDENTITY_MISMATCH, INCONCLUSIVE, LOGGING_ONLY, NO_QUERY)

SUMMARY = {
    NO_QUERY: "the policy never calls world.query(): the world was updated but never consulted",
    LOGGING_ONLY: "every recorded query answer is discarded, printed, or read only in a branch that does not act",
    SUPPORTED: "at least one query answer reaches a motion argument or an acting branch, and a query was recorded at that call site",
    INCONCLUSIVE: "the evidence does not settle whether an answer was used; see the site notes",
    IDENTITY_MISMATCH: "the recorded query trace does not line up with the analyzed policy source",
}

LIMITS = (
    "A corroborated use candidate is static syntax plus a recorded query at that call site. "
    "It is not evidence that the consumer statement executed.",
    "Nothing here says the world's answer was correct, or that consuming it helped the task.",
    "No minimum query count, print statement, model self-report, or forced-output "
    "counterfactual is treated as proof of use.",
    "Dependencies through helper boundaries, containers, comprehensions or dynamic attribute "
    "access are reported inconclusive — that is missing coverage, not evidence of non-use.",
    "Queries written inside a class body or a parameter default are not located at all; a "
    "recorded query with no matching call site is reported as an identity mismatch.",
    "Recorded caller lines are block-local, so two blocks can share a line number. An "
    "ambiguous match is marked and corroborates no site exactly: it is reported inconclusive, "
    "never as corroboration of one of the candidates.",
    "The blocks run in order in one namespace, so a world import in an earlier block is "
    "honored. A value bound in one block and read in a later one is cross-block dataflow this "
    "reader does not model: it is reported inconclusive, never as bound and never read.",
    "A block that does not parse is reported inconclusive; no_query is claimed only when every "
    "block parsed and neither a call site nor a recorded query exists.",
    "Mechanism status is separate from protocol completion and simulator success, and never "
    "gates a trial, a selection, or a finalization.",
)


def revision_errors(case: dict) -> list[str]:
    """Reject a flagged case that is not the one revision this build implements."""
    revision = case.get("world_use_revision")
    if not revision:
        return []
    errors = []
    if revision != REVISION:
        errors.append(f"unknown world_use_revision {revision!r}; this build implements {REVISION!r}")
    if case.get("profile") != PROFILE:
        errors.append(f"world_use_revision requires profile {PROFILE!r}")
    if case.get("condition") != CONDITION:
        errors.append(f"world_use_revision is restricted to condition {CONDITION}")
    return errors


def enabled(case: dict) -> bool:
    """True only for a NEW condition-C judgment cell that opted in explicitly."""
    return bool(case.get("world_use_revision")) and not revision_errors(case)


# --- what counts as a use --------------------------------------------------

# Motion, gripper and IK entry points from the allowed public API. `solve_ik` is
# included because its argument IS the commanded target: a world value reaching
# it decided where the robot goes.
ACTION_CALLS = frozenset({
    "move_to_joints", "solve_ik", "goto_pose", "goto_home_joint_position",
    "open_gripper", "close_gripper",
})
# Calls that only render a value. A world answer reaching one of these is
# logging, exactly the behavior the two old policies showed.
LOG_CALLS = frozenset({"print", "repr", "str", "format", "pprint"})
# Value-shaping calls whose result depends only on their arguments, so a direct
# dependency can be followed through them without modelling anything. Kept short
# on purpose; anything else is inconclusive.
SHAPING_CALLS = frozenset({
    "float", "int", "bool", "abs", "min", "max", "round", "len", "list", "tuple",
    "np.array", "np.asarray", "np.clip", "np.float32", "np.float64", "np.sign",
    "numpy.array", "numpy.asarray",
})
# Expressions whose value depends only on their children.
FLOW_THROUGH = (ast.Subscript, ast.Attribute, ast.BinOp, ast.UnaryOp, ast.Compare,
                ast.BoolOp, ast.IfExp, ast.Tuple, ast.List, ast.Slice, ast.keyword)
# Expressions that end the chain: following them would be modelling, not reading.
OPAQUE = (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
          ast.Await, ast.Yield, ast.YieldFrom, ast.Starred, ast.JoinedStr,
          ast.Dict, ast.Set)


def code_blocks(source: str) -> list[dict]:
    """Split a policy exactly as ``replay_trial._parse_code_blocks`` does.

    The replay splits ``code.py`` on ``# Code block N`` headers, strips each
    block, and executes the blocks separately — so a recorded caller line is
    *block-local*, not a line in the file. Each block therefore carries the line
    offset needed to map it back, and its own digest.
    """
    marker = re.compile(r"^# Code block \d+[^\S\n]*\n", re.M)
    segments, cursor = [], 0
    for match in marker.finditer(source):
        segments.append((cursor, source[cursor:match.start()]))
        cursor = match.end()
    segments.append((cursor, source[cursor:]))
    blocks = []
    for start, raw in segments:
        text = raw.strip()
        if not text:
            continue
        lead = raw[:len(raw) - len(raw.lstrip())].count("\n")
        blocks.append({"index": len(blocks), "source": text,
                       "sha256": hashlib.sha256(text.encode()).hexdigest(),
                       "lines": text.count("\n") + 1,
                       "line_offset": source[:start].count("\n") + lead})
    return blocks


def _link_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._use_parent = parent


def _query_callables(tree: ast.AST) -> tuple[set[str], set[str]]:
    """How this source can reach ``world.query``: module aliases and name imports."""
    modules, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "world":
                    modules.add(alias.asname or "world")
        elif isinstance(node, ast.ImportFrom) and node.module == "world":
            for alias in node.names:
                if alias.name == "query":
                    names.add(alias.asname or "query")
    return modules, names


def _is_query_call(node: ast.AST, modules: set[str], names: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "query":
        return isinstance(func.value, ast.Name) and func.value.id in modules
    return isinstance(func, ast.Name) and func.id in names


def _call_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return f"{func.value.id}.{func.attr}"
        return func.attr
    return None


def _literal_name(call: ast.Call) -> str | None:
    """The queried name, when it is a plain string literal."""
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    for keyword in call.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant) \
                and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _flow(node: ast.AST) -> tuple[ast.AST, ast.AST | None]:
    """Walk out of the expressions whose value depends only on this one."""
    parent = getattr(node, "_use_parent", None)
    while isinstance(parent, FLOW_THROUGH) or (
            isinstance(parent, ast.Call) and _call_name(parent) in SHAPING_CALLS):
        node, parent = parent, getattr(parent, "_use_parent", None)
    return node, parent


def _statements(body: list[ast.stmt]):
    """Statements of one scope in source order, not entering a nested scope."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            yield from _statements(getattr(stmt, field, None) or [])
        for handler in getattr(stmt, "handlers", None) or []:
            yield from _statements(handler.body)


def _own_expressions(stmt: ast.stmt):
    """Expressions belonging directly to this statement, excluding nested ones."""
    for child in ast.iter_child_nodes(stmt):
        if isinstance(child, ast.expr):
            yield child
        elif isinstance(child, ast.withitem):
            yield child.context_expr
            if child.optional_vars is not None:
                yield child.optional_vars


def _acts(branch: ast.stmt) -> bool:
    """Does taking this branch do something: move the robot, or change control flow."""
    for field in ("body", "orelse"):
        for node in getattr(branch, field, None) or []:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and _call_name(inner) in ACTION_CALLS:
                    return True
                if isinstance(inner, (ast.Return, ast.Break, ast.Continue, ast.Raise)):
                    return True
    return False


def _transparent(expr: ast.expr, allowed: set[int]) -> bool:
    """Is this value a direct rearrangement of its inputs, with nothing modelled?"""
    for node in ast.walk(expr):
        if isinstance(node, ast.Call):
            if id(node) not in allowed and _call_name(node) not in SHAPING_CALLS:
                return False
        elif isinstance(node, OPAQUE):
            return False
    return True


def _cross_scope_loads(tree: ast.AST) -> set[str]:
    """Names read inside a nested scope, where this reader does not follow them."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                             ast.ListComp, ast.SetComp, ast.DictComp,
                             ast.GeneratorExp, ast.ClassDef)):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load):
                    names.add(inner.id)
    return names


def _block_loads(tree: ast.AST) -> set[str]:
    """Every name this block reads, anywhere in it, nested scopes included."""
    return {node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}


class _Reader:
    """One executed policy block, read statement by statement inside each scope.

    The replay executes the blocks in order in one namespace, so a block is not
    self-contained: ``inherited`` carries the ways an earlier block already made
    ``world.query`` reachable, and ``later`` carries what the following blocks
    read, which is what keeps this reader from calling a binding "never read"
    when a later block consumes it.
    """

    def __init__(self, block: dict, *, tree: ast.AST | None = None,
                 inherited: tuple[set[str], set[str]] | None = None,
                 later: dict | None = None):
        self.block = block
        self.tree = ast.parse(block["source"]) if tree is None else tree
        _link_parents(self.tree)
        self.local_modules, self.local_names = _query_callables(self.tree)
        inherited_modules, inherited_names = inherited or (set(), set())
        self.modules = self.local_modules | set(inherited_modules)
        self.names = self.local_names | set(inherited_names)
        self.imports_world_locally = bool(self.local_modules or self.local_names)
        self.nested = _cross_scope_loads(self.tree)
        later = later or {}
        self.later_loads: set[str] = set(later.get("loads") or ())
        self.later_unknown = bool(later.get("unknown"))
        self.sites: list[dict] = []

    # -- driving ----------------------------------------------------------

    def run(self) -> list[dict]:
        scopes = [(None, self.tree.body)]
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scopes.append((node.name, node.body))
        for function, body in scopes:
            self._scope(function, body)
        for site in self.sites:
            self._finish(site)
        return self.sites

    def _scope(self, function: str | None, body: list[ast.stmt]) -> None:
        # `derived` maps a local name to the query sites its value came from. It
        # is per scope and per direct assignment only: nothing crosses a function
        # boundary, and nothing is inferred about containers.
        derived: dict[str, set[int]] = {}
        for stmt in _statements(body):
            fresh = self._register(stmt, function)
            touched = {site["index"] for site in fresh if site["forms"] or site["notes"]}
            touched |= self._consume(stmt, derived)
            self._bind(stmt, derived, fresh, touched)

    # -- sites ------------------------------------------------------------

    def _register(self, stmt: ast.stmt, function: str | None) -> list[dict]:
        fresh = []
        for expression in _own_expressions(stmt):
            for node in ast.walk(expression):
                if not _is_query_call(node, self.modules, self.names):
                    continue
                site = {
                    "node": node, "index": len(self.sites),
                    "name": _literal_name(node), "function": function,
                    "block": self.block["index"], "block_line": node.lineno,
                    "line": node.lineno + self.block["line_offset"],
                    "column": node.col_offset,
                    "forms": [], "notes": [], "bound_names": [],
                    "recorded_queries": 0, "recorded_errors": 0,
                    "recorded_names": [], "ambiguous": False,
                }
                self.sites.append(site)
                fresh.append(site)
                self._classify(node, [site])
        return fresh

    def _consume(self, stmt: ast.stmt, derived: dict[str, set[int]]) -> set[int]:
        touched: set[int] = set()
        if not derived:
            return touched
        for expression in _own_expressions(stmt):
            for node in ast.walk(expression):
                if not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
                    continue
                if node.id not in derived:
                    continue
                indices = sorted(derived[node.id])
                touched.update(indices)
                self._classify(node, [self.sites[i] for i in indices])
        return touched

    def _bind(self, stmt: ast.stmt, derived: dict[str, set[int]],
              fresh: list[dict], touched: set[int]) -> None:
        offset = self.block["line_offset"]
        if isinstance(stmt, ast.Assign):
            roots = self._roots(stmt.value, derived, fresh)
            plain = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            simple = len(stmt.targets) == 1 and len(plain) == 1
            allowed = {id(site["node"]) for site in fresh}
            if roots and simple and _transparent(stmt.value, allowed):
                derived[plain[0]] = set(roots)  # a rebinding replaces, never accumulates
                for index in roots:
                    if plain[0] not in self.sites[index]["bound_names"]:
                        self.sites[index]["bound_names"].append(plain[0])
                return
            for name in plain:
                derived.pop(name, None)
            # Only complain about what this statement did not already explain: a
            # query answer that also reached a motion argument here needs no note.
            self._note(roots - touched, "the answer is rebound through Python this reader "
                                        "does not model", stmt.lineno + offset)
        elif isinstance(stmt, ast.AugAssign):
            roots = self._roots(stmt.value, derived, fresh)
            self._note(roots - touched, "the answer is folded in with an augmented "
                                        "assignment", stmt.lineno + offset)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            roots = self._roots(stmt.iter, derived, fresh)
            self._note(roots - touched, "the answer is iterated over", stmt.lineno + offset)

    def _roots(self, expression: ast.expr, derived: dict[str, set[int]],
               fresh: list[dict]) -> set[int]:
        """Query sites this expression depends on, directly and only directly."""
        roots: set[int] = set()
        for node in ast.walk(expression):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                roots |= derived.get(node.id, set())
            for site in fresh:
                if node is site["node"]:
                    roots.add(site["index"])
        return roots

    # -- classification ---------------------------------------------------

    def _classify(self, node: ast.AST, targets: list[dict]) -> None:
        """Where does this world value go, one syntactic step at a time."""
        top, parent = _flow(node)
        offset = self.block["line_offset"]
        if isinstance(parent, ast.Expr):
            self._form(targets, "discarded", parent.lineno + offset,
                       "the answer is neither bound nor read")
        elif isinstance(parent, ast.Assign):
            return  # `_bind` decides: either a tracked binding or an explicit note.
        elif isinstance(parent, (ast.If, ast.While)) and top is parent.test:
            acting = _acts(parent)
            self._form(targets, "control_condition" if acting else "inert_condition",
                       parent.lineno + offset,
                       "the branch calls a motion API or changes control flow" if acting
                       else "the branch neither acts nor changes control flow")
        elif isinstance(parent, ast.Call):
            name = _call_name(parent)
            if name in ACTION_CALLS:
                self._form(targets, "action_argument", parent.lineno + offset,
                           f"the answer is an argument to {name}()")
            elif name in LOG_CALLS:
                self._form(targets, "printed", parent.lineno + offset,
                           f"the answer is only rendered by {name}()")
            else:
                self._note(targets, f"the answer flows into {name or 'a call'}(), which this "
                                    "reader does not follow", parent.lineno + offset)
        elif isinstance(parent, (ast.Return, ast.Yield)):
            self._note(targets, "the answer leaves this scope as a return value",
                       getattr(parent, "lineno", 0) + offset)
        elif parent is None:
            self._note(targets, "the answer sits at the top of an expression this reader "
                                "does not model", node.lineno + offset)
        else:
            self._note(targets, f"the answer flows into {type(parent).__name__}, which this "
                                "reader does not model",
                       getattr(parent, "lineno", node.lineno) + offset)

    def _form(self, targets, form, line, detail) -> None:
        for site in self._sites_of(targets):
            site["forms"].append({"form": form, "line": line, "detail": detail})

    def _note(self, targets, detail, line) -> None:
        for site in self._sites_of(targets):
            site["notes"].append({"line": line, "detail": detail})

    def _sites_of(self, targets):
        ordered = sorted(targets) if isinstance(targets, (set, frozenset)) else targets
        for target in ordered:
            yield target if isinstance(target, dict) else self.sites[target]

    def _read_later(self, site: dict) -> str | None:
        """Why a later block may still read what this block bound, or None.

        Name-based and deliberately conservative: it cannot tell a later read of
        the same answer from a later rebinding of the same name, so it reports
        the uncertainty instead of resolving it.
        """
        if not site["bound_names"]:
            return None
        later = [name for name in site["bound_names"] if name in self.later_loads]
        if later:
            return (f"the name {', '.join(later)} is also read in a later code block, and the "
                    "blocks share one namespace; this reader does not model dataflow across "
                    "blocks, so whether that read is this answer is unresolved")
        if self.later_unknown:
            return ("a later code block does not parse, so this reader cannot tell whether the "
                    "binding is read there")
        return None

    def _finish(self, site: dict) -> None:
        forms = {entry["form"] for entry in site["forms"]}
        if forms & {"action_argument", "control_condition"}:
            site["classification"] = SUPPORTED
        elif site["notes"]:
            site["classification"] = INCONCLUSIVE
        elif (cross_block := self._read_later(site)) is not None:
            # Not logging: this block simply is not where the answer's story ends.
            site["classification"] = INCONCLUSIVE
            site["notes"].append({"line": site["line"], "detail": cross_block})
        elif forms & {"discarded", "printed", "inert_condition"}:
            site["classification"] = LOGGING_ONLY
        elif site["bound_names"]:
            unread = [n for n in site["bound_names"] if n not in self.nested]
            if len(unread) == len(site["bound_names"]):
                site["classification"] = LOGGING_ONLY
                site["forms"].append({
                    "form": "discarded", "line": site["line"],
                    "detail": f"bound to {', '.join(site['bound_names'])} and never read"})
            else:
                site["classification"] = INCONCLUSIVE
                site["notes"].append({
                    "line": site["line"],
                    "detail": "the binding is also read inside a nested scope"})
        else:
            site["classification"] = INCONCLUSIVE
            site["notes"].append({
                "line": site["line"],
                "detail": "the answer's destination is not one this reader models"})


def read_trace(events: Path | str) -> list[dict]:
    """The ordinary ``world_query`` rows of one trial's recorded trace."""
    path = Path(events)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("event") == "world_query":
            rows.append(row)
    return rows


def audit(policy_source: str, *, policy_path=None, trace=(),
          expected_policy_sha256: str | None = None,
          world_sha256: str | None = None) -> dict:
    """Read one policy against one recorded query trace. Evidence, never a grade."""
    policy_sha256 = hashlib.sha256(policy_source.encode()).hexdigest()
    blocks, sites, notes = [], [], []

    # Parse every block before reading any of them. The replay executes the
    # blocks in order in one namespace, so reading a block honestly needs both
    # what the earlier blocks imported and what the later blocks read.
    parsed = []
    for block in code_blocks(policy_source):
        try:
            parsed.append((block, ast.parse(block["source"]), None))
        except SyntaxError as exc:
            parsed.append((block, None, exc))
    loads = [None if tree is None else _block_loads(tree) for _, tree, _ in parsed]

    inherited_modules, inherited_names = set(), set()
    for position, (block, tree, error) in enumerate(parsed):
        summary = {k: block[k] for k in ("index", "sha256", "lines", "line_offset")}
        if tree is None:
            summary.update(parsed=False, query_sites=0, world_import_inherited=False)
            notes.append(f"block {block['index']} does not parse: {error}")
            blocks.append(summary)
            continue
        rest = loads[position + 1:]
        reader = _Reader(
            block, tree=tree, inherited=(inherited_modules, inherited_names),
            later={"loads": set().union(*[names for names in rest if names is not None]),
                   "unknown": any(names is None for names in rest)})
        found = reader.run()
        for site in found:
            site.pop("node", None)
            site["index"] = len(sites)
            sites.append(site)
        inherited_modules |= reader.local_modules
        inherited_names |= reader.local_names
        summary.update(parsed=True, query_sites=len(found),
                       world_import_inherited=bool(found) and not reader.imports_world_locally)
        blocks.append(summary)

    rows = list(trace)
    mismatches = []
    if expected_policy_sha256 and expected_policy_sha256 != policy_sha256:
        mismatches.append({
            "kind": "policy_digest",
            "detail": "the analyzed policy source does not match the digest the trial recorded",
            "recorded": expected_policy_sha256, "analyzed": policy_sha256})
    for position, row in enumerate(rows):
        caller = row.get("caller") or {}
        line, name = caller.get("line"), row.get("name")
        matched = [s for s in sites if s["block_line"] == line
                   and (s["name"] is None or name is None or s["name"] == name)]
        if not matched:
            mismatches.append({
                "kind": "unmatched_recorded_query",
                "query_index": row.get("query_index", position), "name": name,
                "caller_line": line, "caller_file": caller.get("file"),
                "detail": "no query call site with a compatible name at that block-local line"})
            continue
        for site in matched:
            if len(matched) > 1:
                site["ambiguous"] = True
            if row.get("error"):
                site["recorded_errors"] += 1
                continue
            site["recorded_queries"] += 1
            if name is not None and name not in site["recorded_names"]:
                site["recorded_names"].append(name)

    # An ambiguous match is evidence that *one of* several sites ran, which is not
    # evidence about any one of them: it corroborates none of them exactly.
    for site in sites:
        site["corroborated"] = bool(
            site["classification"] == SUPPORTED and site["recorded_queries"]
            and not site["ambiguous"] and not mismatches)
        if site["classification"] == SUPPORTED and not site["corroborated"]:
            site["classification"] = INCONCLUSIVE
            site["notes"].append({
                "line": site["line"],
                "detail": ("the recorded query at this block-local line matches more than one "
                           "call site, so it corroborates none of them exactly")
                if site["ambiguous"] and site["recorded_queries"] else
                ("consumer candidate with no corroborating recorded query at this "
                 "call site (the trial may never have reached it)")})

    counts = {status: sum(1 for s in sites if s["classification"] == status)
              for status in (SUPPORTED, LOGGING_ONLY, INCONCLUSIVE)}
    if mismatches:
        status = IDENTITY_MISMATCH
    elif any(site["corroborated"] for site in sites):
        status = SUPPORTED
    elif all(block["parsed"] for block in blocks) and not sites and not rows:
        # Only a policy this reader could read end to end can be said to have
        # asked nothing; an unparsed block is missing evidence, not a proof.
        status = NO_QUERY
    elif counts[INCONCLUSIVE] or notes:
        status = INCONCLUSIVE
    elif sites:
        status = LOGGING_ONLY
    else:
        status = INCONCLUSIVE
    return {
        "revision": REVISION, "status": status, "summary": SUMMARY[status],
        "policy_path": str(policy_path) if policy_path else None,
        "policy_sha256": policy_sha256, "world_sha256": world_sha256,
        "recorded_policy_sha256": expected_policy_sha256,
        "trace_available": bool(rows), "recorded_queries": len(rows),
        "recorded_query_names": sorted({r.get("name") for r in rows if r.get("name")}),
        "recorded_caller_files": sorted({(r.get("caller") or {}).get("file")
                                         for r in rows if (r.get("caller") or {}).get("file")}),
        "blocks": blocks, "counts": counts, "sites": sites,
        "mismatches": mismatches, "notes": notes, "limits": list(LIMITS),
    }


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


# Query names the prediction contract p1 reserves; the framework answers them.
PREDICTION_QUERIES = frozenset({"prediction_checks", "prediction_summary"})


def prediction_use(report: dict, trace=()) -> dict:
    """The same classification, restricted to the reserved prediction queries.

    Only sites whose queried name is a literal reserved name are counted; a site
    with a dynamic name is not attributed to either side. Advisory like the rest.
    """
    sites = [site for site in report["sites"] if site.get("name") in PREDICTION_QUERIES]
    rows = [row for row in trace if row.get("name") in PREDICTION_QUERIES]
    counts = {status: sum(1 for s in sites if s["classification"] == status)
              for status in (SUPPORTED, LOGGING_ONLY, INCONCLUSIVE)}
    if report["status"] == IDENTITY_MISMATCH:
        status = IDENTITY_MISMATCH
    elif any(site["corroborated"] for site in sites):
        status = SUPPORTED
    elif not sites and not rows and all(block["parsed"] for block in report["blocks"]):
        status = NO_QUERY
    elif counts[INCONCLUSIVE] or not sites:
        status = INCONCLUSIVE
    else:
        status = LOGGING_ONLY
    return {"status": status, "counts": counts,
            "recorded_queries": len([row for row in rows if not row.get("error")]),
            "sites": [site["index"] for site in sites],
            "corroborated_sites": [site["index"] for site in sites if site["corroborated"]]}


def audit_trial(directory: Path | str) -> dict:
    """Audit one recorded judgment trial directory: its ``code.py`` and trace."""
    directory = Path(directory)
    policy = directory / "code.py"
    config = (_read_json(directory / "executable_world_config.json")
              or _read_json(directory / "judgment_world_config.json") or {})
    manifest = _read_json(directory / "judgment_world/manifest.json") or {}
    trace = read_trace(directory / "judgment_world/events.jsonl")
    report = audit(policy.read_text(), policy_path=policy, trace=trace,
                   expected_policy_sha256=config.get("policy_sha256"),
                   world_sha256=manifest.get("world_sha256"))
    report["trial_dir"] = str(directory)
    report["world_manifest_status"] = manifest.get("status")
    if config.get("prediction_contract") == "p1":
        # Only under p1: a contract-off audit stays byte-identical.
        report["prediction_use"] = prediction_use(report, trace)
    return report


def trial_feedback(directory: Path | str, *, write: bool = True) -> dict:
    """Bounded feedback for one ordinary development trial's recorded output.

    Never raises: a mechanism audit that cannot run is reported as unavailable
    beside the task result, and blocks nothing.
    """
    directory = Path(directory)
    try:
        report = audit_trial(directory)
    except (OSError, ValueError, RecursionError) as exc:
        return {"revision": REVISION, "trial_dir": str(directory), "status": "unavailable",
                "detail": f"{type(exc).__name__}: {exc}", "limits": list(LIMITS)}
    audit_path = None
    if write:
        audit_path = directory / ARTIFACT
        try:
            audit_path.write_text(json.dumps(report, indent=2) + "\n")
        except OSError:
            audit_path = None
    feedback = {
        "revision": REVISION, "trial_dir": str(directory), "status": report["status"],
        "summary": report["summary"], "counts": report["counts"],
        "recorded_queries": report["recorded_queries"],
        "trace_available": report["trace_available"],
        "policy_sha256": report["policy_sha256"], "world_sha256": report["world_sha256"],
        "sites": report["sites"][:MAX_REPORTED_SITES],
        "sites_omitted": max(0, len(report["sites"]) - MAX_REPORTED_SITES),
        "mismatches": report["mismatches"][:8], "notes": report["notes"][:8],
        "audit": str(audit_path) if audit_path else None,
        "limits": list(LIMITS),
    }
    if "prediction_use" in report:
        feedback["prediction_use"] = report["prediction_use"]
    return feedback


def selected_pair_report(directories) -> dict:
    """Mechanism status for the frozen pair's own executions, reported beside the result."""
    trials = [trial_feedback(directory, write=False) for directory in directories]
    statuses = [trial.get("status") for trial in trials]
    status = next((s for s in AGGREGATE_ORDER if s in statuses), "unavailable")
    return {
        "revision": REVISION, "status": status,
        "summary": SUMMARY.get(status, "no mechanism audit could be computed"),
        "trials": trials, "trials_audited": len(trials),
        "aggregate_rule": "one corroborated use candidate in any execution of the frozen pair "
                          "is reported as supported; otherwise the most cautious observed "
                          "status is reported",
        "limits": list(LIMITS),
    }
