"""
Opt-in SPARQL query execution statistics.

When enabled, the SPARQL evaluator builds one :class:`QueryStats` object
per query execution.  It records, for every algebra operator that takes
part in the evaluation, a stable per-execution identifier, the
parent/child relation between operators, how often the operator was
evaluated and how many solution rows flowed in and out of it.

Statistics are collected *while the result is iterated*: nothing is
re-executed for the sake of accounting and lazy results are never
materialized into lists.  Enabling statistics therefore neither performs
extra triple reads against the store nor changes the result multiset.

Usage:

>>> from rdflib import Graph
>>> g = Graph()
>>> _ = g.parse(
...     data="@prefix : <http://example.org/> . :a :knows :b . :b :knows :c .",
...     format="turtle",
... )
>>> result = g.query(
...     "SELECT * WHERE { ?s ?p ?o } LIMIT 1", stats=True
... )
>>> rows = list(result)
>>> len(rows)
1
>>> report = result.stats
>>> report.roots[0].name
'SelectQuery'
>>> report.roots[0].rows_out
1

The report follows a single result iteration: it is stored on the
:class:`~rdflib.plugins.sparql.processor.SPARQLResult` (and optionally in
a user supplied :class:`QueryStats` instance), never in module level
state, so concurrently or interleaved iterated queries each get their own
independent report.

Counting semantics
------------------

One :class:`OperatorStats` node exists per algebra operator
(:class:`~rdflib.plugins.sparql.parserutils.CompValue`) of the query.
The node id (``op0``, ``op1``, ...) is assigned in order of first
evaluation and is stable for the duration of one query execution.

- ``calls``: how often evaluation of the operator was started, i.e. how
  often ``evalPart`` produced an evaluation instance for it.  The right
  hand side of a (lazy) ``Join`` or of a ``LeftJoin`` (``OPTIONAL``) is
  evaluated once per row arriving from the left hand side, so these
  operators commonly show ``calls`` greater than one.  ``LeftJoin`` may
  additionally re-evaluate its right hand side for the spec mandated
  no-match recheck, which is counted the same way.
- ``starts``: how many of those evaluation instances were actually
  iterated at least once.  Instances that were created but never pulled
  (e.g. below a ``LIMIT 0``) remain ``not_started``.
- ``rows_in``: rows the operator pulled from its child operators,
  summed over all calls.  Source operators such as ``BGP`` or ``values``
  have no operator input and report ``0``.
- ``rows_out``: rows the operator emitted to its consumer.  For a
  ``BGP`` this is the number of solutions (matched triple paths) read
  from the graph; for ``AggregateJoin`` it is the number of groups
  produced; for a subquery the inner ``SelectQuery``/``Project`` nodes
  are ordinary child nodes whose counts accumulate over every
  (correlated) re-evaluation of the subquery.
- ``opaque``: custom evaluation functions registered in
  ``CUSTOM_EVALS`` participate in the report, but their internals cannot
  be observed.  Operators handled by a custom evaluator are marked
  opaque: only the rows crossing the operator boundary are counted and
  no internal row counts are fabricated (``rows_in`` stays ``0``).

Operator states
---------------

- ``not_started``: evaluation instances were created but none was
  iterated yet.
- ``running``: at least one instance is currently mid-iteration, e.g.
  when the result was only partially consumed.
- ``completed``: all started instances ran to exhaustion.
- ``closed``: at least one instance was closed early, e.g. because
  ``LIMIT`` truncated the stream or the result was explicitly closed.
- ``failed``: at least one instance raised an exception.

When a result is only partially consumed, or truncated by ``LIMIT``,
the report reflects the work that actually happened: operators below the
cut point show the rows they produced so far and end up ``closed`` once
the remaining iterators are released.  Closing a result explicitly
(:meth:`rdflib.query.Result.close`) finalizes the report, which stays
readable afterwards.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any, Callable, Optional

__all__ = [
    "NOT_STARTED",
    "RUNNING",
    "COMPLETED",
    "CLOSED",
    "FAILED",
    "OperatorStats",
    "QueryStats",
]

#: evaluation instances were created but none was iterated yet
NOT_STARTED = "not_started"
#: at least one evaluation instance is currently mid-iteration
RUNNING = "running"
#: all started evaluation instances ran to exhaustion
COMPLETED = "completed"
#: at least one evaluation instance was closed before exhaustion
CLOSED = "closed"
#: at least one evaluation instance raised an exception
FAILED = "failed"


class OperatorStats:
    """Statistics for a single algebra operator within one query execution."""

    def __init__(
        self,
        index: int,
        name: str,
        parent: Optional[OperatorStats],
        part: Any,
    ):
        #: stable identifier of this operator within one query execution
        self.id: str = "op%d" % index
        #: name of the algebra operator, e.g. ``"BGP"`` or ``"LeftJoin"``
        self.name: str = name
        #: the operator whose evaluation triggered this one, if any
        self.parent: Optional[OperatorStats] = parent
        # keep a reference to the algebra part so that its id() cannot be
        # recycled for a different operator while this report is alive
        self._part = part
        #: how often evaluation of this operator was started
        self.calls: int = 0
        #: how many evaluation instances were actually iterated
        self.starts: int = 0
        #: rows pulled from child operators, summed over all calls
        self.rows_in: int = 0
        #: rows emitted to the consumer, summed over all calls
        self.rows_out: int = 0
        #: evaluation instances that ran to exhaustion
        self.completed: int = 0
        #: evaluation instances that were closed before exhaustion
        self.closed: int = 0
        #: evaluation instances that raised an exception
        self.failed: int = 0
        #: True if a custom evaluator handled this operator and its
        #: internals could not be observed
        self.opaque: bool = False
        self._open: int = 0

    @property
    def parent_id(self) -> Optional[str]:
        """id of the parent operator, or None for the root operator"""
        return self.parent.id if self.parent is not None else None

    @property
    def state(self) -> str:
        """
        The state of this operator: one of ``not_started``, ``running``,
        ``completed``, ``closed`` or ``failed``.
        """
        if self.failed:
            return FAILED
        if self._open:
            return RUNNING
        if not self.starts and not self.completed:
            return NOT_STARTED
        if self.closed:
            return CLOSED
        return COMPLETED

    def to_dict(self) -> dict[str, Any]:
        """a plain dict with all counters of this operator"""
        return {
            "id": self.id,
            "name": self.name,
            "parent": self.parent_id,
            "state": self.state,
            "opaque": self.opaque,
            "calls": self.calls,
            "starts": self.starts,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "completed": self.completed,
            "closed": self.closed,
            "failed": self.failed,
        }

    def __repr__(self) -> str:
        return "<OperatorStats %s %s state=%s calls=%d rows_in=%d rows_out=%d>" % (
            self.id,
            self.name,
            self.state,
            self.calls,
            self.rows_in,
            self.rows_out,
        )


class QueryStats:
    """
    Execution statistics of a single SPARQL query evaluation.

    Instances are created per query execution - either by passing
    ``stats=True`` to the query methods, or by passing an existing
    instance to be filled in.  The report is attached to the resulting
    :class:`~rdflib.plugins.sparql.processor.SPARQLResult` as
    ``result.stats``.
    """

    def __init__(self) -> None:
        self._nodes: list[OperatorStats] = []
        self._by_part: dict[int, OperatorStats] = {}
        self._stack: list[OperatorStats] = []

    @property
    def operators(self) -> list[OperatorStats]:
        """all operator nodes, in order of first evaluation"""
        return list(self._nodes)

    @property
    def roots(self) -> list[OperatorStats]:
        """operators without a parent, normally exactly one"""
        return [node for node in self._nodes if node.parent is None]

    def children_of(self, node: OperatorStats) -> list[OperatorStats]:
        """direct child operators of the given operator"""
        return [n for n in self._nodes if n.parent is node]

    def operator(self, op_id: str) -> OperatorStats:
        """look up an operator by its stable id"""
        for node in self._nodes:
            if node.id == op_id:
                return node
        raise KeyError(op_id)

    def to_dict(self) -> dict[str, Any]:
        """a plain dict representation of the whole report"""
        return {"operators": [node.to_dict() for node in self._nodes]}

    def _node_for(self, part: Any) -> OperatorStats:
        key = id(part)
        node = self._by_part.get(key)
        if node is None:
            parent = self._stack[-1] if self._stack else None
            node = OperatorStats(
                index=len(self._nodes),
                name=getattr(part, "name", None) or type(part).__name__,
                parent=parent,
                part=part,
            )
            self._nodes.append(node)
            self._by_part[key] = node
        return node

    def eval_part(
        self,
        ctx: Any,
        part: Any,
        custom_evals: Mapping[str, Callable[[Any, Any], Any]],
        builtin: Callable[[Any, Any], Any],
    ) -> Any:
        """
        Evaluate one algebra part under statistics collection.

        Called by ``evalPart`` in ``rdflib.plugins.sparql.evaluate``;
        mirrors its dispatch: custom evaluation functions first, then the
        builtin operators.
        """
        node = self._node_for(part)
        node.calls += 1
        for name, c in custom_evals.items():
            try:
                result = c(ctx, part)
            except NotImplementedError:
                continue  # the given custom-function did not handle this part
            # a custom evaluator handled this part; its internals cannot
            # be observed, only the rows crossing the operator boundary
            node.opaque = True
            return self._observe(node, result)
        self._stack.append(node)
        try:
            result = builtin(ctx, part)
        finally:
            self._stack.pop()
        return self._observe(node, result)

    def _observe(self, node: OperatorStats, result: Any) -> Any:
        if isinstance(result, MutableMapping) and "bindings" in result:
            # a select-query result mapping; count rows on the bindings
            result["bindings"] = self._count_rows(node, result["bindings"])
            return result
        if isinstance(result, Iterable) and not isinstance(
            result, (str, bytes, Mapping)
        ):
            return self._count_rows(node, result)
        # a non-row result (e.g. ASK/CONSTRUCT/DESCRIBE mappings) is
        # produced eagerly, so the evaluation is already done
        node.completed += 1
        return result

    def _count_rows(
        self, node: OperatorStats, iterable: Iterable[Any]
    ) -> Iterable[Any]:
        node.starts += 1
        node._open += 1
        it = iter(iterable)
        try:
            while True:
                self._stack.append(node)
                try:
                    row = next(it)
                except StopIteration:
                    node.completed += 1
                    return
                except GeneratorExit:
                    node.closed += 1
                    raise
                except BaseException:
                    node.failed += 1
                    raise
                finally:
                    self._stack.pop()
                node.rows_out += 1
                if node.parent is not None:
                    node.parent.rows_in += 1
                try:
                    yield row
                except GeneratorExit:
                    # the consumer closed this instance early; propagate
                    # the close down the chain so inner operators are
                    # finalized as well
                    node.closed += 1
                    close = getattr(it, "close", None)
                    if close is not None:
                        close()
                    raise
        finally:
            node._open -= 1

    def __str__(self) -> str:
        lines = []

        def visit(node: OperatorStats, depth: int) -> None:
            lines.append(
                "%s%s %s state=%s calls=%d rows_in=%d rows_out=%d%s"
                % (
                    "  " * depth,
                    node.id,
                    node.name,
                    node.state,
                    node.calls,
                    node.rows_in,
                    node.rows_out,
                    " opaque" if node.opaque else "",
                )
            )
            for child in self.children_of(node):
                visit(child, depth + 1)

        for root in self.roots:
            visit(root, 0)
        return "\n".join(lines)

