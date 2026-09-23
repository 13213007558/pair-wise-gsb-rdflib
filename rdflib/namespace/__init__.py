"""
# Namespace Utilities

RDFLib provides mechanisms for managing Namespaces.

In particular, there is a [`Namespace`][rdflib.namespace.Namespace] class
that takes as its argument the base URI of the namespace.

```python
>>> from rdflib.namespace import Namespace
>>> RDFS = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")

```

Fully qualified URIs in the namespace can be constructed either by attribute
or by dictionary access on Namespace instances:

```python
>>> RDFS.seeAlso
rdflib.term.URIRef('http://www.w3.org/1999/02/22-rdf-syntax-ns#seeAlso')
>>> RDFS['seeAlso']
rdflib.term.URIRef('http://www.w3.org/1999/02/22-rdf-syntax-ns#seeAlso')

```

## Automatic handling of unknown predicates

As a programming convenience, a namespace binding is automatically
created when [`URIRef`][rdflib.term.URIRef] predicates are added to the graph.

## Importable namespaces

The following namespaces are available by directly importing from rdflib:

* BRICK
* CSVW
* DC
* DCAT
* DCMITYPE
* DCTERMS
* DCAM
* DOAP
* FOAF
* ODRL2
* ORG
* OWL
* PROF
* PROV
* QB
* RDF
* RDFS
* SDO
* SH
* SKOS
* SOSA
* SSN
* TIME
* VANN
* VOID
* WGS
* XSD

```python
>>> from rdflib.namespace import RDFS
>>> RDFS.seeAlso
rdflib.term.URIRef('http://www.w3.org/2000/01/rdf-schema#seeAlso')

```
"""

from __future__ import annotations

import logging
import warnings

try:
    # Python >= 3.14
    from annotationlib import (
        get_annotations,  # type: ignore[attr-defined,unused-ignore]
    )
except ImportError:  # pragma: no cover
    try:
        # Python >= 3.10
        from inspect import get_annotations  # type: ignore[attr-defined,unused-ignore]
    except ImportError:

        def get_annotations(thing: Any) -> dict:
            return thing.__annotations__


from collections.abc import Iterable
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union
from unicodedata import category
from urllib.parse import urldefrag, urljoin

from rdflib.term import URIRef, Variable, _is_valid_uri

if TYPE_CHECKING:
    from rdflib.graph import Graph
    from rdflib.store import Store


__all__ = [
    "is_ncname",
    "split_uri",
    "Namespace",
    "ClosedNamespace",
    "DefinedNamespace",
    "NamespaceManager",
    "NamespaceConflict",
    "NamespaceConflictError",
    "NamespaceSnapshot",
    "BRICK",
    "CSVW",
    "DC",
    "DCAM",
    "DCAT",
    "DCMITYPE",
    "DCTERMS",
    "DOAP",
    "FOAF",
    "GEO",
    "ODRL2",
    "ORG",
    "OWL",
    "PROF",
    "PROV",
    "QB",
    "RDF",
    "RDFS",
    "SDO",
    "SH",
    "SKOS",
    "SOSA",
    "SSN",
    "TIME",
    "VANN",
    "VOID",
    "WGS",
    "XSD",
]

logger = logging.getLogger(__name__)


class Namespace(str):
    """Utility class for quickly generating URIRefs with a common prefix.

    ```python
    >>> from rdflib.namespace import Namespace
    >>> n = Namespace("http://example.org/")
    >>> n.Person # as attribute
    rdflib.term.URIRef('http://example.org/Person')
    >>> n['first-name'] # as item - for things that are not valid python identifiers
    rdflib.term.URIRef('http://example.org/first-name')
    >>> n.Person in n
    True
    >>> n2 = Namespace("http://example2.org/")
    >>> n.Person in n2
    False

    ```
    """

    def __new__(cls, value: Union[str, bytes]) -> Namespace:
        try:
            rt = str.__new__(cls, value)
        except UnicodeDecodeError:
            rt = str.__new__(cls, value, "utf-8")  # type: ignore[arg-type]
        return rt

    # type error: Signature of "title" incompatible with supertype "str"
    @property
    def title(self) -> URIRef:  # type: ignore[override]
        # Override for DCTERMS.title to return a URIRef instead of str.title method
        return URIRef(self + "title")

    def term(self, name: str) -> URIRef:
        # need to handle slices explicitly because of __getitem__ override
        return URIRef(self + (name if isinstance(name, str) else ""))

    def __getitem__(self, key: str) -> URIRef:  # type: ignore[override]
        return self.term(key)

    def __getattr__(self, name: str) -> URIRef:
        if name.startswith("__"):  # ignore any special Python names!
            raise AttributeError
        return self.term(name)

    def __repr__(self) -> str:
        return f"Namespace({super().__repr__()})"

    def __contains__(self, ref: str) -> bool:  # type: ignore[override]
        """Allows to check if a URI is within (starts with) this Namespace.

        ```python
        >>> from rdflib import URIRef
        >>> namespace = Namespace('http://example.org/')
        >>> uri = URIRef('http://example.org/foo')
        >>> uri in namespace
        True
        >>> person_class = namespace['Person']
        >>> person_class in namespace
        True
        >>> obj = URIRef('http://not.example.org/bar')
        >>> obj in namespace
        False

        ```
        """
        return ref.startswith(self)  # test namespace membership with "ref in ns" syntax


