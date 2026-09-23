r"""
This module implements the SPARQL 1.1 Property path operators, as
defined in:
[http://www.w3.org/TR/sparql11-query/#propertypaths](http://www.w3.org/TR/sparql11-query/#propertypaths)

In SPARQL the syntax is as follows:

| Syntax              | Matches                                                                 |
|---------------------|-------------------------------------------------------------------------|
| `iri`               | An IRI. A path of length one.                                           |
| `^elt`              | Inverse path (object to subject).                                       |
| `elt1 / elt2`       | A sequence path of `elt1` followed by `elt2`.                           |
| `elt1 \| elt2`      | An alternative path of `elt1` or `elt2` (all possibilities are tried).  |
| `elt*`              | A path that connects subject and object by zero or more matches of `elt`.|
| `elt+`              | A path that connects subject and object by one or more matches of `elt`.|
| `elt?`              | A path that connects subject and object by zero or one matches of `elt`.|
| `!iri` or <br> `!(iri1 \| ... \| irin)` | Negated property set. An IRI not among `iri1` to `irin`. <br> `!iri` is short for `!(iri)`. |
| `!^iri` or <br> `!(^iri1 \| ... \| ^irin)` | Negated reverse property set. Excludes `^iri1` to `^irin` as reverse paths. <br> `!^iri` is short for `!(^iri)`. |
| `!(iri1 \| ... \| irij \| ^irij+1 \| ... \| ^irin)` | A combination of forward and reverse properties in a negated property set. |
| `(elt)`             | A grouped path `elt`, where parentheses control precedence.             |

This module is used internally by the SPARQL engine, but the property paths
can also be used to query RDFLib Graphs directly.

Where possible the SPARQL syntax is mapped to Python operators, and property
path objects can be constructed from existing URIRefs.

```python
>>> from rdflib import Graph, Namespace
>>> from rdflib.namespace import FOAF

>>> ~FOAF.knows
Path(~http://xmlns.com/foaf/0.1/knows)

>>> FOAF.knows/FOAF.name
Path(http://xmlns.com/foaf/0.1/knows / http://xmlns.com/foaf/0.1/name)

>>> FOAF.name|FOAF.givenName
Path(http://xmlns.com/foaf/0.1/name | http://xmlns.com/foaf/0.1/givenName)

```

Modifiers (?, \*, +) are done using \* (the multiplication operator) and
the strings '\*', '?', '+', also defined as constants in this file.

```python
>>> FOAF.knows*OneOrMore
Path(http://xmlns.com/foaf/0.1/knows+)

```

The path objects can also be used with the normal graph methods.

First some example data:

```python
>>> g=Graph()

>>> g=g.parse(data='''
... @prefix : <ex:> .
...
... :a :p1 :c ; :p2 :f .
... :c :p2 :e ; :p3 :g .
... :g :p3 :h ; :p2 :j .
... :h :p3 :a ; :p2 :g .
...
... :q :px :q .
...
... ''', format='n3') # doctest: +ELLIPSIS

>>> e = Namespace('ex:')

```

Graph contains:

```python
>>> (e.a, e.p1/e.p2, e.e) in g
True

```

Graph generator functions, triples, subjects, objects, etc. :

```python
>>> list(g.objects(e.c, (e.p3*OneOrMore)/e.p2)) # doctest: +NORMALIZE_WHITESPACE
[rdflib.term.URIRef('ex:j'), rdflib.term.URIRef('ex:g'),
    rdflib.term.URIRef('ex:f')]

```

A more complete set of tests:

```python
>>> list(eval_path(g, (None, e.p1/e.p2, None)))==[(e.a, e.e)]
True
>>> list(eval_path(g, (e.a, e.p1|e.p2, None)))==[(e.a,e.c), (e.a,e.f)]
True
>>> list(eval_path(g, (e.c, ~e.p1, None))) == [ (e.c, e.a) ]
True
>>> list(eval_path(g, (e.a, e.p1*ZeroOrOne, None))) == [(e.a, e.a), (e.a, e.c)]
True
>>> list(eval_path(g, (e.c, e.p3*OneOrMore, None))) == [
...     (e.c, e.g), (e.c, e.h), (e.c, e.a)]
True
>>> list(eval_path(g, (e.c, e.p3*ZeroOrMore, None))) == [(e.c, e.c),
...     (e.c, e.g), (e.c, e.h), (e.c, e.a)]
True
>>> list(eval_path(g, (e.a, -e.p1, None))) == [(e.a, e.f)]
True
>>> list(eval_path(g, (e.a, -(e.p1|e.p2), None))) == []
True
>>> list(eval_path(g, (e.g, -~e.p2, None))) == [(e.g, e.j)]
True
>>> list(eval_path(g, (e.e, ~(e.p1/e.p2), None))) == [(e.e, e.a)]
True
>>> list(eval_path(g, (e.a, e.p1/e.p3/e.p3, None))) == [(e.a, e.h)]
True

>>> list(eval_path(g, (e.q, e.px*OneOrMore, None)))
[(rdflib.term.URIRef('ex:q'), rdflib.term.URIRef('ex:q'))]

>>> list(eval_path(g, (None, e.p1|e.p2, e.c)))
[(rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:c'))]

>>> list(eval_path(g, (None, ~e.p1, e.a))) == [ (e.c, e.a) ]
True
>>> list(eval_path(g, (None, e.p1*ZeroOrOne, e.c))) # doctest: +NORMALIZE_WHITESPACE
[(rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:c')),
 (rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:c'))]

>>> list(eval_path(g, (None, e.p3*OneOrMore, e.a))) # doctest: +NORMALIZE_WHITESPACE
[(rdflib.term.URIRef('ex:h'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:g'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:a'))]

>>> list(eval_path(g, (None, e.p3*ZeroOrMore, e.a))) # doctest: +NORMALIZE_WHITESPACE
[(rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:h'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:g'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:a'))]

>>> list(eval_path(g, (None, -e.p1, e.f))) == [(e.a, e.f)]
True
>>> list(eval_path(g, (None, -(e.p1|e.p2), e.c))) == []
True
>>> list(eval_path(g, (None, -~e.p2, e.j))) == [(e.g, e.j)]
True
>>> list(eval_path(g, (None, ~(e.p1/e.p2), e.a))) == [(e.e, e.a)]
True
>>> list(eval_path(g, (None, e.p1/e.p3/e.p3, e.h))) == [(e.a, e.h)]
True

>>> list(eval_path(g, (e.q, e.px*OneOrMore, None)))
[(rdflib.term.URIRef('ex:q'), rdflib.term.URIRef('ex:q'))]

>>> list(eval_path(g, (e.c, (e.p2|e.p3)*ZeroOrMore, e.j)))
[(rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:j'))]

```

No vars specified:

```python
>>> sorted(list(eval_path(g, (None, e.p3*OneOrMore, None)))) #doctest: +NORMALIZE_WHITESPACE
[(rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:g')),
 (rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:h')),
 (rdflib.term.URIRef('ex:g'), rdflib.term.URIRef('ex:a')),
 (rdflib.term.URIRef('ex:g'), rdflib.term.URIRef('ex:h')),
 (rdflib.term.URIRef('ex:h'), rdflib.term.URIRef('ex:a'))]

```

Shortest witnesses

When debugging data it is not enough to know that two nodes are connected
by a path expression; eval_path_witness (also available as the
Path.eval_witness method) additionally returns one shortest witness per
endpoint pair: the graph triples crossed, in traversal order, each tagged
with the direction of traversal. Zero-length matches have an empty witness.

>>> from rdflib.paths import eval_path_witness

>>> [(s, o, len(w)) for s, o, w in eval_path_witness(g, (e.a, e.p1/e.p2, None))]
[(rdflib.term.URIRef('ex:a'), rdflib.term.URIRef('ex:e'), 2)]

>>> list(eval_path_witness(g, (e.c, e.p3*ZeroOrMore, e.c)))
[(rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:c'), ())]

>>> s, o, w = next(iter(eval_path_witness(g, (e.e, ~(e.p1/e.p2), None))))
>>> (s, o) == (e.e, e.a)
True
>>> [(st.predicate, st.forward) for st in w]
[(rdflib.term.URIRef('ex:p2'), False), (rdflib.term.URIRef('ex:p1'), False)]

"""

