"""Token-related types for Cowgol.

After the v3 uplox migration, only ``SourceLocation`` remains here —
``ast.py`` references it on every node and many consumers carry it
through. The lexer and parser themselves now live in the generated
``uplox_cowgol`` module (see ``parser.py`` for the translator that
adapts the v3 AST into the ``ast.py`` class hierarchy).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SourceLocation:
    """Location in source code for error reporting."""
    filename: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column}"