class URIPattern(str):
    """Utility class for creating URIs according to some pattern.

    This supports either new style formatting with .format
    or old-style with % operator.

    ```python
    >>> u=URIPattern("http://example.org/%s/%d/resource")
    >>> u%('books', 12345)
    rdflib.term.URIRef('http://example.org/books/12345/resource')

    ```
    """

    def __new__(cls, value: Union[str, bytes]) -> URIPattern:
        try:
            rt = str.__new__(cls, value)
        except UnicodeDecodeError:
            if TYPE_CHECKING:
                assert isinstance(value, bytes)
            rt = str.__new__(cls, value, "utf-8")
        return rt

    def __mod__(self, *args, **kwargs) -> URIRef:
        return URIRef(super().__mod__(*args, **kwargs))

    def format(self, *args, **kwargs) -> URIRef:
        return URIRef(super().format(*args, **kwargs))

    def __repr__(self) -> str:
        return f"URIPattern({super().__repr__()})"


# _DFNS_RESERVED_ATTRS are attributes for which DefinedNamespaceMeta should
# always raise AttributeError if they are not defined and which should not be
# considered part of __dir__ results. These should be all annotations on
# `DefinedNamespaceMeta`.
_DFNS_RESERVED_ATTRS: set[str] = {
    "__slots__",
    "_NS",
    "_warn",
    "_fail",
    "_extras",
    "_underscore_num",
}

# Some libraries probe classes for certain attributes or items.
# This is a list of those attributes and items that should be ignored.
_IGNORED_ATTR_LOOKUP: set[str] = {
    "_pytestfixturefunction",  # pytest tries to look this up on Defined namespaces
    "_partialmethod",  # sphinx tries to look this up during autodoc generation
}


class DefinedNamespaceMeta(type):
    """Utility metaclass for generating URIRefs with a common prefix."""

    __slots__: tuple[str, ...] = tuple()

    _NS: Namespace
    _warn: bool = True
    _fail: bool = False  # True means mimic ClosedNamespace
    _extras: list[str] = []  # List of non-pythonesque items
    _underscore_num: bool = False  # True means pass "_n" constructs

    @lru_cache(maxsize=None)
    def __getitem__(cls, name: str, default=None) -> URIRef:
        name = str(name)

        if name in _DFNS_RESERVED_ATTRS:
            raise KeyError(
                f"DefinedNamespace like object has no access item named {name!r}"
            )
        elif name in _IGNORED_ATTR_LOOKUP:
            raise KeyError()
        if (cls._warn or cls._fail) and name not in cls:
            if cls._fail:
                raise AttributeError(f"term '{name}' not in namespace '{cls._NS}'")
            else:
                warnings.warn(
                    f"Code: {name} is not defined in namespace {cls.__name__}",
                    stacklevel=3,
                )
        return cls._NS[name]

    def __getattr__(cls, name: str):
        if name in _IGNORED_ATTR_LOOKUP:
            raise AttributeError()
        elif name in _DFNS_RESERVED_ATTRS:
            raise AttributeError(
                f"DefinedNamespace like object has no attribute {name!r}"
            )
        elif name.startswith("__"):
            return super(DefinedNamespaceMeta, cls).__getattribute__(name)
        return cls.__getitem__(name)

    def __repr__(cls) -> str:
        try:
            ns_repr = repr(cls._NS)
        except AttributeError:
            ns_repr = "<DefinedNamespace>"
        return f"Namespace({ns_repr})"

    def __str__(cls) -> str:
        try:
            return str(cls._NS)
        except AttributeError:
            return "<DefinedNamespace>"

    def __add__(cls, other: str) -> URIRef:
        return cls.__getitem__(other)

    def __contains__(cls, item: str) -> bool:
        """Determine whether a URI or an individual item belongs to this namespace"""
        try:
            this_ns = cls._NS
        except AttributeError:
            return False
        item_str = str(item)
        if item_str.startswith(str(this_ns)):
            item_str = item_str[len(str(this_ns)) :]
        return any(
            item_str in get_annotations(c)
            or item_str in c._extras
            or (cls._underscore_num and item_str[0] == "_" and item_str[1:].isdigit())
            for c in cls.mro()
            if issubclass(c, DefinedNamespace)
        )

    def __dir__(cls) -> Iterable[str]:
        attrs = {str(x) for x in get_annotations(cls)}
        # Removing these as they should not be considered part of the namespace.
        attrs.difference_update(_DFNS_RESERVED_ATTRS)
        values = {cls[str(x)] for x in attrs}
        return values

    def as_jsonld_context(self, pfx: str) -> dict:  # noqa: N804
        """Returns this DefinedNamespace as a JSON-LD 'context' object"""
        terms = {pfx: str(self._NS)}
        for key, term in get_annotations(self).items():
            if issubclass(term, URIRef):
                terms[key] = f"{pfx}:{key}"

        return {"@context": terms}


class DefinedNamespace(metaclass=DefinedNamespaceMeta):
    """A Namespace with an enumerated list of members.

    Warnings are emitted if unknown members are referenced if _warn is True.
    """

    __slots__: tuple[str, ...] = tuple()

    def __init__(self):
        raise TypeError("namespace may not be instantiated")


class ClosedNamespace(Namespace):
    """
    A namespace with a closed list of members

    Trying to create terms not listed is an error
    """

    __uris: dict[str, URIRef]

    def __new__(cls, uri: str, terms: list[str]):
        rt = super().__new__(cls, uri)
        rt.__uris = {t: URIRef(rt + t) for t in terms}  # type: ignore[attr-defined]
        return rt

    @property
    def uri(self) -> str:  # Back-compat
        return str(self)

    def term(self, name: str) -> URIRef:
        uri = self.__uris.get(name)
        if uri is None:
            raise KeyError(f"term '{name}' not in namespace '{self}'")
        return uri

    def __getitem__(self, key: str) -> URIRef:  # type: ignore[override]
        return self.term(key)

    def __getattr__(self, name: str) -> URIRef:
        if name.startswith("__"):  # ignore any special Python names!
            raise AttributeError
        else:
            try:
                return self.term(name)
            except KeyError as e:
                raise AttributeError(e)

    def __repr__(self) -> str:
        return f"{self.__module__}.{self.__class__.__name__}({str(self)!r})"

    def __dir__(self) -> list[str]:
        return list(self.__uris)

    def __contains__(self, ref: str) -> bool:  # type: ignore[override]
        return (
            ref in self.__uris.values()
        )  # test namespace membership with "ref in ns" syntax

    def _ipython_key_completions_(self) -> list[str]:
        return dir(self)


