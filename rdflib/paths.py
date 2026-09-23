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

Witnesses
---------

In addition to the endpoint pairs, eval_path_witness() returns one
shortest witness for every reachable endpoint: the sequence of graph
triples crossed, in traversal order, each marked as traversed forwards
or backwards. The length of a witness is the number of RDF edges
crossed, so zero-length matches have an empty witness.

>>> for s, o, w in eval_path_witness(g, (e.a, e.p1/e.p2, e.e)):
...     print(s, o, len(w))
ex:a ex:e 2

>>> list(eval_path_witness(g, (e.c, e.p3*ZeroOrMore, e.c)))
[(rdflib.term.URIRef('ex:c'), rdflib.term.URIRef('ex:c'), ())]

>>> for s, o, w in eval_path_witness(g, (e.e, ~(e.p1/e.p2), None)):
...     print(s, o, [(step.predicate, step.forward) for step in w])
ex:e ex:a [(rdflib.term.URIRef('ex:p2'), False), (rdflib.term.URIRef('ex:p1'), False)]
"""

from __future__ import annotations

import heapq
import itertools
import warnings
from abc import ABC, abstractmethod
from collections.abc import Callable, Generator, Iterator
from functools import total_ordering
from typing import (
    TYPE_CHECKING,
    Any,
    NamedTuple,
    Optional,
    Union,
)

from rdflib.term import Node, URIRef

if TYPE_CHECKING:
    from rdflib._type_checking import _MulPathMod
    from rdflib.graph import Graph, _ObjectType, _PredicateType, _SubjectType
    from rdflib.namespace import NamespaceManager


# property paths

ZeroOrMore = "*"
OneOrMore = "+"
ZeroOrOne = "?"


class WitnessStep(NamedTuple):
    """
    A single step of a path witness.

    "subject", "predicate" and "object" are the triple exactly as stored in
    the graph. "forward" is True when the step traversed the triple from
    subject to object, and False when it was traversed in reverse (from
    object to subject), as happens for inverse paths.
    """

    subject: Node
    predicate: Node
    object: Node
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

    @abstractmethod
    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str: ...

    def witness(
        self,
        graph: Graph,
        subj: Optional[_SubjectType] = None,
        obj: Optional[_ObjectType] = None,
    ) -> Iterator[tuple[_SubjectType, _ObjectType, tuple[WitnessStep, ...]]]:
        """
        Evaluate this path against the graph and yield, for every reachable
        endpoint, the endpoint pair together with one shortest witness.

        This is a convenience wrapper around eval_path_witness();
        see its documentation for details.
        """
        return eval_path_witness(graph, (subj, self, obj))

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


class _ReverseNegatedPath(Path):
    """
    The inverse relation of a NegatedPath.

    Unlike a NegatedPath, which only ever follows triples forwards, this
    follows triples backwards: from a node it reaches the subjects of
    incoming triples whose predicate is not excluded. It is used
    internally to compute witnesses when only the object is bound.
    """

    def __init__(self, arg: NegatedPath):
        self.arg = arg

    def eval(self, graph, subj=None, obj=None):
        for s, p, o in graph.triples((None, None, subj)):
            if (obj is None or s == obj) and _negated_path_allows(
                graph, self.arg, s, p, o
            ):
                yield o, s

    def __repr__(self) -> str:
        return "Path(~! %s)" % ",".join(str(x) for x in self.arg.args)

    def n3(self, namespace_manager: Optional[NamespaceManager] = None) -> str:
        return "^%s" % self.arg.n3(namespace_manager)


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


_WitnessCacheType = dict[
    tuple[Union[Path, URIRef], Node], dict[Node, list[WitnessStep]]
]


def eval_path_witness(
    graph: Graph,
    t: tuple[
        Optional[_SubjectType],
        Union[Path, URIRef],
        Optional[_ObjectType],
    ],
) -> Iterator[tuple[_SubjectType, _ObjectType, tuple[WitnessStep, ...]]]:
    """
    Evaluate a property path against a graph and yield, for every reachable
    endpoint pair, the pair together with one shortest witness of the match.

    A witness is a tuple of WitnessStep, in traversal order, listing the
    graph triples that were crossed and whether each was followed forwards
    or backwards. Its length is the number of RDF edges crossed, so a
    zero-length match (e.g. from a "*" or "?" modifier) has an empty witness.

    Every reachable endpoint pair is yielded exactly once. When several
    witnesses of the same minimal length exist, an arbitrary one is
    returned. Every step of the witness conforms to the path expression:
    the search is a breadth-first/Dijkstra exploration over expression
    steps, not a plain shortest-path search filtered afterwards, and it
    terminates even on cyclic graphs with expressions that match the empty
    path.

    The subject, or both the subject and the object, may be bound. If only
    the object is bound, the search runs backwards from the object. If
    neither is bound, all pairs are enumerated. Note that, as with
    eval_path(), the graph passed in decides which triples are visible:
    for a Dataset/ConjunctiveGraph, pass the graph view (context) the
    witness should be computed in; no triples are drawn from other
    contexts behind the caller's back.
    """
    subj, path, obj = t
    cache: _WitnessCacheType = {}
    if subj is not None:
        for end, witness in _shortest_from(graph, path, subj, cache).items():
            if obj is None or end == obj:
                yield subj, end, tuple(witness)
    elif obj is not None:
        for start, witness in _shortest_from(
            graph, _reverse_path(path), obj, cache
        ).items():
            yield start, obj, tuple(_flip_witness(witness))
    else:
        seen_starts = set()
        for s, o in graph.subject_objects(None):
            for node in (s, o):
                if node in seen_starts:
                    continue
                seen_starts.add(node)
                for end, witness in _shortest_from(graph, path, node, cache).items():
                    yield node, end, tuple(witness)


def _flip_witness(witness: list[WitnessStep]) -> list[WitnessStep]:
    return [
        WitnessStep(step.subject, step.predicate, step.object, not step.forward)
        for step in reversed(witness)
    ]


def _reverse_path(path: Union[Path, URIRef]) -> Union[Path, URIRef]:
    """
    Return a path expression matching the inverse relation of "path",
    so that witnesses can be searched backwards from a bound object.
    """
    if isinstance(path, URIRef):
        return InvPath(path)
    if isinstance(path, InvPath):
        return path.arg
    if isinstance(path, SequencePath):
        return SequencePath(*[_reverse_path(a) for a in reversed(path.args)])
    if isinstance(path, AlternativePath):
        return AlternativePath(*[_reverse_path(a) for a in path.args])
    if isinstance(path, MulPath):
        return MulPath(_reverse_path(path.path), path.mod)
    if isinstance(path, NegatedPath):
        # a negated property set only follows triples forwards, so its
        # inverse is not itself a negated property set
        return _ReverseNegatedPath(path)
    if isinstance(path, _ReverseNegatedPath):
        return path.arg
    raise Exception("Cannot reverse path: %s" % (path,))


def _negated_path_allows(
    graph: Graph, path: NegatedPath, s: Node, p: Node, o: Node
) -> bool:
    for a in path.args:
        if isinstance(a, URIRef):
            if p == a:
                return False
        elif isinstance(a, InvPath):
            if (o, a.arg, s) in graph:
                return False
        else:
            raise Exception("Invalid path in NegatedPath: %s" % (a,))
    return True


def _shortest_from(
    graph: Graph,
    path: Union[Path, URIRef],
    start: Node,
    cache: _WitnessCacheType,
) -> dict[Node, list[WitnessStep]]:
    """
    Compute, for every node reachable from "start" via "path", one shortest
    witness. Results are memoized in "cache" per (path, start) pair.
    """
    key = (path, start)
    cached = cache.get(key)
    if cached is not None:
        return cached
    result: dict[Node, list[WitnessStep]]
    if isinstance(path, URIRef):
        result = {}
        for s, p, o in graph.triples((start, path, None)):
            result[o] = [WitnessStep(s, p, o, True)]
    elif isinstance(path, NegatedPath):
        result = {}
        for s, p, o in graph.triples((start, None, None)):
            if o not in result and _negated_path_allows(graph, path, s, p, o):
                result[o] = [WitnessStep(s, p, o, True)]
    elif isinstance(path, _ReverseNegatedPath):
        result = {}
        for s, p, o in graph.triples((None, None, start)):
            if s not in result and _negated_path_allows(graph, path.arg, s, p, o):
                result[s] = [WitnessStep(s, p, o, False)]
    elif isinstance(path, InvPath):
        arg = path.arg
        if isinstance(arg, URIRef):
            result = {}
            for s, p, o in graph.triples((None, arg, start)):
                result[s] = [WitnessStep(s, p, o, False)]
        else:
            # walking ^arg from start crosses the same edges, in the same
            # directions, as walking the reversed arg from start
            result = _shortest_from(graph, _reverse_path(arg), start, cache)
    elif isinstance(path, AlternativePath):
        result = {}
        for a in path.args:
            for end, witness in _shortest_from(graph, a, start, cache).items():
                if end not in result or len(witness) < len(result[end]):
                    result[end] = witness
    elif isinstance(path, SequencePath):
        result = dict(_shortest_from(graph, path.args[0], start, cache))
        for sub in path.args[1:]:
            nxt: dict[Node, list[WitnessStep]] = {}
            for mid, first_witness in result.items():
                for end, second_witness in _shortest_from(
                    graph, sub, mid, cache
                ).items():
                    witness = first_witness + second_witness
                    if end not in nxt or len(witness) < len(nxt[end]):
                        nxt[end] = witness
            result = nxt
    elif isinstance(path, MulPath):
        result = _shortest_from_mul(graph, path, start, cache)
    else:
        raise Exception("Cannot compute witnesses for: %s" % (path,))
    cache[key] = result
    return result


def _shortest_from_mul(
    graph: Graph, path: MulPath, start: Node, cache: _WitnessCacheType
) -> dict[Node, list[WitnessStep]]:
    """
    Dijkstra search over applications of the inner path of a ?, * or +
    modified path. Edge weights are the lengths of the inner path's own
    shortest witnesses, so the result is shortest in number of RDF edges
    crossed, not in number of modifier applications.
    """
    dist: dict[Node, int] = {start: 0}
    witnesses: dict[Node, list[WitnessStep]] = {start: []}
    counter = itertools.count()
    heap: list[tuple[int, int, Node]] = [(0, next(counter), start)]
    done: set[Node] = set()
    while heap:
        d, _, node = heapq.heappop(heap)
        if node in done:
            continue
        done.add(node)
        if not path.more and node != start:
            # ZeroOrOne: a single application of the inner path
            continue
        for end, witness in _shortest_from(graph, path.path, node, cache).items():
            if end == start and path.zero:
                # the empty match is always the shortest way back to start
                continue
            nd = d + len(witness)
            cur = dist.get(end)
            if cur is None or nd < cur or (end == start and cur == 0):
                dist[end] = nd
                witnesses[end] = witnesses[node] + witness
                heapq.heappush(heap, (nd, next(counter), end))
    result: dict[Node, list[WitnessStep]] = {}
    for end, d in dist.items():
        if d == 0:
            # only start itself, via the empty match
            if path.zero:
                result[end] = []
        else:
            result[end] = witnesses[end]
    return result


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
