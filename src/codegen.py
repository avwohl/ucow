"""Z80 code generator for Cowgol.

Instruction reference (bytes/cycles):
  Z80 vs 8080 differences to note:
  - LD r,r (1/4) vs MOV (1/5) - Z80 faster
  - ADD HL,rp (1/11) vs DAD (1/10) - 8080 faster!
  - INC rp (1/6) vs INX (1/5) - 8080 faster!
  - DEC rp (1/6) vs DCX (1/5) - 8080 faster!
  - JP (3/10) vs JMP (3/10) - same
  - JR (2/12) - saves byte but slower than JP!
  - DJNZ (2/13|8) - great for loops (vs DCR+JNZ = 4 bytes)
  - NEG (2/8) - negate A
  - LDIR (2/21) - block copy

See docs/z80_8080_reference.md for full details.
"""

from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, field
from . import ast
from .types import (
    TypeChecker, CowType, IntType, PtrType, ArrayType, RecordType,
    InterfaceType, INT8, UINT8, INT16, UINT16, INT32, UINT32, INTPTR,
    RecordFieldInfo, SubroutineInfo
)


@dataclass
class Label:
    """A code label."""
    name: str
    counter: int = 0

    @classmethod
    def new(cls, prefix: str = "L") -> 'Label':
        cls.counter += 1
        return cls(f"{prefix}{cls.counter}")


@dataclass
class Variable:
    """Variable allocation info."""
    name: str
    type: CowType
    offset: int  # Offset from data segment start
    size: int


@dataclass
class InlineCandidate:
    """Info about a subroutine that might be inlined."""
    name: str
    decl: ast.SubDecl
    body_size: int  # Estimated size in bytes
    has_local_vars: bool
    has_loops: bool
    call_count: int = 0  # Number of call sites


