"""
Canonical N-Quads serializer (RDFC-1.0).

Serializes a dataset to its canonical N-Quads representation following
the W3C "RDF Dataset Canonicalization 1.0" algorithm, see
:mod:`rdflib.canonicalize`.  Registered as the ``rdfc`` (and
``rdfc10``) serializer plugin, so it can be used through the usual
serialization API::

    dataset.serialize(destination="out.nq", format="rdfc")
    canonical_nquads = dataset.serialize(format="rdfc")

After :meth:`serialize` has run, the mapping from the original blank
nodes to the issued canonical identifiers is available as
:attr:`CanonicalNQuadsSerializer.bnode_map`.
"""

from __future__ import annotations

import warnings
from typing import IO, Any, Dict, Optional

from rdflib.canonicalize import DEFAULT_MAX_WORK, canonicalize_dataset
from rdflib.serializer import Serializer
from rdflib.term import BNode

__all__ = ["CanonicalNQuadsSerializer"]


class CanonicalNQuadsSerializer(Serializer):
    """
    Serializes a context-aware store (e.g. a
    :class:`~rdflib.graph.Dataset`) as canonical RDFC-1.0 N-Quads.

    The output is UTF-8 encoded, code point sorted and directly usable
    for hashing.  Accepts a ``max_work`` keyword argument to bound the
    canonicalization effort, see
    :func:`rdflib.canonicalize.canonicalize_dataset`.
    """

    def __init__(self, store):
        if not store.context_aware:
            raise Exception(
                "Canonical N-Quads serialization only makes sense for "
                "context-aware stores!"
            )
        super().__init__(store)
        #: Mapping from original blank nodes to canonical identifiers,
        #: populated by :meth:`serialize`.
        self.bnode_map: Optional[Dict[BNode, str]] = None
        #: Hex-encoded SHA-256 digest of the serialized canonical dataset.
        self.dataset_hash: Optional[str] = None

    def serialize(
        self,
        stream: IO[bytes],
        base: Optional[str] = None,
        encoding: Optional[str] = None,
        max_work: Optional[int] = DEFAULT_MAX_WORK,
        **kwargs: Any,
    ) -> None:
        if base is not None:
            warnings.warn("CanonicalNQuadsSerializer does not support base.")
        if encoding is not None and encoding.lower() != "utf-8":
            warnings.warn(
                "CanonicalNQuadsSerializer always uses UTF-8 encoding. "
                f"Given encoding was: {encoding}"
            )
        result = canonicalize_dataset(self.store, max_work=max_work)
        self.bnode_map = result.bnode_map
        self.dataset_hash = result.dataset_hash
        stream.write(result.nquads.encode("utf-8"))
