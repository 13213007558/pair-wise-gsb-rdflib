"""
TODO:
"""

from __future__ import annotations

__all__ = [
    "Error",
    "ParserError",
    "UniquenessError",
    "UnsupportedUpdateError",
]


from typing import Any, Optional


class Error(Exception):
    """Base class for rdflib exceptions."""

    def __init__(self, msg: Optional[str] = None):
        Exception.__init__(self, msg)
        self.msg = msg


class ParserError(Error):
    """RDF Parser error."""

    def __init__(self, msg: str):
        Error.__init__(self, msg)
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class UniquenessError(Error):
    """A uniqueness assumption was made in the context, and that is not true"""

    def __init__(self, values: Any):
        Error.__init__(
            self,
            "\
Uniqueness assumption is not fulfilled. Multiple values are: %s"
            % values,
        )


class UnsupportedUpdateError(Error):
    """An update request (or store) is not supported in this context.

    Raised, before anything is executed, when a change report is requested
    for an update that contains operations whose writes cannot be observed
    (e.g. ``LOAD``) or when the underlying store applies updates itself in
    a way that cannot be observed.
    """

    def __init__(self, msg: Optional[str] = None):
        Error.__init__(self, msg)
        self.msg = msg