XMLNS = Namespace("http://www.w3.org/XML/1998/namespace")

if TYPE_CHECKING:
    from rdflib._type_checking import _NamespaceSetString

_with_bind_override_fix = True


class NamespaceManager:
    """Class for managing prefix => namespace mappings

    This class requires an RDFlib Graph as an input parameter and may optionally have
    the parameter bind_namespaces set. This second parameter selects a strategy which
    is one of the following:

    * core:
        * binds several core RDF prefixes only
        * owl, rdf, rdfs, xsd, xml from the NAMESPACE_PREFIXES_CORE object
    * rdflib:
        * binds all the namespaces shipped with RDFLib as DefinedNamespace instances
        * all the core namespaces and all the following: brick, csvw, dc, dcat
        * dcmitype, dcterms, dcam, doap, foaf, geo, odrl, org, prof, prov, qb, schema
        * sh, skos, sosa, ssn, time, vann, void
        * see the NAMESPACE_PREFIXES_RDFLIB object for the up-to-date list
        * this is default
    * none:
        * binds no namespaces to prefixes
        * note this is NOT default behaviour
    * cc:
        * using prefix bindings from prefix.cc which is a online prefixes database
        * not implemented yet - this is aspirational

    !!! warning "Breaking changes"

        The namespaces bound for specific values of `bind_namespaces`
        constitute part of RDFLib's public interface, so changes to them should
        only be additive within the same minor version. Removing values, or
        removing namespaces that are bound by default, constitutes a breaking
        change.

    See the sample usage

    ```python
    >>> import rdflib
    >>> from rdflib import Graph
    >>> from rdflib.namespace import Namespace, NamespaceManager
    >>> EX = Namespace('http://example.com/')
    >>> namespace_manager = NamespaceManager(Graph())
    >>> namespace_manager.bind('ex', EX, override=False)
    >>> g = Graph()
    >>> g.namespace_manager = namespace_manager
    >>> all_ns = [n for n in g.namespace_manager.namespaces()]
    >>> assert ('ex', rdflib.term.URIRef('http://example.com/')) in all_ns

    ```
    """

    def __init__(self, graph: Graph, bind_namespaces: _NamespaceSetString = "rdflib"):
        self.graph = graph
        self.__cache: dict[str, tuple[str, URIRef, str]] = {}
        self.__cache_strict: dict[str, tuple[str, URIRef, str]] = {}
        self.__log = None
        self.__strie: dict[str, Any] = {}
        self.__trie: dict[str, Any] = {}
        # This type declaration is here becuase there is no common base class
        # for all namespaces and without it the inferred type of ns is not
        # compatible with all prefixes.
        ns: Any
        # bind Namespaces as per options.
        # default is core
        if bind_namespaces == "none":
            # binds no namespaces to prefixes
            # note this is NOT default
            pass
        elif bind_namespaces == "rdflib":
            # bind all the Namespaces shipped with RDFLib
            for prefix, ns in _NAMESPACE_PREFIXES_RDFLIB.items():
                self.bind(prefix, ns)
            # ... don't forget the core ones too
            for prefix, ns in _NAMESPACE_PREFIXES_CORE.items():
                self.bind(prefix, ns)
        elif bind_namespaces == "cc":
            # bind any prefix that can be found with lookups to prefix.cc
            # first bind core and rdflib ones
            # work out remainder - namespaces without prefixes
            # only look those ones up
            raise NotImplementedError("Haven't got to this option yet")
        elif bind_namespaces == "core":
            # bind a few core RDF namespaces - default
            for prefix, ns in _NAMESPACE_PREFIXES_CORE.items():
                self.bind(prefix, ns)
        else:
            raise ValueError(f"unsupported namespace set {bind_namespaces}")

    def __contains__(self, ref: str) -> bool:
        # checks if a reference is in any of the managed namespaces with syntax
        # "ref in manager". Note that we don't use "ref in ns", as
        # NamespaceManager.namespaces() returns Iterator[tuple[str, URIRef]]
        # rather than Iterator[tuple[str, Namespace]]
        return any(ref.startswith(ns) for prefix, ns in self.namespaces())

    def reset(self) -> None:
        self.__cache = {}
        self.__strie = {}
        self.__trie = {}
        for p, n in self.namespaces():  # repopulate the trie
            insert_trie(self.__trie, str(n))

    def snapshot(self) -> NamespaceSnapshot:
        """Capture the current bindings of this manager in a
        [NamespaceSnapshot][rdflib.namespace.NamespaceSnapshot].

        The snapshot is standalone: later bindings on this manager are not
        visible to the snapshot, and bindings added to the snapshot never
        leak back into this manager.
        """
        return NamespaceSnapshot(self)

    @property
    def store(self) -> Store:
        return self.graph.store

    def qname(self, uri: str) -> str:
        prefix, namespace, name = self.compute_qname(uri)
        if prefix == "":
            return name
        else:
            return ":".join((prefix, name))

    def curie(self, uri: str, generate: bool = True) -> str:
        """
        From a URI, generate a valid CURIE.

        Result is guaranteed to contain a colon separating the prefix from the
        name, even if the prefix is an empty string.

        !!! warning "Side-effect"
            When `generate` is `True` (which is the default) and there is no
            matching namespace for the URI in the namespace manager then a new
            namespace will be added with prefix `ns{index}`.

            Thus, when `generate` is `True`, this function is not a pure
            function because of this side-effect.

            This default behaviour is chosen so that this function operates
            similarly to `NamespaceManager.qname`.

        Args:
            uri: URI to generate CURIE for.
            generate: Whether to add a prefix for the namespace if one doesn't
                already exist.  Default: `True`.

        Returns:
            CURIE for the URI

        Raises:
            KeyError: If generate is `False` and the namespace doesn't already have
                a prefix.
        """
        prefix, namespace, name = self.compute_qname(uri, generate=generate)
        return ":".join((prefix, name))

    def qname_strict(self, uri: str) -> str:
        prefix, namespace, name = self.compute_qname_strict(uri)
        if prefix == "":
            return name
        else:
            return ":".join((prefix, name))

    def normalizeUri(self, rdfTerm: str) -> str:  # noqa: N802, N803
        """
        Takes an RDF Term and 'normalizes' it into a QName (using the
        registered prefix) or (unlike compute_qname) the Notation 3
        form for URIs: <...URI...>
        """
        try:
            namespace, name = split_uri(rdfTerm)
            if namespace not in self.__strie:
                insert_strie(self.__strie, self.__trie, str(namespace))
            namespace = URIRef(str(namespace))
        except Exception:
            if isinstance(rdfTerm, Variable):
                return "?%s" % rdfTerm
            else:
                return "<%s>" % rdfTerm
        prefix = self.store.prefix(namespace)
        if prefix is None and isinstance(rdfTerm, Variable):
            return "?%s" % rdfTerm
        elif prefix is None:
            return "<%s>" % rdfTerm
        else:
            qNameParts = self.compute_qname(rdfTerm)  # noqa: N806
            return ":".join([qNameParts[0], qNameParts[-1]])

    def compute_qname(
        self, uri: str, generate: bool = True
    ) -> tuple[str, URIRef, str]:
        prefix: Optional[str]
        if uri not in self.__cache:
            if not _is_valid_uri(uri):
                raise ValueError(
                    '"{}" does not look like a valid URI, cannot serialize this. Did you want to urlencode it?'.format(
                        uri
                    )
                )

            try:
                namespace, name = split_uri(uri)
            except ValueError as e:
                namespace = URIRef(uri)
                prefix = self.store.prefix(namespace)
                name = ""  # empty prefix case, safe since not prefix is error
                if not prefix:
                    raise e
            if namespace not in self.__strie:
                insert_strie(self.__strie, self.__trie, namespace)

            if self.__strie[namespace]:
                pl_namespace = get_longest_namespace(self.__strie[namespace], uri)
                if pl_namespace is not None:
                    namespace = pl_namespace
                    name = uri[len(namespace) :]

            namespace = URIRef(namespace)
            prefix = self.store.prefix(namespace)  # warning multiple prefixes problem

            if prefix is None:
                if not generate:
                    raise KeyError(
                        "No known prefix for {} and generate=False".format(namespace)
                    )
                num = 1
                while 1:
                    prefix = "ns%s" % num
                    if not self.store.namespace(prefix):
                        break
                    num += 1
                self.bind(prefix, namespace)
            self.__cache[uri] = (prefix, namespace, name)
        return self.__cache[uri]

    def compute_qname_strict(
        self, uri: str, generate: bool = True
    ) -> tuple[str, str, str]:
        # code repeated to avoid branching on strict every time
        # if output needs to be strict (e.g. for xml) then
        # only the strict output should bear the overhead
        namespace: str
        prefix: Optional[str]
        prefix, namespace, name = self.compute_qname(uri, generate)
        if is_ncname(str(name)):
            return prefix, namespace, name
        else:
            if uri not in self.__cache_strict:
                try:
                    namespace, name = split_uri(uri, NAME_START_CATEGORIES)
                except ValueError:
                    message = (
                        "This graph cannot be serialized to a strict format "
                        "because there is no valid way to shorten {}".format(uri)
                    )
                    raise ValueError(message)
                    # omitted for strict since NCNames cannot be empty
                    # namespace = URIRef(uri)
                    # prefix = self.store.prefix(namespace)
                    # if not prefix:
                    # raise e

                if namespace not in self.__strie:
                    insert_strie(self.__strie, self.__trie, namespace)

                # omitted for strict
                # if self.__strie[namespace]:
                # pl_namespace = get_longest_namespace(self.__strie[namespace], uri)
                # if pl_namespace is not None:
                # namespace = pl_namespace
                # name = uri[len(namespace):]

                namespace = URIRef(namespace)
                prefix = self.store.prefix(
                    namespace
                )  # warning multiple prefixes problem

                if prefix is None:
                    if not generate:
                        raise KeyError(
                            "No known prefix for {} and generate=False".format(
                                namespace
                            )
                        )
                    num = 1
                    while 1:
                        prefix = "ns%s" % num
                        if not self.store.namespace(prefix):
                            break
                        num += 1
                    self.bind(prefix, namespace)
                self.__cache_strict[uri] = (prefix, namespace, name)

            return self.__cache_strict[uri]

    def expand_curie(self, curie: str) -> URIRef:
        """
        Expand a CURIE of the form <prefix:element>, e.g. "rdf:type"
        into its full expression:

        >>> import rdflib
        >>> g = rdflib.Graph()
        >>> g.namespace_manager.expand_curie("rdf:type")
        rdflib.term.URIRef('http://www.w3.org/1999/02/22-rdf-syntax-ns#type')

        Raises exception if a namespace is not bound to the prefix.

        """
        if not type(curie) is str:  # noqa: E714
            raise TypeError(f"Argument must be a string, not {type(curie).__name__}.")
        parts = curie.split(":", 1)
        if len(parts) != 2:
            raise ValueError(
                "Malformed curie argument, format should be e.g. “foaf:name”."
            )
        ns = self.store.namespace(parts[0])
        if ns is not None:
            return URIRef(f"{str(ns)}{parts[1]}")
        else:
            raise ValueError(
                f"Prefix \"{curie.split(':')[0]}\" not bound to any namespace."
            )

    def _store_bind(self, prefix: str, namespace: URIRef, override: bool) -> None:
        if not _with_bind_override_fix:
            return self.store.bind(prefix, namespace)
        try:
            return self.store.bind(prefix, namespace, override=override)
        except TypeError as error:
            if "override" in str(error):
                logger.debug(
                    "caught a TypeError, "
                    "retrying call to %s.bind without override, "
                    "see https://github.com/RDFLib/rdflib/issues/1880 for more info",
                    type(self.store),
                    exc_info=True,
                )
                return self.store.bind(prefix, namespace)

    def bind(
        self,
        prefix: Optional[str],
        namespace: Any,
        override: bool = True,
        replace: bool = False,
    ) -> None:
        """Bind a given namespace to the prefix

        If override, rebind, even if the given namespace is already
        bound to another prefix.

        If replace, replace any existing prefix with the new namespace
        """

        namespace = URIRef(str(namespace))
        # When documenting explain that override only applies in what cases
        if prefix is None:
            prefix = ""
        elif " " in prefix:
            raise KeyError("Prefixes may not contain spaces.")

        bound_namespace = self.store.namespace(prefix)

        # Check if the bound_namespace contains a URI
        # and if so convert it into a URIRef for comparison
        # This is to prevent duplicate namespaces with the
        # same URI
        if bound_namespace:
            bound_namespace = URIRef(bound_namespace)
        if bound_namespace and bound_namespace != namespace:
            if replace:
                self._store_bind(prefix, namespace, override=override)
                insert_trie(self.__trie, str(namespace))
                return
            # prefix already in use for different namespace
            #
            # append number to end of prefix until we find one
            # that's not in use.
            if not prefix:
                prefix = "default"

            num = 1
            while 1:
                new_prefix = "%s%s" % (prefix, num)
                tnamespace = self.store.namespace(new_prefix)
                if tnamespace and namespace == URIRef(tnamespace):
                    # the prefix is already bound to the correct
                    # namespace
                    return
                if not self.store.namespace(new_prefix):
                    break
                num += 1
            self._store_bind(new_prefix, namespace, override=override)
        else:
            bound_prefix = self.store.prefix(namespace)
            if bound_prefix is None:
                self._store_bind(prefix, namespace, override=override)
            elif bound_prefix == prefix:
                pass  # already bound
            else:
                if override or bound_prefix.startswith("_"):  # or a generated prefix
                    self._store_bind(prefix, namespace, override=override)

        insert_trie(self.__trie, str(namespace))

    def namespaces(self) -> Iterable[tuple[str, URIRef]]:
        for prefix, namespace in self.store.namespaces():
            namespace = URIRef(namespace)
            yield prefix, namespace

    def absolutize(self, uri: str, defrag: int = 1) -> URIRef:
        base = Path.cwd().as_uri()
        result = urljoin("%s/" % base, uri, allow_fragments=not defrag)
        if defrag:
            result = urldefrag(result)[0]
        if not defrag:
            if uri and uri[-1] == "#" and result[-1] != "#":
                result = "%s#" % result
        return URIRef(result)


