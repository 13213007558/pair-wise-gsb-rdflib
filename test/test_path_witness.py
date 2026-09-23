from __future__ import annotations

from rdflib import Dataset, Graph, Namespace
from rdflib.paths import (
    OneOrMore,
    WitnessStep,
    ZeroOrMore,
    ZeroOrOne,
    eval_path,
    eval_path_witness,
)

EX = Namespace("http://example.org/")


def replay(graph: Graph, subj, obj, witness) -> None:
    """Replay a witness against the graph, checking every step."""
    node = subj
    for step in witness:
        assert (step.subject, step.predicate, step.object) in graph
        if step.forward:
            assert step.subject == node
            node = step.object
        else:
            assert step.object == node
            node = step.subject
    assert node == obj


def test_witness_follows_expression_not_shortest_graph_path():
    """
    The graph-shortest path from a to b is a single r edge, but it does not
    match p/^q; the witness must use the longer legal path, which crosses
    one edge in reverse.
    """
    g = Graph()
    g.add((EX.a, EX.r, EX.b))  # decoy: shortest in the graph, not matching
    g.add((EX.a, EX.p, EX.x))
    g.add((EX.b, EX.q, EX.x))  # traversed backwards by ^q

    results = list(eval_path_witness(g, (EX.a, EX.p / ~EX.q, EX.b)))

    assert len(results) == 1
    subj, obj, witness = results[0]
    assert (subj, obj) == (EX.a, EX.b)
    assert witness == (
        WitnessStep(EX.a, EX.p, EX.x, True),
        WitnessStep(EX.b, EX.q, EX.x, False),
    )
    replay(g, subj, obj, witness)


def test_witness_with_object_bound_matches_forward_query():
    g = Graph()
    g.add((EX.a, EX.r, EX.b))
    g.add((EX.a, EX.p, EX.x))
    g.add((EX.b, EX.q, EX.x))

    forward = list(eval_path_witness(g, (EX.a, EX.p / ~EX.q, EX.b)))
    backward = list(eval_path_witness(g, (None, EX.p / ~EX.q, EX.b)))

    assert backward == forward
    replay(g, *backward[0])


def test_zero_length_match_has_empty_witness():
    g = Graph()
    g.add((EX.a, EX.p, EX.b))

    results = dict(
        (obj, witness)
        for _, obj, witness in eval_path_witness(g, (EX.a, EX.p * ZeroOrOne, None))
    )

    assert set(results) == {EX.a, EX.b}
    assert results[EX.a] == ()
    assert results[EX.b] == (WitnessStep(EX.a, EX.p, EX.b, True),)


def test_self_loop_dedup_and_termination():
    """
    A self loop makes p* loop back to its start; the endpoint must still be
    reported once, with the empty (zero-length) witness, and the search must
    terminate instead of enumerating ever longer walks.
    """
    g = Graph()
    g.add((EX.n, EX.p, EX.n))
    g.add((EX.n, EX.p, EX.m))
    g.add((EX.m, EX.p, EX.n))

    results = list(eval_path_witness(g, (EX.n, EX.p * ZeroOrMore, None)))

    endpoints = [obj for _, obj, _ in results]
    assert sorted(endpoints) == sorted({EX.n, EX.m})  # no duplicates
    by_end = {obj: witness for _, obj, witness in results}
    assert by_end[EX.n] == ()  # zero-length beats the loop
    assert by_end[EX.m] == (WitnessStep(EX.n, EX.p, EX.m, True),)
    for subj, obj, witness in results:
        replay(g, subj, obj, witness)

    # one-or-more: n is still reachable, now via the loop, and it terminates
    plus = list(eval_path_witness(g, (EX.n, EX.p * OneOrMore, None)))
    plus_by_end = {obj: witness for _, obj, witness in plus}
    assert set(plus_by_end) == {EX.n, EX.m}
    assert plus_by_end[EX.n] == (WitnessStep(EX.n, EX.p, EX.n, True),)
    for subj, obj, witness in plus:
        replay(g, subj, obj, witness)


def test_alternative_branches_yield_endpoint_once():
    g = Graph()
    g.add((EX.a, EX.p, EX.b))
    g.add((EX.a, EX.q, EX.b))

    results = list(eval_path_witness(g, (EX.a, EX.p | EX.q, None)))

    assert len(results) == 1
    subj, obj, witness = results[0]
    assert (subj, obj) == (EX.a, EX.b)
    assert len(witness) == 1  # either branch, both are shortest
    replay(g, subj, obj, witness)


def test_negated_path_witness():
    g = Graph()
    g.add((EX.a, EX.p, EX.b))
    g.add((EX.a, EX.q, EX.c))

    results = list(eval_path_witness(g, (EX.a, -EX.p, None)))

    assert results == [(EX.a, EX.c, (WitnessStep(EX.a, EX.q, EX.c, True),))]


def test_witness_endpoints_match_eval_path():
    g = Graph()
    g.add((EX.a, EX.p, EX.x))
    g.add((EX.x, EX.p, EX.y))
    g.add((EX.y, EX.q, EX.a))
    g.add((EX.b, EX.q, EX.x))
    path = (EX.p | ~EX.q) * OneOrMore

    for subj, obj in [(EX.a, None), (None, EX.y), (None, None)]:
        expected = set(eval_path(g, (subj, path, obj)))
        results = list(eval_path_witness(g, (subj, path, obj)))
        assert {(s, o) for s, o, _ in results} == expected
        for s, o, witness in results:
            replay(g, s, o, witness)


def test_dataset_witnesses_stay_in_selected_graph():
    dataset = Dataset()
    g1 = dataset.graph(EX.g1)
    g2 = dataset.graph(EX.g2)
    g1.add((EX.a, EX.p, EX.x))
    g2.add((EX.x, EX.q, EX.b))

    # the path cannot be completed within a single named graph
    assert list(eval_path_witness(g1, (EX.a, EX.p / EX.q, None))) == []
    assert list(eval_path_witness(g2, (EX.a, EX.p / EX.q, None))) == []

    # but the part that is visible in the selected graph is found
    assert list(eval_path_witness(g1, (EX.a, EX.p, None))) == [
        (EX.a, EX.x, (WitnessStep(EX.a, EX.p, EX.x, True),))
    ]
