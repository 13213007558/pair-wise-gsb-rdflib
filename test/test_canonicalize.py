"""
Tests for RDFC-1.0 dataset canonicalization (:mod:`rdflib.canonicalize`)
and the ``rdfc`` serializer plugin.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

import pytest

from rdflib import BNode, Dataset, Graph, Literal, URIRef
from rdflib.canonicalize import (
    CanonicalizationBudgetExceededError,
    CanonicalizationError,
    canonicalize_dataset,
)
from rdflib.graph import DATASET_DEFAULT_GRAPH_ID
from rdflib.namespace import XSD

VECTOR_DIR = Path(__file__).parent / "data" / "rdfc10"
VECTOR_INPUTS = sorted(VECTOR_DIR.glob("test*-in.nq"))

EX = "http://example.org/"


def parse_vector(path: Path) -> Dataset:
    dataset = Dataset()
    dataset.parse(str(path), format="nquads")
    return dataset


def apply_mapping(dataset: Dataset, bnode_map: dict) -> list:
    """Serialize the input dataset using the issued canonical identifiers."""

    def map_term(term):
        if isinstance(term, BNode):
            return "_:" + bnode_map[term]
        if isinstance(term, Literal):
            from rdflib.plugins.serializers.nt import _quoteLiteral

            return _quoteLiteral(term)
        return term.n3()

    lines = []
    for s, p, o, context in dataset.quads():
        graph_name = context.identifier if isinstance(context, Graph) else context
        parts = [map_term(s), map_term(p), map_term(o)]
        if graph_name != DATASET_DEFAULT_GRAPH_ID:
            parts.append(map_term(graph_name))
        lines.append(" ".join(parts) + " .")
    return sorted(lines)


def renamed_copy(dataset: Dataset, rng: random.Random) -> tuple:
    """
    Return a copy of the dataset with all blank nodes renamed to fresh
    random labels and all quads re-inserted in random order, along with
    the rename mapping.
    """
    bnodes = set()
    for s, p, o, context in dataset.quads():
        for term in (s, p, o):
            if isinstance(term, BNode):
                bnodes.add(term)
        graph_name = context.identifier if isinstance(context, Graph) else context
        if isinstance(graph_name, BNode):
            bnodes.add(graph_name)
    rename = {bnode: BNode(rng.randbytes(16).hex()) for bnode in bnodes}

    def map_term(term):
        return rename.get(term, term)

    quads = []
    for s, p, o, context in dataset.quads():
        graph_name = context.identifier if isinstance(context, Graph) else context
        quads.append((map_term(s), map_term(p), map_term(o), map_term(graph_name)))
    rng.shuffle(quads)

    copy = Dataset()
    for s, p, o, graph_name in quads:
        if graph_name == DATASET_DEFAULT_GRAPH_ID:
            copy.add((s, p, o))
        else:
            copy.graph(graph_name).add((s, p, o))
    return copy, rename


def random_dataset(rng: random.Random) -> Dataset:
    """Generate a random dataset with blank nodes, also in graph names."""
    dataset = Dataset()
    bnodes = [BNode(f"b{index}") for index in range(rng.randint(2, 6))]
    graph_names = [URIRef(f"{EX}g{index}") for index in range(2)] + bnodes[:2]

    def term(allow_bnode=True):
        choice = rng.random()
        if allow_bnode and choice < 0.5:
            return rng.choice(bnodes)
        if choice < 0.8:
            return URIRef(f"{EX}n{rng.randint(0, 3)}")
        return Literal(f"v{rng.randint(0, 3)}")

    for _ in range(rng.randint(3, 15)):
        quad = (term(), URIRef(f"{EX}p{rng.randint(0, 2)}"), term())
        graph_name = rng.choice(graph_names + [None])
        if graph_name is None:
            dataset.add(quad)
        else:
            dataset.graph(graph_name).add(quad)
    return dataset


@pytest.mark.parametrize("vector_input", VECTOR_INPUTS, ids=lambda p: p.name)
def test_w3c_vectors(vector_input: Path):
    """Canonical output must match the recorded RDFC-1.0 vector byte for byte."""
    dataset = parse_vector(vector_input)
    result = canonicalize_dataset(dataset)
    expected = vector_input.with_name(
        vector_input.name.replace("-in.nq", "-rdfc10.nq")
    ).read_bytes()
    actual = result.nquads.encode("utf-8")
    assert actual == expected
    # The output must be directly hashable.
    assert hashlib.sha256(actual).hexdigest() == result.dataset_hash


@pytest.mark.parametrize("vector_input", VECTOR_INPUTS, ids=lambda p: p.name)
def test_mapping_reconstructs_output(vector_input: Path):
    """Applying the bnode mapping to the input yields the canonical output."""
    dataset = parse_vector(vector_input)
    result = canonicalize_dataset(dataset)
    assert apply_mapping(dataset, result.bnode_map) == result.nquads.splitlines()


@pytest.mark.parametrize("vector_input", VECTOR_INPUTS, ids=lambda p: p.name)
def test_rename_and_insertion_order_invariance(vector_input: Path):
    """Random blank node renames and insertion orders give identical bytes."""
    dataset = parse_vector(vector_input)
    result = canonicalize_dataset(dataset)
    rng = random.Random(1234)
    for _ in range(5):
        copy, rename = renamed_copy(dataset, rng)
        renamed_result = canonicalize_dataset(copy)
        assert renamed_result.nquads.encode("utf-8") == result.nquads.encode("utf-8")
        # The renamed mapping must reconstruct the same canonical lines.
        assert apply_mapping(copy, renamed_result.bnode_map) == (
            result.nquads.splitlines()
        )


#: Vectors whose blank nodes are all distinguishable, so the canonical
#: identifier of each original blank node is uniquely determined (not just
#: up to automorphism).
ASYMMETRIC_VECTORS = [
    VECTOR_DIR / f"test{number:03d}-in.nq" for number in (2, 4, 5, 7, 9, 12)
]


@pytest.mark.parametrize("vector_input", ASYMMETRIC_VECTORS, ids=lambda p: p.name)
def test_mapping_is_stable_under_renaming(vector_input: Path):
    """For asymmetric datasets the bnode mapping itself is canonical."""
    dataset = parse_vector(vector_input)
    result = canonicalize_dataset(dataset)
    rng = random.Random(99)
    for _ in range(5):
        copy, rename = renamed_copy(dataset, rng)
        renamed_result = canonicalize_dataset(copy)
        for original, renamed in rename.items():
            assert renamed_result.bnode_map[renamed] == result.bnode_map[original]


def test_random_datasets_rename_invariance():
    rng = random.Random(42)
    for _ in range(20):
        dataset = random_dataset(rng)
        result = canonicalize_dataset(dataset)
        copy, rename = renamed_copy(dataset, rng)
        renamed_result = canonicalize_dataset(copy)
        assert renamed_result.nquads == result.nquads
        assert apply_mapping(copy, renamed_result.bnode_map) == (
            result.nquads.splitlines()
        )


def test_canonical_identifiers_are_contiguous():
    rng = random.Random(7)
    dataset = random_dataset(rng)
    result = canonicalize_dataset(dataset)
    identifiers = sorted(result.bnode_map.values())
    assert identifiers == [f"c14n{index}" for index in range(len(identifiers))]


def test_idempotence():
    """Canonicalizing the canonical output again is a fixed point."""
    for vector_input in VECTOR_INPUTS:
        dataset = parse_vector(vector_input)
        result = canonicalize_dataset(dataset)
        again = Dataset()
        again.parse(data=result.nquads, format="nquads")
        assert canonicalize_dataset(again).nquads == result.nquads


def test_shared_vs_split_bnodes_across_graphs():
    """A bnode shared across graphs differs from one bnode per graph."""
    predicate = URIRef(EX + "p")
    shared = Dataset()
    shared.graph(URIRef(EX + "g1")).add((BNode("x"), predicate, Literal("v")))
    shared.graph(URIRef(EX + "g2")).add((BNode("x"), predicate, Literal("v")))
    split = Dataset()
    split.graph(URIRef(EX + "g1")).add((BNode("x"), predicate, Literal("v")))
    split.graph(URIRef(EX + "g2")).add((BNode("y"), predicate, Literal("v")))
    shared_result = canonicalize_dataset(shared)
    split_result = canonicalize_dataset(split)
    assert shared_result.nquads != split_result.nquads
    # The shared node gets a single canonical identifier in both graphs.
    lines = shared_result.nquads.splitlines()
    assert len(lines) == 2
    subjects = {line.split(" ")[0] for line in lines}
    assert len(subjects) == 1


def test_graph_name_change_affects_result():
    predicate = URIRef(EX + "p")
    first = Dataset()
    first.graph(URIRef(EX + "g1")).add((BNode("x"), predicate, Literal("v")))
    second = Dataset()
    second.graph(URIRef(EX + "g2")).add((BNode("x"), predicate, Literal("v")))
    assert canonicalize_dataset(first).nquads != canonicalize_dataset(second).nquads


def test_literal_lexical_form_affects_result():
    """Distinct lexical forms are distinct terms in canonicalization."""
    subject, predicate = URIRef(EX + "s"), URIRef(EX + "p")
    first = Dataset()
    first.add((subject, predicate, Literal("1", datatype=XSD.integer)))
    second = Dataset()
    second.add(
        (subject, predicate, Literal("01", datatype=XSD.integer, normalize=False))
    )
    first_result = canonicalize_dataset(first)
    second_result = canonicalize_dataset(second)
    assert first_result.nquads != second_result.nquads
    assert '"1"^^' in first_result.nquads
    assert '"01"^^' in second_result.nquads


def test_input_dataset_is_not_modified():
    dataset = parse_vector(VECTOR_DIR / "test009-in.nq")
    before = sorted(
        (s.n3(), p.n3(), o.n3(), (c.identifier if isinstance(c, Graph) else c).n3())
        for s, p, o, c in dataset.quads()
    )
    labels_before = {
        term for quad in dataset.quads() for term in quad[:3] if isinstance(term, BNode)
    }
    canonicalize_dataset(dataset)
    after = sorted(
        (s.n3(), p.n3(), o.n3(), (c.identifier if isinstance(c, Graph) else c).n3())
        for s, p, o, c in dataset.quads()
    )
    labels_after = {
        term for quad in dataset.quads() for term in quad[:3] if isinstance(term, BNode)
    }
    assert before == after
    assert labels_before == labels_after
    assert not any(str(label).startswith("c14n") for label in labels_after)


def symmetric_dataset(size: int) -> Dataset:
    """Fully interconnected blank nodes: worst case for n-degree hashing."""
    dataset = Dataset()
    predicate = URIRef(EX + "p")
    bnodes = [BNode(f"b{index}") for index in range(size)]
    for subject in bnodes:
        for object_ in bnodes:
            if subject is not object_:
                dataset.add((subject, predicate, object_))
    return dataset


def test_budget_exceeded_raises_and_returns_nothing():
    dataset = symmetric_dataset(6)
    with pytest.raises(CanonicalizationBudgetExceededError):
        canonicalize_dataset(dataset, max_work=10)
    # The exception is a well-defined canonicalization error.
    with pytest.raises(CanonicalizationError):
        canonicalize_dataset(dataset, max_work=10)


def test_budget_sufficient_is_deterministic():
    dataset = symmetric_dataset(5)
    first = canonicalize_dataset(dataset, max_work=None)
    second = canonicalize_dataset(dataset, max_work=10**9)
    assert first.nquads == second.nquads


def test_utf8_output_is_hashable():
    dataset = Dataset()
    dataset.add(
        (
            BNode("u"),
            URIRef(EX + "p"),
            Literal("héllo ⺟\n世界", lang=None),
        )
    )
    result = canonicalize_dataset(dataset)
    encoded = result.nquads.encode("utf-8")
    assert encoded.decode("utf-8") == result.nquads
    assert hashlib.sha256(encoded).hexdigest() == result.dataset_hash
    again = canonicalize_dataset(dataset)
    assert hashlib.sha256(again.nquads.encode("utf-8")).hexdigest() == (
        result.dataset_hash
    )


def test_empty_dataset():
    result = canonicalize_dataset(Dataset())
    assert result.nquads == ""
    assert result.bnode_map == {}


def test_default_graph_distinct_from_named_graph():
    triple = (URIRef(EX + "s"), URIRef(EX + "p"), URIRef(EX + "o"))
    in_default = Dataset()
    in_default.add(triple)
    in_named = Dataset()
    in_named.graph(URIRef(EX + "g")).add(triple)
    assert (
        canonicalize_dataset(in_default).nquads != canonicalize_dataset(in_named).nquads
    )


def test_serializer_plugin_roundtrip(tmp_path):
    dataset = parse_vector(VECTOR_DIR / "test009-in.nq")
    expected = canonicalize_dataset(dataset)
    as_string = dataset.serialize(format="rdfc")
    assert as_string == expected.nquads

    destination = tmp_path / "out.nq"
    dataset.serialize(destination=str(destination), format="rdfc")
    assert destination.read_bytes() == expected.nquads.encode("utf-8")


def test_serializer_exposes_mapping():
    from rdflib.plugin import Serializer
    from rdflib.plugin import get as plugin_get

    dataset = parse_vector(VECTOR_DIR / "test003-in.nq")
    serializer_class = plugin_get("rdfc", Serializer)
    serializer = serializer_class(dataset)
    import io

    stream = io.BytesIO()
    serializer.serialize(stream)
    result = canonicalize_dataset(dataset)
    assert stream.getvalue() == result.nquads.encode("utf-8")
    assert serializer.bnode_map == result.bnode_map
    assert serializer.dataset_hash == result.dataset_hash


def test_serializer_passes_budget():
    dataset = symmetric_dataset(6)
    with pytest.raises(CanonicalizationBudgetExceededError):
        dataset.serialize(format="rdfc", max_work=10)


def test_serializer_requires_context_aware_store():
    from rdflib.plugin import Serializer
    from rdflib.plugin import get as plugin_get

    serializer_class = plugin_get("rdfc", Serializer)
    with pytest.raises(Exception):
        serializer_class(Graph())