class NamespaceConflict(Enum):
    """Classification of a namespace binding conflict.

    Returned by
    [NamespaceSnapshot.bind][rdflib.namespace.NamespaceSnapshot.bind] to
    report which kind of conflict a binding caused:

    * NONE: no conflict, the binding was applied cleanly.
    * PREFIX_REBOUND: the same prefix was already bound to a different URI.
    * NAMESPACE_REBOUND: the same URI was already bound to a different
      prefix.
    * DEFAULT_CHANGED: the default (empty) prefix was already bound to a
      different URI.
    """

    NONE = "none"
    PREFIX_REBOUND = "prefix_rebound"
    NAMESPACE_REBOUND = "namespace_rebound"
    DEFAULT_CHANGED = "default_changed"


class NamespaceConflictError(ValueError):
    """Raised when a namespace binding conflicts with an existing binding and
    the conflict policy of the snapshot is "error"."""

    def __init__(
        self,
        conflict: NamespaceConflict,
        prefix: str,
        namespace: URIRef,
        existing: Union[str, URIRef],
    ):
        self.conflict = conflict
        self.prefix = prefix
        self.namespace = namespace
        self.existing = existing
        super().__init__(
            "cannot bind prefix %r to %s: %s (conflicts with %s)"
            % (prefix, namespace, conflict.value, existing)
        )