from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Generator, Iterator
from functools import total_ordering
from typing import (
    TYPE_CHECKING,
    Any,
    NamedTuple,
    Optional,
    Union,
    cast,
)

from rdflib.term import Node, URIRef

if TYPE_CHECKING:
    from rdflib._type_checking import _MulPathMod
    from rdflib.graph import (
        Graph,
        _ObjectType,
        _PredicateType,
        _SubjectType,
        _TripleType,
    )
    from rdflib.namespace import NamespaceManager


# property paths

ZeroOrMore = "*"
OneOrMore = "+"
ZeroOrOne = "?"


class WitnessStep(NamedTuple):
    """
    A single step of a path witness.

    ``(subject, predicate, object)`` is a triple of the queried graph, in its
    original orientation. ``forward`` is ``True`` if the step traverses the
    triple from subject to object, and ``False`` if it traverses it from
    object to subject (i.e. via an inverse path).
    """

    subject: _SubjectType
    predicate: _PredicateType
    object: _ObjectType
    forward: bool


def _n3(
    arg: Union[URIRef, Path], namespace_manager: Optional[NamespaceManager] = None
) -> str:
    if isinstance(arg, (SequencePath, AlternativePath)) and len(arg.args) > 1:
        return "(%s)" % arg.n3(namespace_manager)
    return arg.n3(namespace_manager)


