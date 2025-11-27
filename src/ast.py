"""Abstract Syntax Tree node definitions for Cowgol."""

from dataclasses import dataclass, field
from typing import List, Optional, Any, Union
from .tokens import SourceLocation


# Base classes

@dataclass
class Node:
    """Base class for all AST nodes."""
    location: SourceLocation


@dataclass
class Type(Node):
    """Base class for type nodes."""
    pass


@dataclass
class Statement(Node):
    """Base class for statement nodes."""
    pass


@dataclass
class Declaration(Node):
    """Base class for declaration nodes."""
    pass


# Expression base - we handle resolved_type as a mutable attribute
class Expression(Node):
    """Base class for expression nodes."""
    resolved_type: Optional['Type'] = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)


# Type nodes

@dataclass
class ScalarType(Type):
    """Built-in scalar type (int8, uint16, etc.)."""
    name: str  # 'int8', 'uint8', 'int16', 'uint16', 'int32', 'uint32', 'intptr'

    @property
    def is_signed(self) -> bool:
        return self.name.startswith('int') and not self.name.startswith('intp')

    @property
    def size(self) -> int:
        """Size in bytes."""
        if '8' in self.name:
            return 1
        elif '16' in self.name or self.name == 'intptr':
            return 2  # Assuming 16-bit target
        elif '32' in self.name:
            return 4
        return 2  # Default


@dataclass
class RangedIntType(Type):
    """Ranged integer type: int(min, max)."""
    min_expr: 'Expression'
    max_expr: 'Expression'


@dataclass
class PointerType(Type):
    """Pointer type: [T]."""
    target: Type


@dataclass
class ArrayType(Type):
    """Array type: T[size]."""
    element: Type
    size: Optional['Expression']  # None means size inferred from initializer


@dataclass
class NamedType(Type):
    """Reference to a named type (record, typedef, etc.)."""
    name: str
    resolved: Optional[Type] = field(default=None, compare=False, repr=False)


@dataclass
class IndexOfType(Type):
    """@indexof array_name - type suitable for indexing the array."""
    target: str


@dataclass
class SizeOfType(Type):
    """@sizeof array_name - type suitable for holding the array size."""
    target: str


@dataclass
class RecordType(Type):
    """Record type definition."""
    name: str
    fields: List['RecordField']
    base: Optional[str] = None  # Parent record name for inheritance


@dataclass
class RecordField(Node):
    """A field in a record."""
    name: str
    type: Type
    offset: Optional[int] = None  # Explicit @at() offset


@dataclass
class InterfaceType(Type):
    """Interface (function pointer) type."""
    name: str
    params: List['Parameter']
    returns: List['Parameter']


# Expression nodes - using regular classes with __init__

@dataclass
class NumberLiteral:
    """Numeric literal."""
    location: SourceLocation
    value: int
    resolved_type: Optional[Type] = None


@dataclass
class StringLiteral:
    """String literal."""
    location: SourceLocation
    value: str
    resolved_type: Optional[Type] = None


@dataclass
class Identifier:
    """Variable or constant reference."""
    location: SourceLocation
    name: str
    resolved: Optional[Declaration] = field(default=None, compare=False, repr=False)
    resolved_type: Optional[Type] = None


@dataclass
class BinaryOp:
    """Binary operation: left op right."""
    location: SourceLocation
    op: str  # '+', '-', '*', '/', '%', '&', '|', '^', '<<', '>>'
    left: 'Expression'
    right: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class UnaryOp:
    """Unary operation: op expr."""
    location: SourceLocation
    op: str  # '-', '~', 'not', '&' (address-of)
    operand: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class Comparison:
    """Comparison: left op right."""
    location: SourceLocation
    op: str  # '==', '!=', '<', '<=', '>', '>='
    left: 'Expression'
    right: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class LogicalOp:
    """Logical operation: left op right."""
    location: SourceLocation
    op: str  # 'and', 'or'
    left: 'Expression'
    right: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class NotOp:
    """Logical not: not expr."""
    location: SourceLocation
    operand: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class Cast:
    """Type cast: expr as type."""
    location: SourceLocation
    expr: 'Expression'
    target_type: Type
    resolved_type: Optional[Type] = None


@dataclass
class ArrayAccess:
    """Array subscript: array[index]."""
    location: SourceLocation
    array: 'Expression'
    index: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class FieldAccess:
    """Record field access: expr.field."""
    location: SourceLocation
    record: 'Expression'
    field: str
    resolved_type: Optional[Type] = None


@dataclass
class Dereference:
    """Pointer dereference: [ptr]."""
    location: SourceLocation
    pointer: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class AddressOf:
    """Address-of: &expr."""
    location: SourceLocation
    operand: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class Call:
    """Subroutine or interface call."""
    location: SourceLocation
    target: 'Expression'
    args: List['Expression']
    resolved_type: Optional[Type] = None