# Conflict policies supported by NamespaceSnapshot:
# * "replace": apply the new binding, resolving conflicts the same way the
#   default NamespaceManager does.
# * "keep": keep the existing binding and leave the snapshot unchanged.
# * "error": raise NamespaceConflictError.
_NAMESPACE_CONFLICT_POLICIES = ("replace", "keep", "error")


class NamespaceSnapshot:
    """An explicitly creatable and passable snapshot of namespace bindings.

    A snapshot captures the prefix => namespace mappings of a
    [NamespaceManager][rdflib.namespace.NamespaceManager] (or a fresh set of
    well-known bindings), the default namespace (the binding of the empty
    prefix) and a conflict policy, and then lives independently of its
    source: bindings added to the source manager after the snapshot was
    taken are not visible to the snapshot, and bindings added to the
    snapshot never leak back into the source manager or into any other
    snapshot.

    Serializers that accept a `namespace_snapshot` argument (the Turtle
    and JSON-LD serializers) only consult their own snapshot, so concurrent
    requests can each serialize with different prefixes and formats without
    cross-request leakage.

    ```python
    >>> from rdflib import Graph, URIRef
    >>> from rdflib.namespace import NamespaceSnapshot
    >>> g = Graph()
    >>> g.bind("ex", "http://example.org/")
    >>> snap = NamespaceSnapshot(g.namespace_manager)
    >>> ("ex", URIRef("http://example.org/")) in snap.namespaces()
    True
    >>> snap.bind("ex2", "http://example.org/")
    <NamespaceConflict.NAMESPACE_REBOUND: 'namespace_rebound'>
    >>> dict(g.namespaces()).get("ex2") is None
    True
    >>> child = snap.copy()
    >>> child.bind("ex3", "http://example.org/child/")
    <NamespaceConflict.NONE: 'none'>
    >>> ("ex3", URIRef("http://example.org/child/")) in snap.namespaces()
    False

    ```
    """

    def __init__(
        self,
        manager: Optional[NamespaceManager] = None,
        *,
        bind_namespaces: _NamespaceSetString = "rdflib",
        conflict_policy: str = "replace",
    ):
        if conflict_policy not in _NAMESPACE_CONFLICT_POLICIES:
            raise ValueError(
                "unsupported conflict policy %r, expected one of %s"
                % (conflict_policy, ", ".join(_NAMESPACE_CONFLICT_POLICIES))
            )
        self.conflict_policy = conflict_policy
        self._prefix_to_ns: dict[str, URIRef] = {}
        self._ns_to_prefix: dict[URIRef, str] = {}
        self._qname_cache: dict[str, tuple[str, URIRef, str]] = {}
        if manager is not None:
            for prefix, namespace in manager.namespaces():
                self._bind_raw(prefix, URIRef(namespace))
        elif bind_namespaces == "none":
            # binds no namespaces to prefixes
            pass
        elif bind_namespaces == "rdflib":
            for prefix, ns in _NAMESPACE_PREFIXES_RDFLIB.items():
                self._bind_raw(prefix, URIRef(str(ns)))
            for prefix, ns in _NAMESPACE_PREFIXES_CORE.items():
                self._bind_raw(prefix, URIRef(str(ns)))
        elif bind_namespaces == "core":
            for prefix, ns in _NAMESPACE_PREFIXES_CORE.items():
                self._bind_raw(prefix, URIRef(str(ns)))
        else:
            raise ValueError(f"unsupported namespace set {bind_namespaces}")

    def _bind_raw(self, prefix: str, namespace: URIRef) -> None:
        old_namespace = self._prefix_to_ns.get(prefix)
        if old_namespace is not None and old_namespace != namespace:
            if self._ns_to_prefix.get(old_namespace) == prefix:
                del self._ns_to_prefix[old_namespace]
        old_prefix = self._ns_to_prefix.get(namespace)
        if old_prefix is not None and old_prefix != prefix:
            del self._prefix_to_ns[old_prefix]
        self._prefix_to_ns[prefix] = namespace
        self._ns_to_prefix[namespace] = prefix
        self._qname_cache.clear()

    @property
    def default_namespace(self) -> Optional[URIRef]:
        """The namespace bound to the default (empty) prefix, if any."""
        return self._prefix_to_ns.get("")

    def namespaces(self) -> tuple[tuple[str, URIRef], ...]:
        """Return the captured (prefix, namespace) pairs as a stable tuple.

        The result is materialized when called, in binding order, so
        appending prefixes while iterating over the result does not modify
        the ongoing iteration.
        """
        return tuple(self._prefix_to_ns.items())

    def namespace(self, prefix: Optional[str]) -> Optional[URIRef]:
        """Return the namespace bound to prefix, or None."""
        return self._prefix_to_ns.get("" if prefix is None else str(prefix))

    def prefix(self, namespace: Any) -> Optional[str]:
        """Return the prefix bound to namespace, or None."""
        return self._ns_to_prefix.get(URIRef(str(namespace)))

    def bind(
        self,
        prefix: Optional[str],
        namespace: Any,
        override: bool = True,
        replace: bool = False,
    ) -> NamespaceConflict:
        """Bind a given namespace to a prefix in this snapshot only.

        The binding never leaks into the manager the snapshot was taken
        from or into any other snapshot.

        How a conflict is resolved depends on the conflict policy of the
        snapshot:

        * "replace": resolve the conflict the same way the default
          [NamespaceManager][rdflib.namespace.NamespaceManager] does,
          honouring the override and replace flags.
        * "keep": leave the snapshot unchanged.
        * "error": raise
          [NamespaceConflictError][rdflib.namespace.NamespaceConflictError].

        Returns the kind of conflict the binding caused, distinguishing
        same-URI-new-prefix (NAMESPACE_REBOUND), same-prefix-new-URI
        (PREFIX_REBOUND) and default prefix changes (DEFAULT_CHANGED), or
        NamespaceConflict.NONE if the binding was clean.
        """
        namespace = URIRef(str(namespace))
        if prefix is None:
            prefix = ""
        elif " " in prefix:
            raise KeyError("Prefixes may not contain spaces.")
        prefix = str(prefix)

        bound_namespace = self._prefix_to_ns.get(prefix)
        bound_prefix = self._ns_to_prefix.get(namespace)

        conflict = NamespaceConflict.NONE
        if bound_namespace is not None and bound_namespace != namespace:
            conflict = (
                NamespaceConflict.DEFAULT_CHANGED
                if prefix == ""
                else NamespaceConflict.PREFIX_REBOUND
            )
        elif bound_prefix is not None and bound_prefix != prefix:
            conflict = NamespaceConflict.NAMESPACE_REBOUND

        if conflict is not NamespaceConflict.NONE:
            if self.conflict_policy == "error":
                raise NamespaceConflictError(
                    conflict,
                    prefix,
                    namespace,
                    bound_namespace if bound_namespace is not None else bound_prefix,
                )
            if self.conflict_policy == "keep":
                return conflict

        if conflict in (
            NamespaceConflict.PREFIX_REBOUND,
            NamespaceConflict.DEFAULT_CHANGED,
        ):
            # policy "replace": mirror the default NamespaceManager behaviour
            if replace:
                self._bind_raw(prefix, namespace)
            else:
                # find an unused derived prefix, like the default manager does
                stem = prefix or "default"
                num = 1
                while True:
                    new_prefix = "%s%s" % (stem, num)
                    existing = self._prefix_to_ns.get(new_prefix)
                    if existing is None:
                        break
                    if existing == namespace:
                        return conflict  # already bound to the right namespace
                    num += 1
                self._bind_raw(new_prefix, namespace)
            return conflict

        if conflict is NamespaceConflict.NAMESPACE_REBOUND:
            # policy "replace"
            assert bound_prefix is not None
            if override or bound_prefix.startswith("_"):
                self._bind_raw(prefix, namespace)
            return conflict

        if bound_namespace == namespace or bound_prefix == prefix:
            pass  # already bound
        else:
            self._bind_raw(prefix, namespace)
        return conflict

    def compute_qname(self, uri: str, generate: bool = True) -> tuple[str, URIRef, str]:
        """Compute a (prefix, namespace, local name) triple for uri using
        only the bindings captured in this snapshot.

        Like the default manager, unknown namespaces are bound to a
        generated ns{index} prefix when generate is True; the generated
        binding is added to this snapshot only.
        """
        uri = str(uri)
        cached = self._qname_cache.get(uri)
        if cached is not None:
            return cached
        if not _is_valid_uri(uri):
            raise ValueError(
                '"{}" does not look like a valid URI, cannot serialize this. Did you want to urlencode it?'.format(
                    uri
                )
            )
        try:
            namespace, name = split_uri(uri)
        except ValueError:
            namespace, name = uri, ""

        # Prefer the longest bound namespace that is longer than the split
        # namespace and a prefix of the URI, mirroring NamespaceManager.
        best: Optional[str] = None
        for candidate in self._ns_to_prefix:
            candidate_str = str(candidate)
            if (
                len(candidate_str) > len(namespace)
                and uri.startswith(candidate_str)
                and (best is None or len(candidate_str) > len(best))
            ):
                best = candidate_str
        if best is not None:
            namespace, name = best, uri[len(best) :]

        namespace_uri = URIRef(namespace)
        prefix = self._ns_to_prefix.get(namespace_uri)
        if prefix is None:
            if not generate:
                raise KeyError(
                    "No known prefix for {} and generate=False".format(namespace_uri)
                )
            num = 1
            while True:
                prefix = "ns%s" % num
                if prefix not in self._prefix_to_ns:
                    break
                num += 1
            self._bind_raw(prefix, namespace_uri)
        result = (prefix, namespace_uri, name)
        self._qname_cache[uri] = result
        return result

    def qname(self, uri: str) -> str:
        prefix, _namespace, name = self.compute_qname(uri)
        if prefix == "":
            return name
        else:
            return ":".join((prefix, name))

    def curie(self, uri: str, generate: bool = True) -> str:
        prefix, _namespace, name = self.compute_qname(uri, generate=generate)
        return ":".join((prefix, name))

    def to_jsonld_context(self) -> dict[str, str]:
        """Return the bindings as a JSON-LD context object.

        Prefixes appear in binding order, so the resulting context has a
        stable ordering; the default namespace, if any, is mapped to
        @vocab.
        """
        context: dict[str, str] = {}
        for prefix, namespace in self._prefix_to_ns.items():
            context[prefix if prefix else "@vocab"] = str(namespace)
        return context

    def copy(self) -> NamespaceSnapshot:
        """Return an independent copy of this snapshot.

        Intended for handing bindings to child requests: the copy starts
        with the same bindings, default namespace and conflict policy, but
        incremental bindings on the copy never write back to this snapshot.
        """
        clone = NamespaceSnapshot.__new__(NamespaceSnapshot)
        clone.conflict_policy = self.conflict_policy
        clone._prefix_to_ns = dict(self._prefix_to_ns)
        clone._ns_to_prefix = dict(self._ns_to_prefix)
        clone._qname_cache = {}
        return clone

    def __contains__(self, ref: str) -> bool:
        return any(ref.startswith(str(ns)) for ns in self._ns_to_prefix)

    def __len__(self) -> int:
        return len(self._prefix_to_ns)

    def __repr__(self) -> str:
        return "NamespaceSnapshot(%r)" % (self._prefix_to_ns,)


