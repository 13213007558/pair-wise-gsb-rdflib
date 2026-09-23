from __future__ import annotations

import logging
from typing import Union

import pytest

from rdflib import RDF, RDFS, Dataset, Graph, Namespace, URIRef
from rdflib.namespace import DCAT, DCTERMS
from rdflib.paths import (
    AlternativePath,
    InvPath,
    MulPath,
    NegatedPath,
    OneOrMore,
    Path,
    SequencePath,
    WitnessStep,
    ZeroOrMore,
    ZeroOrOne,
    eval_path,
    eval_path_witness,
)

g = Graph()
nsm = g.namespace_manager


@pytest.mark.parametrize(
    "path, no_nsm, with_nsm",
    [
        (~RDF.type, f"^<{RDF.type}>", "^rdf:type"),
        (
            RDF.type / RDFS.subClassOf,
            f"<{RDF.type}>/<{RDFS.subClassOf}>",
            "rdf:type/rdfs:subClassOf",
        ),
        (
            RDF.type | RDFS.subClassOf,
            f"<{RDF.type}>|<{RDFS.subClassOf}>",
            "rdf:type|rdfs:subClassOf",
        ),
        (-RDF.type, f"!(<{RDF.type}>)", "!(rdf:type)"),
        # type errors: Unsupported operand types for * ("URIRef" and "str")
        # note on type errors: The operator is defined but in an odd place
        (
            RDFS.subClassOf * ZeroOrMore,  # type: ignore[operator]
            f"<{RDFS.subClassOf}>*",
            "rdfs:subClassOf*",
        ),
        (
            RDFS.subClassOf * OneOrMore,  # type: ignore[operator]
            f"<{RDFS.subClassOf}>+",
            "rdfs:subClassOf+",
        ),
        (
            RDFS.subClassOf * ZeroOrOne,  # type: ignore[operator]
            f"<{RDFS.subClassOf}>?",
            "rdfs:subClassOf?",
        ),
        (
            RDF.type / MulPath(RDFS.subClassOf, "*"),
            f"<{RDF.type}>/<{RDFS.subClassOf}>*",
            "rdf:type/rdfs:subClassOf*",
        ),
        (
            RDF.type / ((SequencePath(RDFS.subClassOf)) * "*"),
            f"<{RDF.type}>/<{RDFS.subClassOf}>*",
            "rdf:type/rdfs:subClassOf*",
        ),
        (
            RDF.type / RDFS.subClassOf * "*",
            f"(<{RDF.type}>/<{RDFS.subClassOf}>)*",
            "(rdf:type/rdfs:subClassOf)*",
        ),
        (
            -(RDF.type | RDFS.subClassOf),
            f"!(<{RDF.type}>|<{RDFS.subClassOf}>)",
            "!(rdf:type|rdfs:subClassOf)",
        ),
        (
            -(RDF.type | ((SequencePath(RDFS.subClassOf)) * "*")),
            f"!(<{RDF.type}>|<{RDFS.subClassOf}>*)",
            "!(rdf:type|rdfs:subClassOf*)",
        ),
        (
            SequencePath(RDFS.subClassOf),
            f"<{RDFS.subClassOf}>",
            "rdfs:subClassOf",
        ),
        (
            AlternativePath(RDFS.subClassOf),
            f"<{RDFS.subClassOf}>",
            "rdfs:subClassOf",
        ),
    ],
)
def test_paths_n3(
    path: Union[InvPath, SequencePath, AlternativePath, MulPath, NegatedPath],
    no_nsm: str,
    with_nsm: str,
) -> None:
    logging.debug("path = %s", path)
    assert path.n3() == no_nsm
    assert path.n3(nsm) == with_nsm


def test_mulpath_n3():
    uri = "http://example.com/foo"
    n3 = (URIRef(uri) * ZeroOrMore).n3()
    assert n3 == "<" + uri + ">*"


