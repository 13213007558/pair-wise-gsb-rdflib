from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Optional

from rdflib.namespace import RDF
from rdflib.term import BNode, Literal, Node

if TYPE_CHECKING:
    from rdflib.graph import Graph

__all__ = ["Collection", "CollectionError"]


class CollectionError(ValueError):
    """Raised when an RDF Collection is malformed or when an edit would
    rewrite chain nodes that are shared with another collection."""


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

    def _validated_chain(self) -> tuple[list[Node], list[Node]]:
        """
        Walk the chain from the entry node and return the chain nodes and
        their ``rdf:first`` values.

        Raises :class:`CollectionError` if the chain is malformed: a node
        with a missing or repeated ``rdf:first``/``rdf:rest``, or a cycle.
        An entry node without any ``rdf:first``/``rdf:rest`` triples heads
        the empty list.
        """
        graph = self.graph
        cells: list[Node] = []
        firsts: list[Node] = []
        seen: set[Node] = set()
        cell: Node = self.uri
        while cell != RDF.nil:
            if cell in seen:
                raise CollectionError(
                    "Malformed RDF Collection: cycle detected at %s" % cell.n3()
                )
            seen.add(cell)
            first_values = list(graph.objects(cell, RDF.first))
            rest_values = list(graph.objects(cell, RDF.rest))
            if not cells and not first_values and not rest_values:
                # The entry node holds no chain triples at all: the empty list.
                break
            if len(first_values) != 1:
                raise CollectionError(
                    "Malformed RDF Collection: %s has %d rdf:first values"
                    % (cell.n3(), len(first_values))
                )
            if len(rest_values) != 1:
                raise CollectionError(
                    "Malformed RDF Collection: %s has %d rdf:rest values"
                    % (cell.n3(), len(rest_values))
                )
            cells.append(cell)
            firsts.append(first_values[0])
            cell = rest_values[0]
        return cells, firsts

    def _shared_flags(self, cells: list[Node]) -> list[bool]:
        """
        Flag each chain node that is shared with the world outside this
        collection.

        A node is shared if anything other than its chain predecessor points
        at it (another list's ``rdf:rest``, or any external reference), or if
        it sits behind an already shared node, since it is then reachable
        through that node's ``rdf:rest``.
        """
        graph = self.graph
        shared: list[bool] = []
        previous: Optional[Node] = None
        behind_shared = False
        for cell in cells:
            if previous is None:
                # Ordinary references to the list head are fine; the entry
                # node is only shared if another list's rdf:rest points at it.
                direct = (None, RDF.rest, cell) in graph
            else:
                direct = any(
                    (subject, predicate) != (previous, RDF.rest)
                    for subject, predicate in graph.subject_predicates(cell)
                )
            behind_shared = behind_shared or direct
            shared.append(behind_shared)
            previous = cell
        return shared

    def _new_cell(self, first: Node) -> Node:
        cell = BNode()
        self.graph.add((cell, RDF.first, first))
        return cell

    def _ghost_cell(self, first: Node, rest: Node) -> Node:
        cell = BNode()
        self.graph.add((cell, RDF.first, first))
        self.graph.add((cell, RDF.rest, rest))
        return cell

    def _copy_cell(self, cell: Node) -> Node:
        """Copy a chain node, replicating its ``rdf:first`` and any
        non-first/rest annotations; the ``rdf:rest`` is wired up later."""
        clone = BNode()
        for predicate, obj in self.graph.predicate_objects(cell):
            if predicate != RDF.rest:
                self.graph.add((clone, predicate, obj))
        return clone

    def _link_cells(self, anchor: Node, segment: list[Node], tail: Node) -> None:
        """Point ``anchor`` at ``segment`` and chain it through to ``tail``."""
        graph = self.graph
        graph.set((anchor, RDF.rest, segment[0] if segment else tail))
        for index, cell in enumerate(segment):
            nxt = segment[index + 1] if index + 1 < len(segment) else tail
            graph.set((cell, RDF.rest, nxt))

    def splice(
        self,
        start: int,
        delete_count: int = 0,
        values: Iterable[Node] = (),
        *,
        detach: bool = False,
    ) -> list[Node]:
        """
        Splice the collection like JavaScript's ``Array.prototype.splice``:
        delete ``delete_count`` items starting at index ``start``, insert
        ``values`` in their place, and return the deleted items.

        The whole chain is validated before anything is modified, so a
        malformed list (a missing or repeated ``rdf:first``/``rdf:rest``, a
        cycle) or an out-of-range ``start``/``delete_count`` raises without
        leaving a half-applied edit behind.

        Chain nodes are often shared between collections (one list's tail
        being another list's suffix). By default, if the edit would rewrite
        or remove a shared chain node, :class:`CollectionError` is raised
        and the graph is left untouched. With ``detach=True`` only the chain
        nodes from this collection's entry up to the necessary fork point
        are copied, so the other collection keeps pointing at the original
        tail.

        Preservation rules:

        * The entry resource (``self.uri``) is never replaced; the edit
          always keeps the same entry node, so references to it stay valid.
          If the entry node itself is the tail of another list, splicing
          it is rejected even with ``detach=True``.
        * Inserted values are referenced as-is; the resources in the graph
          are never copied.
        * Non-first/rest annotations on the entry node always stay on the
          entry node. Annotations on a chain node copied during a detach
          are replicated on the copy. Annotations on removed nodes are
          removed along with them; annotations on shared nodes are never
          touched.

        ```python
        >>> from rdflib.term import Literal
        >>> from rdflib.graph import Graph
        >>> from rdflib.namespace import RDF
        >>> g = Graph()
        >>> s0, s1 = BNode(), BNode()
        >>> _ = g.add((s0, RDF.first, Literal(3)))
        >>> _ = g.add((s0, RDF.rest, s1))
        >>> _ = g.add((s1, RDF.first, Literal(4)))
        >>> _ = g.add((s1, RDF.rest, RDF.nil))
        >>> a = Collection(g, BNode(), [Literal(1), Literal(2)])
        >>> _ = g.set((a._end(), RDF.rest, s0))
        >>> b = Collection(g, BNode(), [Literal(9)])
        >>> _ = g.set((b._end(), RDF.rest, s0))
        >>> [int(v) for v in a]
        [1, 2, 3, 4]
        >>> [int(v) for v in b]
        [9, 3, 4]
        >>> a.splice(3, 1)  # doctest: +ELLIPSIS
        Traceback (most recent call last):
        ...
        rdflib.collection.CollectionError: Refusing to splice ...
        >>> deleted = a.splice(3, 1, [Literal(7)], detach=True)
        >>> [int(v) for v in deleted]
        [4]
        >>> [int(v) for v in a]
        [1, 2, 3, 7]
        >>> [int(v) for v in b]
        [9, 3, 4]

        ```
        """
        graph = self.graph
        if self.uri == RDF.nil:
            raise ValueError("Cannot splice the empty list (rdf:nil)")
        new_values = list(values)
        cells, firsts = self._validated_chain()
        size = len(cells)
        if start < 0 or start > size:
            raise IndexError(
                "splice start %r out of range for collection of length %d"
                % (start, size)
            )
        if delete_count < 0 or start + delete_count > size:
            raise IndexError(
                "splice delete_count %r out of range for collection of length %d"
                % (delete_count, size)
            )
        deleted = firsts[start : start + delete_count]
        if delete_count == 0 and not new_values:
            return []
        if size == 0:
            # The empty list: only start == 0, delete_count == 0 gets here.
            if isinstance(self.uri, Literal):
                raise CollectionError(
                    "Cannot splice %s: a literal cannot head a chain" % self.uri.n3()
                )
            segment = [self._new_cell(value) for value in new_values[1:]]
            graph.add((self.uri, RDF.first, new_values[0]))
            self._link_cells(self.uri, segment, RDF.nil)
            return []

        shared = self._shared_flags(cells)
        fork_index = start + delete_count

        def _shared_cell_error(cell: Node) -> CollectionError:
            return CollectionError(
                "Refusing to splice %s: chain node %s is shared with another "
                "collection; pass detach=True to copy the shared prefix and "
                "leave the other collection's tail untouched"
                % (self.uri.n3(), cell.n3())
            )

        if start > 0:
            anchor_index = start - 1
            while anchor_index > 0 and shared[anchor_index]:
                anchor_index -= 1
            if shared[anchor_index]:
                raise CollectionError(
                    "Cannot splice %s: the entry node is the tail of another "
                    "list and cannot be detached or rewritten in place" % self.uri.n3()
                )
            if not detach and anchor_index != start - 1:
                raise _shared_cell_error(cells[start - 1])
            removed = range(start, fork_index)
        else:
            if shared[0]:
                raise CollectionError(
                    "Cannot splice %s: the entry node is the tail of another "
                    "list and cannot be detached or rewritten in place" % self.uri.n3()
                )
            if delete_count == size or new_values:
                removed = range(1, delete_count)
            elif shared[fork_index]:
                if not detach:
                    raise _shared_cell_error(cells[fork_index])
                removed = range(1, delete_count)
            else:
                # The fork node is consumed: its content moves to the entry.
                removed = range(1, delete_count + 1)
        if not detach:
            for i in removed:
                if shared[i]:
                    raise _shared_cell_error(cells[i])

        if start > 0:
            segment = [
                self._copy_cell(cells[i]) for i in range(anchor_index + 1, start)
            ]
            segment.extend(self._new_cell(value) for value in new_values)
            tail = cells[fork_index] if fork_index < size else RDF.nil
            self._link_cells(cells[anchor_index], segment, tail)
        elif new_values:
            entry = cells[0]
            if delete_count == 0:
                # The old head content moves into a fresh cell behind the
                # inserted values; the entry's annotations stay on the entry.
                tail = self._ghost_cell(firsts[0], cells[1] if size > 1 else RDF.nil)
            else:
                tail = cells[fork_index] if fork_index < size else RDF.nil
            segment = [self._new_cell(value) for value in new_values[1:]]
            graph.set((entry, RDF.first, new_values[0]))
            self._link_cells(entry, segment, tail)
        elif delete_count == size:
            entry = cells[0]
            graph.remove((entry, RDF.first, None))
            graph.remove((entry, RDF.rest, None))
        else:
            entry = cells[0]
            graph.set((entry, RDF.first, firsts[fork_index]))
            graph.set(
                (
                    entry,
                    RDF.rest,
                    cells[fork_index + 1] if fork_index + 1 < size else RDF.nil,
                )
            )
        for i in removed:
            if not shared[i]:
                graph.remove((cells[i], None, None))
        return deleted