class CodeGenerator:
    """Generate Z80 assembly from Cowgol AST."""

    def __init__(self, checker: TypeChecker):
        self.checker = checker
        self.output: List[str] = []
        self.data: List[str] = []
        self.variables: Dict[str, Variable] = {}
        self.string_literals: Dict[str, str] = {}  # value -> label
        self.array_initializers: Dict[str, tuple] = {}  # name -> (values, elem_size)
        self.data_offset = 0
        self.label_counter = 0
        self.current_sub: Optional[str] = None
        self.break_labels: List[str] = []
        self.continue_labels: List[str] = []
        # Register tracking for optimization
        self.hl_contains: Optional[str] = None  # Variable name or None
        self.a_contains: Optional[str] = None   # Variable name or None
        # Inlining support
        self.inline_candidates: Dict[str, InlineCandidate] = {}
        self.inlined_subs: Set[str] = set()  # Subs that were inlined (don't generate)
        self.generating_inline: bool = False  # True when generating inline code
        # External symbol tracking for multi-file linking
        self.extern_symbols: Set[str] = set()  # External symbols to import
        # Library mode - no main entry point or runtime
        self.library_mode: bool = False
        # Workspace optimization (set by caller for multi-file mode)
        self.call_graph = None  # CallGraph instance for workspace offsets
        self.global_data_offset = 0  # Size of global variables
        # Track per-subroutine local offsets when using workspace optimization
        self.sub_local_offsets: Dict[str, int] = {}  # sub_name -> current offset within workspace

    def invalidate_regs(self) -> None:
        """Invalidate register tracking (after calls, jumps, etc)."""
        self.hl_contains = None
        self.a_contains = None

    # === Inlining analysis ===

    def analyze_for_inlining(self, program: ast.Program) -> None:
        """Analyze program to find subroutines suitable for inlining."""
        # First pass: find small subroutines
        for decl in program.declarations:
            if isinstance(decl, ast.SubDecl) and decl.body:
                candidate = self._analyze_sub_for_inline(decl)
                if candidate:
                    self.inline_candidates[decl.name] = candidate

        # Second pass: count call sites
        self._count_calls_in_stmts(program.statements)
        for decl in program.declarations:
            if isinstance(decl, ast.SubDecl) and decl.body:
                self._count_calls_in_stmts(decl.body)

        # Decide which to inline based on SIZE reduction (not speed).
        # Original: body_size + N * 3 (CALL) + 1 (RET) = body_size + 3*N + 1
        # Inlined:  N * body_size (body duplicated N times, no CALL/RET)
        # Inline if: N * body_size < body_size + 3*N + 1
        #         -> (N-1) * body_size < 3*N + 1
        # For N=1: 0 < 4, always true (inline subs called once)
        # For N=2: body_size < 7
        # For N=3: 2*body_size < 10 -> body_size < 5
        # For N>=4: body_size <= 4
        for name, candidate in self.inline_candidates.items():
            n = candidate.call_count
            if n == 0:
                continue  # Not called, don't inline (will be dead code)
            body = candidate.body_size
            # Calculate size with and without inlining
            size_with_sub = body + 3 * n + 1  # body + N calls + RET
            size_inlined = n * body           # body repeated N times
            if size_inlined < size_with_sub:
                self.inlined_subs.add(name)

    def _analyze_sub_for_inline(self, decl: ast.SubDecl) -> Optional[InlineCandidate]:
        """Analyze a subroutine to see if it's a candidate for inlining."""
        if not decl.body:
            return None

        # Don't inline subs with parameters or return values (complex setup)
        sub_info = self.checker.subroutines.get(decl.name)
        if sub_info and (sub_info.params or sub_info.returns):
            return None

        # Check for local variable declarations and loops
        has_local_vars = False
        has_loops = False
        body_size = 0

        for stmt in decl.body:
            has_local_vars, has_loops, size = self._analyze_stmt_for_inline(
                stmt, has_local_vars, has_loops
            )
            body_size += size

        # Don't inline if has local vars (need allocation) or loops (break/continue issues)
        if has_local_vars or has_loops:
            return None

        # Don't inline if body is too large (> 20 bytes)
        if body_size > 20:
            return None

        return InlineCandidate(
            name=decl.name,
            decl=decl,
            body_size=body_size,
            has_local_vars=has_local_vars,
            has_loops=has_loops
        )

    def _analyze_stmt_for_inline(self, stmt: ast.Statement,
                                  has_local_vars: bool,
                                  has_loops: bool) -> Tuple[bool, bool, int]:
        """Analyze statement for inlining. Returns (has_local_vars, has_loops, size_estimate)."""
        size = 0

        if isinstance(stmt, ast.VarDecl):
            has_local_vars = True
            size = 3  # Approximate

        elif isinstance(stmt, ast.Assignment):
            # Estimate: load value (3-6) + store (3)
            size = 6

        elif isinstance(stmt, ast.ExprStmt):
            if isinstance(stmt.expr, ast.Call):
                size = 6  # CALL + stack cleanup
            else:
                size = 3

        elif isinstance(stmt, ast.ReturnStmt):
            size = 1  # RET

        elif isinstance(stmt, ast.WhileStmt):
            has_loops = True
            size = 10

        elif isinstance(stmt, ast.LoopStmt):
            has_loops = True
            size = 10

        elif isinstance(stmt, ast.IfStmt):
            size = 6
            for s in stmt.then_body:
                has_local_vars, has_loops, s_size = self._analyze_stmt_for_inline(
                    s, has_local_vars, has_loops
                )
                size += s_size
            if stmt.else_body:
                for s in stmt.else_body:
                    has_local_vars, has_loops, s_size = self._analyze_stmt_for_inline(
                        s, has_local_vars, has_loops
                    )
                    size += s_size

        elif isinstance(stmt, ast.AsmStmt):
            # Count assembly parts
            size = len([p for p in stmt.parts if isinstance(p, str)])

        return has_local_vars, has_loops, size

    def _count_calls_in_stmts(self, stmts: List[ast.Statement]) -> None:
        """Count calls to inline candidates in statements."""
        for stmt in stmts:
            self._count_calls_in_stmt(stmt)

    def _count_calls_in_stmt(self, stmt: ast.Statement) -> None:
        """Count calls in a single statement."""
        if isinstance(stmt, ast.ExprStmt):
            self._count_calls_in_expr(stmt.expr)
        elif isinstance(stmt, ast.Assignment):
            self._count_calls_in_expr(stmt.value)
        elif isinstance(stmt, ast.IfStmt):
            self._count_calls_in_expr(stmt.condition)
            self._count_calls_in_stmts(stmt.then_body)
            for cond, body in stmt.elseifs:
                self._count_calls_in_expr(cond)
                self._count_calls_in_stmts(body)
            if stmt.else_body:
                self._count_calls_in_stmts(stmt.else_body)
        elif isinstance(stmt, ast.WhileStmt):
            self._count_calls_in_expr(stmt.condition)
            self._count_calls_in_stmts(stmt.body)
        elif isinstance(stmt, ast.LoopStmt):
            self._count_calls_in_stmts(stmt.body)
        elif isinstance(stmt, ast.CaseStmt):
            self._count_calls_in_expr(stmt.expr)
            for vals, body in stmt.whens:
                self._count_calls_in_stmts(body)
            if stmt.else_body:
                self._count_calls_in_stmts(stmt.else_body)

    def _count_calls_in_expr(self, expr: ast.Expression) -> None:
        """Count calls in an expression."""
        if expr is None:
            return
        if isinstance(expr, ast.Call):
            if isinstance(expr.target, ast.Identifier):
                name = expr.target.name
                if name in self.inline_candidates:
                    self.inline_candidates[name].call_count += 1
            for arg in expr.args:
                self._count_calls_in_expr(arg)
        elif isinstance(expr, ast.BinaryOp):
            self._count_calls_in_expr(expr.left)
            self._count_calls_in_expr(expr.right)
        elif isinstance(expr, ast.UnaryOp):
            self._count_calls_in_expr(expr.operand)
        elif isinstance(expr, ast.Comparison):
            self._count_calls_in_expr(expr.left)
            self._count_calls_in_expr(expr.right)
        elif isinstance(expr, ast.LogicalOp):
            self._count_calls_in_expr(expr.left)
            self._count_calls_in_expr(expr.right)
        elif isinstance(expr, ast.NotOp):
            self._count_calls_in_expr(expr.operand)
        elif isinstance(expr, ast.ArrayAccess):
            self._count_calls_in_expr(expr.array)
            self._count_calls_in_expr(expr.index)

    def emit(self, line: str) -> None:
        """Emit an assembly line."""
        self.output.append(line)
        # Invalidate tracking for instructions that modify HL or A
        stripped = line.strip().upper()
        # HL-modifying instructions (Z80 mnemonics)
        if any(stripped.startswith(x) for x in ['CALL', 'POP', 'ADD\tHL', 'INC\tHL', 'DEC\tHL', 'EX\tDE', 'LD\tHL', 'LD\tH', 'LD\tL']):
            self.hl_contains = None
        # A-modifying instructions (Z80 mnemonics)
        if any(stripped.startswith(x) for x in ['CALL', 'POP', 'ADD\tA', 'ADC\tA', 'SUB', 'SBC', 'AND', 'OR', 'XOR', 'INC\tA', 'DEC\tA', 'CPL', 'RLA', 'RRA', 'LD\tA', 'CP']):
            self.a_contains = None

    def emit_label(self, label: str) -> None:
        """Emit a label."""
        self.output.append(f"{label}:")
        # Invalidate register tracking at labels - we might jump here from elsewhere
        self.hl_contains = None
        self.a_contains = None

    def emit_data(self, line: str) -> None:
        """Emit a data line."""
        self.data.append(line)

    def new_label(self, prefix: str = "L") -> str:
        """Generate a new unique label."""
        self.label_counter += 1
        return f"{prefix}{self.label_counter}"

    def type_size(self, typ: CowType) -> int:
        """Get size of a type in bytes."""
        return self.checker.type_size(typ)

    def _var_key(self, name: str, is_local: bool = None) -> str:
        """Get the dictionary key for a variable.

        Local variables are scoped to their subroutine to avoid name collisions
        when different subroutines have local variables with the same name.
        """
        # If is_local is explicitly False, it's a global
        if is_local is False:
            return name
        # If is_local is True or we're in a subroutine context, scope the name
        if is_local or self.current_sub:
            # Check if this is a global variable (already in variables as unscoped)
            if name in self.variables and not is_local:
                return name
            # Local variable - scope to current subroutine
            if self.current_sub:
                return f"{self.current_sub}.{name}"
        return name

    def get_var(self, name: str) -> Optional[Variable]:
        """Look up a variable, checking local scope first then global."""
        # First try local scoped name
        if self.current_sub:
            local_key = f"{self.current_sub}.{name}"
            if local_key in self.variables:
                return self.variables[local_key]
        # Fall back to global
        return self.variables.get(name)

    def allocate_var(self, name: str, typ: CowType, is_local: bool = False) -> Variable:
        """Allocate storage for a variable.

        Args:
            name: Variable name
            typ: Variable type
            is_local: True if this is a subroutine local variable

        When call_graph is set (workspace optimization mode), local variables
        are allocated within their subroutine's workspace at the optimized offset.
        Non-concurrent subroutines share the same memory addresses.
        """
        size = self.type_size(typ)
        var_key = self._var_key(name, is_local)

        if self.call_graph and is_local and self.current_sub:
            # Workspace optimization mode: allocate within subroutine's workspace
            workspace_base = self.call_graph.get_workspace_offset(self.current_sub)

            # Get current offset within this sub's workspace
            if self.current_sub not in self.sub_local_offsets:
                self.sub_local_offsets[self.current_sub] = 0

            local_offset = self.sub_local_offsets[self.current_sub]
            var = Variable(name, typ, workspace_base + local_offset, size)
            self.sub_local_offsets[self.current_sub] += size
        else:
            # Standard mode: linear allocation
            var = Variable(name, typ, self.data_offset, size)
            self.data_offset += size

        self.variables[var_key] = var
        return var

    def get_string_label(self, value: str) -> str:
        """Get or create a label for a string literal."""
        if value not in self.string_literals:
            label = self.new_label("STR")
            self.string_literals[value] = label
        return self.string_literals[value]

    def mangle_name(self, name: str) -> str:
        """Mangle a variable name to avoid conflicts with 8080 registers.

        For local variables in subroutines, includes the subroutine name to avoid
        collisions with variables of the same name in other subroutines.
        """
        # Check if this is a local variable (exists in scoped form)
        if self.current_sub:
            local_key = f"{self.current_sub}.{name}"
            if local_key in self.variables:
                # Use subroutine-scoped name for local variable
                return f"v_{self.current_sub}_{name}"
        # Global variable or not found - use simple name
        return f"v_{name}"

    def mangle_var_key(self, var_key: str) -> str:
        """Mangle a variable dictionary key to a label.

        The key is either "name" for globals or "sub.name" for locals.
        """
        if '.' in var_key:
            sub, name = var_key.split('.', 1)
            return f"v_{sub}_{name}"
        return f"v_{var_key}"

    def eval_const_expr(self, expr) -> Optional[int]:
        """Try to evaluate an expression as a compile-time constant.
        Returns the integer value if constant, None otherwise."""
        if isinstance(expr, ast.NumberLiteral):
            return expr.value
        elif isinstance(expr, ast.Identifier):
            if expr.name in self.checker.constants:
                return self.checker.constants[expr.name]
        elif isinstance(expr, ast.UnaryOp):
            operand = self.eval_const_expr(expr.operand)
            if operand is not None:
                if expr.op == '-':
                    return -operand & 0xFFFF
                elif expr.op == '~':
                    return ~operand & 0xFFFF
        elif isinstance(expr, ast.BinaryOp):
            left = self.eval_const_expr(expr.left)
            right = self.eval_const_expr(expr.right)
            if left is not None and right is not None:
                if expr.op == '+':
                    return (left + right) & 0xFFFF
                elif expr.op == '-':
                    return (left - right) & 0xFFFF
                elif expr.op == '*':
                    return (left * right) & 0xFFFF
                elif expr.op == '|':
                    return left | right
                elif expr.op == '&':
                    return left & right
                elif expr.op == '^':
                    return left ^ right
        return None

    def mangle_sub_name(self, name: str) -> str:
        """Mangle a subroutine name to avoid conflicts with Z80 registers."""
        # Only mangle if it conflicts with Z80 register names
        if name.upper() in ('A', 'B', 'C', 'D', 'E', 'H', 'L', 'M', 'SP', 'AF', 'BC', 'DE', 'HL', 'IX', 'IY'):
            return f"s_{name}"
        return name

    # Code generation for expressions

    def gen_expr(self, expr: ast.Expression, target: str = 'HL') -> None:
        """Generate code to evaluate expression, result in target (HL or A)."""

        # Try constant folding first - evaluate at compile time if possible
        const_val = self.eval_const_expr(expr)
        if const_val is not None:
            if target == 'A':
                self.emit(f"\tLD\tA,{const_val & 0xFF}")
            else:
                self.emit(f"\tLD\tHL,{const_val & 0xFFFF}")
            return

        if isinstance(expr, ast.NumberLiteral):
            value = expr.value
            if target == 'A':
                self.emit(f"\tLD\tA,{value & 0xFF}")
            else:
                self.emit(f"\tLD\tHL,{value & 0xFFFF}")

        elif isinstance(expr, ast.StringLiteral):
            label = self.get_string_label(expr.value)
            self.emit(f"\tLD\tHL,{label}")

        elif isinstance(expr, ast.NilLiteral):
            if target == 'A':
                self.emit("\tXOR\tA")
            else:
                self.emit("\tLD\tHL,0")

        elif isinstance(expr, ast.Identifier):
            var = self.get_var(expr.name)
            if var:
                mangled = self.mangle_name(expr.name)
                if var.size == 1:
                    # Check if A already has this value
                    if self.a_contains != expr.name:
                        self.emit(f"\tLD\tA,({mangled})")
                        self.a_contains = expr.name
                    if target == 'HL':
                        self.emit("\tLD\tL,A")
                        self.emit("\tLD\tH,0")
                        self.hl_contains = None  # HL modified
                else:
                    # Check if HL already has this value
                    if self.hl_contains != expr.name:
                        self.emit(f"\tLD\tHL,({mangled})")
                        self.hl_contains = expr.name
                    if target == 'A':
                        self.emit("\tLD\tA,L")
                        self.a_contains = None  # A modified
            else:
                # Check if it's a constant
                if expr.name in self.checker.constants:
                    value = self.checker.constants[expr.name]
                    if target == 'A':
                        self.emit(f"\tLD\tA,{value & 0xFF}")
                    else:
                        self.emit(f"\tLD\tHL,{value & 0xFFFF}")
                # Could be a subroutine reference
                elif expr.name in self.checker.subroutines:
                    self.emit(f"\tLD\tHL,{self.mangle_sub_name(expr.name)}")
                else:
                    # External symbol
                    self.emit(f"\tLD\tHL,{expr.name}")

        elif isinstance(expr, ast.BinaryOp):
            self.gen_binop(expr, target)

        elif isinstance(expr, ast.UnaryOp):
            if expr.op == '-':
                self.gen_expr(expr.operand, target)
                if target == 'A':
                    self.emit("\tCPL")
                    self.emit("\tINC\tA")
                else:
                    # Negate HL: HL = 0 - HL
                    self.emit("\tLD\tA,L")
                    self.emit("\tCPL")
                    self.emit("\tLD\tL,A")
                    self.emit("\tLD\tA,H")
                    self.emit("\tCPL")
                    self.emit("\tLD\tH,A")
                    self.emit("\tINC\tHL")
            elif expr.op == '~':
                operand_size = self.type_size(expr.operand.resolved_type)
                if operand_size == 1:
                    # 8-bit NOT
                    self.gen_expr(expr.operand, 'A')
                    self.emit("\tCPL")
                    if target == 'HL':
                        self.emit("\tLD\tL,A")
                        self.emit("\tLD\tH,0")  # Zero-extend result
                else:
                    # 16-bit NOT
                    self.gen_expr(expr.operand, target)
                    if target == 'A':
                        self.emit("\tCPL")
                    else:
                        self.emit("\tLD\tA,L")
                        self.emit("\tCPL")
                        self.emit("\tLD\tL,A")
                        self.emit("\tLD\tA,H")
                        self.emit("\tCPL")
                        self.emit("\tLD\tH,A")

        elif isinstance(expr, ast.Comparison):
            self.gen_comparison(expr)
            if target == 'HL':
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tH,0")

        elif isinstance(expr, ast.LogicalOp):
            self.gen_logical(expr)
            if target == 'HL':
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tH,0")

        elif isinstance(expr, ast.NotOp):
            self.gen_expr(expr.operand, 'A')
            self.emit("\tOR\tA")  # Set flags
            self.emit("\tLD\tA,0")
            self.emit("\tJP\tNZ,$+4")
            self.emit("\tLD\tA,1")
            if target == 'HL':
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tH,0")

        elif isinstance(expr, ast.Cast):
            # Generate the inner expression
            src_type = expr.expr.resolved_type
            dst_type = expr.resolved_type
            src_size = self.type_size(src_type) if src_type else 2
            dst_size = self.type_size(dst_type) if dst_type else 2

            # Check if this is a sign-extending cast (signed 8-bit to 16-bit)
            src_signed = isinstance(src_type, IntType) and src_type.signed
            dst_signed = isinstance(dst_type, IntType) and dst_type.signed

            if src_size == 1 and dst_size == 2 and src_signed and dst_signed and target == 'HL':
                # Sign-extend int8 to int16
                self.gen_expr(expr.expr, 'A')
                self.emit("\tLD\tL,A")
                self.emit("\tRLCA")           # Rotate sign bit into carry
                self.emit("\tSBC\tA,A")       # A = 0xFF if carry, 0x00 if not
                self.emit("\tLD\tH,A")        # H = sign extension
            else:
                # Normal cast - just generate the inner expression
                self.gen_expr(expr.expr, target)

        elif isinstance(expr, ast.ArrayAccess):
            self.gen_array_access(expr, target)

        elif isinstance(expr, ast.FieldAccess):
            self.gen_field_access(expr, target)

        elif isinstance(expr, ast.Dereference):
            self.gen_expr(expr.pointer, 'HL')
            # Load from address in HL
            typ = expr.resolved_type
            if self.type_size(typ) == 1:
                self.emit("\tLD\tA,(HL)")
                if target == 'HL':
                    self.emit("\tLD\tL,A")
                    self.emit("\tLD\tH,0")
            else:
                self.emit("\tLD\tE,(HL)")
                self.emit("\tINC\tHL")
                self.emit("\tLD\tD,(HL)")
                self.emit("\tEX\tDE,HL")

        elif isinstance(expr, ast.AddressOf):
            # Get address of operand
            if isinstance(expr.operand, ast.Identifier):
                var = self.get_var(expr.operand.name)
                if var:
                    self.emit(f"\tLD\tHL,{self.mangle_name(expr.operand.name)}")
                else:
                    # External or unresolved - use bare name
                    self.emit(f"\tLD\tHL,{expr.operand.name}")
            elif isinstance(expr.operand, ast.FieldAccess):
                self.gen_field_address(expr.operand)
            elif isinstance(expr.operand, ast.ArrayAccess):
                self.gen_array_address(expr.operand)

        elif isinstance(expr, ast.Call):
            self.gen_call(expr, target)

        elif isinstance(expr, ast.SizeOf):
            # Return array size in BYTES (element count * element size)
            if isinstance(expr.target, ast.Expression):
                typ = expr.target.resolved_type
                if isinstance(typ, ArrayType):
                    elem_size = self.type_size(typ.element)
                    byte_size = typ.size * elem_size
                    if target == 'A':
                        self.emit(f"\tLD\tA,{byte_size & 0xFF}")
                    else:
                        self.emit(f"\tLD\tHL,{byte_size}")

        elif isinstance(expr, ast.BytesOf):
            # Return size in bytes
            if isinstance(expr.target, ast.Expression):
                typ = expr.target.resolved_type
                size = self.type_size(typ)
            else:
                size = self.type_size(self.checker.resolve_type(expr.target))
            if target == 'A':
                self.emit(f"\tLD\tA,{size & 0xFF}")
            else:
                self.emit(f"\tLD\tHL,{size}")

        elif isinstance(expr, ast.Next):
            self.gen_expr(expr.pointer, 'HL')
            typ = expr.resolved_type
            if isinstance(typ, PtrType):
                size = self.type_size(typ.target)
                if size == 1:
                    self.emit("\tINC\tHL")
                else:
                    self.emit(f"\tLD\tDE,{size}")
                    self.emit("\tADD\tHL,DE")

        elif isinstance(expr, ast.Prev):
            self.gen_expr(expr.pointer, 'HL')
            typ = expr.resolved_type
            if isinstance(typ, PtrType):
                size = self.type_size(typ.target)
                if size == 1:
                    self.emit("\tDEC\tHL")
                else:
                    # HL = HL - size
                    self.emit(f"\tLD\tDE,-{size}")
                    self.emit("\tADD\tHL,DE")

        elif isinstance(expr, ast.ArrayInitializer):
            # This should only appear in variable initialization
            pass

        else:
            self.emit(f"\t; TODO: {type(expr).__name__}")

    def is_simple_expr(self, expr: ast.Expression) -> bool:
        """Check if expression can be loaded into DE without using HL.

        Simple expressions are:
        - Number literals (LD DE,nn is 3 bytes, saves 1 byte vs LD HL,nn/EX)
        - String literals (address)
        - Constants

        Note: Variable loads via LD DE,(var) are 4 bytes (ED prefix), same as
        LD HL,(var)/EX DE,HL, so no savings. Only optimize for constants.
        """
        if isinstance(expr, (ast.NumberLiteral, ast.StringLiteral, ast.NilLiteral)):
            return True
        if isinstance(expr, ast.Identifier):
            # Check if it's a constant
            if expr.name in self.checker.constants:
                return True
        return False

    def gen_expr_to_de(self, expr: ast.Expression) -> None:
        """Generate code to load a simple expression directly into DE."""
        if isinstance(expr, ast.NumberLiteral):
            self.emit(f"\tLD\tDE,{expr.value & 0xFFFF}")
        elif isinstance(expr, ast.StringLiteral):
            label = self.get_string_label(expr.value)
            self.emit(f"\tLD\tDE,{label}")
        elif isinstance(expr, ast.NilLiteral):
            self.emit("\tLD\tDE,0")
        elif isinstance(expr, ast.Identifier):
            if expr.name in self.checker.constants:
                value = self.checker.constants[expr.name]
                self.emit(f"\tLD\tDE,{value & 0xFFFF}")

    def gen_binop(self, expr: ast.BinaryOp, target: str) -> None:
        """Generate binary operation."""
        op = expr.op

        # For 8-bit operations
        if self.type_size(expr.resolved_type) == 1:
            self.gen_expr(expr.left, 'A')
            self.emit("\tPUSH\tAF")
            self.gen_expr(expr.right, 'A')
            self.emit("\tLD\tE,A")  # Use E for second operand (runtime expects E)
            self.emit("\tPOP\tAF")

            if op == '+':
                self.emit("\tADD\tA,E")
            elif op == '-':
                self.emit("\tSUB\tE")
            elif op == '&':
                self.emit("\tAND\tE")
            elif op == '|':
                self.emit("\tOR\tE")
            elif op == '^':
                self.emit("\tXOR\tE")
            elif op == '*':
                self.emit("\tCALL\t_mul8")  # A * E -> HL (16-bit result)
                # Result already in HL, extract to A if needed
                if target == 'A':
                    self.emit("\tLD\tA,L")
                return  # Don't do the normal A->HL conversion
            elif op == '/':
                self.emit("\tCALL\t_div8")  # A / E -> A quotient, L remainder
                # Result in A, needs normal handling
            elif op == '%':
                self.emit("\tCALL\t_mod8")  # A % E -> L remainder
                self.emit("\tLD\tA,L")  # Move result to A
            elif op == '<<':
                # Shift left by E
                label = self.new_label("SHL")
                end = self.new_label("SHLE")
                self.emit_label(label)
                self.emit("\tLD\tC,A")  # Save value
                self.emit("\tLD\tA,E")
                self.emit("\tOR\tA")
                self.emit(f"\tJP\tZ,{end}")
                self.emit("\tDEC\tE")
                self.emit("\tLD\tA,C")
                self.emit("\tADD\tA,A")
                self.emit(f"\tJP\t{label}")
                self.emit_label(end)
            elif op == '>>':
                # Shift right by E
                label = self.new_label("SHR")
                end = self.new_label("SHRE")
                self.emit_label(label)
                self.emit("\tLD\tC,A")  # Save value
                self.emit("\tLD\tA,E")
                self.emit("\tOR\tA")
                self.emit(f"\tJP\tZ,{end}")
                self.emit("\tDEC\tE")
                self.emit("\tLD\tA,C")
                self.emit("\tOR\tA")  # Clear carry for RRA
                self.emit("\tRRA")
                self.emit(f"\tJP\t{label}")
                self.emit_label(end)

            if target == 'HL':
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tH,0")

        else:
            # 16-bit operations
            # Optimization: if right operand is simple, load it directly into DE
            # This avoids PUSH/POP/EX overhead (saves 3 bytes per operation)
            if self.is_simple_expr(expr.right):
                self.gen_expr(expr.left, 'HL')
                self.gen_expr_to_de(expr.right)
            else:
                # General case: need to preserve left while evaluating right
                self.gen_expr(expr.left, 'HL')
                self.emit("\tPUSH\tHL")
                self.gen_expr(expr.right, 'HL')
                self.emit("\tEX\tDE,HL")  # DE = right
                self.emit("\tPOP\tHL")  # HL = left

            if op == '+':
                self.emit("\tADD\tHL,DE")
            elif op == '-':
                # HL = HL - DE
                self.emit("\tLD\tA,L")
                self.emit("\tSUB\tE")
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tA,H")
                self.emit("\tSBC\tA,D")
                self.emit("\tLD\tH,A")
            elif op == '&':
                self.emit("\tLD\tA,L")
                self.emit("\tAND\tE")
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tA,H")
                self.emit("\tAND\tD")
                self.emit("\tLD\tH,A")
            elif op == '|':
                self.emit("\tLD\tA,L")
                self.emit("\tOR\tE")
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tA,H")
                self.emit("\tOR\tD")
                self.emit("\tLD\tH,A")
            elif op == '^':
                self.emit("\tLD\tA,L")
                self.emit("\tXOR\tE")
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tA,H")
                self.emit("\tXOR\tD")
                self.emit("\tLD\tH,A")
            elif op == '*':
                self.emit("\tCALL\t_mul16")
            elif op == '/':
                self.emit("\tCALL\t_div16")
            elif op == '%':
                self.emit("\tCALL\t_mod16")
            elif op == '<<':
                self.emit("\tLD\tA,E")  # Shift count in A
                self.emit("\tCALL\t_shl16")
            elif op == '>>':
                self.emit("\tLD\tA,E")  # Shift count in A
                self.emit("\tCALL\t_shr16")

            if target == 'A':
                self.emit("\tLD\tA,L")

    def gen_comparison(self, expr: ast.Comparison) -> None:
        """Generate comparison, result in A (0 or 1)."""
        op = expr.op

        # Check for comparison with zero - can optimize
        is_zero_right = isinstance(expr.right, ast.NumberLiteral) and expr.right.value == 0
        is_zero_left = isinstance(expr.left, ast.NumberLiteral) and expr.left.value == 0

        # Optimize: x == 0 or x != 0
        if is_zero_right and op in ('==', '!='):
            self.gen_expr(expr.left, 'HL')
            self.emit("\tLD\tA,H")
            self.emit("\tOR\tL")
            if op == '==':
                # Result is 1 if zero, 0 if nonzero
                end = self.new_label("END")
                self.emit(f"\tJP\tNZ,{end}")
                self.emit("\tLD\tA,1")
                self.emit_label(end)
            else:  # !=
                # Result is 0 if zero, 1 if nonzero
                end = self.new_label("END")
                self.emit(f"\tJP\tZ,{end}")
                self.emit("\tLD\tA,1")
                self.emit_label(end)
            return

        # Optimized comparison when right side is a constant
        right_const = self.eval_const_expr(expr.right)
        if right_const is not None:
            # Generate: left in HL, const in DE directly (no push/pop)
            self.gen_expr(expr.left, 'HL')
            self.emit(f"\tLD\tDE,{right_const}")
        else:
            # General comparison with push/pop
            self.gen_expr(expr.left, 'HL')
            self.emit("\tPUSH\tHL")
            self.gen_expr(expr.right, 'HL')
            self.emit("\tEX\tDE,HL")
            self.emit("\tPOP\tHL")

        # Compare HL with DE
        self.emit("\tLD\tA,H")
        self.emit("\tCP\tD")
        self.emit("\tJP\tNZ,$+6")
        self.emit("\tLD\tA,L")
        self.emit("\tCP\tE")

        # Set result based on flags
        true_label = self.new_label("TRUE")
        end_label = self.new_label("END")

        false_label = self.new_label("FALSE")

        if op == '==':
            self.emit(f"\tJP\tZ,{true_label}")
        elif op == '!=':
            self.emit(f"\tJP\tNZ,{true_label}")
        elif op == '<':
            self.emit(f"\tJP\tC,{true_label}")
        elif op == '>=':
            self.emit(f"\tJP\tNC,{true_label}")
        elif op == '>':
            self.emit(f"\tJP\tZ,{false_label}")  # Equal means not greater
            self.emit(f"\tJP\tNC,{true_label}")
        elif op == '<=':
            self.emit(f"\tJP\tZ,{true_label}")  # Equal means <=
            self.emit(f"\tJP\tC,{true_label}")

        self.emit_label(false_label)
        self.emit("\tXOR\tA")  # False
        self.emit(f"\tJP\t{end_label}")
        self.emit_label(true_label)
        self.emit("\tLD\tA,1")  # True
        self.emit_label(end_label)

    def gen_logical(self, expr: ast.LogicalOp) -> None:
        """Generate short-circuit logical operation."""
        if expr.op == 'and':
            false_label = self.new_label("FALSE")
            end_label = self.new_label("END")

            self.gen_expr(expr.left, 'A')
            self.emit("\tOR\tA")
            self.emit(f"\tJP\tZ,{false_label}")

            self.gen_expr(expr.right, 'A')
            self.emit("\tOR\tA")
            self.emit(f"\tJP\tZ,{false_label}")

            self.emit("\tLD\tA,1")
            self.emit(f"\tJP\t{end_label}")
            self.emit_label(false_label)
            self.emit("\tXOR\tA")
            self.emit_label(end_label)

        elif expr.op == 'or':
            true_label = self.new_label("TRUE")
            end_label = self.new_label("END")

            self.gen_expr(expr.left, 'A')
            self.emit("\tOR\tA")
            self.emit(f"\tJP\tNZ,{true_label}")

            self.gen_expr(expr.right, 'A')
            self.emit("\tOR\tA")
            self.emit(f"\tJP\tNZ,{true_label}")

            self.emit("\tXOR\tA")
            self.emit(f"\tJP\t{end_label}")
            self.emit_label(true_label)
            self.emit("\tLD\tA,1")
            self.emit_label(end_label)

    def gen_array_access(self, expr: ast.ArrayAccess, target: str) -> None:
        """Generate array element access."""
        # Get base address
        self.gen_array_address(expr)

        # Load value from address
        typ = expr.resolved_type
        if self.type_size(typ) == 1:
            self.emit("\tLD\tA,(HL)")
            if target == 'HL':
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tH,0")
        else:
            self.emit("\tLD\tE,(HL)")
            self.emit("\tINC\tHL")
            self.emit("\tLD\tD,(HL)")
            self.emit("\tEX\tDE,HL")
            if target == 'A':
                self.emit("\tLD\tA,L")

    def gen_array_address(self, expr: ast.ArrayAccess) -> None:
        """Generate address of array element in HL."""
        array_type = expr.array.resolved_type
        if isinstance(array_type, ArrayType):
            elem_size = self.type_size(array_type.element)
        elif isinstance(array_type, PtrType):
            elem_size = self.type_size(array_type.target)
        else:
            elem_size = 1

        # Get index
        self.gen_expr(expr.index, 'HL')

        if elem_size > 1:
            # Multiply index by element size
            self.emit(f"\tLD\tDE,{elem_size}")
            self.emit("\tCALL\t_mul16")

        self.emit("\tPUSH\tHL")

        # Get base address
        if isinstance(expr.array, ast.Identifier):
            var = self.get_var(expr.array.name)
            if var:
                if isinstance(array_type, PtrType):
                    # For pointers, load the value (the address it points to)
                    self.emit(f"\tLD\tHL,({self.mangle_name(expr.array.name)})")
                else:
                    # For arrays, use the address of the variable itself
                    self.emit(f"\tLD\tHL,{self.mangle_name(expr.array.name)}")
            else:
                self.emit(f"\tLD\tHL,{expr.array.name}")
        else:
            self.gen_expr(expr.array, 'HL')

        self.emit("\tPOP\tDE")
        self.emit("\tADD\tHL,DE")

    def gen_field_access(self, expr: ast.FieldAccess, target: str) -> None:
        """Generate field access."""
        self.gen_field_address(expr)

        # Load value
        typ = expr.resolved_type
        if self.type_size(typ) == 1:
            self.emit("\tLD\tA,(HL)")
            if target == 'HL':
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tH,0")
        else:
            self.emit("\tLD\tE,(HL)")
            self.emit("\tINC\tHL")
            self.emit("\tLD\tD,(HL)")
            self.emit("\tEX\tDE,HL")
            if target == 'A':
                self.emit("\tLD\tA,L")

    def gen_field_address(self, expr: ast.FieldAccess) -> None:
        """Generate address of record field in HL."""
        # Get record address
        record_type = expr.record.resolved_type
        if isinstance(record_type, PtrType):
            self.gen_expr(expr.record, 'HL')
            record_type = record_type.target
        else:
            if isinstance(expr.record, ast.Identifier):
                var = self.get_var(expr.record.name)
                if var:
                    self.emit(f"\tLD\tHL,{self.mangle_name(expr.record.name)}")
                else:
                    self.emit(f"\tLD\tHL,{expr.record.name}")
            elif isinstance(expr.record, ast.FieldAccess):
                # Nested field access - compute address of the containing field
                self.gen_field_address(expr.record)
            else:
                self.gen_expr(expr.record, 'HL')

        # Add field offset
        if isinstance(record_type, RecordType):
            info = self.checker.records.get(record_type.name)
            if info:
                for field in info.fields:
                    if field.name == expr.field:
                        if field.offset > 0:
                            self.emit(f"\tLD\tDE,{field.offset}")
                            self.emit("\tADD\tHL,DE")
                        break

    def gen_call(self, expr: ast.Call, target: str) -> None:
        """Generate subroutine call."""
        # Check if this call should be inlined
        if isinstance(expr.target, ast.Identifier):
            name = expr.target.name
            if name in self.inlined_subs and not self.generating_inline:
                # Inline the subroutine body directly
                candidate = self.inline_candidates.get(name)
                if candidate and candidate.decl.body:
                    self.emit(f"\t; Inlined: {name}")
                    self.generating_inline = True
                    for stmt in candidate.decl.body:
                        if not isinstance(stmt, ast.ReturnStmt):
                            self.gen_stmt(stmt)
                    self.generating_inline = False
                    return

        # Determine calling convention:
        # - 0 args: no argument passing needed
        # - 1 arg that fits in HL (<=2 bytes): pass in HL
        # - 2 args that fit in HL and DE: pass first in HL, second in DE
        # - More args or larger: push on stack
        use_register_call = 0  # 0=stack, 1=single reg (HL), 2=two regs (HL, DE)
        if len(expr.args) == 1:
            arg = expr.args[0]
            arg_type = arg.resolved_type if hasattr(arg, 'resolved_type') else None
            arg_size = self.type_size(arg_type) if arg_type else 2
            if arg_size <= 2:
                use_register_call = 1
        elif len(expr.args) == 2:
            arg0 = expr.args[0]
            arg1 = expr.args[1]
            arg0_type = arg0.resolved_type if hasattr(arg0, 'resolved_type') else None
            arg1_type = arg1.resolved_type if hasattr(arg1, 'resolved_type') else None
            arg0_size = self.type_size(arg0_type) if arg0_type else 2
            arg1_size = self.type_size(arg1_type) if arg1_type else 2
            if arg0_size <= 2 and arg1_size <= 2:
                use_register_call = 2

        if use_register_call == 1:
            # Single argument: pass in HL, no push/pop needed
            self.gen_expr(expr.args[0], 'HL')
        elif use_register_call == 2:
            # Two arguments: first in HL, second in DE
            # Generate second arg first (into HL), then move to DE
            # Then generate first arg into HL
            self.gen_expr(expr.args[1], 'HL')
            self.emit("\tEX\tDE,HL")  # Move second arg to DE
            self.gen_expr(expr.args[0], 'HL')  # First arg in HL
        else:
            # Push arguments in reverse order (stack calling convention)
            for arg in reversed(expr.args):
                self.gen_expr(arg, 'HL')
                self.emit("\tPUSH\tHL")

        # Call
        if isinstance(expr.target, ast.Identifier):
            name = expr.target.name
            # Check if it's a direct subroutine call or indirect (interface variable)
            if name in self.checker.subroutines:
                # Direct call to known subroutine
                # Use extern_name if defined, for cross-module calls
                sub_info = self.checker.subroutines[name]
                if sub_info.extern_name:
                    self.emit(f"\tCALL\t{sub_info.extern_name}")
                else:
                    self.emit(f"\tCALL\t{self.mangle_sub_name(name)}")
            else:
                # Indirect call through interface variable
                # Load address and use _callhl helper
                var = self.get_var(name)
                if var:
                    self.emit(f"\tLD\tHL,({self.mangle_name(name)})")
                else:
                    self.emit(f"\tLD\tHL,{name}")
                self.emit("\tCALL\t_callhl")  # Helper that does PCHL
        else:
            self.gen_expr(expr.target, 'HL')
            # Call address in HL - use helper
            self.emit("\tCALL\t_callhl")

        # Clean up arguments from stack (only for stack calling convention)
        if use_register_call == 0 and expr.args:
            stack_bytes = len(expr.args) * 2
            if stack_bytes == 2:
                self.emit("\tPOP\tDE")  # Discard
            elif stack_bytes == 4:
                self.emit("\tPOP\tDE")
                self.emit("\tPOP\tDE")
            else:
                # Save return value in DE, adjust SP, then move back to HL
                self.emit("\tEX\tDE,HL")  # Save return value in DE
                self.emit(f"\tLD\tHL,{stack_bytes}")
                self.emit("\tADD\tHL,SP")
                self.emit("\tLD\tSP,HL")
                self.emit("\tEX\tDE,HL")  # Restore return value to HL

        # Result is in HL for 16-bit, A for 8-bit
        if target == 'A' and expr.resolved_type:
            if self.type_size(expr.resolved_type) > 1:
                self.emit("\tLD\tA,L")

    # Code generation for statements

    def gen_stmt(self, stmt: ast.Statement) -> None:
        """Generate code for a statement."""

        if isinstance(stmt, ast.VarDecl):
            self.gen_var_decl(stmt)

        elif isinstance(stmt, ast.ConstDecl):
            # Constants are compile-time only
            pass

        elif isinstance(stmt, ast.Assignment):
            self.gen_assignment(stmt)

        elif isinstance(stmt, ast.MultiAssignment):
            self.gen_multi_assignment(stmt)

        elif isinstance(stmt, ast.IfStmt):
            self.gen_if(stmt)

        elif isinstance(stmt, ast.WhileStmt):
            self.gen_while(stmt)

        elif isinstance(stmt, ast.LoopStmt):
            self.gen_loop(stmt)

        elif isinstance(stmt, ast.BreakStmt):
            if self.break_labels:
                self.emit(f"\tJP\t{self.break_labels[-1]}")

        elif isinstance(stmt, ast.ContinueStmt):
            if self.continue_labels:
                self.emit(f"\tJP\t{self.continue_labels[-1]}")

        elif isinstance(stmt, ast.ReturnStmt):
            self.emit("\tRET")

        elif isinstance(stmt, ast.CaseStmt):
            self.gen_case(stmt)

        elif isinstance(stmt, ast.ExprStmt):
            self.gen_expr(stmt.expr, 'HL')

        elif isinstance(stmt, ast.AsmStmt):
            self.gen_asm(stmt)

        elif isinstance(stmt, ast.NestedSubStmt):
            # Nested subroutine - collect for later generation
            # Store it to be generated at the end of the current function
            if not hasattr(self, 'nested_subs'):
                self.nested_subs = []
            self.nested_subs.append(stmt.sub)

        elif isinstance(stmt, ast.SubDecl):
            self.gen_sub(stmt)

        elif isinstance(stmt, (ast.RecordDecl, ast.TypedefDecl)):
            # Type declarations don't generate code
            pass

    def gen_var_decl(self, stmt: ast.VarDecl) -> None:
        """Generate variable declaration."""
        # Get type from resolved_type, type_name, or init expression
        var_type = None
        if hasattr(stmt, 'resolved_type') and stmt.resolved_type:
            var_type = stmt.resolved_type
        elif stmt.type_name:
            var_type = self.checker.resolve_type(stmt.type_name)
        elif stmt.init and hasattr(stmt.init, 'resolved_type') and stmt.init.resolved_type:
            var_type = stmt.init.resolved_type

        if var_type is None:
            # Default to 16-bit if we can't determine type
            var_type = UINT16

        # Check if already allocated (for global vars)
        var = self.get_var(stmt.name)
        is_preallocated = var is not None
        if not var:
            # Variables declared inside a subroutine are locals
            is_local = self.current_sub is not None
            var = self.allocate_var(stmt.name, var_type, is_local=is_local)

        # Generate initialization if present (even for pre-allocated globals)
        if stmt.init:
            mangled = self.mangle_name(stmt.name)
            if isinstance(stmt.init, ast.ArrayInitializer):
                # Try to evaluate as constant array for data segment init
                elem_size = 1
                if isinstance(var.type, ArrayType):
                    elem_size = self.type_size(var.type.element)
                const_values = []
                all_const = True
                for elem in stmt.init.elements:
                    val = self.eval_const_expr(elem)
                    if val is not None:
                        const_values.append(val)
                    else:
                        all_const = False
                        break
                if all_const and const_values:
                    # Store for data segment initialization
                    self.array_initializers[stmt.name] = (const_values, elem_size)
                elif not is_preallocated:
                    # Non-constant array init for local arrays only
                    self.gen_array_init(var, stmt.init)
            elif isinstance(stmt.init, ast.StringLiteral):
                # String initialization
                label = self.get_string_label(stmt.init.value)
                self.emit(f"\tLD\tHL,{label}")
                self.emit(f"\tLD\t({mangled}),HL")
            else:
                self.gen_expr(stmt.init, 'HL')
                if var.size == 1:
                    self.emit("\tLD\tA,L")
                    self.emit(f"\tLD\t({mangled}),A")
                else:
                    self.emit(f"\tLD\t({mangled}),HL")

    def gen_array_init(self, var: Variable, init: ast.ArrayInitializer) -> None:
        """Generate array initialization."""
        offset = 0
        elem_size = 1
        if isinstance(var.type, ArrayType):
            elem_size = self.type_size(var.type.element)

        mangled = self.mangle_name(var.name)
        for elem in init.elements:
            self.gen_expr(elem, 'HL')
            if elem_size == 1:
                self.emit("\tLD\tA,L")
                self.emit(f"\tLD\t({mangled}+{offset}),A")
            else:
                self.emit(f"\tLD\t({mangled}+{offset}),HL")
            offset += elem_size

    def gen_assignment(self, stmt: ast.Assignment) -> None:
        """Generate assignment statement."""
        # Optimize byte variable increment/decrement: var := var + 1 -> INR M
        if isinstance(stmt.target, ast.Identifier):
            var = self.get_var(stmt.target.name)
            if var and var.size == 1:
                # Check for var := var + 1 or var := var - 1
                if isinstance(stmt.value, ast.BinaryOp):
                    if stmt.value.op == '+' and isinstance(stmt.value.left, ast.Identifier):
                        if stmt.value.left.name == stmt.target.name:
                            inc = self.eval_const_expr(stmt.value.right)
                            if inc == 1:
                                mangled = self.mangle_name(stmt.target.name)
                                self.emit(f"\tLD\tHL,{mangled}")
                                self.emit("\tINC\t(HL)")
                                self.invalidate_regs()  # INR M modifies memory
                                return
                            elif inc == -1:
                                mangled = self.mangle_name(stmt.target.name)
                                self.emit(f"\tLD\tHL,{mangled}")
                                self.emit("\tDEC\t(HL)")
                                self.invalidate_regs()
                                return
                    elif stmt.value.op == '-' and isinstance(stmt.value.left, ast.Identifier):
                        if stmt.value.left.name == stmt.target.name:
                            dec = self.eval_const_expr(stmt.value.right)
                            if dec == 1:
                                mangled = self.mangle_name(stmt.target.name)
                                self.emit(f"\tLD\tHL,{mangled}")
                                self.emit("\tDEC\t(HL)")
                                self.invalidate_regs()
                                return
                            elif dec == -1:
                                mangled = self.mangle_name(stmt.target.name)
                                self.emit(f"\tLD\tHL,{mangled}")
                                self.emit("\tINC\t(HL)")
                                self.invalidate_regs()
                                return

        # Generate value
        self.gen_expr(stmt.value, 'HL')

        # Store to target
        if isinstance(stmt.target, ast.Identifier):
            var = self.get_var(stmt.target.name)
            if var:
                mangled = self.mangle_name(stmt.target.name)
                if var.size == 1:
                    self.emit("\tLD\tA,L")
                    self.emit(f"\tLD\t({mangled}),A")
                    self.a_contains = stmt.target.name  # A now has this var
                else:
                    self.emit(f"\tLD\t({mangled}),HL")
                    self.hl_contains = stmt.target.name  # HL still has this var

        elif isinstance(stmt.target, ast.ArrayAccess):
            self.emit("\tPUSH\tHL")  # Save value
            self.gen_array_address(stmt.target)
            self.emit("\tEX\tDE,HL")  # DE = address
            self.emit("\tPOP\tHL")  # HL = value

            typ = stmt.target.resolved_type
            if self.type_size(typ) == 1:
                self.emit("\tLD\tA,L")
                self.emit("\tLD\t(DE),A")
            else:
                self.emit("\tEX\tDE,HL")
                self.emit("\tLD\t(HL),E")
                self.emit("\tINC\tHL")
                self.emit("\tLD\t(HL),D")

        elif isinstance(stmt.target, ast.FieldAccess):
            self.emit("\tPUSH\tHL")
            self.gen_field_address(stmt.target)
            self.emit("\tEX\tDE,HL")
            self.emit("\tPOP\tHL")

            typ = stmt.target.resolved_type
            if self.type_size(typ) == 1:
                self.emit("\tLD\tA,L")
                self.emit("\tLD\t(DE),A")
            else:
                self.emit("\tEX\tDE,HL")
                self.emit("\tLD\t(HL),E")
                self.emit("\tINC\tHL")
                self.emit("\tLD\t(HL),D")

        elif isinstance(stmt.target, ast.Dereference):
            self.emit("\tPUSH\tHL")
            self.gen_expr(stmt.target.pointer, 'HL')
            self.emit("\tEX\tDE,HL")
            self.emit("\tPOP\tHL")

            typ = stmt.target.resolved_type
            if self.type_size(typ) == 1:
                self.emit("\tLD\tA,L")
                self.emit("\tLD\t(DE),A")
            else:
                self.emit("\tEX\tDE,HL")
                self.emit("\tLD\t(HL),E")
                self.emit("\tINC\tHL")
                self.emit("\tLD\t(HL),D")

    def gen_multi_assignment(self, stmt: ast.MultiAssignment) -> None:
        """Generate multi-value assignment from call."""
        # Call the function
        self.gen_expr(stmt.value, 'HL')

        # Result handling depends on calling convention
        # For now, assume first return in HL, rest on stack
        for i, target in enumerate(stmt.targets):
            if i == 0:
                # First return value in HL
                pass
            else:
                self.emit("\tPOP\tHL")

            if isinstance(target, ast.Identifier):
                var = self.get_var(target.name)
                mangled = self.mangle_name(target.name)
                if var and var.size == 1:
                    self.emit("\tLD\tA,L")
                    self.emit(f"\tLD\t({mangled}),A")
                else:
                    self.emit(f"\tLD\t({mangled}),HL")

    def gen_condition_branch_true(self, cond: ast.Expression, true_label: str) -> None:
        """Generate condition and branch to true_label if true."""
        # For comparison, negate the jump condition
        if isinstance(cond, ast.Comparison):
            op = cond.op
            is_zero_right = isinstance(cond.right, ast.NumberLiteral) and cond.right.value == 0
            if is_zero_right and op in ('==', '!='):
                self.gen_expr(cond.left, 'HL')
                self.emit("\tLD\tA,H")
                self.emit("\tOR\tL")
                if op == '==':
                    self.emit(f"\tJP\tZ,{true_label}")
                else:
                    self.emit(f"\tJP\tNZ,{true_label}")
                return

            # Optimized comparison when right side is a constant
            right_const = self.eval_const_expr(cond.right)
            if right_const is not None:
                # Generate: left in HL, const in DE directly (no push/pop)
                self.gen_expr(cond.left, 'HL')
                self.emit(f"\tLD\tDE,{right_const}")
            else:
                # General comparison with push/pop
                self.gen_expr(cond.left, 'HL')
                self.emit("\tPUSH\tHL")
                self.gen_expr(cond.right, 'HL')
                self.emit("\tEX\tDE,HL")
                self.emit("\tPOP\tHL")

            # Compare HL with DE
            self.emit("\tLD\tA,H")
            self.emit("\tCP\tD")
            self.emit("\tJP\tNZ,$+6")
            self.emit("\tLD\tA,L")
            self.emit("\tCP\tE")
            # Branch if true (opposite of gen_condition_branch)
            if op == '==':
                self.emit(f"\tJP\tZ,{true_label}")
            elif op == '!=':
                self.emit(f"\tJP\tNZ,{true_label}")
            elif op == '<':
                self.emit(f"\tJP\tC,{true_label}")
            elif op == '>=':
                self.emit(f"\tJP\tNC,{true_label}")
            elif op == '>':
                skip_lbl = self.new_label("SKIP")
                self.emit(f"\tJP\tZ,{skip_lbl}")
                self.emit(f"\tJP\tNC,{true_label}")
                self.emit_label(skip_lbl)
            elif op == '<=':
                self.emit(f"\tJP\tZ,{true_label}")
                self.emit(f"\tJP\tC,{true_label}")
            return

        if isinstance(cond, ast.NotOp):
            # !x is true -> x is false
            self.gen_condition_branch(cond.operand, true_label)
            return

        # Default: evaluate and test
        self.gen_expr(cond, 'A')
        self.emit("\tOR\tA")
        self.emit(f"\tJP\tNZ,{true_label}")

    def gen_condition_branch(self, cond: ast.Expression, false_label: str) -> None:
        """Generate condition and branch to false_label if false."""
        # Optimize: direct comparison branching
        if isinstance(cond, ast.Comparison):
            op = cond.op
            # Check for comparison with zero
            is_zero_right = isinstance(cond.right, ast.NumberLiteral) and cond.right.value == 0
            if is_zero_right and op in ('==', '!='):
                self.gen_expr(cond.left, 'HL')
                self.emit("\tLD\tA,H")
                self.emit("\tOR\tL")
                if op == '==':
                    self.emit(f"\tJP\tNZ,{false_label}")
                else:
                    self.emit(f"\tJP\tZ,{false_label}")
                return

            # Check if we can use byte comparison (CPI)
            # This works when left is a byte type and right is a constant 0-255
            right_const = self.eval_const_expr(cond.right)
            left_type = cond.left.resolved_type
            is_byte_cmp = (right_const is not None and
                          0 <= right_const <= 255 and
                          left_type is not None and
                          self.type_size(left_type) == 1)

            if is_byte_cmp:
                # Optimized byte comparison using CPI
                self.gen_expr(cond.left, 'A')
                self.emit(f"\tCP\t{right_const}")
                # Branch based on comparison
                if op == '==':
                    self.emit(f"\tJP\tNZ,{false_label}")
                elif op == '!=':
                    self.emit(f"\tJP\tZ,{false_label}")
                elif op == '<':
                    self.emit(f"\tJP\tNC,{false_label}")
                elif op == '>=':
                    self.emit(f"\tJP\tC,{false_label}")
                elif op == '>':
                    self.emit(f"\tJP\tC,{false_label}")
                    self.emit(f"\tJP\tZ,{false_label}")
                elif op == '<=':
                    true_lbl = self.new_label("TRUE")
                    self.emit(f"\tJP\tZ,{true_lbl}")
                    self.emit(f"\tJP\tNC,{false_label}")
                    self.emit_label(true_lbl)
                return

            # Optimized 16-bit comparison when right side is a constant
            if right_const is not None:
                # Generate: left in HL, const in DE directly (no push/pop)
                self.gen_expr(cond.left, 'HL')
                self.emit(f"\tLD\tDE,{right_const}")
            else:
                # General comparison with push/pop
                self.gen_expr(cond.left, 'HL')
                self.emit("\tPUSH\tHL")
                self.gen_expr(cond.right, 'HL')
                self.emit("\tEX\tDE,HL")
                self.emit("\tPOP\tHL")

            # Compare HL with DE
            self.emit("\tLD\tA,H")
            self.emit("\tCP\tD")
            self.emit("\tJP\tNZ,$+6")
            self.emit("\tLD\tA,L")
            self.emit("\tCP\tE")
            # Branch based on comparison
            if op == '==':
                self.emit(f"\tJP\tNZ,{false_label}")
            elif op == '!=':
                self.emit(f"\tJP\tZ,{false_label}")
            elif op == '<':
                self.emit(f"\tJP\tNC,{false_label}")
            elif op == '>=':
                self.emit(f"\tJP\tC,{false_label}")
            elif op == '>':
                self.emit(f"\tJP\tC,{false_label}")
                self.emit(f"\tJP\tZ,{false_label}")
            elif op == '<=':
                true_lbl = self.new_label("TRUE")
                self.emit(f"\tJP\tZ,{true_lbl}")
                self.emit(f"\tJP\tNC,{false_label}")
                self.emit_label(true_lbl)
            return

        if isinstance(cond, ast.NotOp):
            # !x -> branch if x is true
            true_label = self.new_label("TRUE")
            self.gen_condition_branch(cond.operand, true_label)
            self.emit(f"\tJP\t{false_label}")
            self.emit_label(true_label)
            return

        if isinstance(cond, ast.BinaryOp):
            if cond.op == 'and':
                # a and b -> if !a goto false; if !b goto false
                self.gen_condition_branch(cond.left, false_label)
                self.gen_condition_branch(cond.right, false_label)
                return
            elif cond.op == 'or':
                # a or b -> if a goto continue; if !b goto false
                true_label = self.new_label("OR_TRUE")
                self.gen_condition_branch_true(cond.left, true_label)
                self.gen_condition_branch(cond.right, false_label)
                self.emit_label(true_label)
                return

        # Default: evaluate and test
        self.gen_expr(cond, 'A')
        self.emit("\tOR\tA")
        self.emit(f"\tJP\tZ,{false_label}")

    def gen_if(self, stmt: ast.IfStmt) -> None:
        """Generate if statement."""
        else_label = self.new_label("ELSE")
        end_label = self.new_label("ENDIF")

        # Condition - use optimized branch
        if stmt.elseifs or stmt.else_body:
            self.gen_condition_branch(stmt.condition, else_label)
        else:
            self.gen_condition_branch(stmt.condition, end_label)

        # Then body
        for s in stmt.then_body:
            self.gen_stmt(s)
        if stmt.elseifs or stmt.else_body:
            self.emit(f"\tJP\t{end_label}")

        # Elseifs
        for i, (cond, body) in enumerate(stmt.elseifs):
            self.emit_label(else_label)
            next_label = self.new_label("ELIF") if i < len(stmt.elseifs) - 1 or stmt.else_body else end_label
            else_label = next_label

            # Use optimized condition branching
            self.gen_condition_branch(cond, next_label)

            for s in body:
                self.gen_stmt(s)
            self.emit(f"\tJP\t{end_label}")

        # Else
        if stmt.else_body:
            self.emit_label(else_label)
            for s in stmt.else_body:
                self.gen_stmt(s)

        self.emit_label(end_label)

    def gen_while(self, stmt: ast.WhileStmt) -> None:
        """Generate while loop."""
        loop_label = self.new_label("WHILE")
        end_label = self.new_label("ENDW")

        self.break_labels.append(end_label)
        self.continue_labels.append(loop_label)

        self.emit_label(loop_label)
        # Use optimized condition branching
        self.gen_condition_branch(stmt.condition, end_label)

        for s in stmt.body:
            self.gen_stmt(s)

        self.emit(f"\tJP\t{loop_label}")
        self.emit_label(end_label)

        self.break_labels.pop()
        self.continue_labels.pop()

    def gen_loop(self, stmt: ast.LoopStmt) -> None:
        """Generate infinite loop."""
        loop_label = self.new_label("LOOP")
        end_label = self.new_label("ENDL")

        self.break_labels.append(end_label)
        self.continue_labels.append(loop_label)

        self.emit_label(loop_label)
        for s in stmt.body:
            self.gen_stmt(s)
        self.emit(f"\tJP\t{loop_label}")
        self.emit_label(end_label)

        self.break_labels.pop()
        self.continue_labels.pop()

    def gen_case(self, stmt: ast.CaseStmt) -> None:
        """Generate case statement."""
        end_label = self.new_label("ENDC")

        # Evaluate expression
        self.gen_expr(stmt.expr, 'HL')
        self.emit("\tPUSH\tHL")

        for values, body in stmt.whens:
            next_when = self.new_label("WHEN")

            for val in values:
                self.emit("\tPOP\tHL")
                self.emit("\tPUSH\tHL")
                self.gen_expr(val, 'HL')
                self.emit("\tEX\tDE,HL")
                self.emit("\tPOP\tHL")
                self.emit("\tPUSH\tHL")

                # Compare
                self.emit("\tLD\tA,H")
                self.emit("\tCP\tD")
                self.emit(f"\tJP\tNZ,{next_when}")
                self.emit("\tLD\tA,L")
                self.emit("\tCP\tE")
                self.emit(f"\tJP\tNZ,{next_when}")

            # Match found
            self.emit("\tPOP\tHL")  # Clean stack
            for s in body:
                self.gen_stmt(s)
            self.emit(f"\tJP\t{end_label}")

            self.emit_label(next_when)

        # Else clause
        self.emit("\tPOP\tHL")  # Clean stack
        if stmt.else_body:
            for s in stmt.else_body:
                self.gen_stmt(s)

        self.emit_label(end_label)

    def gen_asm(self, stmt: ast.AsmStmt) -> None:
        """Generate inline assembly."""
        parts = []
        for part in stmt.parts:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, ast.Identifier):
                name = part.name
                # Check if it's a constant - substitute value
                if name in self.checker.constants:
                    parts.append(str(self.checker.constants[name]))
                # Check if it's a subroutine - use subroutine name
                elif name in self.checker.subroutines:
                    parts.append(self.mangle_sub_name(name))
                # Otherwise it's a variable - mangle with v_
                else:
                    parts.append(self.mangle_name(name))
            elif isinstance(part, ast.Expression):
                # For now, just use the expression as-is if it's an identifier
                if isinstance(part, ast.Identifier):
                    name = part.name
                    if name in self.checker.constants:
                        parts.append(str(self.checker.constants[name]))
                    elif name in self.checker.subroutines:
                        parts.append(self.mangle_sub_name(name))
                    else:
                        parts.append(self.mangle_name(name))
                else:
                    parts.append("???")
        # Join parts with tab to separate instruction from operand if needed
        asm_line = ""
        for i, part in enumerate(parts):
            if i > 0 and part and not part[0].isspace() and asm_line and not asm_line[-1].isspace():
                asm_line += "\t"  # Add tab between instruction and operand
            asm_line += part
        self.emit("\t" + asm_line)

    def gen_sub(self, decl: ast.SubDecl) -> None:
        """Generate subroutine."""
        if decl.body is None:
            return  # Forward declaration

        # Skip generating subs that were inlined
        if decl.name in self.inlined_subs:
            return

        self.current_sub = decl.name

        # Get params/returns from checker for @impl (since decl.params is empty)
        sub_info = self.checker.subroutines.get(decl.name)
        if sub_info:
            params = sub_info.params  # List of (name, CowType)
            returns = sub_info.returns
        else:
            # Fallback to decl
            params = [(p.name, self.checker.resolve_type(p.type)) for p in decl.params]
            returns = [(r.name, self.checker.resolve_type(r.type)) for r in decl.returns]

        # Label
        self.emit("")
        self.emit(f"; Subroutine {decl.name}")
        mangled_name = self.mangle_sub_name(decl.name)
        if decl.extern_name:
            self.emit(f"\tPUBLIC\t{decl.extern_name}")
            self.emit_label(decl.extern_name)
            # Only emit mangled name if different from extern name
            if mangled_name != decl.extern_name:
                self.emit_label(mangled_name)
        else:
            self.emit_label(mangled_name)

        # Allocate local variables for parameters and return values
        # These are locals, so they can share space with non-concurrent subs
        for param_name, param_type in params:
            self.allocate_var(param_name, param_type, is_local=True)

        for ret_name, ret_type in returns:
            self.allocate_var(ret_name, ret_type, is_local=True)

        # Determine if we use register calling convention:
        # - 1 param that fits in HL: passed in HL
        # - 2 params that fit: first in HL, second in DE
        # - Otherwise: passed on stack
        use_register_call = 0
        if len(params) == 1:
            param_name, param_type = params[0]
            param_size = self.type_size(param_type) if param_type else 2
            if param_size <= 2:
                use_register_call = 1
        elif len(params) == 2:
            p0_name, p0_type = params[0]
            p1_name, p1_type = params[1]
            p0_size = self.type_size(p0_type) if p0_type else 2
            p1_size = self.type_size(p1_type) if p1_type else 2
            if p0_size <= 2 and p1_size <= 2:
                use_register_call = 2

        if use_register_call == 1:
            # Single argument passed in HL - copy to local variable
            param_name, param_type = params[0]
            var = self.get_var(param_name)
            if var:
                mangled = self.mangle_name(param_name)
                if var.size == 1:
                    self.emit("\tLD\tA,L")
                    self.emit(f"\tLD\t({mangled}),A")
                else:
                    self.emit(f"\tLD\t({mangled}),HL")
        elif use_register_call == 2:
            # Two arguments: first in HL, second in DE
            # Copy first param from HL
            p0_name, p0_type = params[0]
            var0 = self.get_var(p0_name)
            if var0:
                mangled0 = self.mangle_name(p0_name)
                if var0.size == 1:
                    self.emit("\tLD\tA,L")
                    self.emit(f"\tLD\t({mangled0}),A")
                else:
                    self.emit(f"\tLD\t({mangled0}),HL")
            # Copy second param from DE
            p1_name, p1_type = params[1]
            var1 = self.get_var(p1_name)
            if var1:
                mangled1 = self.mangle_name(p1_name)
                if var1.size == 1:
                    self.emit("\tLD\tA,E")
                    self.emit(f"\tLD\t({mangled1}),A")
                else:
                    self.emit("\tEX\tDE,HL")
                    self.emit(f"\tLD\t({mangled1}),HL")
        else:
            # Copy parameters from stack to variables
            # Args are pushed in reverse order at call site, so first param is at lowest offset
            offset = 2  # Return address
            for param_name, param_type in params:
                var = self.get_var(param_name)
                if var:
                    mangled = self.mangle_name(param_name)
                    self.emit(f"\tLD\tHL,{offset}")
                    self.emit("\tADD\tHL,SP")
                    if var.size == 1:
                        self.emit("\tLD\tA,(HL)")
                        self.emit(f"\tLD\t({mangled}),A")
                    else:
                        self.emit("\tLD\tE,(HL)")
                        self.emit("\tINC\tHL")
                        self.emit("\tLD\tD,(HL)")
                        self.emit("\tEX\tDE,HL")
                        self.emit(f"\tLD\t({mangled}),HL")
                    offset += 2

        # Body
        for stmt in decl.body:
            self.gen_stmt(stmt)

        # Return (with first return value in HL)
        if returns:
            ret_var = returns[0][0]  # (name, type) tuple
            var = self.get_var(ret_var)
            mangled_ret = self.mangle_name(ret_var)
            if var and var.size == 1:
                self.emit(f"\tLD\tA,({mangled_ret})")
                self.emit("\tLD\tL,A")
                self.emit("\tLD\tH,0")
            else:
                self.emit(f"\tLD\tHL,({mangled_ret})")

        self.emit("\tRET")

        # Generate any nested subs collected during body generation
        if hasattr(self, 'nested_subs') and self.nested_subs:
            nested = self.nested_subs
            self.nested_subs = []
            for nested_sub in nested:
                self.gen_sub(nested_sub)

        self.current_sub = None

    def gen_program(self, program: ast.Program) -> str:
        """Generate complete program."""
        # Analyze for inlining opportunities before code generation
        self.analyze_for_inlining(program)

        self.emit("; Generated by ucow")
        self.emit("")
        self.emit("\t.Z80")
        self.emit("")

        # Collect external symbols from @decl subroutines (forward declarations)
        for decl in program.declarations:
            if isinstance(decl, ast.SubDecl) and decl.body is None and decl.extern_name:
                self.extern_symbols.add(decl.extern_name)

        # Emit EXTRN directives for external symbols
        if self.extern_symbols:
            self.emit("; External symbols (from other modules)")
            for sym in sorted(self.extern_symbols):
                self.emit(f"\tEXTRN\t{sym}")
            self.emit("")

        # Use CSEG for code segment (will be linked at 0100H)
        self.emit("\tCSEG")
        self.emit("")

        if not self.library_mode:
            # Jump to main (only for main module)
            self.emit("\tJP\t_main")
            self.emit("")

            # Include runtime (only for main module)
            self.emit("\tINCLUDE\t'runtime.mac'")
            self.emit("")

        # First, allocate all global variables (so they're visible in subroutines)
        for stmt in program.statements:
            if isinstance(stmt, ast.VarDecl):
                var_info = self.checker.current_scope.lookup_var(stmt.name)
                if var_info:
                    self.allocate_var(stmt.name, var_info.type)

        # Process declarations (subroutines)
        for decl in program.declarations:
            if isinstance(decl, ast.SubDecl):
                self.gen_sub(decl)

        if not self.library_mode:
            # Main code (only for main module)
            self.emit("")
            self.emit("; Main program")
            self.emit_label("_main")

            for stmt in program.statements:
                self.gen_stmt(stmt)

            # Exit
            self.emit("\tJP\t0")  # Warm boot
            self.emit("")

        # String literals (stay in CSEG - they're read-only)
        self.emit("; String literals")
        for value, label in self.string_literals.items():
            # Output as individual bytes to handle control characters
            bytes_str = ','.join(str(ord(c)) for c in value)
            if bytes_str:
                self.emit(f"{label}:\tDB\t{bytes_str},0")
            else:
                self.emit(f"{label}:\tDB\t0")

        # Initialized arrays (stay in CSEG - they contain initial values)
        self.emit("; Initialized data")
        for name, var in self.variables.items():
            if name in self.array_initializers:
                # Emit initialized array data
                values, elem_size = self.array_initializers[name]
                mangled = self.mangle_name(name)
                if elem_size == 1:
                    # Emit in chunks of 16 for readability
                    for i in range(0, len(values), 16):
                        chunk = values[i:i+16]
                        bytes_str = ','.join(str(v & 0xFF) for v in chunk)
                        if i == 0:
                            self.emit(f"{mangled}:\tDB\t{bytes_str}")
                        else:
                            self.emit(f"\tDB\t{bytes_str}")
                else:
                    # 16-bit values
                    for i in range(0, len(values), 8):
                        chunk = values[i:i+8]
                        words_str = ','.join(str(v & 0xFFFF) for v in chunk)
                        if i == 0:
                            self.emit(f"{mangled}:\tDW\t{words_str}")
                        else:
                            self.emit(f"\tDW\t{words_str}")

        # Uninitialized variables (in DSEG for proper relocation)
        self.emit("")
        self.emit("\tDSEG")
        self.emit("; Variables")

        if self.call_graph:
            # Workspace optimization mode: variables share space
            # Emit a base symbol and use EQU for variable offsets
            self.emit("_wsbase:")

            # Compute total workspace size
            max_offset = 0
            for var_key, var in self.variables.items():
                if var_key not in self.array_initializers and var.name not in self.array_initializers:
                    end = var.offset + var.size
                    if end > max_offset:
                        max_offset = end

            # Emit the total workspace
            if max_offset > 0:
                self.emit(f"\tDS\t{max_offset}")

            # Emit EQU directives for each variable
            self.emit("; Variable addresses (workspace-optimized)")
            for var_key, var in self.variables.items():
                if var_key not in self.array_initializers and var.name not in self.array_initializers:
                    self.emit(f"{self.mangle_var_key(var_key)}\tEQU\t_wsbase+{var.offset}")
        else:
            # Standard mode: each variable has its own allocation
            for var_key, var in self.variables.items():
                if var_key not in self.array_initializers and var.name not in self.array_initializers:
                    self.emit(f"{self.mangle_var_key(var_key)}:\tDS\t{var.size}")

        self.emit("")
        self.emit("\tEND")

        # Apply peephole optimizations iteratively until no more changes
        prev_len = len(self.output) + 1
        passes = 0
        while len(self.output) < prev_len and passes < 10:
            prev_len = len(self.output)
            self.output = self.peephole_optimize(self.output)
            passes += 1

        return '\n'.join(self.output)

    def peephole_optimize(self, lines: List[str]) -> List[str]:
        """Apply peephole optimizations to reduce code size."""
        result = []
        i = 0
        optimizations = 0

        while i < len(lines):
            # Get current and next few lines for pattern matching
            curr = lines[i].strip()
            next1 = lines[i + 1].strip() if i + 1 < len(lines) else ""
            next2 = lines[i + 2].strip() if i + 2 < len(lines) else ""
            next3 = lines[i + 3].strip() if i + 3 < len(lines) else ""

            # Pattern: PUSH H / POP H -> remove both (no-op)
            if curr == "PUSH\tHL" and next1 == "POP\tHL":
                i += 2
                optimizations += 1
                continue

            # Pattern: PUSH D / POP D -> remove both (no-op)
            if curr == "PUSH\tDE" and next1 == "POP\tDE":
                i += 2
                optimizations += 1
                continue

            # Pattern: PUSH PSW / POP PSW -> remove both (no-op)
            if curr == "PUSH\tAF" and next1 == "POP\tAF":
                i += 2
                optimizations += 1
                continue

            # Pattern: LD HL,(x) / PUSH HL / LD HL,(y) / EX DE,HL / POP HL -> LD HL,(y) / EX DE,HL / LD HL,(x)
            # This is a very common pattern for loading two 16-bit operands
            if (curr.startswith("LD\tHL,(") and next1 == "PUSH\tHL" and
                next2.startswith("LD\tHL,(") and next3 == "EX\tDE,HL"):
                next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                if next4 == "POP\tHL":
                    # var_x and var_y include the parens, e.g. "(xxx)"
                    var_x = curr[6:]  # "(xxx)" from "LD HL,(xxx)"
                    var_y = next2[6:]
                    result.append(f"\tLD\tHL,{var_y}")
                    result.append("\tEX\tDE,HL")
                    result.append(f"\tLD\tHL,{var_x}")
                    i += 5
                    optimizations += 1
                    continue

            # Pattern: PUSH HL / LD HL,(x) / EX DE,HL / POP HL / EX DE,HL -> EX DE,HL / LD HL,(x)
            # This saves old HL into DE, then loads x into HL
            if (curr == "PUSH\tHL" and next1.startswith("LD\tHL,(") and
                next2 == "EX\tDE,HL" and next3 == "POP\tHL"):
                next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                if next4 == "EX\tDE,HL":
                    var = next1[6:]  # "(xxx)" from "LD HL,(xxx)"
                    result.append("\tEX\tDE,HL")
                    result.append(f"\tLD\tHL,{var}")
                    i += 5
                    optimizations += 1
                    continue

            # Pattern: PUSH HL / LD HL,(x) / INC HL / EX DE,HL / POP HL / EX DE,HL -> EX DE,HL / LD HL,(x) / INC HL
            # This saves old HL into DE, then loads x+1 into HL
            if (curr == "PUSH\tHL" and next1.startswith("LD\tHL,(") and
                next2 == "INC\tHL" and next3 == "EX\tDE,HL"):
                next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                next5 = lines[i + 5].strip() if i + 5 < len(lines) else ""
                if next4 == "POP\tHL" and next5 == "EX\tDE,HL":
                    var = next1[6:]  # "(xxx)" from "LD HL,(xxx)"
                    result.append("\tEX\tDE,HL")
                    result.append(f"\tLD\tHL,{var}")
                    result.append("\tINC\tHL")
                    i += 6
                    optimizations += 1
                    continue

            # Pattern: PUSH B / POP B -> remove both
            if curr == "PUSH\tBC" and next1 == "POP\tBC":
                i += 2
                optimizations += 1
                continue

            # Pattern: MOV A,L / MOV L,A -> remove second (redundant)
            if curr == "LD\tA,L" and next1 == "LD\tL,A":
                result.append(lines[i])
                i += 2
                optimizations += 1
                continue

            # Pattern: MOV L,A / MOV A,L -> remove second (redundant)
            if curr == "LD\tL,A" and next1 == "LD\tA,L":
                result.append(lines[i])
                i += 2
                optimizations += 1
                continue

            # Pattern: LD (x),HL / LD HL,(x) -> remove second (HL already has value)
            if curr.startswith("LD\t(") and curr.endswith("),HL"):
                var = curr[4:-4]  # Extract x from "LD (x),HL"
                if next1 == f"LD\tHL,({var})":
                    result.append(lines[i])
                    i += 2
                    optimizations += 1
                    continue

            # Pattern: LD (x),A / LD A,(x) -> remove second (A already has value)
            if curr.startswith("LD\t(") and curr.endswith("),A"):
                var = curr[4:-3]  # Extract x from "LD (x),A"
                if next1 == f"LD\tA,({var})":
                    result.append(lines[i])
                    i += 2
                    optimizations += 1
                    continue

            # Pattern: LD HL,x / LD (y),HL / LD HL,(y) -> LD HL,x / LD (y),HL
            if curr.startswith("LD\tHL,") and not curr.startswith("LD\tHL,("):
                if next1.startswith("LD\t(") and next1.endswith("),HL"):
                    var = next1[4:-4]  # Extract y from "LD (y),HL"
                    if next2 == f"LD\tHL,({var})":
                        result.append(lines[i])
                        result.append(lines[i + 1])
                        i += 3
                        optimizations += 1
                        continue

            # Pattern: LD A,x / LD (y),A / LD A,(y) -> LD A,x / LD (y),A
            if curr.startswith("LD\tA,") and not curr.startswith("LD\tA,("):
                if next1.startswith("LD\t(") and next1.endswith("),A"):
                    var = next1[4:-3]  # Extract y from "LD (y),A"
                    if next2 == f"LD\tA,({var})":
                        result.append(lines[i])
                        result.append(lines[i + 1])
                        i += 3
                        optimizations += 1
                        continue

            # Pattern: LXI H,0 / DAD D -> EX\tDE,HL (when just want DE in HL)
            # Can't do this safely without knowing context

            # Pattern: LXI D,2 / CALL _mul16 -> DAD H (multiply by 2 is left shift)
            if curr == "LD\tDE,2" and next1 == "CALL\t_mul16":
                result.append("\tADD\tHL,HL")
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI D,4 / CALL _mul16 -> DAD H / DAD H (multiply by 4)
            if curr == "LD\tDE,4" and next1 == "CALL\t_mul16":
                result.append("\tADD\tHL,HL")
                result.append("\tADD\tHL,HL")
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI D,8 / CALL _mul16 -> DAD H / DAD H / DAD H (multiply by 8)
            if curr == "LD\tDE,8" and next1 == "CALL\t_mul16":
                result.append("\tADD\tHL,HL")
                result.append("\tADD\tHL,HL")
                result.append("\tADD\tHL,HL")
                i += 2
                optimizations += 1
                continue

            # Pattern: CALL sub / RET -> JMP sub (tail call)
            if curr.startswith("CALL\t") and next1 == "RET":
                sub = curr[5:]
                result.append(f"\tJP\t{sub}")
                i += 2
                optimizations += 1
                continue

            # Pattern: MVI H,0 after MOV L,A (common for 8->16 extension)
            # Keep as is - needed for proper zero extension

            # Pattern: JMP to next instruction -> remove
            if curr.startswith("JP\t"):
                target = curr[4:]
                if next1 == f"{target}:":
                    i += 1
                    optimizations += 1
                    continue

            # Pattern: LXI H,x / LXI H,y -> keep only second
            if curr.startswith("LD\tHL,") and next1.startswith("LD\tHL,"):
                i += 1
                optimizations += 1
                continue

            # Pattern: MVI A,x / MVI A,y -> keep only second
            if curr.startswith("LD\tA,") and next1.startswith("LD\tA,"):
                i += 1
                optimizations += 1
                continue

            # Pattern: XRA A / MVI A,0 -> keep only XRA A
            if curr == "XOR\tA" and next1 == "LD\tA,0":
                result.append(lines[i])
                i += 2
                optimizations += 1
                continue

            # Pattern: MVI A,0 / XRA A -> keep only XRA A (shorter)
            if curr == "LD\tA,0" and next1 == "XOR\tA":
                i += 1  # Skip MVI, keep XRA
                optimizations += 1
                continue

            # Pattern: ORA A / ORA A -> keep one
            if curr == "OR\tA" and next1 == "OR\tA":
                result.append(lines[i])
                i += 2
                optimizations += 1
                continue

            # Pattern: RET / RET -> keep one
            if curr == "RET" and next1 == "RET":
                result.append(lines[i])
                i += 2
                optimizations += 1
                continue

            # Pattern: MOV L,A / MVI H,0 / MOV L,A / MVI H,0 -> keep first pair
            if curr == "LD\tL,A" and next1 == "LD\tH,0" and next2 == "LD\tL,A" and next3 == "LD\tH,0":
                result.append(lines[i])
                result.append(lines[i + 1])
                i += 4
                optimizations += 1
                continue

            # Pattern: MOV L,A / MVI H,0 / MOV A,L -> MOV L,A / MVI H,0 (A already has value)
            if curr == "LD\tL,A" and next1 == "LD\tH,0" and next2 == "LD\tA,L":
                result.append(lines[i])
                result.append(lines[i + 1])
                i += 3
                optimizations += 1
                continue

            # Pattern: MOV L,A / MVI H,0 / MOV A,H -> MOV L,A / MVI H,0 / XRA A (H is 0)
            # Actually: MOV L,A / XRA A / MOV H,A saves one byte but changes semantics
            # Safer: MOV L,A / MVI H,0 / XRA A (same length, clearer)
            # Wait, we can't change to XRA A if next instructions need flags!
            # Just keep as-is for now - MOV A,H after MVI H,0 gives A=0

            # Pattern: consecutive identical instructions -> keep one
            # IMPORTANT: Don't remove duplicate CALLs - they have side effects!
            # IMPORTANT: Don't remove duplicate INC/DEC - they have cumulative effects!
            # IMPORTANT: Don't remove duplicate EX - swapping twice restores original state
            #            but code may depend on intermediate state (e.g., value in DE after first swap)
            if (curr == next1 and curr not in ["RET", ""] and
                not curr.endswith(":") and not curr.startswith("CALL") and
                not curr.startswith("INC") and not curr.startswith("DEC") and
                not curr.startswith("ADD") and not curr.startswith("SUB") and
                not curr.startswith("PUSH") and not curr.startswith("POP") and
                not curr.startswith("EX")):
                result.append(lines[i])
                i += 2
                optimizations += 1
                continue

            # Pattern: LHLD x / PUSH H / LXI H,const / EX\tDE,HL / POP H / DAD D -> LHLD x / LXI D,const / DAD D
            if (curr.startswith("LD\tHL,(") and next1 == "PUSH\tHL" and
                next2.startswith("LD\tHL,") and next3 == "EX\tDE,HL"):
                next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                next5 = lines[i + 5].strip() if i + 5 < len(lines) else ""
                if next4 == "POP\tHL" and next5 == "ADD\tHL,DE":
                    const = next2[6:]
                    result.append(lines[i])  # LHLD x
                    result.append(f"\tLD\tDE,{const}")
                    result.append("\tADD\tHL,DE")
                    i += 6
                    optimizations += 1
                    continue

            # Pattern: PUSH H / LXI H,const / EX\tDE,HL / POP H -> LXI D,const (loads DE while preserving HL)
            if (curr == "PUSH\tHL" and next1.startswith("LD\tHL,") and
                next2 == "EX\tDE,HL" and next3 == "POP\tHL"):
                const = next1[6:]
                result.append(f"\tLD\tDE,{const}")
                i += 4
                optimizations += 1
                continue

            # Pattern: PUSH H / LXI H,addr / POP D / DAD D -> LXI D,addr / DAD D
            # Common for array indexing: computing &arr[index] where HL has index
            if (curr == "PUSH\tHL" and next1.startswith("LD\tHL,") and
                next2 == "POP\tDE" and next3 == "ADD\tHL,DE"):
                addr = next1[6:]
                result.append(f"\tLD\tDE,{addr}")
                result.append("\tADD\tHL,DE")
                i += 4
                optimizations += 1
                continue

            # Pattern: MOV L,A / MVI H,0 / PUSH H / CALL x / POP D -> use A directly for 8-bit arg
            # This is complex, skip for now

            # Pattern: LDA x / MOV L,A / MVI H,0 / MOV A,H / ORA L -> LDA x / ORA A (test for zero)
            if curr.startswith("LD\tA,("):
                if next1 == "LD\tL,A" and next2 == "LD\tH,0" and next3 == "LD\tA,H":
                    next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                    if next4 == "ORA\tL":
                        result.append(lines[i])  # LDA x
                        result.append("\tOR\tA")
                        i += 5
                        optimizations += 1
                        continue

            # Pattern: PUSH PSW / MVI A,const / MOV B,A / POP PSW / ADD B -> ADI const
            if curr == "PUSH\tAF" and next1.startswith("LD\tA,") and next2 == "MOV\tB,A" and next3 == "POP\tAF":
                next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                if next4 == "ADD\tB":
                    const = next1[6:]
                    result.append(f"\tADD\tA,{const}")
                    i += 5
                    optimizations += 1
                    continue

            # Pattern: MOV A,L (or LDA x) / STA y / ... / LDA y where A still has value
            # Complex tracking needed, skip

            # Pattern: EX\tDE,HL / EX\tDE,HL -> remove both (no-op)
            # DISABLED: This is only safe if neither HL nor DE values are needed
            # after. In binary ops like ADD HL,DE we need DE to have the value
            # that was in HL before the first EX, but removing both loses it.
            # if curr == "EX\tDE,HL" and next1 == "EX\tDE,HL":
            #     i += 2
            #     optimizations += 1
            #     continue

            # Pattern: INX H / DCX H -> remove both (no-op)
            if curr == "INC\tHL" and next1 == "DEC\tHL":
                i += 2
                optimizations += 1
                continue

            # Pattern: DCX H / INX H -> remove both (no-op)
            if curr == "DEC\tHL" and next1 == "INC\tHL":
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI H,0 / MOV A,L / ORA H -> XRA A (test for zero simpler)
            if curr == "LD\tHL,0" and next1 == "LD\tA,L" and next2 == "ORA\tH":
                result.append("\tXOR\tA")
                i += 3
                optimizations += 1
                continue

            # Pattern: LXI H,0 / MOV A,L -> XRA A (loading zero into A)
            if curr == "LD\tHL,0" and next1 == "LD\tA,L":
                result.append("\tXOR\tA")
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI H,0 / DAD H -> LXI H,0 (0*2 = 0, DAD is useless)
            if curr == "LD\tHL,0" and next1 == "ADD\tHL,HL":
                result.append("\tLD\tHL,0")
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI H,const / DAD H -> LXI H,const*2 (constant folding)
            if curr.startswith("LD\tHL,") and next1 == "ADD\tHL,HL":
                const_str = curr[6:]
                try:
                    const = int(const_str)
                    result.append(f"\tLD\tHL,{const * 2}")
                    i += 2
                    optimizations += 1
                    continue
                except ValueError:
                    pass  # Not a numeric constant, skip

            # Pattern: LXI H,0 / LXI D,addr / DAD D -> LXI H,addr (0 + addr = addr)
            if curr == "LD\tHL,0" and next1.startswith("LD\tDE,") and next2 == "ADD\tHL,DE":
                addr = next1[6:]
                result.append(f"\tLD\tHL,{addr}")
                i += 3
                optimizations += 1
                continue

            # Pattern: MVI A,0 / ORA A -> XRA A (shorter)
            if curr == "LD\tA,0" and next1 == "OR\tA":
                result.append("\tXOR\tA")
                i += 2
                optimizations += 1
                continue

            # Pattern: CALL x / RET -> JMP x (tail call optimization)
            if curr.startswith("CALL\t") and next1 == "RET":
                target = curr[5:]
                result.append(f"\tJP\t{target}")
                i += 2
                optimizations += 1
                continue

            # Pattern: PUSH H / LXI H,0 / EX\tDE,HL / POP H / MOV A,H / CMP D -> MOV A,H / ORA L
            # This is comparing HL with 0
            if (curr == "PUSH\tHL" and next1 == "LD\tHL,0" and
                next2 == "EX\tDE,HL" and next3 == "POP\tHL"):
                next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                next5 = lines[i + 5].strip() if i + 5 < len(lines) else ""
                if next4 == "LD\tA,H" and next5 == "CMP\tD":
                    # This is comparing HL with zero - simplify to ORA
                    result.append("\tLD\tA,H")
                    result.append("\tOR\tL")
                    i += 6
                    # Skip the rest of the comparison (JNZ $+6 / MOV A,L / CMP E)
                    while i < len(lines):
                        check = lines[i].strip()
                        if check.startswith("JNZ\t$") or check == "LD\tA,L" or check == "CMP\tE":
                            i += 1
                        else:
                            break
                    optimizations += 1
                    continue

            # Pattern: LHLD x / MOV A,L / MOV L,A -> LHLD x / MOV A,L
            if curr.startswith("LD\tHL,(") and next1 == "LD\tA,L" and next2 == "LD\tL,A":
                result.append(lines[i])
                result.append("\tLD\tA,L")
                i += 3
                optimizations += 1
                continue

            # Pattern: DAD D / EX\tDE,HL / POP H / EX\tDE,HL -> DAD D / POP D
            # Effect: HL=HL+DE, DE=popped. Same result with 2 fewer instructions.
            if curr == "ADD\tHL,DE" and next1 == "EX\tDE,HL" and next2 == "POP\tHL" and next3 == "EX\tDE,HL":
                result.append("\tADD\tHL,DE")
                result.append("\tPOP\tDE")
                i += 4
                optimizations += 1
                continue

            # Pattern: EX\tDE,HL / POP H / EX\tDE,HL -> POP D (when we just want to pop into DE)
            # But only if what's in HL doesn't matter - need context
            # Let's handle the LXI case: LXI H,x / EX\tDE,HL / POP H / EX\tDE,HL -> LXI D,x / POP H
            if (curr.startswith("LD\tHL,") and next1 == "EX\tDE,HL" and
                next2 == "POP\tHL" and next3 == "EX\tDE,HL"):
                addr = curr[6:]
                result.append(f"\tLD\tDE,{addr}")
                result.append("\tPOP\tHL")
                i += 4
                optimizations += 1
                continue

            # === STRENGTH REDUCTION PATTERNS ===

            # Pattern: ANI 0FFH -> remove (no-op, A AND 0xFF = A)
            if curr == "ANI\t0FFH" or curr == "ANI\t255":
                i += 1
                optimizations += 1
                continue

            # Pattern: ORI 0 -> remove (no-op, A OR 0 = A)
            if curr == "ORI\t0":
                i += 1
                optimizations += 1
                continue

            # Pattern: ANI 0 -> XRA A (A AND 0 = 0)
            if curr == "ANI\t0":
                result.append("\tXOR\tA")
                i += 1
                optimizations += 1
                continue

            # Pattern: ADI 0 -> remove (no-op, A + 0 = A)
            if curr == "ADI\t0":
                i += 1
                optimizations += 1
                continue

            # Pattern: SUI 0 -> remove (no-op, A - 0 = A)
            if curr == "SUI\t0":
                i += 1
                optimizations += 1
                continue

            # Pattern: XRA A / ORA A -> XRA A (ORA A after XRA A is redundant)
            if curr == "XOR\tA" and next1 == "OR\tA":
                result.append(lines[i])
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI H,0 / DAD D -> EX\tDE,HL (0 + DE = DE, put in HL)
            if curr == "LD\tHL,0" and next1 == "ADD\tHL,DE":
                result.append("\tEX\tDE,HL")
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI D,0 / DAD D -> (remove, HL + 0 = HL)
            if curr == "LD\tDE,0" and next1 == "ADD\tHL,DE":
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI D,1 / DAD D -> INX H (HL + 1)
            if curr == "LD\tDE,1" and next1 == "ADD\tHL,DE":
                result.append("\tINC\tHL")
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI D,2 / DAD D -> INX H / INX H (HL + 2)
            if curr == "LD\tDE,2" and next1 == "ADD\tHL,DE":
                result.append("\tINC\tHL")
                result.append("\tINC\tHL")
                i += 2
                optimizations += 1
                continue

            # Pattern: LXI D,3 / DAD D -> INX H / INX H / INX H (HL + 3)
            if curr == "LD\tDE,3" and next1 == "ADD\tHL,DE":
                result.append("\tINC\tHL")
                result.append("\tINC\tHL")
                result.append("\tINC\tHL")
                i += 2
                optimizations += 1
                continue

            # Pattern: MOV A,A -> remove (no-op)
            if curr == "MOV\tA,A":
                i += 1
                optimizations += 1
                continue

            # Pattern: MOV B,B -> remove (no-op)
            if curr == "MOV\tB,B":
                i += 1
                optimizations += 1
                continue

            # Pattern: MOV C,C -> remove (no-op)
            if curr == "MOV\tC,C":
                i += 1
                optimizations += 1
                continue

            # Pattern: MOV D,D -> remove (no-op)
            if curr == "MOV\tD,D":
                i += 1
                optimizations += 1
                continue

            # Pattern: MOV E,E -> remove (no-op)
            if curr == "MOV\tE,E":
                i += 1
                optimizations += 1
                continue

            # Pattern: MOV H,H -> remove (no-op)
            if curr == "MOV\tH,H":
                i += 1
                optimizations += 1
                continue

            # Pattern: MOV L,L -> remove (no-op)
            if curr == "MOV\tL,L":
                i += 1
                optimizations += 1
                continue

            # Pattern: LD HL,const / LD A,L / LD (x),A -> LD A,const / LD (x),A (for small constants)
            # This is very common for 8-bit variable initialization from constants
            if curr.startswith("LD\tHL,") and next1 == "LD\tA,L":
                try:
                    const_str = curr[6:]
                    const = int(const_str)
                    if 0 <= const <= 255:
                        if next2.startswith("LD\t(") and next2.endswith("),A"):
                            result.append(f"\tLD\tA,{const}")
                            result.append(lines[i + 2])  # Keep original with tab
                            i += 3
                            optimizations += 1
                            continue
                except ValueError:
                    pass

            # Pattern: LD HL,0 / LD A,L -> XOR A (0 to A is common)
            if curr == "LD\tHL,0" and next1 == "LD\tA,L":
                # Only if next instruction doesn't need H
                if not next2.startswith("LD\tA,H") and next2 != "LD\tH,0":
                    result.append("\tXOR\tA")
                    i += 2
                    optimizations += 1
                    continue

            # Pattern: LD HL,const / LD A,L / LD H,0 -> LD A,const / LD L,A / LD H,0 (saves LD HL)
            # Actually better: skip H,0 setup if we then do something else with HL
            # For now, just the basic pattern

            # Pattern: PUSH HL / CALL print / POP DE -> CALL print / EX DE,HL (if HL has return)
            # Hmm, print doesn't return in HL, so this is for stack cleanup

            # Pattern: PUSH AF / LD A,(x) / LD E,A / POP AF -> LD A,(x) / LD E,A / PUSH AF...
            # This is tricky due to flag dependencies

            # Pattern: LD A,L / LD L,A / LD H,0 -> keep just LD L,A / LD H,0 if we need HL extension
            # No wait, that changes semantics - A needs the value. Keep as-is.

            # Pattern: LD A,(x) / PUSH AF / LD A,(y) / LD E,A / POP AF ->
            #          LD A,(y) / LD E,A / LD A,(x)  (avoid push/pop for 8-bit ops)
            if curr.startswith("LD\tA,(") and next1 == "PUSH\tAF":
                if next2.startswith("LD\tA,(") and next3 == "LD\tE,A":
                    next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                    if next4 == "POP\tAF":
                        var_x = curr[5:]  # "(xxx)" from "LD\tA,(xxx)" - skip "LD\tA,"
                        var_y = next2[5:]  # "(yyy)" from "LD\tA,(yyy)"
                        result.append(f"\tLD\tA,{var_y}")
                        result.append("\tLD\tE,A")
                        result.append(f"\tLD\tA,{var_x}")
                        i += 5
                        optimizations += 1
                        continue

            # Pattern: LD L,A / LD H,0 / PUSH HL -> LD L,A / LD H,0 / PUSH HL (keep, but check for...)
            # Pattern: LD L,A / LD H,0 / PUSH HL / CALL x / POP DE - the common print pattern

            # Pattern: ADD A,const / LD L,A / LD H,0 when we only need 8-bit result
            # Not safe without knowing context

            # Pattern: LD HL,const / ADD HL,DE -> can sometimes be LXI D,const / DAD D or EX/DAD/EX
            # Complex, skip

            # Pattern: consecutive LD (x),HL / LD HL,(y) / LD (x),HL - redundant middle store
            # Only if y != x - need tracking

            # Pattern: JP z,target / JP target2 / target: -> JP nz,target2 / target:
            # Jump threading - complex, needs label tracking

            # Pattern: LD A,H / OR L / JP Z,x vs LD A,H / OR L / JP NZ,x
            # No optimization, just noting this is common for HL==0 check

            # Pattern: LD HL,(x) / EX DE,HL / LD HL,(x) -> LD HL,(x) / LD D,H / LD E,L
            # Same variable loaded twice - use register copy instead of memory access
            if curr.startswith("LD\tHL,(") and next1 == "EX\tDE,HL":
                var = curr[7:-1]  # Extract x from "LD\tHL,(x)" - skip "LD\tHL,(" and trailing ")"
                if next2 == f"LD\tHL,({var})":
                    result.append(lines[i])
                    result.append("\tLD\tD,H")
                    result.append("\tLD\tE,L")
                    i += 3
                    optimizations += 1
                    continue

            # Pattern: LD A,x / LD x,A -> remove second (x is already x)
            # This is like LD A,B / LD B,A - second is redundant if no intervening ops
            if curr.startswith("LD\tA,") and not curr.startswith("LD\tA,("):
                reg = curr[5:]
                if next1 == f"LD\t{reg},A":
                    result.append(lines[i])
                    i += 2
                    optimizations += 1
                    continue

            # Pattern: LD DE,const / ADD HL,DE for small constants
            # Already have LD DE,1-3 / ADD HL,DE -> INC HL patterns above

            # Pattern: Multiple POP DE after calls when DE not used
            # Can't optimize without liveness analysis

            # Pattern: LD HL,label / PUSH HL / CALL x -> CALL x with inline push
            # Not directly applicable in Z80

            # Pattern: JP cond,L1 / JP L2 / L1: ... when L1 is immediately after
            # This is handled by the "JP to next instruction" pattern

            # Pattern: LD A,0 / OR A -> XOR A (sets zero flag, clears A)
            if curr == "LD\tA,0" and next1 == "OR\tA":
                result.append("\tXOR\tA")
                i += 2
                optimizations += 1
                continue

            # Pattern: LD A,(x) / OR A / JP Z,y -> LD A,(x) / AND A / JP Z,y
            # Same thing, but AND A is what we already emit. No change needed.

            # Pattern: XOR A / LD H,A -> LD H,0 (uses result of XOR A)
            if curr == "XOR\tA" and next1 == "LD\tH,A":
                result.append("\tLD\tH,0")
                i += 2
                optimizations += 1
                continue

            # Pattern: XOR A / LD L,A -> LD L,0 (uses result of XOR A)
            if curr == "XOR\tA" and next1 == "LD\tL,A":
                result.append("\tLD\tL,0")
                i += 2
                optimizations += 1
                continue

            # Pattern: XOR A / LD (x),A -> LD A,0 / LD (x),A (same bytes, but clearer)
            # Actually XOR A is 1 byte, LD A,0 is 2 bytes. Keep XOR A.

            # Pattern: LD HL,const / LD DE,HL -> LD DE,const (direct load)
            # Wait, LD DE,HL isn't a valid Z80 instruction. Skip.

            # Pattern: LD A,const / ADD A,B vs LD A,B / ADD A,const
            # Depends on which constant is smaller

            # Pattern: INC A / DEC A -> remove both
            if curr == "INC\tA" and next1 == "DEC\tA":
                i += 2
                optimizations += 1
                continue

            # Pattern: DEC A / INC A -> remove both
            if curr == "DEC\tA" and next1 == "INC\tA":
                i += 2
                optimizations += 1
                continue

            # Pattern: INC HL / INC HL / INC HL / INC HL -> LD DE,4 / ADD HL,DE (for 4+ increments)
            # Actually INC HL is 1 byte, LD DE,4 is 3 bytes, ADD HL,DE is 1 byte = 4 bytes
            # 4 INC HL = 4 bytes, so break-even at 4. Worth it at 5+.
            if curr == "INC\tHL" and next1 == "INC\tHL" and next2 == "INC\tHL" and next3 == "INC\tHL":
                next4 = lines[i + 4].strip() if i + 4 < len(lines) else ""
                if next4 == "INC\tHL":
                    # Count total consecutive INC HL
                    count = 4
                    j = i + 4
                    while j < len(lines) and lines[j].strip() == "INC\tHL":
                        count += 1
                        j += 1
                    if count >= 5:
                        result.append(f"\tLD\tDE,{count}")
                        result.append("\tADD\tHL,DE")
                        i = j
                        optimizations += 1
                        continue

            # Pattern: LD A,0 -> XOR A (saves 1 byte: 2 bytes vs 1 byte)
            if curr == "LD\tA,0":
                result.append("\tXOR\tA")
                i += 1
                optimizations += 1
                continue

            # Pattern: LD H,0 / LD L,0 -> LD HL,0 (saves 1 byte: 4 bytes vs 3 bytes)
            if curr == "LD\tH,0" and next1 == "LD\tL,0":
                result.append("\tLD\tHL,0")
                i += 2
                optimizations += 1
                continue

            # Pattern: LD L,0 / LD H,0 -> LD HL,0 (saves 1 byte)
            if curr == "LD\tL,0" and next1 == "LD\tH,0":
                result.append("\tLD\tHL,0")
                i += 2
                optimizations += 1
                continue

            # Pattern: DEC B / JR NZ,label -> DJNZ label (saves 1 byte: 3 bytes vs 2 bytes)
            if curr == "DEC\tB" and next1.startswith("JR\tNZ,"):
                label = next1[6:]  # Extract label after "JR\tNZ,"
                result.append(f"\tDJNZ\t{label}")
                i += 2
                optimizations += 1
                continue

            # Pattern: LD B,n / loop: ... / DEC B / JR NZ,loop
            # This is handled by the DEC B / JR NZ pattern above

            # Pattern: OR A / RET Z -> RET Z (OR A sets Z flag but RET Z doesn't need it fresh if A==0)
            # Actually this is wrong - we need OR A to set the flag. Skip.

            # Pattern: LD HL,0 / LD A,H / OR L -> XOR A (testing if HL==0, but HL is known 0)
            if curr == "LD\tHL,0" and next1 == "LD\tA,H" and next2 == "OR\tL":
                result.append("\tLD\tHL,0")
                result.append("\tXOR\tA")  # A=0, Z flag set
                i += 3
                optimizations += 1
                continue

            # Pattern: LD A,H / OR L / JR Z,x when we just set HL to known value
            # Complex - needs value tracking, skip for now

            # Pattern: PUSH HL / POP HL -> remove (no-op)
            if curr == "PUSH\tHL" and next1 == "POP\tHL":
                i += 2
                optimizations += 1
                continue

            # Pattern: PUSH DE / POP DE -> remove (no-op)
            if curr == "PUSH\tDE" and next1 == "POP\tDE":
                i += 2
                optimizations += 1
                continue

            # Pattern: PUSH BC / POP BC -> remove (no-op)
            if curr == "PUSH\tBC" and next1 == "POP\tBC":
                i += 2
                optimizations += 1
                continue

            # Pattern: PUSH AF / POP AF -> remove (no-op, but flags change - be careful)
            # Skip - flags might matter

            # Pattern: EX DE,HL / EX DE,HL -> remove (no-op, swaps twice = original)
            if curr == "EX\tDE,HL" and next1 == "EX\tDE,HL":
                i += 2
                optimizations += 1
                continue

            # Pattern: LD HL,x / LD HL,y -> LD HL,y (first load is dead)
            if curr.startswith("LD\tHL,") and next1.startswith("LD\tHL,"):
                # Skip the first LD HL
                i += 1
                optimizations += 1
                continue

            # Pattern: LD DE,x / LD DE,y -> LD DE,y (first load is dead)
            if curr.startswith("LD\tDE,") and next1.startswith("LD\tDE,"):
                i += 1
                optimizations += 1
                continue

            # Pattern: LD A,x / LD A,y -> LD A,y (first load is dead)
            if curr.startswith("LD\tA,") and next1.startswith("LD\tA,"):
                i += 1
                optimizations += 1
                continue

            # No optimization applied - keep the line
            result.append(lines[i])
            i += 1

        return result


def generate(program: ast.Program, checker: TypeChecker, library_mode: bool = False) -> str:
    """Generate assembly from AST.

    Args:
        program: The AST to generate code from
        checker: The type checker with symbol information
        library_mode: If True, generate a library module without main entry point
    """
    gen = CodeGenerator(checker)
    gen.library_mode = library_mode
    return gen.gen_program(program)
