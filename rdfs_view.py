"""Incremental RDFS closure view over an rdflib graph.

The view keeps the wrapped graph as the store of explicit (base) triples
and maintains the derived closure in its own indexes, so the original graph
is never polluted with inferred triples.  Only this explicitly listed RDFS
rule subset is implemented:

* R1  subClassOf transitivity
* R2  subPropertyOf transitivity
* R3  type propagation over subClassOf      (rdfs9)
* R4  domain                              (rdfs2)
* R5  range                               (rdfs3)
* R6  sub-property propagation over instance edges (rdfs7)

Maintenance is incremental:

* additions use semi-naive forward chaining driven by indexes, so only
  triples joinable with the new fact are touched;
* removals use a DRed-style over-delete + rederive pass.  A conclusion with
  several independent proofs survives while any proof remains, while
  conclusions supported only by cyclic proofs are retracted once their
  non-cyclic (ultimately base) support disappears.

RDFSView.last_visited reports which triples were examined while applying
the most recent add/remove, making it easy to verify that an update
confined to one disconnected region of the graph never visits another.
"""

from __future__ import annotations

from collections import defaultdict, deque

from rdflib import Graph
from rdflib.namespace import RDF, RDFS

TYPE = RDF.type
SC = RDFS.subClassOf
SP = RDFS.subPropertyOf
DOM = RDFS.domain
RNG = RDFS.range