# From: http://www.w3.org/TR/REC-xml#NT-CombiningChar
#
# * Name start characters must have one of the categories Ll, Lu, Lo,
#   Lt, Nl.
#
# * Name characters other than Name-start characters must have one of
#   the categories Mc, Me, Mn, Lm, or Nd.
#
# * Characters in the compatibility area (i.e. with character code
#   greater than #xF900 and less than #xFFFE) are not allowed in XML
#   names.
#
# * Characters which have a font or compatibility decomposition
#   (i.e. those with a "compatibility formatting tag" in field 5 of the
#   database -- marked by field 5 beginning with a "<") are not allowed.
#
# * The following characters are treated as name-start characters rather
#   than name characters, because the property file classifies them as
#   Alphabetic: [#x02BB-#x02C1], #x0559, #x06E5, #x06E6.
#
# * Characters #x20DD-#x20E0 are excluded (in accordance with Unicode
#   2.0, section 5.14).
#
# * Character #x00B7 is classified as an extender, because the property
#   list so identifies it.
#
# * Character #x0387 is added as a name character, because #x00B7 is its
#   canonical equivalent.
#
# * Characters ':' and '_' are allowed as name-start characters.
#
# * Characters '-' and '.' are allowed as name characters.


NAME_START_CATEGORIES = ["Ll", "Lu", "Lo", "Lt", "Nl"]
SPLIT_START_CATEGORIES = NAME_START_CATEGORIES + ["Nd"]
NAME_CATEGORIES = NAME_START_CATEGORIES + ["Mc", "Me", "Mn", "Lm", "Nd"]
ALLOWED_NAME_CHARS = ["\u00B7", "\u0387", "-", ".", "_", "%", "(", ")"]


