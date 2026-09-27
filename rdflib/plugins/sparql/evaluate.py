"""
These method recursively evaluate the SPARQL Algebra

evalQuery is the entry-point, it will setup context and
return the SPARQLResult object

evalPart is called on each level and will delegate to the right method

A `rdflib.plugins.sparql.sparql.QueryContext` is passed along, keeping
information needed for evaluation

A list of dicts (solution mappings) is returned, apart from GroupBy which may
also return a dict of list of dicts
"""

from __future__ import annotations

import collections
import itertools
import re
from collections.abc import Generator, Iterable, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Union,
)
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pyparsing import ParseException

from rdflib.graph import Graph
from rdflib.plugins.sparql import CUSTOM_EVALS, parser
from rdflib.plugins.sparql.aggregates import Aggregator
from rdflib.plugins.sparql.evalutils import (
    _ebv,
    _eval,
    _fillTemplate,
    _join,
    _minus,
    _val,
)
from rdflib.plugins.sparql.parserutils import CompValue, value
from rdflib.plugins.sparql.sparql import (
    AlreadyBound,
    FrozenBindings,
    FrozenDict,
    Query,
    QueryContext,
    SPARQLError,
)
from rdflib.term import BNode, Identifier, Literal, URIRef, Variable

if TYPE_CHECKING:
    from rdflib.paths import Path

import json

try:
    import orjson

    _HAS_ORJSON = True
except ImportError:
    orjson = None  # type: ignore[assignment, unused-ignore]
    _HAS_ORJSON = False

_Triple = tuple[Identifier, Identifier, Identifier]

#: Maximum number of input solutions that are sent to a remote endpoint in a
#: single request when a SERVICE clause is evaluated in "bind-join" mode.
#: When set to a positive integer, the solutions of the pattern to the left
#: of a SERVICE clause are collected and sent to the remote endpoint in
#: batches of at most this many solutions, using a VALUES clause appended to
#: the service query, instead of issuing one request per solution.  The
#: results are then joined back with the input solutions locally.  None
#: (the default) disables bind-join batching and evaluates SERVICE clauses
#: with one request per input solution.
SERVICE_BIND_JOIN_BATCH_SIZE: Optional[int] = None


def _serviceBindJoinBatchSize() -> Optional[int]:
    batch_size = SERVICE_BIND_JOIN_BATCH_SIZE
    if isinstance(batch_size, int) and batch_size > 0:
        return batch_size
    return None


def evalBGP(
    ctx: QueryContext, bgp: list[_Triple]
) -> Generator[FrozenBindings, None, None]:
    """
    A basic graph pattern
    """

    if not bgp:
        yield ctx.solution()
        return

    s, p, o = bgp[0]

    _s = ctx[s]
    _p = ctx[p]
    _o = ctx[o]

    # type error: Item "None" of "Optional[Graph]" has no attribute "triples"
    # type Argument 1 to "triples" of "Graph" has incompatible type "tuple[Union[str, Path, None], Union[str, Path, None], Union[str, Path, None]]"; expected "tuple[Optional[Node], Optional[Node], Optional[Node]]"
    for ss, sp, so in ctx.graph.triples((_s, _p, _o)):  # type: ignore[union-attr, arg-type]
        if None in (_s, _p, _o):
            c = ctx.push()
        else:
            c = ctx

        if _s is None:
            # type error: Incompatible types in assignment (expression has type "Union[Node, Any]", target has type "Identifier")
            c[s] = ss  # type: ignore[assignment]

        try:
            if _p is None:
                # type error: Incompatible types in assignment (expression has type "Union[Node, Any]", target has type "Identifier")
                c[p] = sp  # type: ignore[assignment]
        except AlreadyBound:
            continue

        try:
            if _o is None:
                # type error: Incompatible types in assignment (expression has type "Union[Node, Any]", target has type "Identifier")
                c[o] = so  # type: ignore[assignment]
        except AlreadyBound:
            continue

        for x in evalBGP(c, bgp[1:]):
            yield x


def evalExtend(
    ctx: QueryContext, extend: CompValue
) -> Generator[FrozenBindings, None, None]:
    # TODO: Deal with dict returned from evalPart from GROUP BY

    for c in evalPart(ctx, extend.p):
        try:
            e = _eval(extend.expr, c.forget(ctx, _except=extend._vars))
            if isinstance(e, SPARQLError):
                raise e

            yield c.merge({extend.var: e})

        except SPARQLError:
            yield c