@pytest.mark.parametrize(
    ["lhs", "rhs"],
    [
        (DCTERMS.temporal / DCAT.endDate, DCTERMS.temporal / DCAT.endDate),
        (SequencePath(DCTERMS.temporal, DCAT.endDate), DCTERMS.temporal / DCAT.endDate),
    ],
)
def test_eq(lhs: Path, rhs: Path) -> None:
    logging.debug("lhs = %s/%r, rhs = %s/%r", type(lhs), lhs, type(rhs), rhs)
    assert lhs == rhs


@pytest.mark.parametrize(
    ["lhs", "rhs"],
    [
        (DCTERMS.temporal / DCAT.endDate, DCTERMS.temporal / DCAT.endDate),
        (SequencePath(DCTERMS.temporal, DCAT.endDate), DCTERMS.temporal / DCAT.endDate),
    ],
)
def test_hash(lhs: Path, rhs: Path) -> None:
    logging.debug("lhs = %s/%r, rhs = %s/%r", type(lhs), lhs, type(rhs), rhs)
    assert hash(lhs) == hash(rhs)


@pytest.mark.parametrize(
    ["insert_path", "check_path"],
    [
        (DCTERMS.temporal / DCAT.endDate, DCTERMS.temporal / DCAT.endDate),
        (SequencePath(DCTERMS.temporal, DCAT.endDate), DCTERMS.temporal / DCAT.endDate),
    ],
)
def test_dict_key(insert_path: Path, check_path: Path) -> None:
    d = {insert_path: "foo"}
    assert d[check_path] == "foo"


EX = Namespace("http://example.org/")


def _replay_witness(g: Graph, start, witness):
    """Follow a witness step by step, returning the node it ends at."""
    node = start
    for step in witness:
        assert (step.subject, step.predicate, step.object) in g
        if step.forward:
            assert step.subject == node
            node = step.object
        else:
            assert step.object == node
            node = step.subject
    return node


def test_witness_prefers_legal_route_with_reverse_edge():
    """
    The graph has a one-edge route a -r-> b that does not match the
    expression p / ^q. The witness must follow the longer legal route
    a -p-> m <-q- b, crossing the q edge in reverse.
    """
    g = Graph()
    g.add((EX.a, EX.p, EX.m))
    g.add((EX.b, EX.q, EX.m))
    g.add((EX.a, EX.r, EX.b))

    path = EX.p / ~EX.q
    results = list(eval_path_witness(g, (EX.a, path, EX.b)))
    assert len(results) == 1
    subj, obj, witness = results[0]
    assert (subj, obj) == (EX.a, EX.b)
    assert witness == (
        WitnessStep(EX.a, EX.p, EX.m, True),
        WitnessStep(EX.b, EX.q, EX.m, False),
    )
    # the short but non-conforming r edge is not used
    assert all(step.predicate != EX.r for step in witness)
    assert _replay_witness(g, subj, witness) == obj


def test_witness_dedup_zero_length_and_replay():
    """
    A self loop and two alternative branches: endpoints are reported once,
    the zero-length match has an empty witness, and every witness replays
    from subject to object.
    """
    g = Graph()
    g.add((EX.x, EX.p, EX.x))
    g.add((EX.x, EX.p, EX.y))
    g.add((EX.x, EX.q, EX.y))
    g.add((EX.y, EX.p, EX.z))

    path = (EX.p | EX.q) * ZeroOrMore
    results = list(eval_path_witness(g, (EX.x, path, None)))

    endpoints = [obj for _, obj, _ in results]
    assert sorted(endpoints) == sorted([EX.x, EX.y, EX.z])
    assert len(set(endpoints)) == len(endpoints)

    by_endpoint = {obj: witness for _, obj, witness in results}
    # zero-length match on the start node
    assert by_endpoint[EX.x] == ()
    # y is one edge away despite two branches and the self loop
    assert len(by_endpoint[EX.y]) == 1
    assert len(by_endpoint[EX.z]) == 2

    for subj, obj, witness in results:
        assert _replay_witness(g, subj, witness) == obj


