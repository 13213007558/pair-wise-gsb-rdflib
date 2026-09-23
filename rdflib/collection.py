from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Optional

from rdflib.namespace import RDF
from rdflib.term import BNode, Node

if TYPE_CHECKING:
    from rdflib.graph import Graph

__all__ = ["Collection", "MalformedCollectionError", "splice"]


class MalformedCollectionError(ValueError):
    """
    Raised when an RDF collection (an ``rdf:List`` chain) is structurally
    invalid, for example when a chain node is missing its ``rdf:first`` or
    ``rdf:rest`` arc, has more than one of either, when the chain contains
    a cycle, or when a chain node is not a list node at all.

    The offending node is available as ``node`` and a human readable
    explanation as ``reason``.
    """

    def __init__(self, node: Node, reason: str):
        self.node: Node = node
        self.reason: str = reason
        super().__init__(f"Malformed RDF collection at node {node!r}: {reason}")


class Collection:
    """See "Emulating container types": <https://docs.python.org/reference/datamodel.html#emulating-container-types>

    ```python
    >>> from rdflib.term import Literal
    >>> from rdflib.graph import Graph
    >>> from pprint import pprint
    >>> listname = BNode()
    >>> g = Graph('Memory')
    >>> listItem1 = BNode()
    >>> listItem2 = BNode()
    >>> g.add((listname, RDF.first, Literal(1))) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listname, RDF.rest, listItem1)) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listItem1, RDF.first, Literal(2))) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listItem1, RDF.rest, listItem2)) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listItem2, RDF.rest, RDF.nil)) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> g.add((listItem2, RDF.first, Literal(3))) # doctest: +ELLIPSIS
    <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
    >>> c = Collection(g,listname)
    >>> pprint([term.n3() for term in c])
    ['"1"^^<http://www.w3.org/2001/XMLSchema#integer>',
     '"2"^^<http://www.w3.org/2001/XMLSchema#integer>',
     '"3"^^<http://www.w3.org/2001/XMLSchema#integer>']
    >>> Literal(1) in c
    True
    >>> len(c)
    3
    >>> c._get_container(1) == listItem1
    True
    >>> c.index(Literal(2)) == 1
    True

    ```

    The collection is immutable if `uri` is the empty list (`http://www.w3.org/1999/02/22-rdf-syntax-ns#nil`).
    """

    def __init__(self, graph: Graph, uri: Node, seq: list[Node] = []):
        self.graph = graph
        self.uri = uri or BNode()
        if seq:
            self += seq

    def n3(self) -> str:
        """
        ```python
        >>> from rdflib.term import Literal
        >>> from rdflib.graph import Graph
        >>> listname = BNode()
        >>> g = Graph('Memory')
        >>> listItem1 = BNode()
        >>> listItem2 = BNode()
        >>> g.add((listname, RDF.first, Literal(1))) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listname, RDF.rest, listItem1)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listItem1, RDF.first, Literal(2))) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listItem1, RDF.rest, listItem2)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listItem2, RDF.rest, RDF.nil)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((listItem2, RDF.first, Literal(3))) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> c = Collection(g, listname)
        >>> print(c.n3()) #doctest: +NORMALIZE_WHITESPACE
        ( "1"^^<http://www.w3.org/2001/XMLSchema#integer>
            "2"^^<http://www.w3.org/2001/XMLSchema#integer>
            "3"^^<http://www.w3.org/2001/XMLSchema#integer> )

        ```
        """
        return "( %s )" % (" ".join([i.n3() for i in self]))

    def _get_container(self, index: int) -> Optional[Node]:
        """Gets the first, rest holding node at index."""
        assert isinstance(index, int)
        graph = self.graph
        container: Optional[Node] = self.uri
        i = 0
        while i < index:
            i += 1
            container = graph.value(container, RDF.rest)
            if container is None:
                break
        return container

    def __len__(self) -> int:
        """length of items in collection."""
        return len(list(self.graph.items(self.uri)))

    def index(self, item: Node) -> int:
        """
        Returns the 0-based numerical index of the item in the list
        """
        listname = self.uri
        index = 0
        while True:
            if (listname, RDF.first, item) in self.graph:
                return index
            else:
                newlink = list(self.graph.objects(listname, RDF.rest))
                index += 1
                if newlink == [RDF.nil]:
                    raise ValueError("%s is not in %s" % (item, self.uri))
                elif not newlink:
                    raise Exception("Malformed RDF Collection: %s" % self.uri)
                else:
                    assert len(newlink) == 1, "Malformed RDF Collection: %s" % self.uri
                    listname = newlink[0]

    def __getitem__(self, key: int) -> Node:
        """TODO"""
        c = self._get_container(key)
        if c:
            v = self.graph.value(c, RDF.first)
            if v:
                return v
            else:
                raise KeyError(key)
        else:
            raise IndexError(key)

    def __setitem__(self, key: int, value: Node) -> None:
        """TODO"""
        c = self._get_container(key)
        if c:
            self.graph.set((c, RDF.first, value))
        else:
            raise IndexError(key)

    def __delitem__(self, key: int) -> None:
        """
        ```python
        >>> from rdflib.namespace import RDF, RDFS
        >>> from rdflib import Graph
        >>> from pprint import pformat
        >>> g = Graph()
        >>> a = BNode('foo')
        >>> b = BNode('bar')
        >>> c = BNode('baz')
        >>> g.add((a, RDF.first, RDF.type)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((a, RDF.rest, b)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((b, RDF.first, RDFS.label)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((b, RDF.rest, c)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((c, RDF.first, RDFS.comment)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> g.add((c, RDF.rest, RDF.nil)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> len(g)
        6
        >>> def listAncestry(node, graph):
        ...   for i in graph.subjects(RDF.rest, node):
        ...     yield i
        >>> [str(node.n3())
        ...   for node in g.transitiveClosure(listAncestry, RDF.nil)]
        ['_:baz', '_:bar', '_:foo']
        >>> lst = Collection(g, a)
        >>> len(lst)
        3
        >>> b == lst._get_container(1)
        True
        >>> c == lst._get_container(2)
        True
        >>> del lst[1]
        >>> len(lst)
        2
        >>> len(g)
        4

        ```
        """
        self[key]  # to raise any potential key exceptions
        graph = self.graph
        current = self._get_container(key)
        assert current
        if len(self) == 1 and key > 0:
            pass
        elif key == len(self) - 1:
            # the tail
            priorlink = self._get_container(key - 1)
            # type error: Argument 1 to "set" of "Graph" has incompatible type "tuple[Optional[Node], URIRef, URIRef]"; expected "tuple[Node, Node, Any]"
            self.graph.set((priorlink, RDF.rest, RDF.nil))  # type: ignore[arg-type]
            graph.remove((current, None, None))
        else:
            next = self._get_container(key + 1)
            prior = self._get_container(key - 1)
            assert next and prior
            graph.remove((current, None, None))
            graph.set((prior, RDF.rest, next))

    def __iter__(self) -> Iterator[Node]:
        """Iterator over items in Collections"""
        return self.graph.items(self.uri)

    def _end(self) -> Node:
        # find end of list
        container = self.uri
        while True:
            rest = self.graph.value(container, RDF.rest)
            if rest is None or rest == RDF.nil:
                return container
            else:
                container = rest

    def append(self, item: Node) -> Collection:
        """
        ```python
        >>> from rdflib.term import Literal
        >>> from rdflib.graph import Graph
        >>> listname = BNode()
        >>> g = Graph()
        >>> c = Collection(g,listname,[Literal(1),Literal(2)])
        >>> links = [
        ...     list(g.subjects(object=i, predicate=RDF.first))[0] for i in c]
        >>> len([i for i in links if (i, RDF.rest, RDF.nil) in g])
        1

        ```
        """
        end = self._end()
        if end == RDF.nil:
            raise ValueError("Cannot append to empty list")

        if (end, RDF.first, None) in self.graph:
            # append new node to the end of the linked list
            node = BNode()
            self.graph.set((end, RDF.rest, node))
            end = node

        self.graph.add((end, RDF.first, item))
        self.graph.add((end, RDF.rest, RDF.nil))
        return self

    def __iadd__(self, other: Iterable[Node]):
        end = self._end()
        if end == RDF.nil:
            raise ValueError("Cannot append to empty list")
        self.graph.remove((end, RDF.rest, None))

        for item in other:
            if (end, RDF.first, None) in self.graph:
                nxt = BNode()
                self.graph.add((end, RDF.rest, nxt))
                end = nxt

            self.graph.add((end, RDF.first, item))

        self.graph.add((end, RDF.rest, RDF.nil))
        return self

    def clear(self):
        container: Optional[Node] = self.uri
        graph = self.graph
        while container:
            rest = graph.value(container, RDF.rest)
            graph.remove((container, RDF.first, None))
            graph.remove((container, RDF.rest, None))
            container = rest
        return self