def evalLazyJoin(
    ctx: QueryContext, join: CompValue
) -> Generator[FrozenBindings, None, None]:
    """
    A lazy join will push the variables bound
    in the first part to the second part,
    essentially doing the join implicitly
    hopefully evaluating much fewer triples
    """
    for a in evalPart(ctx, join.p1):
        c = ctx.thaw(a)
        for b in evalPart(c, join.p2):
            yield b.merge(a)  # merge, as some bindings may have been forgotten


def evalJoin(ctx: QueryContext, join: CompValue) -> Generator[FrozenDict, None, None]:
    # TODO: Deal with dict returned from evalPart from GROUP BY
    # only ever for join.p1

    if join.p2.name == "ServiceGraphPattern":
        batch_size = _serviceBindJoinBatchSize()
        if batch_size is not None:
            plan = _prepareServiceBindJoin(ctx, join.p2)
            if plan is not None:
                return evalServiceBindJoin(ctx, join, plan, batch_size)

    if join.lazy:
        return evalLazyJoin(ctx, join)
    else:
        a = evalPart(ctx, join.p1)
        b = set(evalPart(ctx, join.p2))
        return _join(a, b)


def evalUnion(ctx: QueryContext, union: CompValue) -> list[Any]:
    branch1_branch2 = []
    for x in evalPart(ctx, union.p1):
        branch1_branch2.append(x)
    for x in evalPart(ctx, union.p2):
        branch1_branch2.append(x)
    return branch1_branch2


def evalMinus(ctx: QueryContext, minus: CompValue) -> Generator[FrozenDict, None, None]:
    a = evalPart(ctx, minus.p1)
    b = set(evalPart(ctx, minus.p2))
    return _minus(a, b)


def evalLeftJoin(
    ctx: QueryContext, join: CompValue
) -> Generator[FrozenBindings, None, None]:
    # import pdb; pdb.set_trace()
    for a in evalPart(ctx, join.p1):
        ok = False
        c = ctx.thaw(a)
        for b in evalPart(c, join.p2):
            if _ebv(join.expr, b.forget(ctx)):
                ok = True
                yield b.merge(a)
        if not ok:
            # we've cheated, the ctx above may contain
            # vars bound outside our scope
            # before we yield a solution without the OPTIONAL part
            # check that we would have had no OPTIONAL matches
            # even without prior bindings...
            p1_vars = join.p1._vars
            if p1_vars is None or not any(
                _ebv(join.expr, b)
                for b in evalPart(ctx.thaw(a.remember(p1_vars)), join.p2)
            ):
                yield a


