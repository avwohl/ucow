"""Type system for Cowgol."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from . import ast
from .tokens import SourceLocation


class TypeError(Exception):
    """Type checking error."""
    def __init__(self, message: str, location: SourceLocation):
        self.location = location
        super().__init__(f"{location}: {message}")


# Canonical type representations

@dataclass(frozen=True)
class CowType:
    """Base class for canonical types."""
    pass


@dataclass(frozen=True)
class IntType(CowType):
    """Integer type."""
    size: int  # 1, 2, or 4 bytes
    signed: bool

    def __str__(self) -> str:
        prefix = 'int' if self.signed else 'uint'
        return f"{prefix}{self.size * 8}"


@dataclass(frozen=True)
class PtrType(CowType):
    """Pointer type."""
    target: CowType

    def __str__(self) -> str:
        return f"[{self.target}]"


@dataclass(frozen=True)
class ForwardRefType(CowType):
    """Forward reference to a type that will be defined later."""
    name: str

    def __str__(self) -> str:
        return f"<forward:{self.name}>"


@dataclass(frozen=True)
class ArrayType(CowType):
    """Array type."""
    element: CowType
    size: int  # Number of elements

    def __str__(self) -> str:
        return f"{self.element}[{self.size}]"


@dataclass(frozen=True)
class RecordType(CowType):
    """Record type."""
    name: str
    # Fields stored separately in TypeChecker

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class InterfaceType(CowType):
    """Interface (function pointer) type."""
    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True)
class VoidType(CowType):
    """Void type (for statements)."""
    def __str__(self) -> str:
        return "void"


# Standard types
INT8 = IntType(1, True)
UINT8 = IntType(1, False)
INT16 = IntType(2, True)
UINT16 = IntType(2, False)
INT32 = IntType(4, True)
UINT32 = IntType(4, False)
INTPTR = INT16  # 16-bit target
VOID = VoidType()


@dataclass
class RecordFieldInfo:
    """Information about a record field."""
    name: str
    type: CowType
    offset: int


@dataclass
class RecordInfo:
    """Information about a record type."""
    name: str
    fields: List[RecordFieldInfo]
    size: int
    base: Optional[str] = None


@dataclass
class SubroutineInfo:
    """Information about a subroutine."""
    name: str
    params: List[Tuple[str, CowType]]
    returns: List[Tuple[str, CowType]]
    extern_name: Optional[str] = None
    implements: Optional[str] = None


@dataclass
class InterfaceInfo:
    """Information about an interface."""
    name: str
    params: List[Tuple[str, CowType]]
    returns: List[Tuple[str, CowType]]


@dataclass
class VariableInfo:
    """Information about a variable."""
    name: str
    type: CowType
    is_const: bool = False
    const_value: Optional[int] = None


class Scope:
    """A lexical scope."""
    def __init__(self, parent: Optional['Scope'] = None):
        self.parent = parent
        self.variables: Dict[str, VariableInfo] = {}
        self.types: Dict[str, CowType] = {}

    def define_var(self, name: str, info: VariableInfo) -> None:
        """Define a variable in this scope."""
        if name in self.variables:
            raise ValueError(f"Variable '{name}' already defined in this scope")
        self.variables[name] = info

    def lookup_var(self, name: str) -> Optional[VariableInfo]:
        """Look up a variable, searching parent scopes."""
        if name in self.variables:
            return self.variables[name]
        if self.parent:
            return self.parent.lookup_var(name)
        return None

    def define_type(self, name: str, typ: CowType) -> None:
        """Define a type in this scope."""
        self.types[name] = typ

    def lookup_type(self, name: str) -> Optional[CowType]:
        """Look up a type, searching parent scopes."""
        if name in self.types:
            return self.types[name]
        if self.parent:
            return self.parent.lookup_type(name)
        return None


class TypeChecker:
    """Type checker and semantic analyzer."""

    def __init__(self):
        self.global_scope = Scope()
        self.current_scope = self.global_scope
        self.records: Dict[str, RecordInfo] = {}
        self.subroutines: Dict[str, SubroutineInfo] = {}
        self.interfaces: Dict[str, InterfaceInfo] = {}
        self.constants: Dict[str, int] = {}  # name -> value for constant lookup during codegen
        self.errors: List[str] = []
        self.current_sub: Optional[SubroutineInfo] = None

        # Register built-in types
        self.global_scope.define_type('int8', INT8)
        self.global_scope.define_type('uint8', UINT8)
        self.global_scope.define_type('int16', INT16)
        self.global_scope.define_type('uint16', UINT16)
        self.global_scope.define_type('int32', INT32)
        self.global_scope.define_type('uint32', UINT32)
        self.global_scope.define_type('intptr', INTPTR)
        self.global_scope.define_type('string', PtrType(UINT8))  # string = [uint8]

        # Register built-in subroutines (runtime library)
        self._register_builtins()

    def _is_integer_type(self, typ: CowType) -> bool:
        """Check if a type is an integer type."""
        return typ in (INT8, UINT8, INT16, UINT16, INT32, UINT32, INTPTR)

    def _register_builtins(self) -> None:
        """Register built-in subroutines from runtime library."""
        builtins = [
            # (name, params, returns)
            ('print', [('ptr', PtrType(UINT8))], []),
            ('print_char', [('c', UINT8)], []),
            ('print_nl', [], []),
            ('print_i8', [('value', UINT8)], []),
            ('print_i16', [('value', UINT16)], []),
            ('print_i32', [('value', UINT32)], []),
            ('print_hex_i8', [('value', UINT8)], []),
            ('print_hex_i16', [('value', UINT16)], []),
            ('print_hex_i32', [('value', UINT32)], []),
            ('Exit', [], []),
            ('ExitWithError', [], []),
            ('MemSet', [('ptr', PtrType(UINT8)), ('data', UINT8), ('length', INTPTR)], []),
            ('MemZero', [('ptr', PtrType(UINT8)), ('size', INTPTR)], []),
        ]

        for name, params, returns in builtins:
            info = SubroutineInfo(name, params, returns)
            self.subroutines[name] = info

    def error(self, message: str, location: SourceLocation) -> None:
        """Record a type error."""
        self.errors.append(f"{location}: {message}")

    def push_scope(self) -> Scope:
        """Enter a new scope."""
        self.current_scope = Scope(self.current_scope)
        return self.current_scope

    def pop_scope(self) -> None:
        """Exit current scope."""
        if self.current_scope.parent:
            self.current_scope = self.current_scope.parent

    def resolve_type_allow_forward(self, node: ast.Type) -> CowType:
        """Resolve a type, allowing forward references for record/interface types."""
        if isinstance(node, ast.ScalarType):
            typ = self.current_scope.lookup_type(node.name)
            if typ is None:
                # Allow forward reference - will be resolved later
                return ForwardRefType(node.name)
            return typ
        elif isinstance(node, ast.NamedType):
            typ = self.current_scope.lookup_type(node.name)
            if typ is None:
                return ForwardRefType(node.name)
            return typ
        else:
            # For other types, use regular resolution
            return self.resolve_type(node)

    def resolve_type(self, node: ast.Type) -> CowType:
        """Resolve an AST type node to a canonical type."""
        if isinstance(node, ast.ScalarType):
            typ = self.current_scope.lookup_type(node.name)
            if typ is None:
                self.error(f"Unknown type: {node.name}", node.location)
                return UINT8
            return typ

        elif isinstance(node, ast.PointerType):
            # Allow forward references for pointer targets
            target = self.resolve_type_allow_forward(node.target)
            return PtrType(target)

        elif isinstance(node, ast.ArrayType):
            element = self.resolve_type(node.element)
            if node.size is None:
                # Size must be inferred from initializer
                return ArrayType(element, 0)
            # Try to evaluate size as constant
            size = self.eval_const(node.size)
            if size is None:
                self.error("Array size must be constant", node.location)
                size = 1
            return ArrayType(element, size)

        elif isinstance(node, ast.NamedType):
            typ = self.current_scope.lookup_type(node.name)
            if typ is None:
                self.error(f"Unknown type: {node.name}", node.location)
                return UINT8
            return typ

        elif isinstance(node, ast.IndexOfType):
            # @indexof array_name - return type suitable for indexing the array
            # For simplicity, use uint8 for small arrays, uint16 otherwise
            var_info = self.current_scope.lookup_var(node.target)
            if var_info and isinstance(var_info.type, ArrayType):
                if var_info.type.size <= 256:
                    return UINT8
            return UINT16

        elif isinstance(node, ast.SizeOfType):
            # @sizeof array_name - return type suitable for holding array size
            # For simplicity, use uint8 for small arrays, uint16 otherwise
            var_info = self.current_scope.lookup_var(node.target)
            if var_info and isinstance(var_info.type, ArrayType):
                if var_info.type.size <= 256:
                    return UINT8
            return UINT16

        elif isinstance(node, ast.RangedIntType):
            # int(min, max) - choose smallest integer type that fits the range
            min_val = self.eval_const(node.min_expr)
            max_val = self.eval_const(node.max_expr)
            if min_val is None:
                min_val = 0
            if max_val is None:
                max_val = 65535
            # Determine type based on range
            if min_val >= 0:
                if max_val <= 255:
                    return UINT8
                elif max_val <= 65535:
                    return UINT16
                else:
                    return UINT32
            else:
                if min_val >= -128 and max_val <= 127:
                    return INT8
                elif min_val >= -32768 and max_val <= 32767:
                    return INT16
                else:
                    return INT32

        else:
            self.error(f"Cannot resolve type: {type(node).__name__}", node.location)
            return UINT8

    def eval_const(self, expr: ast.Expression) -> Optional[int]:
        """Try to evaluate an expression as a compile-time constant."""
        if isinstance(expr, ast.NumberLiteral):
            return expr.value

        elif isinstance(expr, ast.Identifier):
            # Check constants dictionary first
            if expr.name in self.constants:
                return self.constants[expr.name]
            info = self.current_scope.lookup_var(expr.name)
            if info and info.is_const and info.const_value is not None:
                return info.const_value
            return None

        elif isinstance(expr, ast.BinaryOp):
            left = self.eval_const(expr.left)
            right = self.eval_const(expr.right)
            if left is None or right is None:
                return None

            ops = {
                '+': lambda a, b: a + b,
                '-': lambda a, b: a - b,
                '*': lambda a, b: a * b,
                '/': lambda a, b: a // b if b != 0 else 0,
                '%': lambda a, b: a % b if b != 0 else 0,
                '&': lambda a, b: a & b,
                '|': lambda a, b: a | b,
                '^': lambda a, b: a ^ b,
                '<<': lambda a, b: a << b,
                '>>': lambda a, b: a >> b,
            }
            if expr.op in ops:
                return ops[expr.op](left, right)
            return None

        elif isinstance(expr, ast.UnaryOp):
            operand = self.eval_const(expr.operand)
            if operand is None:
                return None
            if expr.op == '-':
                return -operand
            if expr.op == '~':
                return ~operand
            return None

        elif isinstance(expr, ast.Cast):
            return self.eval_const(expr.expr)

        return None

    def type_size(self, typ: CowType) -> int:
        """Get the size of a type in bytes."""
        if isinstance(typ, IntType):
            return typ.size
        elif isinstance(typ, PtrType):
            return 2  # 16-bit pointers
        elif isinstance(typ, ArrayType):
            return self.type_size(typ.element) * typ.size
        elif isinstance(typ, RecordType):
            info = self.records.get(typ.name)
            return info.size if info else 0
        elif isinstance(typ, InterfaceType):
            return 2  # Interface references are pointer-sized
        return 0

    def check_expr(self, expr: ast.Expression) -> CowType:
        """Type-check an expression and return its type."""
        if isinstance(expr, ast.NumberLiteral):
            # Numeric literals have flexible type
            expr.resolved_type = INTPTR  # Default
            return INTPTR

        elif isinstance(expr, ast.StringLiteral):
            # String literals are pointers to uint8
            typ = PtrType(UINT8)
            expr.resolved_type = typ
            return typ

        elif isinstance(expr, ast.NilLiteral):
            # nil can be any pointer type
            typ = PtrType(UINT8)
            expr.resolved_type = typ
            return typ

        elif isinstance(expr, ast.Identifier):
            info = self.current_scope.lookup_var(expr.name)
            if info is None:
                # Check if it's a subroutine name
                if expr.name in self.subroutines:
                    sub = self.subroutines[expr.name]
                    if sub.implements:
                        typ = InterfaceType(sub.implements)
                        expr.resolved_type = typ
                        return typ
                    # Return a marker type for subroutines
                    expr.resolved_type = VOID
                    return VOID
                self.error(f"Undefined variable: {expr.name}", expr.location)
                return UINT8
            expr.resolved_type = info.type
            return info.type

        elif isinstance(expr, ast.BinaryOp):
            left_type = self.check_expr(expr.left)
            right_type = self.check_expr(expr.right)

            # Cowgol is permissive with type mixing:
            # - Pointer + integer = pointer (pointer arithmetic)
            # - Integer + integer (any sizes) = result type depends on operation
            # - Bitwise ops on any integer types
            result_type = left_type

            # Pointer arithmetic: pointer +/- integer = pointer
            if isinstance(left_type, PtrType) and self._is_integer_type(right_type):
                result_type = left_type
            elif isinstance(right_type, PtrType) and self._is_integer_type(left_type):
                result_type = right_type
            # Both integers: use the larger/left type
            elif self._is_integer_type(left_type) and self._is_integer_type(right_type):
                result_type = left_type  # Cowgol uses left operand type
            # Otherwise allow with warning if types differ significantly
            elif left_type != right_type:
                # Allow numeric literal flexibility
                if not (isinstance(expr.left, ast.NumberLiteral) or
                        isinstance(expr.right, ast.NumberLiteral)):
                    # Just silently allow - Cowgol is very permissive
                    pass

            expr.resolved_type = result_type
            return result_type

        elif isinstance(expr, ast.UnaryOp):
            operand_type = self.check_expr(expr.operand)
            expr.resolved_type = operand_type
            return operand_type

        elif isinstance(expr, ast.Comparison):
            self.check_expr(expr.left)
            self.check_expr(expr.right)
            # Comparisons return a boolean-ish value but Cowgol has no bool
            expr.resolved_type = UINT8
            return UINT8

        elif isinstance(expr, ast.LogicalOp):
            self.check_expr(expr.left)
            self.check_expr(expr.right)
            expr.resolved_type = UINT8
            return UINT8

        elif isinstance(expr, ast.NotOp):
            self.check_expr(expr.operand)
            expr.resolved_type = UINT8
            return UINT8

        elif isinstance(expr, ast.Cast):
            self.check_expr(expr.expr)
            typ = self.resolve_type(expr.target_type)
            expr.resolved_type = typ
            return typ

        elif isinstance(expr, ast.ArrayAccess):
            array_type = self.check_expr(expr.array)
            self.check_expr(expr.index)

            if isinstance(array_type, ArrayType):
                expr.resolved_type = array_type.element
                return array_type.element
            elif isinstance(array_type, PtrType):
                # Pointer arithmetic - resolve forward refs in target
                target = array_type.target
                if isinstance(target, ForwardRefType):
                    resolved = self.current_scope.lookup_type(target.name)
                    if resolved:
                        target = resolved
                expr.resolved_type = target
                return target
            else:
                self.error(f"Cannot index non-array type: {array_type}", expr.location)
                return UINT8

        elif isinstance(expr, ast.FieldAccess):
            record_type = self.check_expr(expr.record)

            # Handle pointer-to-record
            if isinstance(record_type, PtrType):
                record_type = record_type.target

            # Resolve forward references
            if isinstance(record_type, ForwardRefType):
                resolved = self.current_scope.lookup_type(record_type.name)
                if resolved:
                    record_type = resolved

            if isinstance(record_type, RecordType):
                info = self.records.get(record_type.name)
                if info:
                    for field in info.fields:
                        if field.name == expr.field:
                            expr.resolved_type = field.type
                            return field.type
                self.error(f"Unknown field: {expr.field}", expr.location)
            else:
                self.error(f"Cannot access field of non-record type", expr.location)
            return UINT8

        elif isinstance(expr, ast.Dereference):
            ptr_type = self.check_expr(expr.pointer)
            if isinstance(ptr_type, PtrType):
                expr.resolved_type = ptr_type.target
                return ptr_type.target
            else:
                self.error(f"Cannot dereference non-pointer type", expr.location)
                return UINT8

        elif isinstance(expr, ast.AddressOf):
            typ = self.check_expr(expr.operand)
            result = PtrType(typ)
            expr.resolved_type = result
            return result

        elif isinstance(expr, ast.Call):
            target_type = self.check_expr(expr.target)

            # Look up subroutine
            if isinstance(expr.target, ast.Identifier):
                name = expr.target.name
                if name in self.subroutines:
                    sub = self.subroutines[name]
                    # Check argument count
                    if len(expr.args) != len(sub.params):
                        self.error(
                            f"Wrong number of arguments: expected {len(sub.params)}, got {len(expr.args)}",
                            expr.location
                        )
                    for arg in expr.args:
                        self.check_expr(arg)

                    if sub.returns:
                        expr.resolved_type = sub.returns[0][1]
                        return sub.returns[0][1]
                    expr.resolved_type = VOID
                    return VOID

            # Interface call
            if isinstance(target_type, InterfaceType):
                info = self.interfaces.get(target_type.name)
                if info:
                    for arg in expr.args:
                        self.check_expr(arg)
                    if info.returns:
                        expr.resolved_type = info.returns[0][1]
                        return info.returns[0][1]
                expr.resolved_type = VOID
                return VOID

            self.error(f"Cannot call non-subroutine", expr.location)
            return UINT8

        elif isinstance(expr, ast.SizeOf):
            # Returns the number of elements in an array
            if isinstance(expr.target, ast.Expression):
                typ = self.check_expr(expr.target)
                if isinstance(typ, ArrayType):
                    expr.resolved_type = INTPTR
                    return INTPTR
            self.error("@sizeof requires array", expr.location)
            return INTPTR

        elif isinstance(expr, ast.BytesOf):
            # Returns size in bytes
            # Need to resolve the target's type to compute its size
            if isinstance(expr.target, ast.Expression):
                self.check_expr(expr.target)
            expr.resolved_type = INTPTR
            return INTPTR

        elif isinstance(expr, ast.Next):
            typ = self.check_expr(expr.pointer)
            expr.resolved_type = typ
            return typ

        elif isinstance(expr, ast.Prev):
            typ = self.check_expr(expr.pointer)
            expr.resolved_type = typ
            return typ

        elif isinstance(expr, ast.ArrayInitializer):
            if expr.elements:
                elem_type = self.check_expr(expr.elements[0])
                for elem in expr.elements[1:]:
                    self.check_expr(elem)
                expr.resolved_type = ArrayType(elem_type, len(expr.elements))
                return expr.resolved_type
            return ArrayType(UINT8, 0)

        else:
            self.error(f"Unknown expression type: {type(expr).__name__}", expr.location)
            return UINT8

    def check_stmt(self, stmt: ast.Statement) -> None:
        """Type-check a statement."""
        if isinstance(stmt, ast.VarDecl):
            if stmt.type:
                var_type = self.resolve_type(stmt.type)
            elif stmt.init:
                var_type = self.check_expr(stmt.init)
            else:
                self.error("Variable needs type or initializer", stmt.location)
                var_type = UINT8

            if stmt.init:
                self.check_expr(stmt.init)

            # Handle array size inference
            if isinstance(var_type, ArrayType) and var_type.size == 0:
                if stmt.init and isinstance(stmt.init, ast.ArrayInitializer):
                    var_type = ArrayType(var_type.element, len(stmt.init.elements))

            # Store resolved type on AST node for code generation
            stmt.resolved_type = var_type

            self.current_scope.define_var(
                stmt.name,
                VariableInfo(stmt.name, var_type)
            )

        elif isinstance(stmt, ast.ConstDecl):
            value = self.eval_const(stmt.value)
            if value is None:
                self.error("Constant value must be compile-time constant", stmt.location)
                value = 0

            var_type = self.check_expr(stmt.value)
            self.current_scope.define_var(
                stmt.name,
                VariableInfo(stmt.name, var_type, is_const=True, const_value=value)
            )
            # Also store in constants dict for easy lookup during codegen
            self.constants[stmt.name] = value

        elif isinstance(stmt, ast.Assignment):
            target_type = self.check_expr(stmt.target)
            value_type = self.check_expr(stmt.value)

        elif isinstance(stmt, ast.MultiAssignment):
            for target in stmt.targets:
                self.check_expr(target)
            self.check_expr(stmt.value)

        elif isinstance(stmt, ast.IfStmt):
            self.check_expr(stmt.condition)
            for s in stmt.then_body:
                self.check_stmt(s)
            for cond, body in stmt.elseifs:
                self.check_expr(cond)
                for s in body:
                    self.check_stmt(s)
            if stmt.else_body:
                for s in stmt.else_body:
                    self.check_stmt(s)

        elif isinstance(stmt, ast.WhileStmt):
            self.check_expr(stmt.condition)
            for s in stmt.body:
                self.check_stmt(s)

        elif isinstance(stmt, ast.LoopStmt):
            for s in stmt.body:
                self.check_stmt(s)

        elif isinstance(stmt, ast.CaseStmt):
            self.check_expr(stmt.expr)
            for values, body in stmt.whens:
                for v in values:
                    self.check_expr(v)
                for s in body:
                    self.check_stmt(s)
            if stmt.else_body:
                for s in stmt.else_body:
                    self.check_stmt(s)

        elif isinstance(stmt, ast.ExprStmt):
            self.check_expr(stmt.expr)

        elif isinstance(stmt, (ast.BreakStmt, ast.ContinueStmt, ast.ReturnStmt)):
            pass

        elif isinstance(stmt, ast.AsmStmt):
            for part in stmt.parts:
                if isinstance(part, ast.Expression):
                    self.check_expr(part)

        elif isinstance(stmt, ast.NestedSubStmt):
            # Nested subroutine - check it like a regular sub
            self.check_sub(stmt.sub)

        elif isinstance(stmt, ast.SubDecl):
            self.check_sub(stmt)

        elif isinstance(stmt, ast.RecordDecl):
            self.check_record(stmt)

        elif isinstance(stmt, ast.TypedefDecl):
            self.check_typedef(stmt)

        elif isinstance(stmt, ast.InterfaceDecl):
            self.check_interface(stmt)

    def check_sub(self, decl: ast.SubDecl) -> None:
        """Check a subroutine declaration."""
        # For @impl, get params/returns from existing declaration
        if decl.is_impl:
            existing = self.subroutines.get(decl.name)
            if existing is None:
                self.error(f"No @decl found for @impl {decl.name}", decl.location)
                params = []
                returns = []
            else:
                params = existing.params
                returns = existing.returns
        elif decl.implements and not decl.params:
            # Sub implements interface with no explicit params - get from interface
            iface = self.interfaces.get(decl.implements)
            if iface:
                params = iface.params
                returns = iface.returns
            else:
                params = []
                returns = []
        else:
            # Convert parameters to canonical types
            params = [(p.name, self.resolve_type(p.type)) for p in decl.params]
            returns = [(r.name, self.resolve_type(r.type)) for r in decl.returns]

        info = SubroutineInfo(
            decl.name, params, returns,
            decl.extern_name, decl.implements
        )
        self.subroutines[decl.name] = info

        if decl.body is not None:
            self.push_scope()
            old_sub = self.current_sub
            self.current_sub = info

            # Add parameters to scope
            for name, typ in params:
                self.current_scope.define_var(name, VariableInfo(name, typ))
            for name, typ in returns:
                self.current_scope.define_var(name, VariableInfo(name, typ))

            for stmt in decl.body:
                self.check_stmt(stmt)

            self.current_sub = old_sub
            self.pop_scope()

    def check_record(self, decl: ast.RecordDecl) -> None:
        """Check a record declaration."""
        record = decl.record
        fields = []
        offset = 0

        # Inherit from base
        if record.base:
            base_info = self.records.get(record.base)
            if base_info:
                fields.extend(base_info.fields)
                offset = base_info.size
            else:
                self.error(f"Unknown base record: {record.base}", record.location)

        for f in record.fields:
            field_type = self.resolve_type(f.type)
            if f.offset is not None:
                field_offset = f.offset
            else:
                field_offset = offset
                offset += self.type_size(field_type)

            fields.append(RecordFieldInfo(f.name, field_type, field_offset))

        # Calculate total size (maximum offset + last field size)
        size = 0
        for f in fields:
            end = f.offset + self.type_size(f.type)
            if end > size:
                size = end

        info = RecordInfo(record.name, fields, size, record.base)
        self.records[record.name] = info
        self.current_scope.define_type(record.name, RecordType(record.name))

    def check_typedef(self, decl: ast.TypedefDecl) -> None:
        """Check a typedef declaration."""
        typ = self.resolve_type(decl.type)
        self.current_scope.define_type(decl.name, typ)

    def check_interface(self, decl: ast.InterfaceDecl) -> None:
        """Check an interface declaration."""
        iface = decl.interface
        params = [(p.name, self.resolve_type(p.type)) for p in iface.params]
        returns = [(r.name, self.resolve_type(r.type)) for r in iface.returns]

        info = InterfaceInfo(iface.name, params, returns)
        self.interfaces[iface.name] = info
        self.current_scope.define_type(iface.name, InterfaceType(iface.name))

    def check_program(self, program: ast.Program) -> bool:
        """Type-check an entire program."""
        # Pass 0: collect constants first (needed for array sizes in type declarations)
        for stmt in program.statements:
            if isinstance(stmt, ast.ConstDecl):
                value = self.eval_const(stmt.value)
                if value is not None:
                    self.constants[stmt.name] = value

        # First pass: collect type declarations (records, typedefs, interfaces)
        for decl in program.declarations:
            if isinstance(decl, ast.RecordDecl):
                self.check_record(decl)
            elif isinstance(decl, ast.TypedefDecl):
                self.check_typedef(decl)
            elif isinstance(decl, ast.InterfaceDecl):
                self.check_interface(decl)
            elif isinstance(decl, ast.IncludeDecl):
                pass  # Handled by preprocessor

        # Second pass: register subroutine signatures (needed for interface implementers)
        for decl in program.declarations:
            if isinstance(decl, ast.SubDecl):
                self._register_sub_signature(decl)

        # Third pass: process top-level variable declarations
        # (so they're visible inside subroutines, and can reference subs)
        for stmt in program.statements:
            if isinstance(stmt, (ast.VarDecl, ast.ConstDecl)):
                self.check_stmt(stmt)

        # Fourth pass: check subroutine bodies (references global vars)
        for decl in program.declarations:
            if isinstance(decl, ast.SubDecl):
                self.check_sub(decl)

        # Fifth pass: check remaining top-level statements
        for stmt in program.statements:
            if not isinstance(stmt, (ast.VarDecl, ast.ConstDecl)):
                self.check_stmt(stmt)

        return len(self.errors) == 0

    def _register_sub_signature(self, decl: ast.SubDecl) -> None:
        """Register a subroutine signature without checking its body."""
        # Skip if already registered (e.g., @decl/@impl)
        if decl.name in self.subroutines and not decl.is_impl:
            return

        # For @impl, the signature comes from @decl
        if decl.is_impl:
            return

        # Resolve parameter and return types
        if decl.implements and not decl.params:
            # Get params from interface
            iface = self.interfaces.get(decl.implements)
            if iface:
                params = iface.params
                returns = iface.returns
            else:
                params = []
                returns = []
        else:
            params = [(p.name, self.resolve_type(p.type)) for p in decl.params]
            returns = [(r.name, self.resolve_type(r.type)) for r in decl.returns]

        # Register the subroutine
        self.subroutines[decl.name] = SubroutineInfo(
            name=decl.name,
            params=params,
            returns=returns,
            extern_name=decl.extern_name,
            implements=decl.implements
        )