@dataclass
class SizeOf:
    """@sizeof operator."""
    location: SourceLocation
    target: Union[Type, 'Expression']
    resolved_type: Optional[Type] = None


@dataclass
class BytesOf:
    """@bytesof operator."""
    location: SourceLocation
    target: Union[Type, 'Expression']
    resolved_type: Optional[Type] = None


@dataclass
class IndexOf:
    """@indexof operator - returns type for array indexing."""
    location: SourceLocation
    target: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class Next:
    """@next operator - advance pointer by element size."""
    location: SourceLocation
    pointer: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class Prev:
    """@prev operator - move pointer back by element size."""
    location: SourceLocation
    pointer: 'Expression'
    resolved_type: Optional[Type] = None


@dataclass
class ArrayInitializer:
    """Array initializer: {expr, expr, ...}."""
    location: SourceLocation
    elements: List['Expression']
    resolved_type: Optional[Type] = None


@dataclass
class RecordInitializer:
    """Record initializer: {field1, field2, ...}."""
    location: SourceLocation
    elements: List['Expression']
    resolved_type: Optional[Type] = None


@dataclass
class NilLiteral:
    """The nil pointer constant."""
    location: SourceLocation
    resolved_type: Optional[Type] = None


# Type alias for any expression
Expression = Union[
    NumberLiteral, StringLiteral, Identifier, BinaryOp, UnaryOp,
    Comparison, LogicalOp, NotOp, Cast, ArrayAccess, FieldAccess,
    Dereference, AddressOf, Call, SizeOf, BytesOf, IndexOf,
    Next, Prev, ArrayInitializer, RecordInitializer, NilLiteral
]


# Statement nodes

@dataclass
class VarDecl(Statement):
    """Variable declaration."""
    name: str
    type: Optional[Type]
    init: Optional[Expression]


@dataclass
class ConstDecl(Statement):
    """Constant declaration."""
    name: str
    value: Expression


@dataclass
class Assignment(Statement):
    """Assignment statement."""
    target: Expression
    value: Expression


@dataclass
class MultiAssignment(Statement):
    """Multiple assignment from subroutine with multiple returns."""
    targets: List[Expression]
    value: Call


@dataclass
class IfStmt(Statement):
    """If statement."""
    condition: Expression
    then_body: List[Statement]
    elseifs: List[tuple]  # List of (condition, body) tuples
    else_body: Optional[List[Statement]]


@dataclass
class WhileStmt(Statement):
    """While loop."""
    condition: Expression
    body: List[Statement]


@dataclass
class LoopStmt(Statement):
    """Infinite loop."""
    body: List[Statement]


@dataclass
class BreakStmt(Statement):
    """Break out of loop."""
    pass


@dataclass
class ContinueStmt(Statement):
    """Continue to next loop iteration."""
    pass


@dataclass
class ReturnStmt(Statement):
    """Return from subroutine."""
    pass


@dataclass
class CaseStmt(Statement):
    """Case/switch statement."""
    expr: Expression
    whens: List[tuple]  # List of (values, body) tuples
    else_body: Optional[List[Statement]]


@dataclass
class ExprStmt(Statement):
    """Expression as statement (usually a call)."""
    expr: Expression


@dataclass
class AsmStmt(Statement):
    """Inline assembly."""
    parts: List[Union[str, Expression]]  # Alternating strings and expressions


@dataclass
class NestedSubStmt(Statement):
    """Nested subroutine definition (statement context)."""
    sub: 'SubDecl'


# Declaration nodes

@dataclass
class Parameter(Node):
    """Subroutine parameter."""
    name: str
    type: Type


@dataclass
class SubDecl(Declaration):
    """Subroutine declaration."""
    name: str
    params: List[Parameter]
    returns: List[Parameter]
    body: Optional[List[Statement]]  # None for forward declaration
    extern_name: Optional[str] = None
    implements: Optional[str] = None  # Interface name
    is_decl: bool = False  # @decl forward declaration
    is_impl: bool = False  # @impl implementation


@dataclass
class RecordDecl(Declaration):
    """Record type declaration."""
    record: RecordType


@dataclass
class TypedefDecl(Declaration):
    """Type alias declaration."""
    name: str
    type: Type


@dataclass
class InterfaceDecl(Declaration):
    """Interface declaration."""
    interface: InterfaceType


@dataclass
class IncludeDecl(Declaration):
    """Include directive."""
    path: str


# Top-level

@dataclass
class Program(Node):
    """Top-level program."""
    declarations: List[Declaration]
    statements: List[Statement]