class RDFSView:
    """Read-through view of a base graph plus its maintained RDFS closure."""

    def __init__(self, graph=None):
        self.graph = graph if graph is not None else Graph()
        # Explicit facts; kept in sync with the wrapped graph (the view must
        # be the only writer).
        self._base = set(self.graph)
        # Facts with at least one recorded derivation (may overlap _base).
        self._derived = set()
        # conclusion -> {(antecedent, ...), ...}; ground rule instantiations.
        self._justifs = defaultdict(set)
        # antecedent -> {conclusion, ...}; used by the deletion cascade.
        self._conclusions_of = defaultdict(set)
        # Indexes over believed (base or derived) triples.
        self._by_pred = defaultdict(set)  # p -> {(s, o)}
        self._sc_out = defaultdict(set)  # a -> {b : a subClassOf b}
        self._sc_in = defaultdict(set)  # b -> {a : a subClassOf b}
        self._sp_out = defaultdict(set)  # p -> {q : p subPropertyOf q}
        self._sp_in = defaultdict(set)  # q -> {p : p subPropertyOf q}
        self._instances = defaultdict(set)  # c -> {x : x type c}
        self._dom = defaultdict(set)  # p -> {c : p domain c}
        self._rng = defaultdict(set)  # p -> {c : p range c}
        # Triples examined while applying the most recent add/remove.
        self._visited = []
        self._fire_from(list(self._base))

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    def _believed(self, t):
        return t in self._base or t in self._derived

    def is_explicit(self, t):
        """True if t is a base fact stored in the wrapped graph."""
        return t in self._base

    def is_derived(self, t):
        """True if t has derivation support (it may also be explicit)."""
        return t in self._derived

    def explicit_facts(self):
        return frozenset(self._base)

    def derived_facts(self):
        return frozenset(self._derived)

    @property
    def last_visited(self):
        """Triples examined while applying the most recent add/remove."""
        return frozenset(self._visited)

    def _note(self, t):
        self._visited.append(t)

    # ------------------------------------------------------------------
    # container protocol over the closure (explicit + derived)
    # ------------------------------------------------------------------
    def __contains__(self, t):
        return self._believed(t)

    def __iter__(self):
        return iter(self._base | self._derived)

    def __len__(self):
        return len(self._base | self._derived)

    def triples(self, pattern):
        s, p, o = pattern
        for t in self:
            if (
                (s is None or t[0] == s)
                and (p is None or t[1] == p)
                and (o is None or t[2] == o)
            ):
                yield t

    def to_graph(self):
        """Materialize the current closure into a new, separate graph."""
        g = Graph()
        for t in self:
            g.add(t)
        return g

    # ------------------------------------------------------------------
    # mutation
    # ------------------------------------------------------------------
    def add(self, t):
        """Add a base triple; repeated adds of the same triple are no-ops."""
        self._visited = []
        if t in self._base:
            return
        self._note(t)
        self._base.add(t)
        self.graph.add(t)
        if t in self._derived:
            # Already believed through derivation; its joins already fired.
            return
        self._fire_from([t])

    def remove(self, t):
        """Retract a base triple and repair the closure (DRed)."""
        self._visited = []
        if t not in self._base:
            return
        self._note(t)
        self._base.discard(t)
        self.graph.remove(t)
        deleted = set()
        if t in self._derived:
            self._derived.discard(t)
            deleted.add(t)
        # t is no longer believed (base removed; derived status discarded);
        # pull it out of the join indexes before cascading.
        self._deindex(t)
        # Over-deletion: drop everything derived (transitively) from t,
        # regardless of alternative proofs.
        stack = [t]
        while stack:
            u = stack.pop()
            for d in self._conclusions_of.get(u, ()):
                self._note(d)
                if d in self._derived and d not in self._base and d not in deleted:
                    self._derived.discard(d)
                    self._deindex(d)
                    deleted.add(d)
                    stack.append(d)
        # Rederivation: restore deleted facts that still have a proof whose
        # antecedents all remain believed.  Facts kept alive only by cyclic
        # proofs fail this test and stay deleted.
        changed = True
        while changed:
            changed = False
            for d in list(deleted):
                if self._believed(d):
                    continue
                for j in self._justifs.get(d, ()):
                    self._note(d)
                    if all(self._believed(a) for a in j):
                        for a in j:
                            self._note(a)
                        self._derived.add(d)
                        self._index(d)
                        changed = True
                        break

    # ------------------------------------------------------------------
    # inference engine
    # ------------------------------------------------------------------
    def _fire_from(self, seed):
        """Index newly believed facts and fire every rule join they join."""
        queue = deque(seed)
        while queue:
            f = queue.popleft()
            self._index(f)
            self._note(f)
            for conclusion, justif in self._joins_for(f):
                self._conclude(conclusion, justif, queue)

    def _conclude(self, c, j, queue):
        # Justifications are recorded even for already-believed conclusions,
        # so every proof of a fact is available to the rederivation pass.
        self._justifs[c].add(j)
        for a in j:
            self._conclusions_of[a].add(c)
        if c not in self._derived:
            self._derived.add(c)
            if c not in self._base:
                queue.append(c)

    def _joins_for(self, f):
        """All rule instantiations the newly believed fact participates in."""
        s, p, o = f
        joins = []
        # f as a property edge (x p y): R4 domain, R5 range, R6 sub-property.
        for c in self._dom.get(p, ()):
            g = (p, DOM, c)
            self._note(g)
            joins.append(((s, TYPE, c), (f, g)))
        for c in self._rng.get(p, ()):
            g = (p, RNG, c)
            self._note(g)
            joins.append(((o, TYPE, c), (f, g)))
        for q in self._sp_out.get(p, ()):
            g = (p, SP, q)
            self._note(g)
            joins.append(((s, q, o), (f, g)))
        if p == TYPE:
            # f is (x type c); R3 with superclasses of c.
            for b in self._sc_out.get(o, ()):
                g = (o, SC, b)
                self._note(g)
                joins.append(((s, TYPE, b), (f, g)))
        elif p == SC:
            # f is (a sc b); R1 in both positions, R3 as the sc premise.
            for x in self._sc_in.get(s, ()):
                g = (x, SC, s)
                self._note(g)
                joins.append(((x, SC, o), (f, g)))
            for c in self._sc_out.get(o, ()):
                g = (o, SC, c)
                self._note(g)
                joins.append(((s, SC, c), (f, g)))
            for x in self._instances.get(s, ()):
                g = (x, TYPE, s)
                self._note(g)
                joins.append(((x, TYPE, o), (f, g)))
        elif p == SP:
            # f is (p sp q); R2 in both positions, R6 as the sp premise.
            for r in self._sp_out.get(o, ()):
                g = (o, SP, r)
                self._note(g)
                joins.append(((s, SP, r), (f, g)))
            for x in self._sp_in.get(s, ()):
                g = (x, SP, s)
                self._note(g)
                joins.append(((x, SP, o), (f, g)))
            for x, y in self._by_pred.get(s, ()):
                g = (x, s, y)
                self._note(g)
                joins.append(((x, o, y), (f, g)))
        elif p == DOM:
            # f is (p dom c); R4 with existing p-edges.
            for x, y in self._by_pred.get(s, ()):
                g = (x, s, y)
                self._note(g)
                joins.append(((x, TYPE, o), (f, g)))
        elif p == RNG:
            # f is (p rng c); R5 with existing p-edges.
            for x, y in self._by_pred.get(s, ()):
                g = (x, s, y)
                self._note(g)
                joins.append(((y, TYPE, o), (f, g)))
        return joins

    # ------------------------------------------------------------------
    # indexes over believed triples
    # ------------------------------------------------------------------
    def _index(self, t):
        s, p, o = t
        self._by_pred[p].add((s, o))
        if p == SC:
            self._sc_out[s].add(o)
            self._sc_in[o].add(s)
        elif p == SP:
            self._sp_out[s].add(o)
            self._sp_in[o].add(s)
        elif p == TYPE:
            self._instances[o].add(s)
        elif p == DOM:
            self._dom[s].add(o)
        elif p == RNG:
            self._rng[s].add(o)

    def _deindex(self, t):
        s, p, o = t
        self._by_pred[p].discard((s, o))
        if p == SC:
            self._sc_out[s].discard(o)
            self._sc_in[o].discard(s)
        elif p == SP:
            self._sp_out[s].discard(o)
            self._sp_in[o].discard(s)
        elif p == TYPE:
            self._instances[o].discard(s)
        elif p == DOM:
            self._dom[s].discard(o)
        elif p == RNG:
            self._rng[s].discard(o)
