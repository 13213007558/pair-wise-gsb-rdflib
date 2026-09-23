"""
Incrementally maintained RDFS closure view.

The view wraps an ordinary rdflib Graph that holds the *explicit* (base)
triples and keeps a separate, incrementally maintained set of *derived*
triples.  The underlying graph is never mutated with derived triples.

The first version implements a fixed, explicitly listed subset of the RDFS
rules:

* rdfs:subClassOf transitivity;
* rdfs:subPropertyOf transitivity;
* instance propagation along rdfs:subClassOf;
* rdfs:domain and rdfs:range inference;
* property instance propagation along rdfs:subPropertyOf.

Each conclusion keeps the set of its immediate proofs (rule instantiations).
A conclusion therefore survives the loss of any single proof as long as
another proof remains.  Deletions use the delete/re-derive (DRed) strategy,
restricted to the cone of facts that depend on the removed fact, so cyclic
inheritance can never keep a conclusion alive after all of its well-founded
support has disappeared, and unrelated parts of the graph are not
recomputed.

All mutation must go through the view (add / remove / update); direct edits
of the wrapped graph are not observed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional, Tuple

from rdflib.graph import Graph
from rdflib.namespace import RDF, RDFS
from rdflib.term import Node

Triple = Tuple[Node, Node, Node]
TriplePattern = Tuple[Optional[Node], Optional[Node], Optional[Node]]


@dataclass(frozen=True)
class _Derivation:
    """One immediate proof of conclusion via a named rule."""

    rule: str
    conclusion: Triple
    antecedents: Tuple[Triple, ...]


class IncrementalRDFSView:
    """Graph-like read view over a base graph plus its RDFS closure.

    :param graph: the graph holding the explicit triples.  It is used both
        as the source of truth for explicit facts and as the target of
        explicit add / remove calls.  Derived triples are never written to
        it.

    Maintenance statistics are exposed on access_counts (a counter of how
    many maintenance steps observed each fact) and stats (aggregate
    counters).  They make it possible to verify that an update only
    recomputed the affected part of the graph.
    """

    def __init__(self, graph: Graph) -> None:
        self.graph = graph

        # Conclusion -> its currently valid immediate proofs.  A fact is
        # believed iff it is explicit or it has at least one derivation.
        self._derivations: dict[Triple, set[_Derivation]] = {}
        # Reverse index: fact -> proofs consuming it as an antecedent.
        self._consumers: dict[Triple, set[_Derivation]] = {}
        # All facts (explicit or derived) currently considered true.
        self._believed: set[Triple] = set()

        # Indexes over the believed facts (explicit and derived).
        self._sc_sub: dict[Node, set[Node]] = {}
        self._sc_sup: dict[Node, set[Node]] = {}
        self._sp_sub: dict[Node, set[Node]] = {}
        self._sp_sup: dict[Node, set[Node]] = {}
        self._types: dict[Node, set[Node]] = {}
        self._domains: dict[Node, set[Node]] = {}
        self._ranges: dict[Node, set[Node]] = {}
        self._preds: dict[Node, set[Tuple[Node, Node]]] = {}

        self.stats: Counter[str] = Counter()
        # Number of maintenance steps that observed each fact.  Facts in a
        # disconnected region stay at zero while another region changes.
        self.access_counts: Counter[Triple] = Counter()

        # Index the pre-existing explicit facts first, then fire all rules:
        # an explicit fact may itself be a conclusion reached while seeding
        # from another explicit fact, so ordering of single triples cannot
        # be relied on.
        explicit = set(graph)
        self._pending = [
            triple for triple in explicit if self._believe(triple)
        ]
        while self._pending:
            self._fire(self._pending.pop())
        del self._pending

    # ------------------------------------------------------------------
    # Graph-like read API
    # ------------------------------------------------------------------

    def __contains__(self, triple: object) -> bool:
        return triple in self.graph or triple in self._derivations

    def __iter__(self) -> Iterator[Triple]:
        return iter(self._materialized())

    def __len__(self) -> int:
        return len(self._materialized())

    def triples(self, pattern: TriplePattern) -> Iterator[Triple]:
        subject, predicate, obj = pattern
        for triple in self._materialized():
            if (
                (subject is None or triple[0] == subject)
                and (predicate is None or triple[1] == predicate)
                and (obj is None or triple[2] == obj)
            ):
                yield triple

    def _materialized(self) -> set[Triple]:
        return set(self.graph) | set(self._derivations)

    def explicit_triples(self) -> Iterator[Triple]:
        """Iterate over the triples present in the wrapped graph."""
        return iter(self.graph)

    def derived_triples(self) -> Iterator[Triple]:
        """Iterate over triples that currently have at least one proof."""
        return iter(self._derivations)

    def is_explicit(self, triple: Triple) -> bool:
        return triple in self.graph

    def is_derived(self, triple: Triple) -> bool:
        return triple in self._derivations

    def proofs(self, triple: Triple) -> int:
        """Number of currently valid immediate proofs of triple."""
        return len(self._derivations.get(triple, ()))

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, triple: Triple) -> None:
        """Assert triple as an explicit fact.

        Re-adding an already explicit triple has no effect.
        """
        if triple in self.graph:
            return
        self.stats["adds"] += 1
        self.graph.add(triple)
        if triple in self._derivations:
            # Already believed via derivation: no new rule instantiation
            # can appear; the fact simply gains explicit support.
            return
        self._forward(triple)

    def remove(self, triple: Triple) -> None:
        """Retract triple from the explicit facts.

        Removing a triple that is not explicit (including a purely derived
        one) is a no-op.
        """
        if triple not in self.graph:
            return
        self.stats["removes"] += 1
        self.graph.remove(triple)
        # The fact may still carry proofs, but those proofs must be
        # re-validated: a cyclic inheritance chain can otherwise keep a
        # fact alive through a proof that ultimately rests on itself.
        self._delete_rederive(triple)

    def update(
        self,
        adds: Iterable[Triple] = (),
        removes: Iterable[Triple] = (),
    ) -> None:
        """Apply a batch of explicit additions and/or retractions."""
        for triple in adds:
            self.add(triple)
        for triple in removes:
            self.remove(triple)

    # ------------------------------------------------------------------
    # Index maintenance over the believed facts
    # ------------------------------------------------------------------

    def _touch(self, triple: Triple) -> None:
        self.access_counts[triple] += 1

    @staticmethod
    def _index_add(index: dict[Node, set[Node]], key: Node, value: Node) -> None:
        index.setdefault(key, set()).add(value)

    @staticmethod
    def _index_discard(
        index: dict[Node, set[Node]], key: Node, value: Node
    ) -> None:
        values = index.get(key)
        if values is not None:
            values.discard(value)
            if not values:
                del index[key]

    def _believe(self, triple: Triple) -> None:
        """Register a fact as believed in the lookup indexes.

        Returns True if the fact was not believed already.
        """
        if triple in self._believed:
            return False
        self._believed.add(triple)
        self._touch(triple)
        subject, predicate, obj = triple
        if predicate == RDFS.subClassOf:
            self._index_add(self._sc_sub, subject, obj)
            self._index_add(self._sc_sup, obj, subject)
        elif predicate == RDFS.subPropertyOf:
            self._index_add(self._sp_sub, subject, obj)
            self._index_add(self._sp_sup, obj, subject)
        elif predicate == RDF.type:
            self._index_add(self._types, obj, subject)
        elif predicate == RDFS.domain:
            self._index_add(self._domains, subject, obj)
        elif predicate == RDFS.range:
            self._index_add(self._ranges, subject, obj)
        self._index_add(self._preds, predicate, (subject, obj))
        return True

    def _unbelieve(self, triple: Triple) -> None:
        """Remove a fact from the lookup indexes."""
        self._believed.discard(triple)
        self._touch(triple)
        subject, predicate, obj = triple
        if predicate == RDFS.subClassOf:
            self._index_discard(self._sc_sub, subject, obj)
            self._index_discard(self._sc_sup, obj, subject)
        elif predicate == RDFS.subPropertyOf:
            self._index_discard(self._sp_sub, subject, obj)
            self._index_discard(self._sp_sup, obj, subject)
        elif predicate == RDF.type:
            self._index_discard(self._types, obj, subject)
        elif predicate == RDFS.domain:
            self._index_discard(self._domains, subject, obj)
        elif predicate == RDFS.range:
            self._index_discard(self._ranges, subject, obj)
        self._index_discard(self._preds, predicate, (subject, obj))

    # ------------------------------------------------------------------
    # Insertion: semi-naive forward chaining
    # ------------------------------------------------------------------

    def _record(self, derivation: _Derivation) -> None:
        conclusion = derivation.conclusion
        proofs = self._derivations.setdefault(conclusion, set())
        if derivation in proofs:
            return
        proofs.add(derivation)
        self.stats["derivations_added"] += 1
        for antecedent in derivation.antecedents:
            self._consumers.setdefault(antecedent, set()).add(derivation)
        if len(proofs) == 1 and conclusion not in self._believed:
            # The conclusion only just became believed.
            self._believe(conclusion)
            self._pending.append(conclusion)

    def _forward(self, seed: Triple) -> None:
        self._pending: list[Triple] = [seed] if self._believe(seed) else []
        while self._pending:
            triple = self._pending.pop()
            self._fire(triple)
        del self._pending

    def _fire(self, newly_believed: Triple) -> None:
        """Generate every proof whose last-believed antecedent may be
        newly_believed."""
        subject, predicate, obj = newly_believed

        if predicate == RDFS.subClassOf:
            # (subject sc obj) is new
            for super_class in self._sc_sub.get(obj, ()):
                self._record(
                    _Derivation(
                        "subClassOf-transitive",
                        (subject, RDFS.subClassOf, super_class),
                        (
                            newly_believed,
                            (obj, RDFS.subClassOf, super_class),
                        ),
                    )
                )
            for sub_class in self._sc_sup.get(subject, ()):
                self._record(
                    _Derivation(
                        "subClassOf-transitive",
                        (sub_class, RDFS.subClassOf, obj),
                        (
                            (sub_class, RDFS.subClassOf, subject),
                            newly_believed,
                        ),
                    )
                )
            for instance in self._types.get(subject, ()):
                self._record(
                    _Derivation(
                        "subClassOf-type",
                        (instance, RDF.type, obj),
                        (
                            (instance, RDF.type, subject),
                            newly_believed,
                        ),
                    )
                )

        if predicate == RDFS.subPropertyOf:
            # (subject sp obj) is new
            for super_property in self._sp_sub.get(obj, ()):
                self._record(
                    _Derivation(
                        "subPropertyOf-transitive",
                        (subject, RDFS.subPropertyOf, super_property),
                        (
                            newly_believed,
                            (obj, RDFS.subPropertyOf, super_property),
                        ),
                    )
                )
            for sub_property in self._sp_sup.get(subject, ()):
                self._record(
                    _Derivation(
                        "subPropertyOf-transitive",
                        (sub_property, RDFS.subPropertyOf, obj),
                        (
                            (sub_property, RDFS.subPropertyOf, subject),
                            newly_believed,
                        ),
                    )
                )
            for prop_subject, prop_object in self._preds.get(subject, ()):
                self._record(
                    _Derivation(
                        "subPropertyOf-instance",
                        (prop_subject, obj, prop_object),
                        (
                            newly_believed,
                            (prop_subject, subject, prop_object),
                        ),
                    )
                )

        if predicate == RDF.type:
            # (subject type obj) is new
            for super_class in self._sc_sub.get(obj, ()):
                self._record(
                    _Derivation(
                        "subClassOf-type",
                        (subject, RDF.type, super_class),
                        (
                            newly_believed,
                            (obj, RDFS.subClassOf, super_class),
                        ),
                    )
                )

        if predicate == RDFS.domain:
            # (subject domain obj) is new
            for prop_subject, prop_object in self._preds.get(subject, ()):
                self._record(
                    _Derivation(
                        "domain",
                        (prop_subject, RDF.type, obj),
                        (
                            newly_believed,
                            (prop_subject, subject, prop_object),
                        ),
                    )
                )

        if predicate == RDFS.range:
            # (subject range obj) is new
            for prop_subject, prop_object in self._preds.get(subject, ()):
                self._record(
                    _Derivation(
                        "range",
                        (prop_object, RDF.type, obj),
                        (
                            newly_believed,
                            (prop_subject, subject, prop_object),
                        ),
                    )
                )

        # Every believed triple can be an instance of a property.
        for super_property in self._sp_sub.get(predicate, ()):
            self._record(
                _Derivation(
                    "subPropertyOf-instance",
                    (subject, super_property, obj),
                    (
                        (predicate, RDFS.subPropertyOf, super_property),
                        newly_believed,
                    ),
                )
            )
        for domain_class in self._domains.get(predicate, ()):
            self._record(
                _Derivation(
                    "domain",
                    (subject, RDF.type, domain_class),
                    (
                        (predicate, RDFS.domain, domain_class),
                        newly_believed,
                    ),
                )
            )
        for range_class in self._ranges.get(predicate, ()):
            self._record(
                _Derivation(
                    "range",
                    (obj, RDF.type, range_class),
                    (
                        (predicate, RDFS.range, range_class),
                        newly_believed,
                    ),
                )
            )

    # ------------------------------------------------------------------
    # Deletion: delete/re-derive restricted to the affected cone
    # ------------------------------------------------------------------

    def _is_believed(self, triple: Triple) -> bool:
        return triple in self._believed

    def _delete_rederive(self, lost: Triple) -> None:
        """Re-validate everything that may depend on the lost fact.

        1. Find the cone of facts that consume (transitively) the fact.
        2. Recompute the well-founded supported subset inside the cone:
           support must ultimately rest on an explicit fact or on a fact
           outside the cone, so purely circular proofs do not count.
        3. Drop proofs and facts that lost all support; keep the rest.
        """
        cone = self._dependence_cone(lost)

        supported: set[Triple] = {fact for fact in cone if fact in self.graph}
        # Least fixed point: a cone fact survives with a derivation only if
        # all of its antecedents survive or live outside the cone (and are
        # therefore unaffected and still believed).
        changed = True
        while changed:
            changed = False
            for fact in cone:
                if fact in supported:
                    continue
                for derivation in self._derivations.get(fact, ()):
                    if all(
                        ant in supported
                        or (ant not in cone and self._is_believed(ant))
                        for ant in derivation.antecedents
                    ):
                        supported.add(fact)
                        changed = True
                        break

        removed = cone - supported
        for fact in removed:
            self._touch(fact)
            for derivation in self._derivations.pop(fact, ()):
                self._drop_derivation_indexes(derivation)
            self._unbelieve(fact)
            self._consumers.pop(fact, None)
        for fact in supported:
            self._touch(fact)
            proofs = self._derivations.get(fact)
            if not proofs:
                continue
            valid = {
                derivation
                for derivation in proofs
                if not any(ant in removed for ant in derivation.antecedents)
            }
            for derivation in proofs - valid:
                self._drop_derivation_indexes(derivation)
                self.stats["derivations_dropped"] += 1
            if valid:
                self._derivations[fact] = valid
            else:
                del self._derivations[fact]

    def _drop_derivation_indexes(self, derivation: _Derivation) -> None:
        for antecedent in derivation.antecedents:
            consumers = self._consumers.get(antecedent)
            if consumers is not None:
                consumers.discard(derivation)
                if not consumers:
                    del self._consumers[antecedent]

    def _dependence_cone(self, lost: Triple) -> set[Triple]:
        cone: set[Triple] = set()
        stack = [lost]
        while stack:
            fact = stack.pop()
            if fact in cone:
                continue
            cone.add(fact)
            self._touch(fact)
            for derivation in self._consumers.get(fact, ()):
                conclusion = derivation.conclusion
                if conclusion not in cone:
                    stack.append(conclusion)
        return cone
