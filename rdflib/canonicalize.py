"""
RDF Dataset Canonicalization (RDFC-1.0) for RDFLib.

This module implements the W3C "RDF Dataset Canonicalization 1.0"
algorithm (RDFC-1.0, the successor of URDNA2015), see
https://www.w3.org/TR/rdf-canon/ .  It operates on whole datasets: blank
nodes in named graph names participate in the algorithm together with
blank nodes in subject and object position, so shared blank nodes,
symmetric structures, blank node cycles and cross-graph references are
all canonicalized consistently.

The main entry point is :func:`canonicalize_dataset`, which returns the
canonical N-Quads representation of a dataset along with the mapping from
the original blank nodes to the issued canonical identifiers.  The input
dataset is only read, never modified.

A serializer plugin (``format="rdfc"``) built on top of this module is
available in :mod:`rdflib.plugins.serializers.rdfc`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import permutations
from typing import Any, Callable, Dict, List, Optional, Tuple

from rdflib.graph import DATASET_DEFAULT_GRAPH_ID, ConjunctiveGraph, Graph
from rdflib.plugins.serializers.nt import _quoteLiteral
from rdflib.term import BNode, Literal, Node

__all__ = [
    "CanonicalizationError",
    "CanonicalizationBudgetExceededError",
    "CanonicalizationResult",
    "canonicalize_dataset",
]

#: Default upper bound on the number of blank node permutations the
#: n-degree hashing may explore before :class:`CanonicalizationBudgetExceededError`
#: is raised.  ``None`` disables the limit.
DEFAULT_MAX_WORK: Optional[int] = 1_000_000

#: Position markers used by the "Hash Related Blank Node" algorithm.
_POSITIONS = {0: "s", 2: "o", 3: "g"}


class CanonicalizationError(RuntimeError):
    """Base class for errors raised during dataset canonicalization."""


class CanonicalizationBudgetExceededError(CanonicalizationError):
    """
    Raised when the configured work budget is exhausted while
    canonicalizing a dataset.

    No (partial) canonical result is produced in this case; the caller
    either has to raise the budget (``max_work``) or accept that the
    dataset is too symmetric to canonicalize within the limit.
    """


@dataclass
class CanonicalizationResult:
    """
    Result of :func:`canonicalize_dataset`.

    .. attribute:: nquads

        The canonical N-Quads representation of the dataset as a single
        string.  Lines are sorted by Unicode code point and terminated
        with a newline; encode as UTF-8 to obtain the canonical byte
        stream.

    .. attribute:: bnode_map

        Mapping from each original :class:`~rdflib.term.BNode` of the
        input dataset to its canonical identifier (e.g. "c14n0").

    .. attribute:: dataset_hash

        The hex-encoded SHA-256 digest of the UTF-8 encoded canonical
        N-Quads, as defined by the RDFC-1.0 "hash dataset" result.
    """

    nquads: str
    bnode_map: Dict[BNode, str]
    dataset_hash: str


def _term_n3(term: Node) -> str:
    """Serialize a term the same way the N-Quads serializer does."""
    if isinstance(term, Literal):
        return _quoteLiteral(term)
    return term.n3()


class _IdentifierIssuer:
    """Issues "_:prefix<counter>" identifiers, remembering the order."""

    __slots__ = ("prefix", "counter", "issued")

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.counter = 0
        # Insertion ordered: iteration order is the issuance order.
        self.issued: Dict[BNode, str] = {}

    def issue(self, bnode: BNode) -> str:
        identifier = self.issued.get(bnode)
        if identifier is None:
            identifier = f"{self.prefix}{self.counter}"
            self.counter += 1
            self.issued[bnode] = identifier
        return identifier

    def get(self, bnode: BNode) -> Optional[str]:
        return self.issued.get(bnode)

    def copy(self) -> _IdentifierIssuer:
        clone = _IdentifierIssuer(self.prefix)
        clone.counter = self.counter
        clone.issued = dict(self.issued)
        return clone


# A quad slot is either (False, serialized term) for a grounded term or
# (True, BNode) for a blank node.  None marks the absent graph name of a
# default-graph quad.
_Slot = Optional[Tuple[bool, Any]]
_Quad = Tuple[_Slot, _Slot, _Slot, _Slot]


class _RDFC10Canonicalizer:
    """
    Implementation of the RDFC-1.0 canonicalization algorithm.

    The algorithm never makes a choice based on the traversal order of the
    input: every tie is broken by comparing hashes or serialized paths, so
    the issued canonical identifiers do not depend on blank node labels or
    insertion order of the input dataset.
    """

    def __init__(
        self,
        quads: List[_Quad],
        hashfunc: Callable[[bytes], Any],
        max_work: Optional[int],
    ):
        self.quads = quads
        self.hashfunc = hashfunc
        self.max_work = max_work
        self.work_done = 0
        self.canonical_issuer = _IdentifierIssuer("_:c14n")
        # bnode -> indices of the quads it occurs in
        self.bnode_quads: Dict[BNode, List[int]] = {}
        for index, quad in enumerate(quads):
            for slot in quad:
                if slot is not None and slot[0]:
                    self.bnode_quads.setdefault(slot[1], []).append(index)
        self._first_degree_cache: Dict[BNode, str] = {}

    def _hash(self, data: str) -> str:
        return self.hashfunc(data.encode("utf-8")).hexdigest()

    def _consume_work(self) -> None:
        self.work_done += 1
        if self.max_work is not None and self.work_done > self.max_work:
            raise CanonicalizationBudgetExceededError(
                f"canonicalization work budget of {self.max_work} permutation(s) "
                "exhausted; the dataset is too symmetric to canonicalize "
                "within the configured limit"
            )

    def _serialize_quad(self, quad: _Quad, bnode_label: Callable[[BNode], str]) -> str:
        parts = []
        for slot in quad:
            if slot is None:
                continue
            if slot[0]:
                parts.append(bnode_label(slot[1]))
            else:
                parts.append(slot[1])
        return " ".join(parts) + " .\n"

    def hash_first_degree_quads(self, reference: BNode) -> str:
        """RDFC-1.0 "Hash First Degree Quads" for a blank node."""
        cached = self._first_degree_cache.get(reference)
        if cached is not None:
            return cached
        lines = []
        for index in self.bnode_quads[reference]:
            lines.append(
                self._serialize_quad(
                    self.quads[index],
                    lambda bnode: "_:a" if bnode == reference else "_:z",
                )
            )
        lines.sort()
        result = self._hash("".join(lines))
        self._first_degree_cache[reference] = result
        return result

    def hash_related_blank_node(
        self, related: BNode, quad_index: int, issuer: _IdentifierIssuer, position: str
    ) -> str:
        """RDFC-1.0 "Hash Related Blank Node"."""
        identifier = self.canonical_issuer.get(related)
        if identifier is None:
            identifier = issuer.get(related)
        if identifier is None:
            identifier = self.hash_first_degree_quads(related)
        input_ = position
        if position != "g":
            predicate = self.quads[quad_index][1]
            assert predicate is not None and not predicate[0]
            input_ += predicate[1]
        input_ += identifier
        return self._hash(input_)

    def hash_n_degree_quads(
        self, reference: BNode, issuer: _IdentifierIssuer
    ) -> Tuple[_IdentifierIssuer, str]:
        """RDFC-1.0 "Hash N-Degree Quads" (recursive)."""
        hash_to_related: Dict[str, List[BNode]] = {}
        for index in self.bnode_quads[reference]:
            quad = self.quads[index]
            for slot_index, position in _POSITIONS.items():
                slot = quad[slot_index]
                if slot is None or not slot[0]:
                    continue
                related = slot[1]
                if related == reference:
                    continue
                related_hash = self.hash_related_blank_node(
                    related, index, issuer, position
                )
                hash_to_related.setdefault(related_hash, []).append(related)

        data_to_hash = ""
        for related_hash in sorted(hash_to_related):
            data_to_hash += related_hash
            chosen_path: Optional[str] = None
            chosen_issuer: Optional[_IdentifierIssuer] = None
            for permutation in permutations(hash_to_related[related_hash]):
                self._consume_work()
                issuer_copy = issuer.copy()
                path = ""
                recursion_list: List[BNode] = []
                skip = False
                for related in permutation:
                    canonical_identifier = self.canonical_issuer.get(related)
                    if canonical_identifier is not None:
                        path += canonical_identifier
                    else:
                        if issuer_copy.get(related) is None:
                            recursion_list.append(related)
                        path += issuer_copy.issue(related)
                    if (
                        chosen_path is not None
                        and len(path) >= len(chosen_path)
                        and path > chosen_path
                    ):
                        skip = True
                        break
                if skip:
                    continue
                for related in recursion_list:
                    result_issuer, result_hash = self.hash_n_degree_quads(
                        related, issuer_copy
                    )
                    path += issuer_copy.issue(related)
                    path += "<" + result_hash + ">"
                    issuer_copy = result_issuer
                if chosen_path is None or path < chosen_path:
                    chosen_path = path
                    chosen_issuer = issuer_copy
            assert chosen_path is not None and chosen_issuer is not None
            data_to_hash += chosen_path
            issuer = chosen_issuer
        return issuer, self._hash(data_to_hash)

    def canonicalize(self) -> Tuple[str, Dict[BNode, str]]:
        """RDFC-1.0 "Canonicalize Dataset" main algorithm."""
        hash_to_bnodes: Dict[str, List[BNode]] = {}
        for bnode in self.bnode_quads:
            first_degree = self.hash_first_degree_quads(bnode)
            hash_to_bnodes.setdefault(first_degree, []).append(bnode)

        for first_degree in sorted(hash_to_bnodes):
            bnodes = hash_to_bnodes[first_degree]
            if len(bnodes) == 1:
                self.canonical_issuer.issue(bnodes[0])
                continue
            hash_path_list: List[Tuple[str, _IdentifierIssuer]] = []
            for bnode in bnodes:
                if self.canonical_issuer.get(bnode) is not None:
                    continue
                temporary_issuer = _IdentifierIssuer("_:b")
                temporary_issuer.issue(bnode)
                result_issuer, result_hash = self.hash_n_degree_quads(
                    bnode, temporary_issuer
                )
                hash_path_list.append((result_hash, result_issuer))
            hash_path_list.sort(key=lambda result: result[0])
            for _, result_issuer in hash_path_list:
                for bnode in result_issuer.issued:
                    self.canonical_issuer.issue(bnode)

        issued = self.canonical_issuer.issued
        lines = [
            self._serialize_quad(quad, lambda bnode: issued[bnode])
            for quad in self.quads
        ]
        lines.sort()
        nquads = "".join(lines)
        bnode_map = {bnode: identifier[2:] for bnode, identifier in issued.items()}
        return nquads, bnode_map


def _dataset_quads(dataset: Graph) -> List[_Quad]:
    """
    Read a dataset (or plain graph) into deduplicated quad slots.

    Deduplication is term based (on the serialized form), so literals that
    are merely equal in value but different in lexical form, such as
    "1"^^xsd:integer and "01"^^xsd:integer, are kept apart.  The input
    graph is only read, never modified.
    """

    def slot(term: Node) -> Tuple[bool, Any]:
        if isinstance(term, BNode):
            return (True, term)
        return (False, _term_n3(term))

    quads: List[_Quad] = []
    seen = set()
    if isinstance(dataset, ConjunctiveGraph):
        quad_source = (
            (s, p, o, context.identifier if isinstance(context, Graph) else context)
            for s, p, o, context in dataset.quads()
        )
    else:
        quad_source = ((s, p, o, None) for s, p, o in dataset)
    for s, p, o, graph_name in quad_source:
        if graph_name == DATASET_DEFAULT_GRAPH_ID:
            graph_name = None
        quad: _Quad = (
            slot(s),
            slot(p),
            slot(o),
            slot(graph_name) if graph_name is not None else None,
        )
        # Term-level identity key; blank nodes compare by label, grounded
        # terms by their serialized form.
        key = tuple(
            "" if part is None else ("_:" + str(part[1])) if part[0] else part[1]
            for part in quad
        )
        if key in seen:
            continue
        seen.add(key)
        quads.append(quad)
    return quads


def canonicalize_dataset(
    dataset: Graph,
    max_work: Optional[int] = DEFAULT_MAX_WORK,
    hash_name: str = "sha256",
) -> CanonicalizationResult:
    """
    Canonicalize a dataset following the W3C RDFC-1.0 algorithm.

    Args:
        dataset: The dataset to canonicalize.  Usually a
            :class:`~rdflib.graph.Dataset` or
            :class:`~rdflib.graph.ConjunctiveGraph`; a plain
            :class:`~rdflib.graph.Graph` is treated as a dataset
            consisting of a single default graph.  The input is only read,
            never modified.
        max_work: Upper bound on the number of blank node permutations the
            algorithm may explore.  When the budget is exhausted,
            :class:`CanonicalizationBudgetExceededError` is raised and no
            (partial) result is returned.  ``None`` disables the limit.
        hash_name: Name of the hash algorithm to use; RDFC-1.0 mandates
            "sha256".

    Returns:
        A :class:`CanonicalizationResult` with the canonical N-Quads
        string, the mapping from original blank nodes to canonical
        identifiers, and the SHA-256 dataset hash.
    """

    def hashfunc(data: bytes) -> Any:
        return hashlib.new(hash_name, data)

    quads = _dataset_quads(dataset)
    canonicalizer = _RDFC10Canonicalizer(quads, hashfunc, max_work)
    nquads, bnode_map = canonicalizer.canonicalize()
    dataset_hash = hashlib.new(hash_name, nquads.encode("utf-8")).hexdigest()
    return CanonicalizationResult(
        nquads=nquads, bnode_map=bnode_map, dataset_hash=dataset_hash
    )