# http://www.w3.org/TR/REC-xml-names/#NT-NCName
#  [4] NCName ::= (Letter | '_') (NCNameChar)* /* An XML Name, minus
#      the ":" */
#  [5] NCNameChar ::= Letter | Digit | '.' | '-' | '_' | CombiningChar
#      | Extender


def is_ncname(name: str) -> int:
    if name:
        first = name[0]
        if first == "_" or category(first) in NAME_START_CATEGORIES:
            for i in range(1, len(name)):
                c = name[i]
                if not category(c) in NAME_CATEGORIES:  # noqa: E713
                    if c in ALLOWED_NAME_CHARS:
                        continue
                    return 0
                # if in compatibility area
                # if decomposition(c)!='':
                #    return 0

            return 1

    return 0


def split_uri(
    uri: str, split_start: list[str] = SPLIT_START_CATEGORIES
) -> tuple[str, str]:
    if uri.startswith(XMLNS):
        # The XML namespace has no trailing delimiter, so only split on it when
        # the remainder is a usable local name; otherwise fall through to the
        # generic splitting below (e.g. ".../namespace/" must not become "/").
        ln = uri[len(XMLNS) :]
        if ln == "" or is_ncname(ln):
            return (XMLNS, ln)
    length = len(uri)
    for i in range(0, length):
        c = uri[-i - 1]
        if not category(c) in NAME_CATEGORIES:  # noqa: E713
            if c in ALLOWED_NAME_CHARS:
                continue
            for j in range(-1 - i, length):
                if category(uri[j]) in split_start or uri[j] == "_":
                    # _ prevents early split, roundtrip not generate
                    ns = uri[:j]
                    if not ns:
                        break
                    ln = uri[j:]
                    return (ns, ln)
            break
    raise ValueError("Can't split '{}'".format(uri))