@total_ordering
class Path(ABC):
    """Base class for all property paths."""

    __or__: Callable[[Path, Union[URIRef, Path]], AlternativePath]
    __invert__: Callable[[Path], InvPath]
    __neg__: Callable[[Path], NegatedPath]
    __truediv__: Callable[[Path, Union[URIRef, Path]], SequencePath]
    __mul__: Callable[[Path, str], MulPath]

    @abstractmethod
    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Iterator[tuple[_SubjectType, _ObjectType]]: ...

    def eval_witness(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Iterator[tuple[_SubjectType, _ObjectType, tuple[WitnessStep, ...]]]:
        """
        Evaluate this path on the given graph, yielding, for every reachable
        endpoint, the endpoint pair together with one shortest witness.

        This is a convenience wrapper around :func:`eval_path_witness`;
        see there for the exact semantics.
        """
        return eval_path_witness(graph, (subj, self, obj))

    @abstractmethod
    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str: ...

    def __hash__(self):
        return hash(repr(self))

    def __eq__(self, other):
        return repr(self) == repr(other)

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, (Path, Node)):
            raise TypeError(
                "unorderable types: %s() < %s()" % (repr(self), repr(other))
            )
        return repr(self) < repr(other)


class InvPath(Path):
    def __init__(self, arg: Union[Path, URIRef]):
        self.arg = arg

    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Generator[tuple[_ObjectType, _SubjectType], None, None]:
        for s, o in eval_path(graph, (obj, self.arg, subj)):
            yield o, s

    def __repr__(self) -> str:
        return "Path(~%s)" % (self.arg,)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "^%s" % _n3(self.arg, namespace_manager)