def _unique_predicate_object(graph: Graph, node: Node, predicate: Node) -> Node:
    """
    Return the single object of ``(node, predicate, ...)`` in ``graph``,
    raising ``MalformedCollectionError`` naming ``node`` if there is not
    exactly one.
    """
    objects = list(graph.objects(node, predicate))
    if not objects:
        raise MalformedCollectionError(node, f"missing {predicate.n3()} arc")
    if len(objects) > 1:
        raise MalformedCollectionError(
            node, f"multiple {predicate.n3()} arcs ({len(objects)} found)"
        )
    return objects[0]


def _normalize_slice_index(index: int, length: int) -> int:
    """
    Normalize ``index`` the way Python sequence slicing does: negative
    indices count back from the end, and the result is clamped to
    ``[0, length]``.
    """
    if index < 0:
        index += length
        if index < 0:
            index = 0
    elif index > length:
        index = length
    return index


def splice(
    graph: Graph,
    head: Node,
    start: int,
    stop: int,
    values: Iterable[Node] = (),
) -> Node:
    """
    Splice an RDF collection without modifying any existing list structure.

    Returns the head of a new list that is a copy of the list headed by
    ``head`` with the half-open interval ``[start, stop)`` replaced by
    ``values``.  The original chain is left completely untouched, so any
    other list that shares nodes with it (for example a common ``rdf:rest``
    tail) keeps working unchanged; it is up to the caller to decide whether
    external references to ``head`` should be updated to the returned head.

    Only the chain nodes that must change are newly allocated blank nodes:
    the nodes before ``start`` (their ``rdf:rest`` pointers have to be
    redirected) and one node per inserted value.  The untouched tail starting
    at ``stop`` is shared with the original list, not copied.

    ``start`` and ``stop`` follow Python's slice rules: negative
    indices count back from the end of the list, indices beyond the list are
    clamped to its length, indices below ``-len(list)`` are clamped to
    ``0``, and if ``stop`` ends up before ``start`` it is treated
    as equal to ``start`` (a pure insertion at ``start``).  Some
    consequences:

    - ``splice(graph, head, i, i, values)`` inserts ``values`` before
      index ``i``; ``i = 0`` inserts at the head and ``i = len``
      (or any larger value) appends at the tail.
    - ``splice(graph, head, start, stop)`` with no ``values`` deletes
      the interval.
    - Splicing into ``RDF.nil`` (the empty list) inserts ``values``.
    - Deleting everything yields ``RDF.nil`` as the new head.

    ``values`` may contain any RDF term, including duplicate values and
    blank nodes.

    Before anything is added to ``graph``, the whole chain reachable from
    ``head`` is validated: every chain node must have exactly one
    ``rdf:first`` and exactly one ``rdf:rest`` arc, the chain must end
    at ``RDF.nil``, and it must not contain a cycle.  Any violation raises
    ``MalformedCollectionError`` identifying the offending node, and the
    graph is left exactly as it was found - no half-built new chain is left
    behind.

    Only ``graph`` is ever read or written; if ``graph`` is one context
    of a ``Dataset``, same-named nodes in the dataset's other graphs are
    neither consulted nor modified.

    ```python
    >>> from rdflib import Graph, Literal
    >>> from rdflib.collection import Collection, splice
    >>> g = Graph()
    >>> head = BNode()
    >>> Collection(g, head, [Literal(1), Literal(2), Literal(3)])
    <rdflib.collection.Collection object at ...>
    >>> new_head = splice(g, head, 1, 2, [Literal(20), Literal(21)])
    >>> list(Collection(g, new_head)) == [Literal(1), Literal(20), Literal(21), Literal(3)]
    True
    >>> list(Collection(g, head)) == [Literal(1), Literal(2), Literal(3)]  # unchanged
    True

    ```
    """
    values = list(values)

    # Traverse and validate the whole chain before touching the graph, so a
    # malformed list cannot leave a half-built new chain behind.
    nodes: list[Node] = []
    firsts: list[Node] = []
    seen: set[Node] = set()
    node: Node = head
    while node != RDF.nil:
        if node in seen:
            raise MalformedCollectionError(
                node, "cycle detected: node is reachable from itself via rdf:rest"
            )
        seen.add(node)
        firsts.append(_unique_predicate_object(graph, node, RDF.first))
        rest = _unique_predicate_object(graph, node, RDF.rest)
        nodes.append(node)
        node = rest

    length = len(nodes)
    start = _normalize_slice_index(start, length)
    stop = _normalize_slice_index(stop, length)
    if stop < start:
        stop = start

    # The tail starting at "stop" is unaffected and shared with the original
    # list; everything before it is rebuilt with new blank nodes.
    suffix_head: Node = nodes[stop] if stop < length else RDF.nil
    new_nodes: list[Node] = [BNode() for _ in range(start + len(values))]
    new_firsts: list[Node] = firsts[:start] + values

    chain: list[Node] = new_nodes + [suffix_head]
    for current, first, rest in zip(new_nodes, new_firsts, chain[1:]):
        graph.add((current, RDF.first, first))
        graph.add((current, RDF.rest, rest))

    return new_nodes[0] if new_nodes else suffix_head