def insert_trie(
    trie: dict[str, Any], value: str
) -> dict[str, Any]:  # aka get_subtrie_or_insert
    """Insert a value into the trie if it is not already contained in the trie.
    Return the subtree for the value regardless of whether it is a new value
    or not."""
    if value in trie:
        return trie[value]
    multi_check = False
    for key in tuple(trie.keys()):
        if len(value) > len(key) and value.startswith(key):
            return insert_trie(trie[key], value)
        elif key.startswith(value):  # we know the value is not in the trie
            if not multi_check:
                trie[value] = {}
                multi_check = True  # there can be multiple longer existing prefixes
            dict_ = trie.pop(
                key
            )  # does not break strie since key<->dict_ remains unchanged
            trie[value][key] = dict_
    if value not in trie:
        trie[value] = {}
    return trie[value]


def insert_strie(strie: dict[str, Any], trie: dict[str, Any], value: str) -> None:
    if value not in strie:
        strie[value] = insert_trie(trie, value)


def get_longest_namespace(trie: dict[str, Any], value: str) -> Optional[str]:
    for key in trie:
        if value.startswith(key):
            out = get_longest_namespace(trie[key], value)
            if out is None:
                return key
            else:
                return out
    return None


from rdflib.namespace._BRICK import BRICK
from rdflib.namespace._CSVW import CSVW
from rdflib.namespace._DC import DC
from rdflib.namespace._DCAM import DCAM
from rdflib.namespace._DCAT import DCAT
from rdflib.namespace._DCMITYPE import DCMITYPE
from rdflib.namespace._DCTERMS import DCTERMS
from rdflib.namespace._DOAP import DOAP
from rdflib.namespace._FOAF import FOAF
from rdflib.namespace._GEO import GEO
from rdflib.namespace._ODRL2 import ODRL2
from rdflib.namespace._ORG import ORG
from rdflib.namespace._OWL import OWL
from rdflib.namespace._PROF import PROF
from rdflib.namespace._PROV import PROV
from rdflib.namespace._QB import QB
from rdflib.namespace._RDF import RDF
from rdflib.namespace._RDFS import RDFS
from rdflib.namespace._SDO import SDO
from rdflib.namespace._SH import SH
from rdflib.namespace._SKOS import SKOS
from rdflib.namespace._SOSA import SOSA
from rdflib.namespace._SSN import SSN
from rdflib.namespace._TIME import TIME
from rdflib.namespace._VANN import VANN
from rdflib.namespace._VOID import VOID
from rdflib.namespace._WGS import WGS
from rdflib.namespace._XSD import XSD

# prefixes for the core Namespaces shipped with RDFLib
_NAMESPACE_PREFIXES_CORE = {
    "owl": OWL,
    "rdf": RDF,
    "rdfs": RDFS,
    "xsd": XSD,
    # Namespace binding for XML - needed for RDF/XML
    "xml": XMLNS,
}


# prefixes for all the non-core Namespaces shipped with RDFLib
_NAMESPACE_PREFIXES_RDFLIB = {
    "brick": BRICK,
    "csvw": CSVW,
    "dc": DC,
    "dcat": DCAT,
    "dcmitype": DCMITYPE,
    "dcterms": DCTERMS,
    "dcam": DCAM,
    "doap": DOAP,
    "foaf": FOAF,
    "geo": GEO,
    "odrl": ODRL2,
    "org": ORG,
    "prof": PROF,
    "prov": PROV,
    "qb": QB,
    "schema": SDO,
    "sh": SH,
    "skos": SKOS,
    "sosa": SOSA,
    "ssn": SSN,
    "time": TIME,
    "vann": VANN,
    "void": VOID,
    "wgs": WGS,
}
