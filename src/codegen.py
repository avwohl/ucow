"""8080 code generator for Cowgol."""

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


class CodeGenerator:
    """Generate 8080 assembly from Cowgol AST."""

    def __init__(self, checker: TypeChecker):
        self.checker = checker
        self.output: List[str] = []
        self.data: List[str] = []
        self.variables: Dict[str, Variable] = {}
        self.string_literals: Dict[str, str] = {}  # value -> label
        self.data_offset = 0
        self.label_counter = 0
        self.current_sub: Optional[str] = None
        self.break_labels: List[str] = []
        self.continue_labels: List[str] = []

    def emit(self, line: str) -> None:
        """Emit an assembly line."""
        self.output.append(line)

    def emit_label(self, label: str) -> None:
        """Emit a label."""
        self.output.append(f"{label}:")

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

    def allocate_var(self, name: str, typ: CowType) -> Variable:
        """Allocate storage for a variable."""
        size = self.type_size(typ)
        var = Variable(name, typ, self.data_offset, size)
        self.variables[name] = var
        self.data_offset += size
        return var

    def get_string_label(self, value: str) -> str:
        """Get or create a label for a string literal."""
        if value not in self.string_literals:
            label = self.new_label("STR")
            self.string_literals[value] = label
        return self.string_literals[value]

    def mangle_name(self, name: str) -> str:
        """Mangle a variable name to avoid conflicts with 8080 registers."""
        # Prefix with v_ to avoid conflicts with register names (a,b,c,d,e,h,l,m)
        # and reserved words
        return f"v_{name}"

    def mangle_sub_name(self, name: str) -> str:
        """Mangle a subroutine name to avoid conflicts with 8080 registers."""
        # Only mangle if it conflicts with 8080 register names
        if name.upper() in ('A', 'B', 'C', 'D', 'E', 'H', 'L', 'M', 'SP', 'PSW'):
            return f"s_{name}"
        return name

    # Code generation for expressions

    def gen_expr(self, expr: ast.Expression, target: str = 'HL') -> None:
        """Generate code to evaluate expression, result in target (HL or A)."""

        if isinstance(expr, ast.NumberLiteral):
            value = expr.value
            if target == 'A':
                self.emit(f"\tMVI\tA,{value & 0xFF}")
            else:
                self.emit(f"\tLXI\tH,{value & 0xFFFF}")

        elif isinstance(expr, ast.StringLiteral):
            label = self.get_string_label(expr.value)
            self.emit(f"\tLXI\tH,{label}")

        elif isinstance(expr, ast.NilLiteral):
            if target == 'A':
                self.emit("\tXRA\tA")
            else:
                self.emit("\tLXI\tH,0")

        elif isinstance(expr, ast.Identifier):
            var = self.variables.get(expr.name)
            if var:
                mangled = self.mangle_name(expr.name)
                if var.size == 1:
                    self.emit(f"\tLDA\t{mangled}")
                    if target == 'HL':
                        self.emit("\tMOV\tL,A")
                        self.emit("\tMVI\tH,0")
                else:
                    self.emit(f"\tLHLD\t{mangled}")
                    if target == 'A':
                        self.emit("\tMOV\tA,L")
            else:
                # Check if it's a constant
                if expr.name in self.checker.constants:
                    value = self.checker.constants[expr.name]
                    if target == 'A':
                        self.emit(f"\tMVI\tA,{value & 0xFF}")
                    else:
                        self.emit(f"\tLXI\tH,{value & 0xFFFF}")
                # Could be a subroutine reference
                elif expr.name in self.checker.subroutines:
                    self.emit(f"\tLXI\tH,{self.mangle_sub_name(expr.name)}")
                else:
                    # External symbol
                    self.emit(f"\tLXI\tH,{expr.name}")

        elif isinstance(expr, ast.BinaryOp):
            self.gen_binop(expr, target)

        elif isinstance(expr, ast.UnaryOp):
            if expr.op == '-':
                self.gen_expr(expr.operand, target)
                if target == 'A':
                    self.emit("\tCMA")
                    self.emit("\tINR\tA")
                else:
                    # Negate HL: HL = 0 - HL
                    self.emit("\tMOV\tA,L")
                    self.emit("\tCMA")
                    self.emit("\tMOV\tL,A")
                    self.emit("\tMOV\tA,H")
                    self.emit("\tCMA")
                    self.emit("\tMOV\tH,A")
                    self.emit("\tINX\tH")
            elif expr.op == '~':
                self.gen_expr(expr.operand, target)
                if target == 'A':
                    self.emit("\tCMA")
                else:
                    self.emit("\tMOV\tA,L")
                    self.emit("\tCMA")
                    self.emit("\tMOV\tL,A")
                    self.emit("\tMOV\tA,H")
                    self.emit("\tCMA")
                    self.emit("\tMOV\tH,A")

        elif isinstance(expr, ast.Comparison):
            self.gen_comparison(expr)
            if target == 'HL':
                self.emit("\tMOV\tL,A")
                self.emit("\tMVI\tH,0")

        elif isinstance(expr, ast.LogicalOp):
            self.gen_logical(expr)
            if target == 'HL':
                self.emit("\tMOV\tL,A")
                self.emit("\tMVI\tH,0")

        elif isinstance(expr, ast.NotOp):
            self.gen_expr(expr.operand, 'A')
            self.emit("\tORA\tA")  # Set flags
            self.emit("\tMVI\tA,0")
            self.emit("\tJNZ\t$+4")
            self.emit("\tMVI\tA,1")
            if target == 'HL':
                self.emit("\tMOV\tL,A")
                self.emit("\tMVI\tH,0")

        elif isinstance(expr, ast.Cast):
            # Generate the inner expression
            self.gen_expr(expr.expr, target)
            # Type conversion handled by size

        elif isinstance(expr, ast.ArrayAccess):
            self.gen_array_access(expr, target)

        elif isinstance(expr, ast.FieldAccess):
            self.gen_field_access(expr, target)

        elif isinstance(expr, ast.Dereference):
            self.gen_expr(expr.pointer, 'HL')
            # Load from address in HL
            typ = expr.resolved_type
            if self.type_size(typ) == 1:
                self.emit("\tMOV\tA,M")
                if target == 'HL':
                    self.emit("\tMOV\tL,A")
                    self.emit("\tMVI\tH,0")
            else:
                self.emit("\tMOV\tE,M")
                self.emit("\tINX\tH")
                self.emit("\tMOV\tD,M")
                self.emit("\tXCHG")

        elif isinstance(expr, ast.AddressOf):
            # Get address of operand
            if isinstance(expr.operand, ast.Identifier):
                var = self.variables.get(expr.operand.name)
                if var:
                    self.emit(f"\tLXI\tH,{self.mangle_name(expr.operand.name)}")
            elif isinstance(expr.operand, ast.FieldAccess):
                self.gen_field_address(expr.operand)
            elif isinstance(expr.operand, ast.ArrayAccess):
                self.gen_array_address(expr.operand)

        elif isinstance(expr, ast.Call):
            self.gen_call(expr, target)

        elif isinstance(expr, ast.SizeOf):
            # Return array size
            if isinstance(expr.target, ast.Expression):
                typ = expr.target.resolved_type
                if isinstance(typ, ArrayType):
                    if target == 'A':
                        self.emit(f"\tMVI\tA,{typ.size & 0xFF}")
                    else:
                        self.emit(f"\tLXI\tH,{typ.size}")

        elif isinstance(expr, ast.BytesOf):
            # Return size in bytes
            if isinstance(expr.target, ast.Expression):
                typ = expr.target.resolved_type
                size = self.type_size(typ)
            else:
                size = self.type_size(self.checker.resolve_type(expr.target))
            if target == 'A':
                self.emit(f"\tMVI\tA,{size & 0xFF}")
            else:
                self.emit(f"\tLXI\tH,{size}")

        elif isinstance(expr, ast.Next):
            self.gen_expr(expr.pointer, 'HL')
            typ = expr.resolved_type
            if isinstance(typ, PtrType):
                size = self.type_size(typ.target)
                if size == 1:
                    self.emit("\tINX\tH")
                else:
                    self.emit(f"\tLXI\tD,{size}")
                    self.emit("\tDAD\tD")

        elif isinstance(expr, ast.Prev):
            self.gen_expr(expr.pointer, 'HL')
            typ = expr.resolved_type
            if isinstance(typ, PtrType):
                size = self.type_size(typ.target)
                if size == 1:
                    self.emit("\tDCX\tH")
                else:
                    # HL = HL - size
                    self.emit(f"\tLXI\tD,-{size}")
                    self.emit("\tDAD\tD")

        elif isinstance(expr, ast.ArrayInitializer):
            # This should only appear in variable initialization
            pass

        else:
            self.emit(f"\t; TODO: {type(expr).__name__}")

    def gen_binop(self, expr: ast.BinaryOp, target: str) -> None:
        """Generate binary operation."""
        op = expr.op

        # For 8-bit operations
        if self.type_size(expr.resolved_type) == 1:
            self.gen_expr(expr.left, 'A')
            self.emit("\tPUSH\tPSW")
            self.gen_expr(expr.right, 'A')
            self.emit("\tMOV\tB,A")
            self.emit("\tPOP\tPSW")

            if op == '+':
                self.emit("\tADD\tB")
            elif op == '-':
                self.emit("\tSUB\tB")
            elif op == '&':
                self.emit("\tANA\tB")
            elif op == '|':
                self.emit("\tORA\tB")
            elif op == '^':
                self.emit("\tXRA\tB")
            elif op == '*':
                self.emit("\tCALL\t_mul8")
            elif op == '/':
                self.emit("\tCALL\t_div8")
            elif op == '%':
                self.emit("\tCALL\t_mod8")
            elif op == '<<':
                # Shift left by B
                label = self.new_label("SHL")
                end = self.new_label("SHLE")
                self.emit_label(label)
                self.emit("\tMOV\tC,A")
                self.emit("\tMOV\tA,B")
                self.emit("\tORA\tA")
                self.emit(f"\tJZ\t{end}")
                self.emit("\tDCR\tB")
                self.emit("\tMOV\tA,C")
                self.emit("\tADD\tA")
                self.emit(f"\tJMP\t{label}")
                self.emit_label(end)
            elif op == '>>':
                # Shift right by B
                label = self.new_label("SHR")
                end = self.new_label("SHRE")
                self.emit_label(label)
                self.emit("\tMOV\tC,A")
                self.emit("\tMOV\tA,B")
                self.emit("\tORA\tA")
                self.emit(f"\tJZ\t{end}")
                self.emit("\tDCR\tB")
                self.emit("\tMOV\tA,C")
                self.emit("\tORA\tA")
                self.emit("\tRAR")
                self.emit(f"\tJMP\t{label}")
                self.emit_label(end)

            if target == 'HL':
                self.emit("\tMOV\tL,A")
                self.emit("\tMVI\tH,0")

        else:
            # 16-bit operations
            self.gen_expr(expr.left, 'HL')
            self.emit("\tPUSH\tH")
            self.gen_expr(expr.right, 'HL')
            self.emit("\tXCHG")  # DE = right
            self.emit("\tPOP\tH")  # HL = left

            if op == '+':
                self.emit("\tDAD\tD")
            elif op == '-':
                # HL = HL - DE
                self.emit("\tMOV\tA,L")
                self.emit("\tSUB\tE")
                self.emit("\tMOV\tL,A")
                self.emit("\tMOV\tA,H")
                self.emit("\tSBB\tD")
                self.emit("\tMOV\tH,A")
            elif op == '&':
                self.emit("\tMOV\tA,L")
                self.emit("\tANA\tE")
                self.emit("\tMOV\tL,A")
                self.emit("\tMOV\tA,H")
                self.emit("\tANA\tD")
                self.emit("\tMOV\tH,A")
            elif op == '|':
                self.emit("\tMOV\tA,L")
                self.emit("\tORA\tE")
                self.emit("\tMOV\tL,A")
                self.emit("\tMOV\tA,H")
                self.emit("\tORA\tD")
                self.emit("\tMOV\tH,A")
            elif op == '^':
                self.emit("\tMOV\tA,L")
                self.emit("\tXRA\tE")
                self.emit("\tMOV\tL,A")
                self.emit("\tMOV\tA,H")
                self.emit("\tXRA\tD")
                self.emit("\tMOV\tH,A")
            elif op == '*':
                self.emit("\tCALL\t_mul16")
            elif op == '/':
                self.emit("\tCALL\t_div16")
            elif op == '%':
                self.emit("\tCALL\t_mod16")
            elif op == '<<':
                self.emit("\tCALL\t_shl16")
            elif op == '>>':
                self.emit("\tCALL\t_shr16")

            if target == 'A':
                self.emit("\tMOV\tA,L")

    def gen_comparison(self, expr: ast.Comparison) -> None:
        """Generate comparison, result in A (0 or 1)."""
        op = expr.op

        # Generate both sides
        self.gen_expr(expr.left, 'HL')
        self.emit("\tPUSH\tH")
        self.gen_expr(expr.right, 'HL')
        self.emit("\tXCHG")
        self.emit("\tPOP\tH")

        # Compare HL with DE
        self.emit("\tMOV\tA,H")
        self.emit("\tCMP\tD")
        self.emit("\tJNZ\t$+6")
        self.emit("\tMOV\tA,L")
        self.emit("\tCMP\tE")

        # Set result based on flags
        true_label = self.new_label("TRUE")
        end_label = self.new_label("END")

        false_label = self.new_label("FALSE")

        if op == '==':
            self.emit(f"\tJZ\t{true_label}")
        elif op == '!=':
            self.emit(f"\tJNZ\t{true_label}")
        elif op == '<':
            self.emit(f"\tJC\t{true_label}")
        elif op == '>=':
            self.emit(f"\tJNC\t{true_label}")
        elif op == '>':
            self.emit(f"\tJZ\t{false_label}")  # Equal means not greater
            self.emit(f"\tJNC\t{true_label}")
        elif op == '<=':
            self.emit(f"\tJZ\t{true_label}")  # Equal means <=
            self.emit(f"\tJC\t{true_label}")

        self.emit_label(false_label)
        self.emit("\tXRA\tA")  # False
        self.emit(f"\tJMP\t{end_label}")
        self.emit_label(true_label)
        self.emit("\tMVI\tA,1")  # True
        self.emit_label(end_label)

    def gen_logical(self, expr: ast.LogicalOp) -> None:
        """Generate short-circuit logical operation."""
        if expr.op == 'and':
            false_label = self.new_label("FALSE")
            end_label = self.new_label("END")

            self.gen_expr(expr.left, 'A')
            self.emit("\tORA\tA")
            self.emit(f"\tJZ\t{false_label}")

            self.gen_expr(expr.right, 'A')
            self.emit("\tORA\tA")
            self.emit(f"\tJZ\t{false_label}")

            self.emit("\tMVI\tA,1")
            self.emit(f"\tJMP\t{end_label}")
            self.emit_label(false_label)
            self.emit("\tXRA\tA")
            self.emit_label(end_label)

        elif expr.op == 'or':
            true_label = self.new_label("TRUE")
            end_label = self.new_label("END")

            self.gen_expr(expr.left, 'A')
            self.emit("\tORA\tA")
            self.emit(f"\tJNZ\t{true_label}")

            self.gen_expr(expr.right, 'A')
            self.emit("\tORA\tA")
            self.emit(f"\tJNZ\t{true_label}")

            self.emit("\tXRA\tA")
            self.emit(f"\tJMP\t{end_label}")
            self.emit_label(true_label)
            self.emit("\tMVI\tA,1")
            self.emit_label(end_label)

    def gen_array_access(self, expr: ast.ArrayAccess, target: str) -> None:
        """Generate array element access."""
        # Get base address
        self.gen_array_address(expr)

        # Load value from address
        typ = expr.resolved_type
        if self.type_size(typ) == 1:
            self.emit("\tMOV\tA,M")
            if target == 'HL':
                self.emit("\tMOV\tL,A")
                self.emit("\tMVI\tH,0")
        else:
            self.emit("\tMOV\tE,M")
            self.emit("\tINX\tH")
            self.emit("\tMOV\tD,M")
            self.emit("\tXCHG")
            if target == 'A':
                self.emit("\tMOV\tA,L")

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
            self.emit(f"\tLXI\tD,{elem_size}")
            self.emit("\tCALL\t_mul16")

        self.emit("\tPUSH\tH")

        # Get base address
        if isinstance(expr.array, ast.Identifier):
            var = self.variables.get(expr.array.name)
            if var:
                self.emit(f"\tLXI\tH,{self.mangle_name(expr.array.name)}")
            else:
                self.emit(f"\tLXI\tH,{expr.array.name}")
        else:
            self.gen_expr(expr.array, 'HL')

        self.emit("\tPOP\tD")
        self.emit("\tDAD\tD")

    def gen_field_access(self, expr: ast.FieldAccess, target: str) -> None:
        """Generate field access."""
        self.gen_field_address(expr)

        # Load value
        typ = expr.resolved_type
        if self.type_size(typ) == 1:
            self.emit("\tMOV\tA,M")
            if target == 'HL':
                self.emit("\tMOV\tL,A")
                self.emit("\tMVI\tH,0")
        else:
            self.emit("\tMOV\tE,M")
            self.emit("\tINX\tH")
            self.emit("\tMOV\tD,M")
            self.emit("\tXCHG")
            if target == 'A':
                self.emit("\tMOV\tA,L")

    def gen_field_address(self, expr: ast.FieldAccess) -> None:
        """Generate address of record field in HL."""
        # Get record address
        record_type = expr.record.resolved_type
        if isinstance(record_type, PtrType):
            self.gen_expr(expr.record, 'HL')
            record_type = record_type.target
        else:
            if isinstance(expr.record, ast.Identifier):
                var = self.variables.get(expr.record.name)
                if var:
                    self.emit(f"\tLXI\tH,{self.mangle_name(expr.record.name)}")
                else:
                    self.emit(f"\tLXI\tH,{expr.record.name}")
            else:
                self.gen_expr(expr.record, 'HL')

        # Add field offset
        if isinstance(record_type, RecordType):
            info = self.checker.records.get(record_type.name)
            if info:
                for field in info.fields:
                    if field.name == expr.field:
                        if field.offset > 0:
                            self.emit(f"\tLXI\tD,{field.offset}")
                            self.emit("\tDAD\tD")
                        break

    def gen_call(self, expr: ast.Call, target: str) -> None:
        """Generate subroutine call."""
        # Push arguments in reverse order
        for arg in reversed(expr.args):
            self.gen_expr(arg, 'HL')
            self.emit("\tPUSH\tH")

        # Call
        if isinstance(expr.target, ast.Identifier):
            name = expr.target.name
            # Check if it's a direct subroutine call or indirect (interface variable)
            if name in self.checker.subroutines:
                # Direct call to known subroutine
                self.emit(f"\tCALL\t{self.mangle_sub_name(name)}")
            else:
                # Indirect call through interface variable
                # Load address and use _callhl helper
                var = self.variables.get(name)
                if var:
                    self.emit(f"\tLHLD\t{self.mangle_name(name)}")
                else:
                    self.emit(f"\tLXI\tH,{name}")
                self.emit("\tCALL\t_callhl")  # Helper that does PCHL
        else:
            self.gen_expr(expr.target, 'HL')
            # Call address in HL - use helper
            self.emit("\tCALL\t_callhl")

        # Clean up arguments from stack
        if expr.args:
            stack_bytes = len(expr.args) * 2
            if stack_bytes == 2:
                self.emit("\tPOP\tD")  # Discard
            elif stack_bytes == 4:
                self.emit("\tPOP\tD")
                self.emit("\tPOP\tD")
            else:
                # Save return value, adjust SP, restore return value
                self.emit("\tPUSH\tH")  # Save return value
                self.emit(f"\tLXI\tH,{stack_bytes + 2}")  # +2 for saved value
                self.emit("\tDAD\tSP")
                self.emit("\tSPHL")
                self.emit("\tPOP\tH")  # Restore return value

        # Result is in HL for 16-bit, A for 8-bit
        if target == 'A' and expr.resolved_type:
            if self.type_size(expr.resolved_type) > 1:
                self.emit("\tMOV\tA,L")

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
                self.emit(f"\tJMP\t{self.break_labels[-1]}")

        elif isinstance(stmt, ast.ContinueStmt):
            if self.continue_labels:
                self.emit(f"\tJMP\t{self.continue_labels[-1]}")

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
        var = self.variables.get(stmt.name)
        if not var:
            var = self.allocate_var(stmt.name, var_type)

            # Generate initialization if present
            if stmt.init:
                mangled = self.mangle_name(stmt.name)
                if isinstance(stmt.init, ast.ArrayInitializer):
                    self.gen_array_init(var, stmt.init)
                elif isinstance(stmt.init, ast.StringLiteral):
                    # String initialization
                    label = self.get_string_label(stmt.init.value)
                    self.emit(f"\tLXI\tH,{label}")
                    self.emit(f"\tSHLD\t{mangled}")
                else:
                    self.gen_expr(stmt.init, 'HL')
                    if var.size == 1:
                        self.emit("\tMOV\tA,L")
                        self.emit(f"\tSTA\t{mangled}")
                    else:
                        self.emit(f"\tSHLD\t{mangled}")

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
                self.emit("\tMOV\tA,L")
                self.emit(f"\tSTA\t{mangled}+{offset}")
            else:
                self.emit(f"\tSHLD\t{mangled}+{offset}")
            offset += elem_size

    def gen_assignment(self, stmt: ast.Assignment) -> None:
        """Generate assignment statement."""
        # Generate value
        self.gen_expr(stmt.value, 'HL')

        # Store to target
        if isinstance(stmt.target, ast.Identifier):
            var = self.variables.get(stmt.target.name)
            if var:
                mangled = self.mangle_name(stmt.target.name)
                if var.size == 1:
                    self.emit("\tMOV\tA,L")
                    self.emit(f"\tSTA\t{mangled}")
                else:
                    self.emit(f"\tSHLD\t{mangled}")

        elif isinstance(stmt.target, ast.ArrayAccess):
            self.emit("\tPUSH\tH")  # Save value
            self.gen_array_address(stmt.target)
            self.emit("\tXCHG")  # DE = address
            self.emit("\tPOP\tH")  # HL = value

            typ = stmt.target.resolved_type
            if self.type_size(typ) == 1:
                self.emit("\tMOV\tA,L")
                self.emit("\tSTAX\tD")
            else:
                self.emit("\tXCHG")
                self.emit("\tMOV\tM,E")
                self.emit("\tINX\tH")
                self.emit("\tMOV\tM,D")

        elif isinstance(stmt.target, ast.FieldAccess):
            self.emit("\tPUSH\tH")
            self.gen_field_address(stmt.target)
            self.emit("\tXCHG")
            self.emit("\tPOP\tH")

            typ = stmt.target.resolved_type
            if self.type_size(typ) == 1:
                self.emit("\tMOV\tA,L")
                self.emit("\tSTAX\tD")
            else:
                self.emit("\tXCHG")
                self.emit("\tMOV\tM,E")
                self.emit("\tINX\tH")
                self.emit("\tMOV\tM,D")

        elif isinstance(stmt.target, ast.Dereference):
            self.emit("\tPUSH\tH")
            self.gen_expr(stmt.target.pointer, 'HL')
            self.emit("\tXCHG")
            self.emit("\tPOP\tH")

            typ = stmt.target.resolved_type
            if self.type_size(typ) == 1:
                self.emit("\tMOV\tA,L")
                self.emit("\tSTAX\tD")
            else:
                self.emit("\tXCHG")
                self.emit("\tMOV\tM,E")
                self.emit("\tINX\tH")
                self.emit("\tMOV\tM,D")

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
                self.emit("\tPOP\tH")

            if isinstance(target, ast.Identifier):
                var = self.variables.get(target.name)
                mangled = self.mangle_name(target.name)
                if var and var.size == 1:
                    self.emit("\tMOV\tA,L")
                    self.emit(f"\tSTA\t{mangled}")
                else:
                    self.emit(f"\tSHLD\t{mangled}")

    def gen_if(self, stmt: ast.IfStmt) -> None:
        """Generate if statement."""
        else_label = self.new_label("ELSE")
        end_label = self.new_label("ENDIF")

        # Condition
        self.gen_expr(stmt.condition, 'A')
        self.emit("\tORA\tA")
        if stmt.elseifs or stmt.else_body:
            self.emit(f"\tJZ\t{else_label}")
        else:
            self.emit(f"\tJZ\t{end_label}")

        # Then body
        for s in stmt.then_body:
            self.gen_stmt(s)
        if stmt.elseifs or stmt.else_body:
            self.emit(f"\tJMP\t{end_label}")

        # Elseifs
        for i, (cond, body) in enumerate(stmt.elseifs):
            self.emit_label(else_label)
            next_label = self.new_label("ELIF") if i < len(stmt.elseifs) - 1 or stmt.else_body else end_label
            else_label = next_label

            self.gen_expr(cond, 'A')
            self.emit("\tORA\tA")
            self.emit(f"\tJZ\t{next_label}")

            for s in body:
                self.gen_stmt(s)
            self.emit(f"\tJMP\t{end_label}")

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
        self.gen_expr(stmt.condition, 'A')
        self.emit("\tORA\tA")
        self.emit(f"\tJZ\t{end_label}")

        for s in stmt.body:
            self.gen_stmt(s)

        self.emit(f"\tJMP\t{loop_label}")
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
        self.emit(f"\tJMP\t{loop_label}")
        self.emit_label(end_label)

        self.break_labels.pop()
        self.continue_labels.pop()

    def gen_case(self, stmt: ast.CaseStmt) -> None:
        """Generate case statement."""
        end_label = self.new_label("ENDC")

        # Evaluate expression
        self.gen_expr(stmt.expr, 'HL')
        self.emit("\tPUSH\tH")

        for values, body in stmt.whens:
            next_when = self.new_label("WHEN")

            for val in values:
                self.emit("\tPOP\tH")
                self.emit("\tPUSH\tH")
                self.gen_expr(val, 'HL')
                self.emit("\tXCHG")
                self.emit("\tPOP\tH")
                self.emit("\tPUSH\tH")

                # Compare
                self.emit("\tMOV\tA,H")
                self.emit("\tCMP\tD")
                self.emit(f"\tJNZ\t{next_when}")
                self.emit("\tMOV\tA,L")
                self.emit("\tCMP\tE")
                self.emit(f"\tJNZ\t{next_when}")

            # Match found
            self.emit("\tPOP\tH")  # Clean stack
            for s in body:
                self.gen_stmt(s)
            self.emit(f"\tJMP\t{end_label}")

            self.emit_label(next_when)

        # Else clause
        self.emit("\tPOP\tH")  # Clean stack
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
        if decl.extern_name:
            self.emit(f"\tPUBLIC\t{decl.extern_name}")
            self.emit_label(decl.extern_name)
        self.emit_label(self.mangle_sub_name(decl.name))

        # Allocate local variables (parameters are passed on stack)
        for param_name, param_type in params:
            self.allocate_var(param_name, param_type)

        for ret_name, ret_type in returns:
            self.allocate_var(ret_name, ret_type)

        # Copy parameters from stack to variables
        # Args are pushed in reverse order at call site, so first param is at lowest offset
        offset = 2  # Return address
        for param_name, param_type in params:
            var = self.variables.get(param_name)
            if var:
                mangled = self.mangle_name(param_name)
                self.emit(f"\tLXI\tH,{offset}")
                self.emit("\tDAD\tSP")
                if var.size == 1:
                    self.emit("\tMOV\tA,M")
                    self.emit(f"\tSTA\t{mangled}")
                else:
                    self.emit("\tMOV\tE,M")
                    self.emit("\tINX\tH")
                    self.emit("\tMOV\tD,M")
                    self.emit("\tXCHG")
                    self.emit(f"\tSHLD\t{mangled}")
                offset += 2

        # Body
        for stmt in decl.body:
            self.gen_stmt(stmt)

        # Return (with first return value in HL)
        if returns:
            ret_var = returns[0][0]  # (name, type) tuple
            var = self.variables.get(ret_var)
            mangled_ret = self.mangle_name(ret_var)
            if var and var.size == 1:
                self.emit(f"\tLDA\t{mangled_ret}")
                self.emit("\tMOV\tL,A")
                self.emit("\tMVI\tH,0")
            else:
                self.emit(f"\tLHLD\t{mangled_ret}")

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
        self.emit("; Generated by ucow")
        self.emit("")
        self.emit("\t.8080")
        self.emit("")

        # Use CSEG for code segment (will be linked at 0100H)
        self.emit("\tCSEG")
        self.emit("")

        # Jump to main
        self.emit("\tJMP\t_main")
        self.emit("")

        # Include runtime
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

        # Main code
        self.emit("")
        self.emit("; Main program")
        self.emit_label("_main")

        for stmt in program.statements:
            self.gen_stmt(stmt)

        # Exit
        self.emit("\tJMP\t0")  # Warm boot
        self.emit("")

        # Data segment
        self.emit("; Data segment")
        self.emit_label("_data")

        # Variables
        for name, var in self.variables.items():
            self.emit(f"{self.mangle_name(name)}:\tDS\t{var.size}")

        # String literals
        for value, label in self.string_literals.items():
            # Output as individual bytes to handle control characters
            bytes_str = ','.join(str(ord(c)) for c in value)
            if bytes_str:
                self.emit(f"{label}:\tDB\t{bytes_str},0")
            else:
                self.emit(f"{label}:\tDB\t0")

        self.emit("")
        self.emit("\tEND")

        return '\n'.join(self.output)


def generate(program: ast.Program, checker: TypeChecker) -> str:
    """Generate assembly from AST."""
    gen = CodeGenerator(checker)
    return gen.gen_program(program)
