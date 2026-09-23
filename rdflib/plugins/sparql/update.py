"""
Code for carrying out Update Operations
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, Optional

from rdflib.graph import ConjunctiveGraph, Dataset, Graph
from rdflib.plugins.sparql.evaluate import evalBGP, evalPart
from rdflib.plugins.sparql.evalutils import _fillTemplate, _join
from rdflib.plugins.sparql.parserutils import CompValue
from rdflib.plugins.sparql.sparql import FrozenDict, QueryContext, Update
from rdflib.term import Identifier, URIRef, Variable


class Quad(NamedTuple):
    """
    A triple together with the identifier of the graph it belongs to.

    Used in [`UpdateReport`][rdflib.plugins.sparql.update.UpdateReport] to
    describe which quads an update request inserted or deleted.
    """

    subject: Identifier
    predicate: Identifier
    object: Identifier
    graph: Identifier

    @property
    def triple(self) -> tuple[Identifier, Identifier, Identifier]:
        return (self.subject, self.predicate, self.object)


@dataclass
class OperationChanges:
    """
    The quads actually inserted and deleted by a single update operation.

    Only effective changes are listed: inserting a quad that is already
    present is not reported as an insertion, and deleting a quad that is
    not present is not reported as a deletion.
    """

    name: str
    inserted: list[Quad] = field(default_factory=list)
    deleted: list[Quad] = field(default_factory=list)


class UpdateReport:
    """
    The changes performed by a SPARQL Update request.

    Returned by
    [`evalUpdateWithReport`][rdflib.plugins.sparql.update.evalUpdateWithReport]
    and [`Graph.update_with_report`][rdflib.graph.Graph.update_with_report].

    ``operations`` holds the per-operation changes in execution order, while
    ``inserted``/``deleted`` give the net change of the whole request, i.e.
    the quads that are present/absent after the request but were not
    before it.
    """

    def __init__(self) -> None:
        self.operations: list[OperationChanges] = []
        self._net_inserted: set[Quad] = set()
        self._net_deleted: set[Quad] = set()

    @property
    def inserted(self) -> set[Quad]:
        """Net set of quads added by the whole request."""
        return set(self._net_inserted)

    @property
    def deleted(self) -> set[Quad]:
        """Net set of quads removed by the whole request."""
        return set(self._net_deleted)

    def __repr__(self) -> str:
        return "UpdateReport(operations=%r, inserted=%r, deleted=%r)" % (
            self.operations,
            self._net_inserted,
            self._net_deleted,
        )


class UpdateRecorder:
    """
    Records the writes performed while evaluating an update request.

    The eval functions below call back into this recorder for every triple
    they effectively add or remove, so the report always reflects the actual
    state of the store (including blank node identities) without
    re-evaluating any WHERE clause.
    """

    def __init__(self) -> None:
        self.report = UpdateReport()
        self._operation: Optional[OperationChanges] = None

    def begin_operation(self, name: str) -> None:
        self._operation = OperationChanges(name)
        self.report.operations.append(self._operation)

    def record_insert(
        self, graph: Graph, triple: tuple[Identifier, Identifier, Identifier]
    ) -> None:
        quad = Quad(triple[0], triple[1], triple[2], graph.identifier)
        assert self._operation is not None
        self._operation.inserted.append(quad)
        if quad in self.report._net_deleted:
            self.report._net_deleted.discard(quad)
        else:
            self.report._net_inserted.add(quad)

    def record_delete(
        self, graph: Graph, triple: tuple[Identifier, Identifier, Identifier]
    ) -> None:
        quad = Quad(triple[0], triple[1], triple[2], graph.identifier)
        assert self._operation is not None
        self._operation.deleted.append(quad)
        if quad in self.report._net_inserted:
            self.report._net_inserted.discard(quad)
        else:
            self.report._net_deleted.add(quad)


def _write_graph(g: Graph) -> Graph:
    """
    The graph that triples added to ``g`` actually land in.

    Adding a triple to a ConjunctiveGraph/Dataset writes to its default
    context.
    """
    if isinstance(g, Dataset):
        return g.default_graph
    if isinstance(g, ConjunctiveGraph):
        return g.default_context
    return g


def _recorded_insert(
    recorder: UpdateRecorder,
    g: Graph,
    triples: Iterator[tuple[Identifier, Identifier, Identifier]],
) -> None:
    """Recording version of ``g += triples``."""
    target = _write_graph(g)
    for t in triples:
        if t not in target:
            target.add(t)
            recorder.record_insert(target, t)


def _contexts(
    g: ConjunctiveGraph,
    triple: Optional[tuple[Identifier, Identifier, Identifier]] = None,
) -> Iterator[Graph]:
    """g.contexts(), without the deprecation warning on Dataset."""
    if isinstance(g, Dataset):
        return g.graphs(triple)
    return g.contexts(triple)


def _recorded_delete(
    recorder: UpdateRecorder,
    g: Graph,
    triples: Iterator[tuple[Identifier, Identifier, Identifier]],
) -> None:
    """Recording version of ``g -= triples``."""
    if isinstance(g, ConjunctiveGraph):
        # ConjunctiveGraph.remove removes the triple from all contexts
        for t in triples:
            # Dataset.graphs() always includes the default graph, even if
            # the triple is not in it, so filter on actual membership.
            contexts = [c for c in _contexts(g, t) if t in c]
            g.remove(t)
            for c in contexts:
                recorder.record_delete(c, t)
    else:
        for t in triples:
            if t in g:
                g.remove(t)
                recorder.record_delete(g, t)


def _recorded_clear(recorder: UpdateRecorder, g: Graph) -> None:
    """Recording version of ``g.remove((None, None, None))``."""
    if isinstance(g, ConjunctiveGraph):
        # remove((None, None, None)) clears all contexts
        removed = [(c, t) for c in list(_contexts(g)) for t in c]
        g.remove((None, None, None))
        for c, t in removed:
            recorder.record_delete(c, t)
    else:
        removed = list(g)
        g.remove((None, None, None))
        for t in removed:
            recorder.record_delete(g, t)


def _recorded_remove_graph(recorder: UpdateRecorder, g: Graph) -> None:
    """Record all triples of ``g`` as deleted (for DROP/MOVE on graph-aware stores)."""
    if isinstance(g, ConjunctiveGraph):
        for c in list(_contexts(g)):
            for t in c:
                recorder.record_delete(c, t)
    else:
        for t in g:
            recorder.record_delete(g, t)


_RECORDER_UNSUPPORTED_OPS = frozenset({"Load"})
"""
Update operations whose writes cannot be observed by an UpdateRecorder.
"""


def _graphOrDefault(ctx: QueryContext, g: str) -> Optional[Graph]:
    if g == "DEFAULT":
        return ctx.graph
    else:
        return ctx.dataset.get_context(g)


def _graphAll(ctx: QueryContext, g: str) -> Sequence[Graph]:
    """
    return a list of graphs
    """
    if g == "DEFAULT":
        # type error: List item 0 has incompatible type "Optional[Graph]"; expected "Graph"
        return [ctx.graph]  # type: ignore[list-item]
    elif g == "NAMED":
        return [
            # type error: Item "None" of "Optional[Graph]" has no attribute "identifier"
            c
            for c in ctx.dataset.contexts()
            if c.identifier != ctx.graph.identifier  # type: ignore[union-attr]
        ]
    elif g == "ALL":
        return list(ctx.dataset.contexts())
    else:
        return [ctx.dataset.get_context(g)]


def evalLoad(ctx: QueryContext, u: CompValue) -> None:
    """
    http://www.w3.org/TR/sparql11-update/#load
    """

    if TYPE_CHECKING:
        assert isinstance(u.iri, URIRef)

    if u.graphiri:
        ctx.load(u.iri, default=False, into=u.graphiri)
    else:
        ctx.load(u.iri, default=True)


def evalCreate(ctx: QueryContext, u: CompValue) -> None:
    """
    http://www.w3.org/TR/sparql11-update/#create
    """
    g = ctx.dataset.get_context(u.graphiri)
    if len(g) > 0:
        raise Exception("Graph %s already exists." % g.identifier)
    raise Exception("Create not implemented!")


def evalClear(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    """
    http://www.w3.org/TR/sparql11-update/#clear
    """
    for g in _graphAll(ctx, u.graphiri):
        if recorder is None:
            g.remove((None, None, None))
        else:
            _recorded_clear(recorder, g)


def evalDrop(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    """
    http://www.w3.org/TR/sparql11-update/#drop
    """
    if ctx.dataset.store.graph_aware:
        for g in _graphAll(ctx, u.graphiri):
            if recorder is not None:
                _recorded_remove_graph(recorder, g)
            ctx.dataset.store.remove_graph(g)
    else:
        evalClear(ctx, u, recorder)


def evalInsertData(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    """
    http://www.w3.org/TR/sparql11-update/#insertData
    """
    # add triples
    g = ctx.graph
    if recorder is None:
        g += u.triples
    else:
        _recorded_insert(recorder, g, iter(u.triples))
    # add quads
    # u.quads is a dict of graphURI=>[triples]
    for g in u.quads:
        # type error: Argument 1 to "get_context" of "ConjunctiveGraph" has incompatible type "Optional[Graph]"; expected "Union[IdentifiedNode, str, None]"
        cg = ctx.dataset.get_context(g)
        if recorder is None:
            cg += u.quads[g]
        else:
            _recorded_insert(recorder, cg, iter(u.quads[g]))


def evalDeleteData(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    """
    http://www.w3.org/TR/sparql11-update/#deleteData
    """
    # remove triples
    g = ctx.graph
    if recorder is None:
        g -= u.triples
    else:
        _recorded_delete(recorder, g, iter(u.triples))

    # remove quads
    # u.quads is a dict of graphURI=>[triples]
    for g in u.quads:
        # type error: Argument 1 to "get_context" of "ConjunctiveGraph" has incompatible type "Optional[Graph]"; expected "Union[IdentifiedNode, str, None]"
        cg = ctx.dataset.get_context(g)
        if recorder is None:
            cg -= u.quads[g]
        else:
            _recorded_delete(recorder, cg, iter(u.quads[g]))


def evalDeleteWhere(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    """
    http://www.w3.org/TR/sparql11-update/#deleteWhere
    """

    res: Iterator[FrozenDict] = evalBGP(ctx, u.triples)
    for g in u.quads:
        cg = ctx.dataset.get_context(g)
        c = ctx.pushGraph(cg)
        res = _join(res, list(evalBGP(c, u.quads[g])))

    # type error: Incompatible types in assignment (expression has type "FrozenBindings", variable has type "QueryContext")
    for c in res:  # type: ignore[assignment]
        g = ctx.graph
        if recorder is None:
            g -= _fillTemplate(u.triples, c)  # type: ignore[operator]
        else:
            _recorded_delete(recorder, g, _fillTemplate(u.triples, c))

        for g in u.quads:
            cg = ctx.dataset.get_context(c.get(g))
            if recorder is None:
                cg -= _fillTemplate(u.quads[g], c)
            else:
                _recorded_delete(recorder, cg, _fillTemplate(u.quads[g], c))


def evalModify(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    originalctx = ctx

    # Using replaces the dataset for evaluating the where-clause
    dg: Optional[Graph]
    if u.using:
        otherDefault = False
        for d in u.using:
            if d.default:
                if not otherDefault:
                    # replace current default graph
                    dg = Graph()
                    ctx = ctx.pushGraph(dg)
                    otherDefault = True

                ctx.load(d.default, default=True)

            elif d.named:
                g = d.named
                ctx.load(g, default=False)

    # "The WITH clause provides a convenience for when an operation
    # primarily refers to a single graph. If a graph name is specified
    # in a WITH clause, then - for the purposes of evaluating the
    # WHERE clause - this will define an RDF Dataset containing a
    # default graph with the specified name, but only in the absence
    # of USING or USING NAMED clauses. In the presence of one or more
    # graphs referred to in USING clauses and/or USING NAMED clauses,
    # the WITH clause will be ignored while evaluating the WHERE
    # clause."
    if not u.using and u.withClause:
        g = ctx.dataset.get_context(u.withClause)
        ctx = ctx.pushGraph(g)

    res = evalPart(ctx, u.where)

    if u.using:
        if otherDefault:
            ctx = originalctx  # restore original default graph
        if u.withClause:
            g = ctx.dataset.get_context(u.withClause)
            ctx = ctx.pushGraph(g)

    for c in list(res):
        # TODO: Make this more intentional and without the weird type checking logic
        #       once ConjunctiveGraph is removed and Dataset no longer inherits from
        #       Graph.
        dg = ctx.graph if type(ctx.graph) is Graph else ctx.dataset.default_context
        if u.delete:
            if recorder is None:
                # type error: Unsupported left operand type for - ("None")
                # type error: Unsupported operand types for - ("Graph" and "Generator[tuple[Identifier, Identifier, Identifier], None, None]")
                dg -= _fillTemplate(u.delete.triples, c)  # type: ignore[operator]

                for g, q in u.delete.quads.items():
                    cg = ctx.dataset.get_context(c.get(g))
                    cg -= _fillTemplate(q, c)
            else:
                _recorded_delete(recorder, dg, _fillTemplate(u.delete.triples, c))

                for g, q in u.delete.quads.items():
                    cg = ctx.dataset.get_context(c.get(g))
                    _recorded_delete(recorder, cg, _fillTemplate(q, c))

        if u.insert:
            if recorder is None:
                # type error: Unsupported left operand type for + ("None")
                # type error: Unsupported operand types for + ("Graph" and "Generator[tuple[Identifier, Identifier, Identifier], None, None]")
                dg += _fillTemplate(u.insert.triples, c)  # type: ignore[operator]

                for g, q in u.insert.quads.items():
                    cg = ctx.dataset.get_context(c.get(g))
                    cg += _fillTemplate(q, c)
            else:
                _recorded_insert(recorder, dg, _fillTemplate(u.insert.triples, c))

                for g, q in u.insert.quads.items():
                    cg = ctx.dataset.get_context(c.get(g))
                    _recorded_insert(recorder, cg, _fillTemplate(q, c))


def evalAdd(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    """

    add all triples from src to dst

    http://www.w3.org/TR/sparql11-update/#add
    """
    src, dst = u.graph

    srcg = _graphOrDefault(ctx, src)
    dstg = _graphOrDefault(ctx, dst)

    # type error: Item "None" of "Optional[Graph]" has no attribute "identifier"
    if srcg.identifier == dstg.identifier:  # type: ignore[union-attr]
        return

    if recorder is None:
        # type error: Unsupported left operand type for + ("None")
        dstg += srcg  # type: ignore[operator]
    else:
        _recorded_insert(recorder, dstg, iter(list(srcg)))


def evalMove(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    """

    remove all triples from dst
    add all triples from src to dst
    remove all triples from src

    http://www.w3.org/TR/sparql11-update/#move
    """

    src, dst = u.graph

    srcg = _graphOrDefault(ctx, src)
    dstg = _graphOrDefault(ctx, dst)

    # type error: Item "None" of "Optional[Graph]" has no attribute "identifier"
    if srcg.identifier == dstg.identifier:  # type: ignore[union-attr]
        return

    if recorder is None:
        # type error: Item "None" of "Optional[Graph]" has no attribute "remove"
        dstg.remove((None, None, None))  # type: ignore[union-attr]

        # type error: Unsupported left operand type for + ("None")
        dstg += srcg  # type: ignore[operator]
    else:
        _recorded_clear(recorder, dstg)
        _recorded_insert(recorder, dstg, iter(list(srcg)))

    if ctx.dataset.store.graph_aware:
        if recorder is not None:
            _recorded_remove_graph(recorder, srcg)
        # type error: Argument 1 to "remove_graph" of "Store" has incompatible type "Optional[Graph]"; expected "Graph"
        ctx.dataset.store.remove_graph(srcg)  # type: ignore[arg-type]
    else:
        if recorder is None:
            # type error: Item "None" of "Optional[Graph]" has no attribute "remove"
            srcg.remove((None, None, None))  # type: ignore[union-attr]
        else:
            _recorded_clear(recorder, srcg)


def evalCopy(
    ctx: QueryContext, u: CompValue, recorder: Optional[UpdateRecorder] = None
) -> None:
    """

    remove all triples from dst
    add all triples from src to dst

    http://www.w3.org/TR/sparql11-update/#copy
    """

    src, dst = u.graph

    srcg = _graphOrDefault(ctx, src)
    dstg = _graphOrDefault(ctx, dst)

    # type error: Item "None" of "Optional[Graph]" has no attribute "remove"
    if srcg.identifier == dstg.identifier:  # type: ignore[union-attr]
        return

    if recorder is None:
        # type error: Item "None" of "Optional[Graph]" has no attribute "remove"
        dstg.remove((None, None, None))  # type: ignore[union-attr]

        # type error: Unsupported left operand type for + ("None")
        dstg += srcg  # type: ignore[operator]
    else:
        _recorded_clear(recorder, dstg)
        _recorded_insert(recorder, dstg, iter(list(srcg)))


def evalUpdate(
    graph: Graph,
    update: Update,
    initBindings: Optional[Mapping[str, Identifier]] = None,
    recorder: Optional[UpdateRecorder] = None,
) -> None:
    """http://www.w3.org/TR/sparql11-update/#updateLanguage

    'A request is a sequence of operations [...] Implementations MUST
    ensure that operations of a single request are executed in a
    fashion that guarantees the same effects as executing them in
    lexical order.

    Operations all result either in success or failure.

    If multiple operations are present in a single request, then a
    result of failure from any operation MUST abort the sequence of
    operations, causing the subsequent operations to be ignored.'

    This will return None on success and raise Exceptions on error

    !!! warning "Security Considerations"

        This method can access indirectly requested network endpoints, for
        example, query processing will attempt to access network endpoints
        specified in `SERVICE` directives.

        When processing untrusted or potentially malicious queries, measures
        should be taken to restrict network and file access.

        For information on available security measures, see the RDFLib
        [Security Considerations](../security_considerations.md)
        documentation.

    """

    if recorder is not None:
        # Fail before executing anything: the writes performed by these
        # operations cannot be observed by the recorder.
        unsupported = sorted(
            {u.name for u in update.algebra if u.name in _RECORDER_UNSUPPORTED_OPS}
        )
        if unsupported:
            raise NotImplementedError(
                "Cannot record changes for update request: the %s operation(s) "
                "are not supported, as the writes they perform cannot be "
                "observed." % ", ".join(unsupported)
            )

    for u in update.algebra:
        initBindings = dict((Variable(k), v) for k, v in (initBindings or {}).items())

        ctx = QueryContext(graph, initBindings=initBindings)
        ctx.prologue = u.prologue

        if recorder is not None:
            recorder.begin_operation(u.name)

        try:
            if u.name == "Load":
                evalLoad(ctx, u)
            elif u.name == "Clear":
                evalClear(ctx, u, recorder)
            elif u.name == "Drop":
                evalDrop(ctx, u, recorder)
            elif u.name == "Create":
                evalCreate(ctx, u)
            elif u.name == "Add":
                evalAdd(ctx, u, recorder)
            elif u.name == "Move":
                evalMove(ctx, u, recorder)
            elif u.name == "Copy":
                evalCopy(ctx, u, recorder)
            elif u.name == "InsertData":
                evalInsertData(ctx, u, recorder)
            elif u.name == "DeleteData":
                evalDeleteData(ctx, u, recorder)
            elif u.name == "DeleteWhere":
                evalDeleteWhere(ctx, u, recorder)
            elif u.name == "Modify":
                evalModify(ctx, u, recorder)
            else:
                raise Exception("Unknown update operation: %s" % (u,))
        except:  # noqa: E722
            if not u.silent:
                raise


def evalUpdateWithReport(
    graph: Graph,
    update: Update,
    initBindings: Optional[Mapping[str, Identifier]] = None,
) -> UpdateReport:
    """
    Like evalUpdate, but returns an UpdateReport describing the quads each
    operation actually inserted and deleted.

    The report is recorded while the request executes; no WHERE clause is
    evaluated twice and blank nodes in the report are the identities that
    were actually written to the store.

    LOAD operations are not supported, as their writes cannot be observed;
    a NotImplementedError is raised before anything is executed.
    """
    recorder = UpdateRecorder()
    evalUpdate(graph, update, initBindings, recorder=recorder)
    return recorder.report
