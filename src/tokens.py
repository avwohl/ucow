"""Token definitions for Cowgol lexer."""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Any, Optional


class TokenType(Enum):
    # Literals
    NUMBER = auto()
    STRING = auto()
    CHAR = auto()
    ID = auto()

    # Keywords
    AND = auto()
    AS = auto()
    BREAK = auto()
    CASE = auto()
    CONST = auto()
    CONTINUE = auto()
    ELSE = auto()
    ELSEIF = auto()
    END = auto()
    IF = auto()
    IMPLEMENTS = auto()
    INCLUDE = auto()
    INTERFACE = auto()
    IS = auto()
    LOOP = auto()
    NOT = auto()
    OR = auto()
    RECORD = auto()
    RETURN = auto()
    SUB = auto()
    THEN = auto()
    TYPEDEF = auto()
    VAR = auto()
    WHEN = auto()
    WHILE = auto()
    NIL = auto()

    # Type keywords
    INT8 = auto()
    UINT8 = auto()
    INT16 = auto()
    UINT16 = auto()
    INT32 = auto()
    UINT32 = auto()
    INTPTR = auto()

    # Operators
    PLUS = auto()          # +
    MINUS = auto()         # -
    STAR = auto()          # *
    SLASH = auto()         # /
    PERCENT = auto()       # %
    AMPERSAND = auto()     # &
    PIPE = auto()          # |
    CARET = auto()         # ^
    TILDE = auto()         # ~
    LSHIFT = auto()        # <<
    RSHIFT = auto()        # >>

    # Comparison
    EQ = auto()            # ==
    NE = auto()            # !=
    LT = auto()            # <
    LE = auto()            # <=
    GT = auto()            # >
    GE = auto()            # >=

    # Assignment
    ASSIGN = auto()        # :=

    # Delimiters
    LPAREN = auto()        # (
    RPAREN = auto()        # )
    LBRACKET = auto()      # [
    RBRACKET = auto()      # ]
    LBRACE = auto()        # {
    RBRACE = auto()        # }
    COMMA = auto()         # ,
    COLON = auto()         # :
    SEMICOLON = auto()     # ;
    DOT = auto()           # .

    # Special
    AT = auto()            # @
    EOF = auto()
    NEWLINE = auto()


# Mapping of keyword strings to token types
KEYWORDS = {
    'and': TokenType.AND,
    'as': TokenType.AS,
    'break': TokenType.BREAK,
    'case': TokenType.CASE,
    'const': TokenType.CONST,
    'continue': TokenType.CONTINUE,
    'else': TokenType.ELSE,
    'elseif': TokenType.ELSEIF,
    'end': TokenType.END,
    'if': TokenType.IF,
    'implements': TokenType.IMPLEMENTS,
    'include': TokenType.INCLUDE,
    'interface': TokenType.INTERFACE,
    'is': TokenType.IS,
    'loop': TokenType.LOOP,
    'not': TokenType.NOT,
    'or': TokenType.OR,
    'record': TokenType.RECORD,
    'return': TokenType.RETURN,
    'sub': TokenType.SUB,
    'then': TokenType.THEN,
    'typedef': TokenType.TYPEDEF,
    'var': TokenType.VAR,
    'when': TokenType.WHEN,
    'while': TokenType.WHILE,
    'nil': TokenType.NIL,

    # Types
    'int8': TokenType.INT8,
    'uint8': TokenType.UINT8,
    'int16': TokenType.INT16,
    'uint16': TokenType.UINT16,
    'int32': TokenType.INT32,
    'uint32': TokenType.UINT32,
    'intptr': TokenType.INTPTR,
}


@dataclass
class SourceLocation:
    """Location in source code for error reporting."""
    filename: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column}"


@dataclass
class Token:
    """A lexical token."""
    type: TokenType
    value: Any  # The actual value (number, string, identifier name)
    location: SourceLocation

    def __str__(self) -> str:
        if self.value is not None:
            return f"{self.type.name}({self.value})"
        return self.type.name
