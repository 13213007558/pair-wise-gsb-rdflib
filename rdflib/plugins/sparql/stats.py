"""
Execution statistics for SPARQL query evaluation.

This module implements opt-in, per-query-execution statistics for the
RDFLib SPARQL evaluator. Statistics are collected *while the query result
is being iterated*: the evaluation stays lazy, the query is never
re-executed for the sake of statistics, and lazy results are never
materialized into lists.

Enabling statistics
-------------------

Pass stats=True to [Graph.query][rdflib.graph.Graph.query]::

    result = graph.query("SELECT ... ", stats=True)
    for row in result:
        ...
    print(result.stats.report())

The returned [SPARQLResult][rdflib.plugins.sparql.processor.SPARQLResult]
carries a [QueryStats][rdflib.plugins.sparql.stats.QueryStats] object in
its stats attribute. The object belongs to exactly one query
execution/result iteration; nothing is kept in module level state, so
concurrently or interleaved iterated queries each have their own
independent statistics.

What is counted
---------------

Every algebra operator (BGP, Join, LeftJoin/OPTIONAL, AggregateJoin,
sub-SelectQuery, Slice/LIMIT, ...) gets an
[OperatorStats][rdflib.plugins.sparql.stats.OperatorStats] node with:

- node_id: a stable identifier of this operator within this query
  execution (op0, op1 ... assigned by a deterministic pre-order walk of
  the query algebra).
- parent_id / children: the operator tree of the query algebra.
- calls: how often evaluation of this operator was invoked, i.e. how
  many (lazy) iterators were created for it. Operators in the right-hand
  side of a lazy join, in an OPTIONAL (LeftJoin) or a sub-query may be
  evaluated once per incoming row of their left-hand side, so calls
  can be larger than one.
- rows_out: rows this operator actually produced (delivered to its
  parent operator) so far.
- rows_in: rows this operator actually read. For operators with
  children this is the sum of the rows_out of their direct children
  (every row a child produces is consumed by its parent). For BGP leaf
  operators it is the number of triples read from the store
  (source_rows). For operators evaluated by a registered custom
  evaluator (rdflib.plugins.sparql.CUSTOM_EVALS) the internals cannot
  be observed: the node is marked opaque and rows_in is None
  instead of a fabricated number.

Counting semantics of specific operators:

- Join: rows_in is the sum of rows read from both sides. For a
  lazy join the right side is evaluated once per left row, so the right
  child's calls and rows_out reflect all of these evaluations.
- OPTIONAL (LeftJoin): like a join, but the right side may be
  evaluated an additional time per left row without a match (the
  evaluator re-checks whether the optional part could have matched
  without prior bindings). All of these evaluations are counted.
- Aggregation (AggregateJoin/Group): reads its child completely
  (rows_in is the total number of grouped rows) and produces one row
  per group.
- Sub-queries: a nested SelectQuery is a normal operator node in
  the tree; its calls/rows_in/rows_out only cover the evaluations
  that actually happened within this query execution.
- Slice (LIMIT/OFFSET): only rows actually pulled through the slice
  are counted downstream. If the slice truncates the result, operators
  below it end up in state closed (they were not read to completion).

Lifecycle states
----------------

Each operator node, and the report as a whole, is in one of these states:

- not_started: no iterator of this operator was ever advanced.
- running: at least one iterator was advanced and has not finished.
- completed: all started iterators were read to exhaustion.
- closed: an iterator was closed before it was exhausted, e.g.
  because a LIMIT truncated the result, the user stopped iterating and
  closed the result ([Result.close][rdflib.query.Result.close]), or the
  result was discarded.
- failed: evaluation raised an exception.

If a result is only partially consumed, the report simply reflects the
work that has happened so far. After a result is explicitly closed, the
final statistics remain readable from result.stats.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from rdflib.plugins.sparql.parserutils import CompValue

__all__ = [
    "NOT_STARTED",
    "RUNNING",
    "COMPLETED",
    "CLOSED",
    "FAILED",
    "OperatorStats",
    "QueryStats",
]

#: lifecycle states of an operator node / of a whole query execution
NOT_STARTED = "not_started"
RUNNING = "running"
COMPLETED = "completed"
CLOSED = "closed"
FAILED = "failed"

#: CompValue keys that hold sub-algebra (child operators)
_CHILD_KEYS = ("p", "p1", "p2")


class OperatorStats:
    """
    Statistics for a single algebra operator within one query execution.

    See the [module documentation][rdflib.plugins.sparql.stats] for the
    exact counting semantics.
    """

    def __init__(
        self,
        query_stats: QueryStats,
        node_id: str,
        name: str,
        parent_id: Optional[str] = None,
    ):
        self.query_stats = query_stats
        #: stable identifier of this operator within this query execution
        self.node_id = node_id
        #: name of the algebra operator, e.g. "BGP", "Join", "LeftJoin"
        self.name = name
        #: node_id of the parent operator, None for the root
        self.parent_id = parent_id
        #: node_ids of the child operators
        self.children: list[str] = []
        #: how often evaluation of this operator was invoked
        self.calls = 0
        #: rows produced by this operator so far
        self.rows_out = 0
        #: triples read from the store (BGP operators only)
        self.source_rows = 0
        #: True if a custom evaluator handled this operator and its
        #: internals cannot be observed
        self.opaque = False
        #: name of the custom evaluator that handled this operator, if any
        self.handler: Optional[str] = None
        #: lifecycle state, one of not_started/running/completed/closed/failed
        self.state = NOT_STARTED
        #: of the calls made, how many of their iterators completed,
        #: were closed early or failed
        self.completed_calls = 0
        self.closed_calls = 0
        self.failed_calls = 0
        self._active = 0

    @property
    def rows_in(self) -> Optional[int]:
        """
        Rows this operator actually read.

        The sum of the output rows of the child operators, or, for BGP
        leaf operators, the number of triples read from the store.
        None for opaque operators evaluated by a custom evaluator,
        whose internals cannot be observed.
        """
        if self.opaque:
            return None
        if not self.children:
            return self.source_rows if self.name == "BGP" else 0
        return sum(self.query_stats.nodes[child].rows_out for child in self.children)

    def _begin(self) -> None:
        self._active += 1
        if self.state != FAILED:
            self.state = RUNNING

    def _completed(self) -> None:
        self._active -= 1
        self.completed_calls += 1
        if self._active <= 0 and self.state != FAILED:
            self.state = COMPLETED

    def _closed(self) -> None:
        self._active -= 1
        self.closed_calls += 1
        if self._active <= 0 and self.state != FAILED:
            self.state = CLOSED

    def _failed(self) -> None:
        self._active -= 1
        self.failed_calls += 1
        self.state = FAILED

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "id": self.node_id,
            "operator": self.name,
            "parent": self.parent_id,
            "children": list(self.children),
            "calls": self.calls,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "state": self.state,
            "opaque": self.opaque,
        }
        if self.name == "BGP":
            res["source_rows"] = self.source_rows
        if self.handler is not None:
            res["handler"] = self.handler
        if self.calls > 1 or self.closed_calls or self.failed_calls:
            res["completed_calls"] = self.completed_calls
            res["closed_calls"] = self.closed_calls
            res["failed_calls"] = self.failed_calls
        return res

    def __repr__(self) -> str:
        return (
            f"<OperatorStats {self.node_id} {self.name} state={self.state} "
            f"calls={self.calls} rows_in={self.rows_in} rows_out={self.rows_out}>"
        )


class QueryStats:
    """
    Execution statistics of one SPARQL query execution / result iteration.

    Instances are created per query execution when statistics are enabled
    (Graph.query(..., stats=True)) and are available as result.stats.
    """

    def __init__(self, algebra: CompValue):
        #: identifier of this query execution
        self.query_id = uuid.uuid4().hex
        #: operator nodes by node_id, in pre-order of the algebra tree
        self.nodes: dict[str, OperatorStats] = {}
        #: node_id of the root operator
        self.root_id: Optional[str] = None
        self._by_part: dict[int, OperatorStats] = {}
        self._counter = 0
        self._build(algebra, None)

    def _new_id(self) -> str:
        node_id = f"op{self._counter}"
        self._counter += 1
        return node_id

    def _build(self, part: CompValue, parent_id: Optional[str]) -> None:
        from rdflib.plugins.sparql.parserutils import CompValue

        node = OperatorStats(self, self._new_id(), part.name, parent_id)
        self.nodes[node.node_id] = node
        self._by_part[id(part)] = node
        if parent_id is None:
            self.root_id = node.node_id
        else:
            self.nodes[parent_id].children.append(node.node_id)
        for key in _CHILD_KEYS:
            if key in part:
                child = part[key]
                if isinstance(child, CompValue):
                    self._build(child, node.node_id)

    def node_for(self, part: CompValue) -> OperatorStats:
        """
        Return the stats node for an algebra part, creating one on demand
        for parts that are not reachable from the query algebra root
        (e.g. synthetic parts built by custom evaluators).
        """
        node = self._by_part.get(id(part))
        if node is None:
            node = OperatorStats(
                self, self._new_id(), getattr(part, "name", str(part)), None
            )
            self.nodes[node.node_id] = node
            self._by_part[id(part)] = node
        return node

    @property
    def state(self) -> str:
        """Lifecycle state of the query execution as a whole."""
        if self.root_id is None:
            return NOT_STARTED
        return self.nodes[self.root_id].state

    def wrap(self, node: OperatorStats, ret: Any) -> Any:
        """
        Wrap the return value of an operator evaluation so that rows are
        counted as they are actually delivered. Query-type operators
        return a result mapping; their (lazy) bindings are wrapped
        instead. Anything that is not iterable is returned unchanged.
        """
        if isinstance(ret, dict):
            bindings = ret.get("bindings")
            if bindings is not None:
                ret["bindings"] = self._count_rows(node, bindings)
            else:
                # eagerly evaluated query forms (ASK, CONSTRUCT, DESCRIBE)
                if ret.get("type_") == "ASK":
                    node.rows_out = 1 if ret.get("askAnswer") else 0
                elif ret.get("graph") is not None:
                    node.rows_out = len(ret["graph"])
                node._begin()
                node._completed()
            return ret
        if isinstance(ret, Iterable) and not isinstance(ret, (str, bytes)):
            return self._count_rows(node, ret)
        return ret

    def _count_rows(self, node: OperatorStats, iterable: Iterable[Any]) -> Any:
        try:
            iterator = iter(iterable)
        except TypeError:
            return iterable
        return self._counting(node, iterator)

    @staticmethod
    def _counting(node: OperatorStats, iterator: Iterator[Any]) -> Iterator[Any]:
        node._begin()
        try:
            for row in iterator:
                node.rows_out += 1
                yield row
        except GeneratorExit:
            # the consumer stopped early (LIMIT, Result.close(), ASK,
            # discarded result, ...): propagate the close downstream so
            # child operators are marked as closed as well
            close = getattr(iterator, "close", None)
            if close is not None:
                close()
            node._closed()
            raise
        except BaseException:
            node._failed()
            raise
        node._completed()

    def to_dict(self) -> dict[str, Any]:
        """The statistics as a plain, JSON-serializable dict."""
        return {
            "query_id": self.query_id,
            "state": self.state,
            "operators": [node.to_dict() for node in self.nodes.values()],
        }

    def report(self) -> str:
        """A human readable report of this query execution."""
        lines = [
            f"SPARQL query execution stats: query_id={self.query_id} "
            f"state={self.state}",
            (
                f"{'id':<6} {'operator':<16} {'state':<12} {'calls':>5} "
                f"{'rows_in':>8} {'rows_out':>8}  parent notes"
            ),
        ]
        for node in self.nodes.values():
            rows_in = "?" if node.rows_in is None else str(node.rows_in)
            notes = []
            if node.opaque:
                notes.append(f"opaque(custom-eval:{node.handler})")
            if node.name == "BGP":
                notes.append(f"source_rows={node.source_rows}")
            lines.append(
                f"{node.node_id:<6} {node.name:<16} {node.state:<12} "
                f"{node.calls:>5} {rows_in:>8} {node.rows_out:>8}  "
                f"{node.parent_id or '-':<6} {' '.join(notes)}"
            )
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.report()

    def __repr__(self) -> str:
        return f"<QueryStats query_id={self.query_id} state={self.state}>"