class SequencePath(Path):
    def __init__(self, *args: Union[Path, URIRef]):
        self.args: list[Union[Path, URIRef]] = []
        for a in args:
            if isinstance(a, SequencePath):
                self.args += a.args
            else:
                self.args.append(a)

    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
        def _eval_seq(
            paths: list[Union[Path, URIRef]],
            subj: Optional[_SubjectType],
            obj: Optional[_ObjectType],
        ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            if paths[1:]:
                for s, o in eval_path(graph, (subj, paths[0], None)):
                    for r in _eval_seq(paths[1:], o, obj):
                        yield s, r[1]

            else:
                for s, o in eval_path(graph, (subj, paths[0], obj)):
                    yield s, o

        def _eval_seq_bw(
            paths: list[Union[Path, URIRef]],
            subj: Optional[_SubjectType],
            obj: _ObjectType,
        ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            if paths[:-1]:
                for s, o in eval_path(graph, (None, paths[-1], obj)):
                    for r in _eval_seq(paths[:-1], subj, s):
                        yield r[0], o

            else:
                for s, o in eval_path(graph, (subj, paths[0], obj)):
                    yield s, o

        if subj:
            return _eval_seq(self.args, subj, obj)
        elif obj:
            return _eval_seq_bw(self.args, subj, obj)
        else:  # no vars bound, we can start anywhere
            return _eval_seq(self.args, subj, obj)

    def __repr__(self) -> str:
        return "Path(%s)" % " / ".join(str(x) for x in self.args)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "/".join(_n3(a, namespace_manager) for a in self.args)


class AlternativePath(Path):
    def __init__(self, *args: Union[Path, URIRef]):
        self.args: list[Union[Path, URIRef]] = []
        for a in args:
            if isinstance(a, AlternativePath):
                self.args += a.args
            else:
                self.args.append(a)

    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
        for x in self.args:
            for y in eval_path(graph, (subj, x, obj)):
                yield y

    def __repr__(self) -> str:
        return "Path(%s)" % " | ".join(str(x) for x in self.args)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "|".join(_n3(a, namespace_manager) for a in self.args)


class MulPath(Path):
    def __init__(self, path: Union[Path, URIRef], mod: _MulPathMod):
        self.path = path
        self.mod = mod

        if mod == ZeroOrOne:
            self.zero = True
            self.more = False
        elif mod == ZeroOrMore:
            self.zero = True
            self.more = True
        elif mod == OneOrMore:
            self.zero = False
            self.more = True
        else:
            raise Exception("Unknown modifier %s" % mod)

    def eval(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
        first: bool = True,
    ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
        if self.zero and first:
            if subj and obj:
                if subj == obj:
                    yield subj, obj
            elif subj:
                yield subj, subj
            elif obj:
                yield obj, obj

        def _fwd(
            subj: Optional[_SubjectType] = None,
            obj: Optional[_ObjectType] = None,
            seen: Optional[set[_SubjectType]] = None,
        ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            # type error: Item "None" of "Optional[set[Node]]" has no attribute "add"
            # type error: Argument 1 to "add" of "set" has incompatible type "Optional[Node]"; expected "Node"
            seen.add(subj)  # type: ignore[union-attr, arg-type]

            for s, o in eval_path(graph, (subj, self.path, None)):
                if not obj or o == obj:
                    yield s, o
                if self.more:
                    # type error: Unsupported right operand type for in ("Optional[set[Node]]")
                    if o in seen:  # type: ignore[operator]
                        continue
                    for s2, o2 in _fwd(o, obj, seen):
                        yield s, o2

        def _bwd(
            subj: Optional[_SubjectType] = None,
            obj: Optional[_ObjectType] = None,
            seen: Optional[set[_ObjectType]] = None,
        ) -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            # type error: Item "None" of "Optional[set[Node]]" has no attribute "add"
            # type error: Argument 1 to "add" of "set" has incompatible type "Optional[Node]"; expected "Node"
            seen.add(obj)  # type: ignore[union-attr, arg-type]

            for s, o in eval_path(graph, (None, self.path, obj)):
                if not subj or subj == s:
                    yield s, o
                if self.more:
                    # type error: Unsupported right operand type for in ("Optional[set[Node]]")
                    if s in seen:  # type: ignore[operator]
                        continue

                    for s2, o2 in _bwd(None, s, seen):
                        yield s2, o

        def _all_fwd_paths() -> Generator[tuple[_SubjectType, _ObjectType], None, None]:
            if self.zero:
                seen1 = set()
                # According to the spec, ALL nodes are possible solutions
                # (even literals)
                # we cannot do this without going through ALL triples
                # unless we keep an index of all terms somehow
                # but let's just hope this query doesn't happen very often...
                for s, o in graph.subject_objects(None):
                    if s not in seen1:
                        seen1.add(s)
                        yield s, s
                    if o not in seen1:
                        seen1.add(o)
                        yield o, o

            seen = set()
            for s, o in eval_path(graph, (None, self.path, None)):
                if not self.more:
                    yield s, o
                else:
                    if s not in seen:
                        seen.add(s)
                        f = list(_fwd(s, None, set()))
                        for s1, o1 in f:
                            assert s1 == s
                            yield s1, o1

        done = set()  # the spec does, by defn, not allow duplicates
        if subj:
            for x in _fwd(subj, obj, set()):
                if x not in done:
                    done.add(x)
                    yield x
        elif obj:
            for x in _bwd(subj, obj, set()):
                if x not in done:
                    done.add(x)
                    yield x
        else:
            for x in _all_fwd_paths():
                if x not in done:
                    done.add(x)
                    yield x

    def __repr__(self) -> str:
        return "Path(%s%s)" % (self.path, self.mod)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "%s%s" % (_n3(self.path, namespace_manager), self.mod)


class NegatedPath(Path):
    def __init__(self, arg: Union[AlternativePath, InvPath, URIRef]):
        self.args: list[Union[URIRef, Path]]
        if isinstance(arg, (URIRef, InvPath)):
            self.args = [arg]
        elif isinstance(arg, AlternativePath):
            self.args = arg.args
        else:
            raise Exception(
                "Can only negate URIRefs, InvPaths or "
                + "AlternativePaths, not: %s" % (arg,)
            )

    def eval(self, graph, subj=None, obj=None):
        for s, p, o in graph.triples((subj, None, obj)):
            for a in self.args:
                if isinstance(a, URIRef):
                    if p == a:
                        break
                elif isinstance(a, InvPath):
                    if (o, a.arg, s) in graph:
                        break
                else:
                    raise Exception("Invalid path in NegatedPath: %s" % a)
            else:
                yield s, o

    def __repr__(self) -> str:
        return "Path(! %s)" % ",".join(str(x) for x in self.args)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "!(%s)" % ("|".join(_n3(arg, namespace_manager) for arg in self.args))


class PathList(list):
    pass


def path_alternative(self: Union[URIRef, Path], other: Union[URIRef, Path]):
    """
    alternative path
    """
    if not isinstance(other, (URIRef, Path)):
        raise Exception("Only URIRefs or Paths can be in paths!")
    return AlternativePath(self, other)


def path_sequence(self: Union[URIRef, Path], other: Union[URIRef, Path]):
    """
    sequence path
    """
    if not isinstance(other, (URIRef, Path)):
        raise Exception("Only URIRefs or Paths can be in paths!")
    return SequencePath(self, other)


def evalPath(  # noqa: N802
    graph: Graph,
    t: tuple[
        Optional[_SubjectType],
        Union[None, Path, _PredicateType],
        Optional[_ObjectType],
    ],
) -> Iterator[tuple[_SubjectType, _ObjectType]]:
    warnings.warn(
        DeprecationWarning(
            "rdflib.path.evalPath() is deprecated, use the (snake-cased) eval_path(). "
            "The mixed-case evalPath() function name is incompatible with PEP8 "
            "recommendations and will be replaced by eval_path() in rdflib 7.0.0."
        )
    )
    return eval_path(graph, t)


def eval_path(
    graph: Graph,
    t: tuple[
        Optional[_SubjectType],
        Union[None, Path, _PredicateType],
        Optional[_ObjectType],
    ],
) -> Iterator[tuple[_SubjectType, _ObjectType]]:
    return ((s, o) for s, p, o in graph.triples(t))


def mul_path(p: Union[URIRef, Path], mul: _MulPathMod) -> MulPath:
    """
    cardinality path
    """
    return MulPath(p, mul)


def inv_path(p: Union[URIRef, Path]) -> InvPath:
    """
    inverse path
    """
    return InvPath(p)


def neg_path(p: Union[URIRef, AlternativePath, InvPath]) -> NegatedPath:
    """
    negated path
    """
    return NegatedPath(p)


class _EdgeMatcher(ABC):
    """Matches a single RDF edge of a path NFA against a graph."""

    @abstractmethod
    def matches(
        self, graph: Graph, node: Node
    ) -> Iterator[tuple[Node, _TripleType, bool]]:
        """
        Yield ``(next_node, triple, forward)`` for every graph triple that
        this matcher can traverse starting at ``node``. ``triple`` is kept
        in its original graph orientation; ``forward`` records the direction
        of traversal.
        """


class _ForwardMatcher(_EdgeMatcher):
    def __init__(self, predicate: _PredicateType):
        self.predicate = predicate

    def matches(
        self, graph: Graph, node: Node
    ) -> Iterator[tuple[Node, _TripleType, bool]]:
        for s, p, o in graph.triples((node, self.predicate, None)):
            yield o, (s, p, o), True


class _BackwardMatcher(_EdgeMatcher):
    def __init__(self, predicate: _PredicateType):
        self.predicate = predicate
    def matches(
        self, graph: Graph, node: Node
    ) -> Iterator[tuple[Node, _TripleType, bool]]:
        for s, p, o in graph.triples((None, self.predicate, node)):
            yield s, (s, p, o), False


class _NegatedMatcher(_EdgeMatcher):
    """Matches one edge of a :class:`NegatedPath`, in either direction."""

    def __init__(
        self, args: list[Union[URIRef, Path]], reversed_: bool = False
    ):
        self.forward_excluded: set[URIRef] = set()
        self.backward_excluded: list[Union[URIRef, Path]] = []
        for a in args:
            if isinstance(a, URIRef):
                self.forward_excluded.add(a)
            elif isinstance(a, InvPath):
                self.backward_excluded.append(a.arg)
            else:
                raise Exception("Invalid path in NegatedPath: %s" % a)
        self.reversed = reversed_

    def matches(
        self, graph: Graph, node: Node
    ) -> Iterator[tuple[Node, _TripleType, bool]]:
        if self.reversed:
            for s, p, o in graph.triples((None, None, node)):
                if p in self.forward_excluded:
                    continue
                if any((node, q, s) in graph for q in self.backward_excluded):
                    continue
                yield s, (s, p, o), False
        else:
            for s, p, o in graph.triples((node, None, None)):
                if p in self.forward_excluded:
                    continue
                if any((o, q, s) in graph for q in self.backward_excluded):
                    continue
                yield o, (s, p, o), True


class _PathNFA:
    """
    A NFA (Thompson's construction) over :class:`_EdgeMatcher` transitions.

    Transitions with a ``None`` matcher are epsilon transitions.
    """

    def __init__(self) -> None:
        self.transitions: list[list[tuple[Optional[_EdgeMatcher], int]]] = []

    def new_state(self) -> int:
        self.transitions.append([])
        return len(self.transitions) - 1

    def add(
        self, source: int, matcher: Optional[_EdgeMatcher], target: int
    ) -> None:
        self.transitions[source].append((matcher, target))

    def epsilon_closures(self) -> list[frozenset[int]]:
        closures = []
        for state in range(len(self.transitions)):
            seen = {state}
            stack = [state]
            while stack:
                current = stack.pop()
                for matcher, target in self.transitions[current]:
                    if matcher is None and target not in seen:
                        seen.add(target)
                        stack.append(target)
            closures.append(frozenset(seen))
        return closures


def _compile_path(
    path: Union[Path, _PredicateType], nfa: _PathNFA, reversed_: bool = False
) -> tuple[int, int]:
    """
    Compile ``path`` into ``nfa``, returning its ``(start, accept)`` states.

    With ``reversed_`` the NFA matches the path backwards, i.e. from objects
    to subjects; every edge matcher then traverses triples in the opposite
    direction.
    """
    if isinstance(path, InvPath):
        return _compile_path(path.arg, nfa, not reversed_)
    if isinstance(path, SequencePath):
        args = path.args[::-1] if reversed_ else path.args
        start, accept = _compile_path(args[0], nfa, reversed_)
        for arg in args[1:]:
            next_start, next_accept = _compile_path(arg, nfa, reversed_)
            nfa.add(accept, None, next_start)
            accept = next_accept
        return start, accept
    if isinstance(path, AlternativePath):
        start, accept = nfa.new_state(), nfa.new_state()
        for arg in path.args:
            arg_start, arg_accept = _compile_path(arg, nfa, reversed_)
            nfa.add(start, None, arg_start)
            nfa.add(arg_accept, None, accept)
        return start, accept
    if isinstance(path, MulPath):
        start, accept = nfa.new_state(), nfa.new_state()
        inner_start, inner_accept = _compile_path(path.path, nfa, reversed_)
        nfa.add(start, None, inner_start)
        nfa.add(inner_accept, None, accept)
        if path.zero:
            nfa.add(start, None, accept)
        if path.more:
            nfa.add(inner_accept, None, inner_start)
        return start, accept
    if isinstance(path, NegatedPath):
        start, accept = nfa.new_state(), nfa.new_state()
        nfa.add(start, _NegatedMatcher(path.args, reversed_), accept)
        return start, accept
    # anything else is a plain predicate, matching a single forward edge
    start, accept = nfa.new_state(), nfa.new_state()
    matcher: _EdgeMatcher
    if reversed_:
        matcher = _BackwardMatcher(cast("_PredicateType", path))
    else:
        matcher = _ForwardMatcher(cast("_PredicateType", path))
    nfa.add(start, matcher, accept)
    return start, accept


def _bfs_witnesses(
    graph: Graph,
    nfa: _PathNFA,
    closures: list[frozenset[int]],
    start: int,
    accept: int,
    source: _SubjectType,
    target: Optional[_ObjectType] = None,
) -> Iterator[tuple[_ObjectType, tuple[WitnessStep, ...]]]:
    """
    Breadth-first search from ``source`` over (graph node, NFA state) pairs.

    Yields ``(endpoint, witness)`` for every reachable endpoint exactly once,
    in non-decreasing order of witness length; the first witness found for an
    endpoint crosses a minimal number of RDF edges. Cycles cannot cause
    non-termination because every (node, state set) pair is expanded at most
    once.
    """
    start_states = closures[start]
    visited = {(source, start_states)}
    queue: deque[tuple[Node, frozenset[int], tuple[WitnessStep, ...]]] = deque(
        [(source, start_states, ())]
    )
    yielded = set()
    if accept in start_states and (target is None or source == target):
        yielded.add(source)
        yield source, ()
        if target is not None:
            return
    while queue:
        node, states, witness = queue.popleft()
        for state in states:
            for matcher, next_state in nfa.transitions[state]:
                if matcher is None:
                    continue
                for next_node, triple, forward in matcher.matches(graph, node):
                    next_states = closures[next_state]
                    key = (next_node, next_states)
                    if key in visited:
                        continue
                    visited.add(key)
                    next_witness = witness + (
                        WitnessStep(triple[0], triple[1], triple[2], forward),
                    )
                    if (
                        accept in next_states
                        and next_node not in yielded
                        and (target is None or next_node == target)
                    ):
                        yielded.add(next_node)
                        yield next_node, next_witness
                        if target is not None:
                            return
                    queue.append((next_node, next_states, next_witness))


def eval_path_witness(
    graph: Graph,
    t: tuple[
        Optional[_SubjectType],
        Union[Path, _PredicateType],
        Optional[_ObjectType],
    ],
) -> Iterator[tuple[_SubjectType, _ObjectType, tuple[WitnessStep, ...]]]:
    """
    Evaluate a path expression on ``graph`` and return, for every reachable
    endpoint pair, one shortest witness.

    Like :func:`eval_path`, the pattern ``t`` is a ``(subject, path, object)``
    triple in which subject and/or object may be ``None``. For every
    ``(subject, object)`` pair connected by the path expression, a single
    tuple ``(subject, object, witness)`` is yielded. ``witness`` is a tuple
    of :class:`WitnessStep` listing, in traversal order, the graph triples
    crossed along one shortest matching walk, each tagged with the direction
    it is traversed in. Shortest is measured in the number of RDF edges
    crossed, so zero-length matches (e.g. of ``path*`` or ``path?``) have an
    empty witness. If several shortest witnesses exist for a pair, an
    arbitrary one is returned.

    The search is a breadth-first search over the expression itself, so
    every step of a witness satisfies the expression, and cycles or
    zero-length-capable expressions cannot cause non-termination.

    Only the graph passed in is consulted: when a :class:`~rdflib.Dataset`
    context (named graph) is passed, witnesses never combine triples from
    other contexts.
    """
    subj, path, obj = t
    nfa = _PathNFA()
    if subj is not None:
        start, accept = _compile_path(path, nfa)
        closures = nfa.epsilon_closures()
        for end, witness in _bfs_witnesses(
            graph, nfa, closures, start, accept, subj, obj
        ):
            yield subj, end, witness
    elif obj is not None:
        # match the path backwards from the object, then flip the witness
        start, accept = _compile_path(path, nfa, reversed_=True)
        closures = nfa.epsilon_closures()
        for end, witness in _bfs_witnesses(
            graph, nfa, closures, start, accept, obj, None
        ):
            yield end, obj, tuple(
                WitnessStep(
                    step.subject, step.predicate, step.object, not step.forward
                )
                for step in reversed(witness)
            )
    else:
        start, accept = _compile_path(path, nfa)
        closures = nfa.epsilon_closures()
        seen_sources = set()
        for s, o in graph.subject_objects(None):
            for source in (s, o):
                if source in seen_sources:
                    continue
                seen_sources.add(source)
                for end, witness in _bfs_witnesses(
                    graph, nfa, closures, start, accept, source, None
                ):
                    yield source, end, witness


if __name__ == "__main__":
    pass
else:
    # monkey patch
    # (these cannot be directly in terms.py
    #  as it would introduce circular imports)

    URIRef.__or__ = path_alternative  # type: ignore[method-assign, assignment]
    # ignore typing here as URIRef inherits from str,
    # which has an incompatible definition of __mul__.
    URIRef.__mul__ = mul_path  # type: ignore
    URIRef.__invert__ = inv_path
    URIRef.__neg__ = neg_path
    URIRef.__truediv__ = path_sequence

    Path.__invert__ = inv_path
    # type error: Incompatible types in assignment (expression has type "Callable[[Union[URIRef, AlternativePath, InvPath]], NegatedPath]", variable has type "Callable[[Path], NegatedPath]")
    Path.__neg__ = neg_path  # type: ignore[assignment]
    # type error: Incompatible types in assignment (expression has type "Callable[[Union[URIRef, Path], Literal['*', '+', '?']], MulPath]", variable has type "Callable[[Path, str], MulPath]")
    Path.__mul__ = mul_path  # type: ignore[assignment]
    Path.__or__ = path_alternative  # type: ignore[method-assign, assignment]
    Path.__truediv__ = path_sequence