def evalFilter(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    # TODO: Deal with dict returned from evalPart!
    for c in evalPart(ctx, part.p):
        if _ebv(
            part.expr,
            c.forget(ctx, _except=part._vars) if not part.no_isolated_scope else c,
        ):
            yield c


def evalGraph(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    if ctx.dataset is None:
        raise Exception(
            "Non-conjunctive-graph doesn't know about "
            + "graphs. Try a query without GRAPH."
        )

    ctx = ctx.clone()
    graph: Union[str, Path, None, Graph] = ctx[part.term]
    prev_graph = ctx.graph
    if graph is None:
        for graph in ctx.dataset.contexts():
            # in SPARQL the default graph is NOT a named graph
            if graph == ctx.dataset.default_context:
                continue

            c = ctx.pushGraph(graph)
            c = c.push()
            graphSolution = [{part.term: graph.identifier}]
            for x in _join(evalPart(c, part.p), graphSolution):
                x.ctx.graph = prev_graph
                yield x

    else:
        if TYPE_CHECKING:
            assert not isinstance(graph, Graph)
        # type error: Argument 1 to "get_context" of "ConjunctiveGraph" has incompatible type "Union[str, Path]"; expected "Union[Node, str, None]"
        c = ctx.pushGraph(ctx.dataset.get_context(graph))  # type: ignore[arg-type]
        for x in evalPart(c, part.p):
            x.ctx.graph = prev_graph
            yield x


def evalValues(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    for r in part.p.res:
        c = ctx.push()
        try:
            for k, v in r.items():
                if v != "UNDEF":
                    c[k] = v
        except AlreadyBound:
            continue

        yield c.solution()


def evalMultiset(ctx: QueryContext, part: CompValue):
    if part.p.name == "values":
        return evalValues(ctx, part)

    return evalPart(ctx, part.p)


def evalPart(ctx: QueryContext, part: CompValue) -> Any:
    # try custom evaluation functions
    for name, c in CUSTOM_EVALS.items():
        try:
            return c(ctx, part)
        except NotImplementedError:
            pass  # the given custome-function did not handle this part

    if part.name == "BGP":
        # Reorder triples patterns by number of bound nodes in the current ctx
        # Do patterns with more bound nodes first
        triples = sorted(
            part.triples, key=lambda t: len([n for n in t if ctx[n] is None])
        )

        return evalBGP(ctx, triples)
    elif part.name == "Filter":
        return evalFilter(ctx, part)
    elif part.name == "Join":
        return evalJoin(ctx, part)
    elif part.name == "LeftJoin":
        return evalLeftJoin(ctx, part)
    elif part.name == "Graph":
        return evalGraph(ctx, part)
    elif part.name == "Union":
        return evalUnion(ctx, part)
    elif part.name == "ToMultiSet":
        return evalMultiset(ctx, part)
    elif part.name == "Extend":
        return evalExtend(ctx, part)
    elif part.name == "Minus":
        return evalMinus(ctx, part)

    elif part.name == "Project":
        return evalProject(ctx, part)
    elif part.name == "Slice":
        return evalSlice(ctx, part)
    elif part.name == "Distinct":
        return evalDistinct(ctx, part)
    elif part.name == "Reduced":
        return evalReduced(ctx, part)

    elif part.name == "OrderBy":
        return evalOrderBy(ctx, part)
    elif part.name == "Group":
        return evalGroup(ctx, part)
    elif part.name == "AggregateJoin":
        return evalAggregateJoin(ctx, part)

    elif part.name == "SelectQuery":
        return evalSelectQuery(ctx, part)
    elif part.name == "AskQuery":
        return evalAskQuery(ctx, part)
    elif part.name == "ConstructQuery":
        return evalConstructQuery(ctx, part)

    elif part.name == "ServiceGraphPattern":
        return evalServiceQuery(ctx, part)

    elif part.name == "DescribeQuery":
        return evalDescribeQuery(ctx, part)

    else:
        raise Exception("I dont know: %s" % part.name)


def evalServiceQuery(ctx: QueryContext, part: CompValue):
    match = re.match(
        "^service <(.*)>[ \n]*{(.*)}[ \n]*$",
        # type error: Argument 2 to "get" of "CompValue" has incompatible type "str"; expected "bool"  [arg-type]
        part.get("service_string", ""),  # type: ignore[arg-type]
        re.DOTALL | re.I,
    )

    if match:
        service_url = match.group(1)
        service_query = _buildQueryStringForServiceCall(ctx, match.group(2))
        json_dict = _serviceRequest(service_url, service_query)
        variables = json_dict["head"]["vars"]
        # or just return the bindings?
        res = json_dict["results"]["bindings"]
        if len(res) > 0:
            for r in res:
                # type error: Argument 2 to "_yieldBindingsFromServiceCallResult" has incompatible type "str"; expected "dict[str, dict[str, str]]"
                for bound in _yieldBindingsFromServiceCallResult(ctx, r, variables):
                    yield bound


def _serviceRequest(service_url: str, service_query: str) -> Any:
    """
    Send a SPARQL query to a remote endpoint and return the parsed JSON
    result.  Raises an Exception if the endpoint does not respond with a
    200 status code.
    """
    query_settings = {"query": service_query, "output": "json"}
    headers = {
        "accept": "application/sparql-results+json",
        "user-agent": "rdflibForAnUser",
    }
    # GET is easier to cache so prefer that if the query is not to long
    if len(service_query) < 600:
        response = urlopen(
            Request(service_url + "?" + urlencode(query_settings), headers=headers)
        )
    else:
        response = urlopen(
            Request(
                service_url,
                data=urlencode(query_settings).encode(),
                headers=headers,
            )
        )
    if response.status == 200:
        if _HAS_ORJSON:
            return orjson.loads(response.read())
        else:
            return json.loads(response.read())
    raise Exception(
        "Service: %s responded with code: %s", service_url, response.status
    )


"""
    Build a query string to be used by the service call.
    It is supposed to pass in the existing bound solutions.
    Re-adds prefixes if added and sets the base.
    Wraps it in select if needed.
"""


def _buildQueryStringForServiceCall(ctx: QueryContext, service_query: str) -> str:
    try:
        parser.parseQuery(service_query)
    except ParseException:
        # This could be because we don't have a select around the service call.
        service_query = "SELECT REDUCED * WHERE {" + service_query + "}"
        # type error: Item "None" of "Optional[Prologue]" has no attribute "namespace_manager"
        for p in ctx.prologue.namespace_manager.store.namespaces():  # type: ignore[union-attr]
            service_query = "PREFIX " + p[0] + ":" + p[1].n3() + " " + service_query
        # re add the base if one was defined
        # type error: Item "None" of "Optional[Prologue]" has no attribute "base"
        base = ctx.prologue.base  # type: ignore[union-attr]
        if base is not None and len(base) > 0:
            service_query = "BASE <" + base + "> " + service_query
    sol = [v for v in ctx.solution() if isinstance(v, Variable)]
    if len(sol) > 0:
        variables = " ".join([v.n3() for v in sol])
        variables_bound = " ".join([ctx.get(v).n3() for v in sol])
        service_query = (
            service_query + "VALUES (" + variables + ") {(" + variables_bound + ")}"
        )
    return service_query


def _yieldBindingsFromServiceCallResult(
    ctx: QueryContext, r: dict[str, dict[str, str]], variables: list[str]
) -> Generator[FrozenBindings, None, None]:
    yield FrozenBindings(ctx, _bindingsFromServiceCallResult(r, variables))


def _bindingsFromServiceCallResult(
    r: dict[str, dict[str, str]],
    variables: list[str],
    bnode_map: Optional[dict[str, BNode]] = None,
) -> dict[Variable, Identifier]:
    res_dict: dict[Variable, Identifier] = {}
    for var in variables:
        if var in r and r[var]:
            var_binding = r[var]
            var_type = var_binding["type"]
            if var_type == "uri":
                res_dict[Variable(var)] = URIRef(var_binding["value"])
            elif var_type == "literal":
                res_dict[Variable(var)] = Literal(
                    var_binding["value"],
                    datatype=var_binding.get("datatype"),
                    lang=var_binding.get("xml:lang"),
                )
            # This is here because of
            # https://www.w3.org/TR/2006/NOTE-rdf-sparql-json-res-20061004/#variable-binding-results
            elif var_type == "typed-literal":
                res_dict[Variable(var)] = Literal(
                    var_binding["value"], datatype=URIRef(var_binding["datatype"])
                )
            elif var_type == "bnode":
                if bnode_map is None:
                    res_dict[Variable(var)] = BNode(var_binding["value"])
                else:
                    # Blank node labels are only scoped to a single response.
                    # Remap them so that identical labels returned by
                    # different requests are not conflated into one node,
                    # while identical labels within one response still
                    # denote the same node.
                    label = var_binding["value"]
                    if label not in bnode_map:
                        bnode_map[label] = BNode()
                    res_dict[Variable(var)] = bnode_map[label]
            else:
                raise ValueError(f"invalid type {var_type!r} for variable {var!r}")
    return res_dict


_SERVICE_STRING_RE = re.compile(
    r"^\s*service\s+(?:silent\s+)?(?:<[^>]*>|[?$][^\s{]+)\s*\{(.*)\}\s*$",
    re.DOTALL | re.IGNORECASE,
)

#: Solution modifiers and aggregates make a SELECT query unsafe to batch:
#: they are applied per request when evaluating row by row, but would apply
#: to the whole batch when the input solutions are combined into a single
#: request.  Queries containing them are evaluated one solution at a time.
#: (DISTINCT/REDUCED are safe: the internal row variable keeps the solutions
#: of different input rows distinct.)
_SERVICE_UNBATCHABLE_RE = re.compile(
    r"\b(?:limit|offset|group\s+by|having|count|sum|avg|min|max|sample"
    r"|group_concat)\b",
    re.IGNORECASE,
)

#: Name of the internal variable used to correlate remote results with the
#: input solutions of a bind-join batch.  If a user query already uses this
#: name, underscores are prepended until it no longer collides.
_SERVICE_BIND_JOIN_ROW_VARIABLE = "__rdflib_service_bind_join_row"


class _ServiceBindJoinPlan:
    """
    Precomputed information needed to rewrite a SERVICE pattern into a
    bind-join batch query.
    """

    def __init__(
        self,
        term: Union[URIRef, Variable],
        silent: bool,
        inner_query: str,
        prologue_prefix: str,
        projection_insert_at: Optional[int],
        values_insert_at: Optional[int],
    ):
        self.term = term
        self.silent = silent
        self.inner_query = inner_query
        self.prologue_prefix = prologue_prefix
        self.projection_insert_at = projection_insert_at
        self.values_insert_at = values_insert_at

    def build_query(self, values_clause: str, row_variable: str) -> str:
        if self.values_insert_at is None:
            # A bare group graph pattern: wrap it in a SELECT and put the
            # VALUES clause inside the group so that the join variables are
            # projected by SELECT *.
            return (
                self.prologue_prefix
                + "SELECT REDUCED * WHERE { "
                + self.inner_query
                + " "
                + values_clause
                + " }"
            )
        query = self.inner_query
        close_at = self.values_insert_at
        if self.projection_insert_at is not None:
            # The inner query has an explicit projection; add the internal
            # row variable to it so that it is returned by the endpoint.
            inject = " ?" + row_variable + " "
            query = (
                query[: self.projection_insert_at]
                + inject
                + query[self.projection_insert_at :]
            )
            close_at += len(inject)
        # Put the VALUES clause inside the outermost group graph pattern;
        # this is semantically equivalent to a trailing VALUES clause (the
        # join happens before any solution modifiers are applied).
        return query[:close_at] + " " + values_clause + " " + query[close_at:]


def _findServiceQueryBraces(query: str) -> Optional[tuple[int, int]]:
    """
    Find the first top-level "{ ... }" group in a query string, skipping
    comments, string literals and IRI references.  Returns the positions of
    the opening and closing brace, or None if there is no balanced group.
    """
    start: Optional[int] = None
    depth = 0
    i = 0
    n = len(query)
    while i < n:
        ch = query[i]
        if ch == "#":
            j = query.find("\n", i)
            i = n if j < 0 else j + 1
        elif ch == '"' or ch == "'":
            if query.startswith(ch * 3, i):
                j = query.find(ch * 3, i + 3)
                i = n if j < 0 else j + 3
            else:
                j = i + 1
                while j < n:
                    if query[j] == "\\":
                        j += 1
                    elif query[j] == ch:
                        break
                    j += 1
                i = j + 1
        elif ch == "<":
            # IRI references cannot contain spaces or braces; anything else
            # is a comparison operator.
            j = i + 1
            while j < n and query[j] not in "> \t\r\n{}\"'":
                j += 1
            if j < n and query[j] == ">":
                i = j + 1
            else:
                i += 1
        elif ch == "{":
            if start is None:
                start = i
            depth += 1
            i += 1
        elif ch == "}":
            depth -= 1
            i += 1
            if depth == 0 and start is not None:
                return start, i - 1
        else:
            i += 1
    return None


def _prepareServiceBindJoin(
    ctx: QueryContext, part: CompValue
) -> Optional[_ServiceBindJoinPlan]:
    """
    Analyze a ServiceGraphPattern and return a plan for bind-join batch
    evaluation, or None if the pattern cannot be batched (in which case it
    should be evaluated one input solution at a time).
    """
    # NB: CompValue.get returns the key itself when it is absent, so
    # presence checks must use "in".
    service_string = part["service_string"] if "service_string" in part else ""
    match = _SERVICE_STRING_RE.match(service_string) if service_string else None
    if match is None:
        return None
    term = part.get("term")
    if not isinstance(term, (URIRef, Variable)):
        return None
    inner_query = match.group(1)
    silent = "silent" in part
    try:
        parser.parseQuery(inner_query)
    except ParseException:
        # The service pattern is a bare group graph pattern; it is wrapped
        # in a SELECT and the VALUES clause is placed inside the group.
        prefix = ""
        # type error: Item "None" of "Optional[Prologue]" has no attribute "namespace_manager"
        for p in ctx.prologue.namespace_manager.store.namespaces():  # type: ignore[union-attr]
            prefix = prefix + "PREFIX " + p[0] + ":" + p[1].n3() + " "
        # re add the base if one was defined
        # type error: Item "None" of "Optional[Prologue]" has no attribute "base"
        base = ctx.prologue.base  # type: ignore[union-attr]
        if base is not None and len(base) > 0:
            prefix = "BASE <" + base + "> " + prefix
        plan: Optional[_ServiceBindJoinPlan] = _ServiceBindJoinPlan(
            term, silent, inner_query, prefix, None, None
        )
    else:
        if re.match(r"\s*select\b", inner_query, re.IGNORECASE) is None:
            # Only SELECT queries can be rewritten for bind-join.
            return None
        if _SERVICE_UNBATCHABLE_RE.search(inner_query) is not None:
            # Solution modifiers and aggregates would change meaning when
            # several input solutions are combined into one request.
            return None
        braces = _findServiceQueryBraces(inner_query)
        if braces is None:
            return None
        open_at, close_at = braces
        projection_match = re.match(
            r"\s*select\s+(?:(?:distinct|reduced)\s+)?",
            inner_query,
            re.IGNORECASE,
        )
        assert projection_match is not None
        projection_start = projection_match.end()
        where_match = re.search(
            r"\bwhere\b", inner_query[projection_start:open_at], re.IGNORECASE
        )
        projection_insert_at: Optional[int] = (
            projection_start + where_match.start() if where_match else open_at
        )
        if inner_query[projection_start:projection_insert_at].strip() == "*":
            # SELECT * projects the join variables already.
            projection_insert_at = None
        plan = _ServiceBindJoinPlan(
            term, silent, inner_query, "", projection_insert_at, close_at
        )
    # Verify that the rewritten query is still syntactically valid; if not,
    # fall back to evaluating one input solution at a time.
    try:
        parser.parseQuery(
            plan.build_query(
                "VALUES (?" + _SERVICE_BIND_JOIN_ROW_VARIABLE + ") { ( 0 ) }",
                _SERVICE_BIND_JOIN_ROW_VARIABLE,
            )
        )
    except ParseException:
        return None
    return plan


def evalServiceBindJoin(
    ctx: QueryContext,
    join: CompValue,
    plan: _ServiceBindJoinPlan,
    batch_size: int,
) -> Generator[FrozenBindings, None, None]:
    """
    Evaluate a join whose right-hand side is a SERVICE clause in bind-join
    mode: the input solutions are sent to the remote endpoint in batches of
    at most batch_size solutions using a VALUES clause, and the results are
    joined back with the input solutions locally.  This only changes the
    number of requests, not the SPARQL bag semantics of the join.
    """
    rows = list(evalPart(ctx, join.p1))
    endpoint_groups: list[tuple[str, list[FrozenBindings]]]
    if isinstance(plan.term, URIRef):
        endpoint_groups = [(str(plan.term), rows)]
        no_endpoint: list[FrozenBindings] = []
    else:
        # The endpoint is dynamic: batch input solutions per endpoint IRI.
        groups: dict[str, list[FrozenBindings]] = {}
        no_endpoint = []
        for row in rows:
            endpoint = row.get(plan.term)
            if isinstance(endpoint, URIRef):
                groups.setdefault(str(endpoint), []).append(row)
            else:
                no_endpoint.append(row)
        endpoint_groups = list(groups.items())
    for endpoint, group_rows in endpoint_groups:
        for start in range(0, len(group_rows), batch_size):
            yield from _evalServiceBindJoinBatch(
                ctx, plan, endpoint, group_rows[start : start + batch_size]
            )
    for row in no_endpoint:
        # No endpoint IRI is available for this solution; this is a service
        # failure for this solution.
        if plan.silent:
            yield row
        else:
            raise SPARQLError(
                "Service: endpoint variable %s is not bound to an IRI" % plan.term
            )


def _evalServiceBindJoinBatch(
    ctx: QueryContext,
    plan: _ServiceBindJoinPlan,
    endpoint: str,
    rows: list[FrozenBindings],
) -> Generator[FrozenBindings, None, None]:
    thawed = [ctx.thaw(row) for row in rows]
    row_bindings = [
        {k: v for k, v in c.solution().items() if isinstance(k, Variable)}
        for c in thawed
    ]
    mentioned = set(re.findall(r"[?$](\w+)", plan.inner_query))
    # Only send bindings for variables that also occur in the service
    # query; other bindings cannot affect the remote evaluation.
    send_vars: list[Variable] = []
    for bindings in row_bindings:
        for var in bindings:
            if var not in send_vars and str(var) in mentioned:
                send_vars.append(var)
    used_names = set(mentioned)
    for bindings in row_bindings:
        used_names.update(str(v) for v in bindings)
    row_variable = _SERVICE_BIND_JOIN_ROW_VARIABLE
    while row_variable in used_names:
        row_variable = "_" + row_variable
    values_rows = []
    for index, bindings in enumerate(row_bindings):
        values = [str(index)]
        for var in send_vars:
            value = bindings.get(var)
            if value is None or isinstance(value, BNode):
                # Unbound variables stay unbound (UNDEF); blank nodes cannot
                # be referenced in a VALUES clause and are filtered out by
                # the compatibility check below instead.
                values.append("UNDEF")
            else:
                values.append(value.n3())
        values_rows.append("( " + " ".join(values) + " )")
    values_clause = (
        "VALUES (?"
        + " ?".join([row_variable] + [str(v) for v in send_vars])
        + ") { "
        + " ".join(values_rows)
        + " }"
    )
    query = plan.build_query(values_clause, row_variable)
    row_variable_term = Variable(row_variable)
    try:
        json_dict = _serviceRequest(endpoint, query)
        variables = json_dict["head"]["vars"]
        parsed: list[tuple[int, dict[Variable, Identifier]]] = []
        bnode_map: dict[str, BNode] = {}
        for r in json_dict["results"]["bindings"]:
            res_dict = _bindingsFromServiceCallResult(r, variables, bnode_map)
            row_id = res_dict.pop(row_variable_term, None)
            if row_id is None:
                raise SPARQLError(
                    "Service: %s did not return the bind-join row variable"
                    % endpoint
                )
            index = int(row_id)
            if 0 <= index < len(rows):
                parsed.append((index, res_dict))
    except Exception:
        if plan.silent:
            # A failed batch with SILENT evaluates to the input solutions.
            for row in rows:
                yield row
            return
        raise
    for index, res_dict in parsed:
        local = row_bindings[index]
        # Only join compatible solutions; this mirrors the compatible join
        # semantics for variables that could not be restricted remotely
        # (e.g. blank nodes sent as UNDEF).
        if all(var not in local or local[var] == val for var, val in res_dict.items()):
            yield FrozenBindings(thawed[index], res_dict).merge(rows[index])


def evalGroup(ctx: QueryContext, group: CompValue):
    """
    http://www.w3.org/TR/sparql11-query/#defn_algGroup
    """
    # grouping should be implemented by evalAggregateJoin
    return evalPart(ctx, group.p)


def evalAggregateJoin(
    ctx: QueryContext, agg: CompValue
) -> Generator[FrozenBindings, None, None]:
    # import pdb ; pdb.set_trace()
    p = evalPart(ctx, agg.p)
    # p is always a Group, we always get a dict back

    group_expr = agg.p.expr
    res: dict[Any, Any] = collections.defaultdict(
        lambda: Aggregator(aggregations=agg.A)
    )

    if group_expr is None:
        # no grouping, just COUNT in SELECT clause
        # get 1 aggregator for counting
        aggregator = res[True]
        for row in p:
            aggregator.update(row)
    else:
        for row in p:
            # determine right group aggregator for row
            k = tuple(_eval(e, row, False) for e in group_expr)
            res[k].update(row)

    # all rows are done; yield aggregated values
    for aggregator in res.values():
        yield FrozenBindings(ctx, aggregator.get_bindings())

    # there were no matches
    if len(res) == 0:
        yield FrozenBindings(ctx)


def evalOrderBy(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    res = evalPart(ctx, part.p)

    for e in reversed(part.expr):
        reverse = bool(e.order and e.order == "DESC")
        res = sorted(
            res, key=lambda x: _val(value(x, e.expr, variables=True)), reverse=reverse
        )

    return res


def evalSlice(ctx: QueryContext, slice: CompValue):
    res = evalPart(ctx, slice.p)

    return itertools.islice(
        res,
        slice.start,
        slice.start + slice.length if slice.length is not None else None,
    )


def evalReduced(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    """apply REDUCED to result

    REDUCED is not as strict as DISTINCT, but if the incoming rows were sorted
    it should produce the same result with limited extra memory and time per
    incoming row.
    """

    # This implementation uses a most recently used strategy and a limited
    # buffer size. It relates to a LRU caching algorithm:
    # https://en.wikipedia.org/wiki/Cache_algorithms#Least_Recently_Used_.28LRU.29
    MAX = 1
    # TODO: add configuration or determine "best" size for most use cases
    # 0: No reduction
    # 1: compare only with the last row, almost no reduction with
    #    unordered incoming rows
    # N: The greater the buffer size the greater the reduction but more
    #    memory and time are needed

    # mixed data structure: set for lookup, deque for append/pop/remove
    mru_set = set()
    mru_queue: collections.deque[Any] = collections.deque()

    for row in evalPart(ctx, part.p):
        if row in mru_set:
            # forget last position of row
            mru_queue.remove(row)
        else:
            # row seems to be new
            yield row
            mru_set.add(row)
            if len(mru_set) > MAX:
                # drop the least recently used row from buffer
                mru_set.remove(mru_queue.pop())
        # put row to the front
        mru_queue.appendleft(row)


def evalDistinct(
    ctx: QueryContext, part: CompValue
) -> Generator[FrozenBindings, None, None]:
    res = evalPart(ctx, part.p)

    done = set()
    for x in res:
        if x not in done:
            yield x
            done.add(x)


def evalProject(ctx: QueryContext, project: CompValue):
    res = evalPart(ctx, project.p)
    return (row.project(project.PV) for row in res)


def evalSelectQuery(
    ctx: QueryContext, query: CompValue
) -> Mapping[str, Union[str, list[Variable], Iterable[FrozenDict]]]:
    res: dict[str, Union[str, list[Variable], Iterable[FrozenDict]]] = {}
    res["type_"] = "SELECT"
    res["bindings"] = evalPart(ctx, query.p)
    res["vars_"] = query.PV
    return res


def evalAskQuery(ctx: QueryContext, query: CompValue) -> Mapping[str, Union[str, bool]]:
    res: dict[str, Union[bool, str]] = {}
    res["type_"] = "ASK"
    res["askAnswer"] = False
    for x in evalPart(ctx, query.p):
        res["askAnswer"] = True
        break

    return res


def evalConstructQuery(
    ctx: QueryContext, query: CompValue
) -> Mapping[str, Union[str, Graph]]:
    template = query.template

    if not template:
        # a construct-where query
        template = query.p.p.triples  # query->project->bgp ...

    graph = Graph()

    for c in evalPart(ctx, query.p):
        graph += _fillTemplate(template, c)

    res: dict[str, Union[str, Graph]] = {}
    res["type_"] = "CONSTRUCT"
    res["graph"] = graph

    return res


def evalDescribeQuery(ctx: QueryContext, query) -> dict[str, Union[str, Graph]]:
    # Create a result graph and bind namespaces from the graph being queried
    graph = Graph()
    # type error: Item "None" of "Optional[Graph]" has no attribute "namespaces"
    for pfx, ns in ctx.graph.namespaces():  # type: ignore[union-attr]
        graph.bind(pfx, ns)

    to_describe = set()

    # Explicit IRIs may be provided to a DESCRIBE query.
    # If there is a WHERE clause, explicit IRIs may be provided in
    # addition to projected variables. Find those explicit IRIs and
    # prepare to describe them.
    for iri in query.PV:
        if isinstance(iri, URIRef):
            to_describe.add(iri)

    # If there is a WHERE clause, evaluate it then find the unique set of
    # resources to describe across all bindings and projected variables
    if query.p is not None:
        bindings = evalPart(ctx, query.p)
        to_describe.update(*(set(binding.values()) for binding in bindings))

    # Get a CBD for all resources identified to describe
    for resource in to_describe:
        # type error: Item "None" of "Optional[Graph]" has no attribute "cbd"
        ctx.graph.cbd(resource, target_graph=graph)  # type: ignore[union-attr]

    res: dict[str, Union[str, Graph]] = {}
    res["type_"] = "DESCRIBE"
    res["graph"] = graph

    return res


def evalQuery(
    graph: Graph,
    query: Query,
    initBindings: Optional[Mapping[str, Identifier]] = None,
    base: Optional[str] = None,
) -> Mapping[Any, Any]:
    """Evaluate a SPARQL query against a graph.

    !!! warning "Caution"

        This method can access indirectly requested network endpoints, for
        example, query processing will attempt to access network endpoints
        specified in `SERVICE` directives.

        When processing untrusted or potentially malicious queries, measures
        should be taken to restrict network and file access.

        For information on available security measures, see the RDFLib
        [Security Considerations](../security_considerations.md)
        documentation.
    """
    main = query.algebra

    initBindings = dict((Variable(k), v) for k, v in (initBindings or {}).items())

    ctx = QueryContext(
        graph, initBindings=initBindings, datasetClause=main.datasetClause
    )

    ctx.prologue = query.prologue

    return evalPart(ctx, main)
