from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Optional

from rdflib.namespace import RDF
from rdflib.term import BNode, Node

if TYPE_CHECKING:
    from rdflib.graph import Graph

__all__ = ["Collection"]


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

    def _validated_chain(self) -> tuple[list[Node], list[Node]]:
        """
        Return the chain nodes and their `rdf:first` values, from the
        collection entry down to the last node before `rdf:nil`.

        Raises `ValueError` if the list is malformed: a missing or
        duplicated `rdf:first`/`rdf:rest` arc, or a cycle.
        """
        graph = self.graph
        if self.uri == RDF.nil:
            raise ValueError("Cannot splice the empty list (rdf:nil)")
        if (self.uri, RDF.first, None) not in graph and (
            self.uri,
            RDF.rest,
            None,
        ) not in graph:
            # An entry without any list arcs denotes the empty list.
            return [], []
        chain: list[Node] = []
        items: list[Node] = []
        seen: set[Node] = {RDF.nil}
        node: Node = self.uri
        while True:
            if node in seen:
                raise ValueError(
                    "Malformed RDF Collection: %s (cycle at %s)" % (self.uri, node)
                )
            seen.add(node)
            firsts = list(graph.objects(node, RDF.first))
            if len(firsts) != 1:
                raise ValueError(
                    "Malformed RDF Collection: %s "
                    "(node %s has %d rdf:first arcs)" % (self.uri, node, len(firsts))
                )
            rests = list(graph.objects(node, RDF.rest))
            if len(rests) != 1:
                raise ValueError(
                    "Malformed RDF Collection: %s "
                    "(node %s has %d rdf:rest arcs)" % (self.uri, node, len(rests))
                )
            chain.append(node)
            items.append(firsts[0])
            node = rests[0]
            if node == RDF.nil:
                return chain, items

    def splice(
        self,
        start: int,
        delete_count: int = 0,
        values: Iterable[Node] = (),
        *,
        detach: bool = False,
    ) -> list[Node]:
        """
        Remove `delete_count` items starting at index `start` and insert
        `values` in their place, returning the removed items.  This is the
        RDF-list analogue of JavaScript's `Array.prototype.splice`.

        The whole chain is validated before anything is changed: a missing
        or duplicated `rdf:first`/`rdf:rest` arc, a cycle, or an
        out-of-range `start`/`delete_count` raises and leaves the graph
        untouched.

        List tails may be shared between collections.  A chain node is
        *shared* when some other list can reach it, i.e. when a subject
        other than its predecessor in this collection points with
        `rdf:rest` at the node itself or at any node before it.  By
        default, splicing refuses to rewrite or remove shared nodes and
        raises `ValueError`.
        With `detach=True` only the minimal required segment of the chain
        is copied instead, so the other list keeps pointing at the original
        chain:

        * shared nodes that would be removed are simply left in place for
          the other list; this collection stops referencing them;
        * a shared node whose `rdf:rest` must be redirected is copied,
          along with every shared node between it and this collection's
          entry, and the copies are spliced into this chain;
        * the collection entry itself is never copied or removed: it is
          rewritten in place and keeps its identity, so external references
          to it keep designating this (edited) list.  If the entry itself
          is shared and would have to be rewritten, splicing fails even
          with `detach=True`.

        Items are resources of the graph, not part of the chain: they are
        never copied or removed, only `rdf:first` arcs change.
        Non-`rdf:first`/`rdf:rest` annotations on chain nodes are
        preserved on the entry, duplicated onto copied nodes (the originals
        keep theirs), and removed together with any chain node that is
        deleted.

        ```python
        >>> from rdflib import Graph, Literal
        >>> g = Graph()
        >>> c = Collection(g, BNode(), [Literal(1), Literal(2), Literal(3)])
        >>> removed = c.splice(1, 1, [Literal(20), Literal(21)])
        >>> [v.toPython() for v in removed]
        [2]
        >>> [v.toPython() for v in c]
        [1, 20, 21, 3]

        ```

        Splicing a list whose tail is shared with another list refuses to
        rewrite the shared chain nodes by default, while `detach=True`
        leaves the other list untouched:

        ```python
        >>> g = Graph()
        >>> a = Collection(
        ...     g, BNode(), [Literal(1), Literal(2), Literal(3), Literal(4)])
        >>> shared = a._get_container(2)
        >>> b = Collection(g, BNode(), [Literal(9)])
        >>> g.set((b._end(), RDF.rest, shared)) # doctest: +ELLIPSIS
        <Graph identifier=... (<class 'rdflib.graph.Graph'>)>
        >>> [v.toPython() for v in b]
        [9, 3, 4]
        >>> a.splice(2, 1, [Literal(30)]) # doctest: +ELLIPSIS
        Traceback (most recent call last):
        ...
        ValueError: Cannot splice ... shared chain node(s) ...
        >>> removed = a.splice(2, 1, [Literal(30)], detach=True)
        >>> [v.toPython() for v in removed]
        [3]
        >>> [v.toPython() for v in a]
        [1, 2, 30, 4]
        >>> [v.toPython() for v in b]
        [9, 3, 4]

        ```
        """
        graph = self.graph
        new_values = list(values)
        chain, items = self._validated_chain()
        length = len(chain)

        if start < 0 or start > length:
            raise IndexError(
                "splice start %d out of range for collection of length %d"
                % (start, length)
            )
        if delete_count < 0:
            raise ValueError("delete_count must be >= 0")
        if start + delete_count > length:
            raise IndexError(
                "splice of %d items at %d exceeds collection of length %d"
                % (delete_count, start, length)
            )

        removed_items = items[start : start + delete_count]
        if delete_count == 0 and not new_values:
            return []

        if length == 0:
            # Splicing values into an empty list; the bare entry is reused.
            if next(graph.subjects(RDF.rest, self.uri), None) is not None:
                raise ValueError(
                    "Cannot splice %s: the collection entry is shared and "
                    "entry nodes are never copied" % (self.uri,)
                )
            graph.add((self.uri, RDF.first, new_values[0]))
            previous = self.uri
            for value in new_values[1:]:
                node = BNode()
                graph.add((node, RDF.first, value))
                graph.add((previous, RDF.rest, node))
                previous = node
            graph.add((previous, RDF.rest, RDF.nil))
            return []

        # A node is shared if another list can reach it: some subject other
        # than its predecessor in this chain rests into it or into any node
        # before it, so sharing propagates down to the end of the chain.
        shared: list[bool] = []
        ancestor_shared = False
        for index, chain_node in enumerate(chain):
            predecessor = chain[index - 1] if index > 0 else None
            if not ancestor_shared:
                ancestor_shared = any(
                    subject != predecessor
                    for subject in graph.subjects(RDF.rest, chain_node)
                )
            shared.append(ancestor_shared)

        def is_shared(index: int) -> bool:
            return shared[index]

        # Chain nodes the splice would rewrite in place or remove.
        if start == 0:
            rewritten = [0]
            if not new_values and delete_count < length:
                # The entry absorbs the first surviving node.
                removed = list(range(1, delete_count + 1))
            else:
                removed = list(range(1, delete_count))
        else:
            rewritten = [start - 1]
            removed = list(range(start, start + delete_count))

        shared_removed = [index for index in removed if is_shared(index)]

        if any(is_shared(index) for index in rewritten) or shared_removed:
            if not detach:
                raise ValueError(
                    "Cannot splice %s: shared chain node(s) would be "
                    "rewritten or removed; pass detach=True to detach this "
                    "list from the shared chain segment" % (self.uri,)
                )
            if start == 0 and is_shared(0):
                raise ValueError(
                    "Cannot splice %s: the collection entry is shared and "
                    "entry nodes are never copied" % (self.uri,)
                )

        link: Optional[Node] = None
        if start > 0:
            if detach and is_shared(start - 1):
                # The node whose rdf:rest must be redirected is shared:
                # copy it and every shared node up to the first private
                # ancestor, which is then repointed at the copies.
                private = start - 1
                while private >= 0 and is_shared(private):
                    private -= 1
                if private < 0:
                    raise ValueError(
                        "Cannot splice %s: the shared chain segment reaches "
                        "the collection entry and entry nodes are never "
                        "copied" % (self.uri,)
                    )
                copies: list[Node] = []
                for index in range(private + 1, start):
                    source = chain[index]
                    copy = BNode()
                    for predicate, obj in graph.predicate_objects(source):
                        if predicate not in (RDF.first, RDF.rest):
                            graph.add((copy, predicate, obj))
                    graph.add((copy, RDF.first, items[index]))
                    copies.append(copy)
                for left, right in zip(copies, copies[1:]):
                    graph.add((left, RDF.rest, right))
                graph.set((chain[private], RDF.rest, copies[0]))
                link = copies[-1]
            else:
                link = chain[start - 1]

        leave = set(shared_removed)
        if start > 0:
            assert link is not None
            successor = (
                chain[start + delete_count]
                if start + delete_count < length
                else RDF.nil
            )
            previous = link
            for value in new_values:
                node = BNode()
                graph.add((node, RDF.first, value))
                graph.set((previous, RDF.rest, node))
                previous = node
            graph.set((previous, RDF.rest, successor))
        else:
            entry = chain[0]
            if not new_values and delete_count == length:
                # Clearing the list; the entry survives as a bare node.
                graph.remove((entry, RDF.first, None))
                graph.remove((entry, RDF.rest, None))
            elif not new_values:
                # The entry absorbs the first surviving node.
                survivor = chain[delete_count]
                graph.set((entry, RDF.first, items[delete_count]))
                graph.set((entry, RDF.rest, next(graph.objects(survivor, RDF.rest))))
            else:
                graph.set((entry, RDF.first, new_values[0]))
                if delete_count == 0:
                    # The old head value needs a new home.
                    successor = BNode()
                    graph.add((successor, RDF.first, items[0]))
                    graph.add(
                        (successor, RDF.rest, chain[1] if length > 1 else RDF.nil)
                    )
                else:
                    successor = (
                        chain[delete_count] if delete_count < length else RDF.nil
                    )
                previous = entry
                for value in new_values[1:]:
                    node = BNode()
                    graph.add((node, RDF.first, value))
                    graph.set((previous, RDF.rest, node))
                    previous = node
                graph.set((previous, RDF.rest, successor))

        for index in removed:
            if index in leave:
                # Shared nodes stay in place for the other list.
                continue
            graph.remove((chain[index], None, None))
        return removed_items

    def clear(self):
        container: Optional[Node] = self.uri
        graph = self.graph
        while container:
            rest = graph.value(container, RDF.rest)
            graph.remove((container, RDF.first, None))
            graph.remove((container, RDF.rest, None))
            container = rest
        return self
