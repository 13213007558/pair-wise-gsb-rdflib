from __future__ import annotations

import codecs
import warnings
from typing import IO, TYPE_CHECKING, Any, Optional, Union

from rdflib.graph import Graph
from rdflib.serializer import Serializer
from rdflib.term import Literal, Node, QuotedTriple

if TYPE_CHECKING:
    from rdflib.graph import _TripleType

"""
N-Triples RDF graph serializer for RDFLib.
See <http://www.w3.org/TR/rdf-testcases/#ntriples> for details about the
format.
"""

__all__ = ["NTSerializer"]


class NTSerializer(Serializer):
    """Serializes RDF graphs to NTriples format."""

    def __init__(self, store: Graph):
        Serializer.__init__(self, store)

    def serialize(
        self,
        stream: IO[bytes],
        base: Optional[str] = None,
        encoding: Optional[str] = "utf-8",
        **kwargs: Any,
    ) -> None:
        if base is not None:
            warnings.warn("NTSerializer does not support base.")
        if encoding != "utf-8":
            warnings.warn(
                "NTSerializer always uses UTF-8 encoding. "
                f"Given encoding was: {encoding}"
            )

        for triple in self.store:
            stream.write(_nt_row(triple).encode())


class NT11Serializer(NTSerializer):
    """Serializes RDF graphs to RDF 1.1 NTriples format.

    Exactly like nt - only utf8 encoded.
    """

    def __init__(self, store: Graph):
        Serializer.__init__(self, store)  # default to utf-8


def _nt_row(triple: _TripleType) -> str:
    return (
        f"{_nt_term(triple[0])} {_nt_term(triple[1])} {_nt_term(triple[2])} .\n"
    )


def _nt_term(term: Node) -> str:
    """Render a term in N-Triples(-star) syntax.

    Quoted triples are rendered recursively as << ... >>, with literals
    inside them escaped the same way as top-level literals.
    """
    if isinstance(term, Literal):
        return _quoteLiteral(term)
    if isinstance(term, QuotedTriple):
        return (
            f"<< {_nt_term(term.subject)} {_nt_term(term.predicate)}"
            f" {_nt_term(term.object)} >>"
        )
    return term.n3()


def _quoteLiteral(l_: Literal) -> str:  # noqa: N802
    """A simpler version of term.Literal.n3()"""

    encoded = _quote_encode(l_)

    if l_.language:
        if l_.datatype:
            raise Exception("Literal has datatype AND language!")
        return "%s@%s" % (encoded, l_.language)
    elif l_.datatype:
        return "%s^^<%s>" % (encoded, l_.datatype)
    else:
        return "%s" % encoded


def _quote_encode(l_: str) -> str:
    # Accept either an rdflib Literal or a plain string
    s = str(l_)

    parts = ""
    for ch in s:
        code = ord(ch)
        if ch == "\\":
            parts += "\\\\"
        elif ch == '"':
            parts += '\\"'
        elif ch == "\n":
            parts += "\\n"
        elif ch == "\r":
            parts += "\\r"
        elif ch == "\t":
            parts += "\\t"
        elif code == 0x08:  # backspace
            parts += "\\b"
        elif code == 0x0C:  # form feed
            parts += "\\f"
        elif (
            code == 0x0B or (code < 0x20) or (code == 0x7F)
        ):  # vertical tab -> use \u000B
            parts += f"\\u{code:04X}"
        else:
            parts += ch

    return '"' + parts + '"'


def _nt_unicode_error_resolver(
    err: UnicodeError,
) -> tuple[Union[str, bytes], int]:
    """
    Do unicode char replaces as defined in https://www.w3.org/TR/2004/REC-rdf-testcases-20040210/#ntrip_strings
    """

    def _replace_single(c):
        c = ord(c)
        fmt = "\\u%04X" if c <= 0xFFFF else "\\U%08X"
        return fmt % c

    # type error: "UnicodeError" has no attribute "object"
    # type error: "UnicodeError" has no attribute "start"
    # type error: "UnicodeError" has no attribute "end"
    string = err.object[err.start : err.end]  # type: ignore[attr-defined]
    # type error: "UnicodeError" has no attribute "end"
    return "".join(_replace_single(c) for c in string), err.end  # type: ignore[attr-defined]


codecs.register_error("_rdflib_nt_escape", _nt_unicode_error_resolver)