def test_witness_self_loop_one_or_more():
    g = Graph()
    g.add((EX.x, EX.p, EX.x))

    results = list(eval_path_witness(g, (EX.x, EX.p * OneOrMore, None)))
    assert len(results) == 1
    subj, obj, witness = results[0]
    assert (subj, obj) == (EX.x, EX.x)
    assert witness == (WitnessStep(EX.x, EX.p, EX.x, True),)


def test_witness_bound_object_runs_backwards():
    g = Graph()
    g.add((EX.a, EX.p, EX.m))
    g.add((EX.b, EX.q, EX.m))

    path = EX.p / ~EX.q
    results = list(eval_path_witness(g, (None, path, EX.b)))
    assert len(results) == 1
    subj, obj, witness = results[0]
    assert (subj, obj) == (EX.a, EX.b)
    assert witness == (
        WitnessStep(EX.a, EX.p, EX.m, True),
        WitnessStep(EX.b, EX.q, EX.m, False),
    )
    assert _replay_witness(g, subj, witness) == obj


def test_witness_no_match():
    g = Graph()
    g.add((EX.a, EX.r, EX.b))
    assert list(eval_path_witness(g, (EX.a, EX.p / ~EX.q, EX.b))) == []


_WITNESS_CONSISTENCY_DATA = """
@prefix : <http://example.org/> .

:a :p1 :c ; :p2 :f .
:c :p2 :e ; :p3 :g .
:g :p3 :h ; :p2 :j .
:h :p3 :a ; :p2 :g .

:q :px :q .
"""


@pytest.mark.parametrize(
    "path",
    [
        EX.p1,
        EX.p1 / EX.p2,
        EX.p1 | EX.p2,
        ~EX.p1,
        -EX.p1,
        -(EX.p1 | EX.p2),
        -~EX.p2,
        ~(EX.p1 / EX.p2),
        EX.p1 / EX.p3 / EX.p3,
        EX.p1 * ZeroOrOne,
        EX.p3 * ZeroOrMore,
        EX.p3 * OneOrMore,
        EX.px * OneOrMore,
        (EX.p2 | EX.p3) * ZeroOrMore,
        (EX.p1 / EX.p2) | (EX.p3 * OneOrMore),
    ],
)
@pytest.mark.parametrize(
    "bound",
    [
        (None, None),
        (EX.a, None),
        (EX.c, None),
        (EX.q, None),
        (None, EX.e),
        (None, EX.a),
        (EX.a, EX.e),
        (EX.c, EX.j),
    ],
)
def test_witness_consistent_with_eval_path(path, bound):
    """
    The witness query returns exactly the endpoint pairs of the classic
    endpoint query, and each witness replays to its endpoint.
    """
    g = Graph().parse(data=_WITNESS_CONSISTENCY_DATA, format="n3")
    subj, obj = bound
    expected = set(eval_path(g, (subj, path, obj)))
    actual = set()
    for s, o, witness in eval_path_witness(g, (subj, path, obj)):
        assert (s, o) not in actual
        actual.add((s, o))
        assert _replay_witness(g, s, witness) == o
    assert actual == expected


def test_witness_uses_callers_graph_view():
    """
    A path that would be connectable by joining two named graphs must not
    be reported when the caller evaluates against a single graph view.
    """
    ds = Dataset()
    g1 = ds.graph(EX.g1)
    g2 = ds.graph(EX.g2)
    g1.add((EX.a, EX.p, EX.b))
    g2.add((EX.b, EX.p, EX.c))

    path = EX.p / EX.p
    assert list(eval_path_witness(g1, (EX.a, path, None))) == []
    results = list(eval_path_witness(g2, (EX.b, path, None)))
    assert [(s, o) for s, o, _ in results] == []
    # sanity: within one graph the path does match when the edges are there
    g2.add((EX.c, EX.p, EX.d))
    results = list(eval_path_witness(g2, (EX.b, path, EX.d)))
    assert len(results) == 1
    assert _replay_witness(g2, EX.b, results[0][2]) == EX.d
