from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Optional

from rdflib.namespace import RDF
from rdflib.term import BNode, Node

if TYPE_CHECKING:
    from rdflib.graph import Graph

__all__ = ["Collection", "MalformedListError"]


class MalformedListError(ValueError):
    """Raised when an RDF list chain is structurally invalid.

    The offending node is available as :attr:`node`.
    """

    def __init__(self, node: Node, message: str):
        self.node = node
        super().__init__(f"Malformed RDF list at node {node.n3()}: {message}")


def _list_nodes(graph: Graph, head: Node) -> list[tuple[Node, Node, Node]]:
    """Walk the list chain rooted at "head" and return a "(node, first,
    rest)" tuple per cell, validating the structure along the way.

    Raises MalformedListError naming the offending node if a cell is
    missing or has multiple rdf:first/rdf:rest triples, if the chain
    contains a cycle, or if the chain ends in a node that is not rdf:nil
    and not a list cell.
    """
    nodes: list[tuple[Node, Node, Node]] = []
    seen: set[Node] = set()
    current = head
    while current != RDF.nil:
        if current in seen:
            raise MalformedListError(
                current, "cycle detected: rdf:rest chain loops back to this node"
            )
        seen.add(current)
        firsts = list(graph.objects(current, RDF.first))
        rests = list(graph.objects(current, RDF.rest))
        if not firsts and not rests:
            raise MalformedListError(
                current, "not an RDF list node (no rdf:first or rdf:rest)"
            )
        if len(firsts) != 1:
            raise MalformedListError(
                current, f"expected exactly one rdf:first, found {len(firsts)}"
            )
        if len(rests) != 1:
            raise MalformedListError(
                current, f"expected exactly one rdf:rest, found {len(rests)}"
            )
        nodes.append((current, firsts[0], rests[0]))
        current = rests[0]
    return nodes


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

    def splice(
        self, start: int, stop: int, replacement: Iterable[Node] = ()
    ) -> Node:
        """Return the head of a new list in which the half-open range
        "[start, stop)" of this list is replaced by the items in
        "replacement", without modifying the existing chain.

        The original list (and any other list sharing parts of its
        "rdf:rest" chain) is left completely intact: no existing triple
        is removed or changed.  Cells in the unaffected tail (everything
        from index "stop" onwards) are shared with the new list, so
        structural sharing with other lists is preserved; only the cells
        that must change (the replaced range and the prefix leading up to
        it) are copied to freshly allocated blank nodes.  The caller
        decides whether to point any external references at the returned
        head.

        "start" and "stop" follow Python slice semantics, exactly as
        "slice(start, stop).indices(len(self))":

        * negative values count back from the end of the list
          ("-1" is the last item);
        * values beyond either end are clamped to "[0, len(self)]";
        * if "stop < start" after normalization the range is empty and
          the operation is a pure insertion at "start".

        Deleting is splicing with an empty "replacement"; inserting is
        splicing with "start == stop" (including "0, 0" for the head,
        "len(self), len(self)" for the tail, and on the empty list
        "RDF.nil").  Replacing the whole list with nothing returns
        "RDF.nil".  Items in "replacement" may be any RDF term,
        including duplicate values and blank nodes.

        The whole chain is validated before anything is added: a
        MalformedListError naming the offending node is raised for
        missing or multiple rdf:first/rdf:rest triples, cycles, or a
        tail node that is not rdf:nil, and a failed call leaves the
        graph untouched.  Only the graph this collection is bound to is
        read or modified; same-named nodes in other graphs of a dataset
        are not affected.

        ```python
        >>> from rdflib.term import Literal
        >>> from rdflib.graph import Graph
        >>> g = Graph()
        >>> head = BNode()
        >>> c = Collection(g, head, [Literal(1), Literal(2), Literal(3)])
        >>> new_head = c.splice(1, 2, [Literal(20), Literal(21)])
        >>> [v.toPython() for v in Collection(g, new_head)]
        [1, 20, 21, 3]
        >>> [v.toPython() for v in c]  # the original list is untouched
        [1, 2, 3]
        >>> c.splice(0, 3) is RDF.nil  # deleting everything yields nil
        True

        ```
        """
        graph = self.graph
        items = list(replacement)
        # Validate the whole chain before adding anything, so a malformed
        # list cannot leave a half-built new chain behind.
        nodes = _list_nodes(graph, self.uri)
        length = len(nodes)
        start, stop, _ = slice(start, stop).indices(length)
        if stop < start:
            stop = start
        if start == stop and not items:
            return self.uri

        entries = [first for _, first, _ in nodes[:start]] + items
        # The unaffected tail is shared with the existing chain.
        tail = nodes[stop][0] if stop < length else RDF.nil
        if not entries:
            return tail

        new_nodes = [BNode() for _ in entries]
        for position, (node, item) in enumerate(zip(new_nodes, entries)):
            graph.add((node, RDF.first, item))
            graph.add(
                (
                    node,
                    RDF.rest,
                    new_nodes[position + 1] if position + 1 < len(new_nodes) else tail,
                )
            )
        return new_nodes[0]
