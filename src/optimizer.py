"""Multi-pass AST optimizer for Cowgol.

Runs optimization passes on the AST until no more changes are made.
This happens BEFORE code generation, operating on the semantic level.

Implements optimizations from MBASIC project:
- Constant Folding
- Strength Reduction
- Algebraic Simplification
- Dead Code Elimination (basic)
- Copy Propagation (basic)
"""

from typing import Optional, List, Tuple, Dict, Any, Set
from . import ast
from .types import TypeChecker


class ASTOptimizer:
    """Multi-pass optimizer that transforms the AST."""

    def __init__(self, type_checker: TypeChecker):
        self.tc = type_checker
        self.changes_made = False
        self.pass_count = 0
        self.total_changes = 0
        self.debug = False

        # For copy propagation: maps variable name -> expression it equals
        # e.g., after "y = x", copies['y'] = Identifier('x')
        self.copies: Dict[str, ast.Expression] = {}

        # For constant propagation within a block
        # Maps variable name -> constant value
        self.var_constants: Dict[str, int] = {}

        # Track which variables are modified (for invalidation)
        self.modified_vars: Set[str] = set()

        # For CSE: maps expression string -> variable holding the value
        # e.g., after "t = a + b", available_exprs['a + b'] = 't'
        self.available_exprs: Dict[str, str] = {}

        # Counter for generating temp variable names (for CSE)
        self.temp_counter: int = 0

        # For loop-invariant code motion: statements hoisted from loops
        self._hoisted_stmts: List[ast.Statement] = []

        # For dead store elimination: variables known to be read at outer scope
        # This prevents eliminating assignments in nested bodies that are read outside
        self._outer_read_vars: Set[str] = set()

    def optimize(self, program: ast.Program, max_passes: int = 10) -> int:
        """Run optimization passes until stable or max passes reached.

        Returns total number of changes made.
        """
        self.total_changes = 0

        for self.pass_count in range(1, max_passes + 1):
            self.changes_made = False

            # Optimize all subroutine bodies
            for decl in program.declarations:
                if isinstance(decl, ast.SubDecl) and decl.body:
                    # Reset tracking for each subroutine
                    self._reset_tracking()
                    # Collect return variable names - these are implicitly "read"
                    # by the return mechanism, so don't eliminate assignments to them
                    return_vars = set()
                    # Get from decl.returns if available
                    if decl.returns:
                        for ret in decl.returns:
                            return_vars.add(ret.name)
                    # Also check type checker for @impl subroutines
                    sub_info = self.tc.subroutines.get(decl.name)
                    if sub_info and sub_info.returns:
                        for ret_name, _ in sub_info.returns:
                            return_vars.add(ret_name)
                    decl.body = self._optimize_statements(decl.body, return_vars)

            # Optimize top-level statements
            self._reset_tracking()
            program.statements = self._optimize_statements(program.statements, set())

            if self.debug:
                print(f"  Pass {self.pass_count}: {self.changes_made} changes")

            if not self.changes_made:
                break

        return self.total_changes

    def _mark_changed(self):
        """Mark that a change was made this pass."""
        self.changes_made = True
        self.total_changes += 1

    def _reset_tracking(self):
        """Reset copy/constant tracking for a new scope."""
        self.copies.clear()
        self.var_constants.clear()
        self.modified_vars.clear()
        self.available_exprs.clear()

    def _invalidate_var(self, name: str):
        """Invalidate tracking for a variable that was modified."""
        self.modified_vars.add(name)
        # Remove from copies if it was a copy source
        self.copies.pop(name, None)
        # Remove any copies that reference this variable
        to_remove = [k for k, v in self.copies.items()
                     if isinstance(v, ast.Identifier) and v.name == name]
        for k in to_remove:
            del self.copies[k]
        # Remove from constants
        self.var_constants.pop(name, None)
        # Remove any available expressions that reference this variable
        to_remove = [k for k in self.available_exprs.keys() if name in k]
        for k in to_remove:
            del self.available_exprs[k]

    def _invalidate_all(self):
        """Invalidate all tracking (after control flow change)."""
        self.copies.clear()
        self.var_constants.clear()
        self.available_exprs.clear()

    def _get_target_var(self, expr: ast.Expression) -> Optional[str]:
        """Get the variable name being assigned to, if simple."""
        if isinstance(expr, ast.Identifier):
            return expr.name
        return None

    def _is_simple_var(self, expr: ast.Expression) -> bool:
        """Check if expression is a simple variable reference."""
        return isinstance(expr, ast.Identifier)

    def _get_vars_in_expr(self, expr: ast.Expression) -> Set[str]:
        """Get all variable names referenced in an expression."""
        result = set()
        self._collect_vars(expr, result)
        return result

    def _collect_vars(self, expr: ast.Expression, result: Set[str]):
        """Recursively collect variable names from an expression."""
        if expr is None:
            return
        if isinstance(expr, ast.Identifier):
            result.add(expr.name)
        elif isinstance(expr, ast.BinaryOp):
            self._collect_vars(expr.left, result)
            self._collect_vars(expr.right, result)
        elif isinstance(expr, ast.UnaryOp):
            self._collect_vars(expr.operand, result)
        elif isinstance(expr, ast.Comparison):
            self._collect_vars(expr.left, result)
            self._collect_vars(expr.right, result)
        elif isinstance(expr, ast.LogicalOp):
            self._collect_vars(expr.left, result)
            self._collect_vars(expr.right, result)
        elif isinstance(expr, ast.NotOp):
            self._collect_vars(expr.operand, result)
        elif isinstance(expr, ast.Cast):
            self._collect_vars(expr.expr, result)
        elif isinstance(expr, ast.ArrayAccess):
            self._collect_vars(expr.array, result)
            self._collect_vars(expr.index, result)
        elif isinstance(expr, ast.FieldAccess):
            self._collect_vars(expr.record, result)
        elif isinstance(expr, ast.Dereference):
            self._collect_vars(expr.pointer, result)
        elif isinstance(expr, ast.AddressOf):
            self._collect_vars(expr.operand, result)
        elif isinstance(expr, ast.Call):
            self._collect_vars(expr.target, result)
            for arg in expr.args:
                self._collect_vars(arg, result)

    def _expr_to_key(self, expr: ast.Expression) -> Optional[str]:
        """Convert an expression to a canonical string key for CSE.

        Returns None if the expression shouldn't be considered for CSE.
        """
        if isinstance(expr, ast.NumberLiteral):
            return f"#{expr.value}"
        elif isinstance(expr, ast.Identifier):
            return f"${expr.name}"
        elif isinstance(expr, ast.BinaryOp):
            left_key = self._expr_to_key(expr.left)
            right_key = self._expr_to_key(expr.right)
            if left_key and right_key:
                # Normalize commutative operations
                if expr.op in ('+', '*', '&', '|', '^', '==', '!='):
                    if left_key > right_key:
                        left_key, right_key = right_key, left_key
                return f"({left_key}{expr.op}{right_key})"
        elif isinstance(expr, ast.UnaryOp):
            operand_key = self._expr_to_key(expr.operand)
            if operand_key:
                return f"({expr.op}{operand_key})"
        elif isinstance(expr, ast.Comparison):
            left_key = self._expr_to_key(expr.left)
            right_key = self._expr_to_key(expr.right)
            if left_key and right_key:
                return f"({left_key}{expr.op}{right_key})"
        elif isinstance(expr, ast.ArrayAccess):
            array_key = self._expr_to_key(expr.array)
            index_key = self._expr_to_key(expr.index)
            if array_key and index_key:
                return f"{array_key}[{index_key}]"
        elif isinstance(expr, ast.FieldAccess):
            record_key = self._expr_to_key(expr.record)
            if record_key:
                return f"{record_key}.{expr.field}"
        # Don't CSE calls, dereferences, address-of (side effects or aliases)
        return None

    def _is_cse_candidate(self, expr: ast.Expression) -> bool:
        """Check if an expression is a good candidate for CSE."""
        # Only consider non-trivial expressions
        if isinstance(expr, (ast.NumberLiteral, ast.Identifier, ast.StringLiteral)):
            return False
        # Don't CSE calls (side effects)
        if isinstance(expr, ast.Call):
            return False
        # Don't CSE dereferences (aliasing issues)
        if isinstance(expr, ast.Dereference):
            return False
        # Don't CSE address-of
        if isinstance(expr, ast.AddressOf):
            return False
        return True

    def _get_modified_vars_in_stmts(self, stmts: List[ast.Statement]) -> Set[str]:
        """Get all variables modified in a list of statements."""
        modified = set()
        for stmt in stmts:
            self._collect_modified_vars(stmt, modified)
        return modified

    def _collect_modified_vars(self, stmt: ast.Statement, result: Set[str]):
        """Collect all variables modified by a statement."""
        if isinstance(stmt, ast.Assignment):
            var = self._get_target_var(stmt.target)
            if var:
                result.add(var)
        elif isinstance(stmt, ast.MultiAssignment):
            for target in stmt.targets:
                var = self._get_target_var(target)
                if var:
                    result.add(var)
        elif isinstance(stmt, ast.VarDecl):
            result.add(stmt.name)
        elif isinstance(stmt, ast.IfStmt):
            for s in stmt.then_body:
                self._collect_modified_vars(s, result)
            for _, body in stmt.elseifs:
                for s in body:
                    self._collect_modified_vars(s, result)
            if stmt.else_body:
                for s in stmt.else_body:
                    self._collect_modified_vars(s, result)
        elif isinstance(stmt, ast.WhileStmt):
            for s in stmt.body:
                self._collect_modified_vars(s, result)
        elif isinstance(stmt, ast.LoopStmt):
            for s in stmt.body:
                self._collect_modified_vars(s, result)
        elif isinstance(stmt, ast.CaseStmt):
            for _, body in stmt.whens:
                for s in body:
                    self._collect_modified_vars(s, result)
            if stmt.else_body:
                for s in stmt.else_body:
                    self._collect_modified_vars(s, result)

    def _is_loop_invariant(self, expr: ast.Expression, modified_vars: Set[str]) -> bool:
        """Check if an expression is loop-invariant (doesn't depend on modified vars)."""
        if expr is None:
            return True
        if isinstance(expr, ast.NumberLiteral):
            return True
        if isinstance(expr, ast.StringLiteral):
            return True
        if isinstance(expr, ast.Identifier):
            return expr.name not in modified_vars
        if isinstance(expr, ast.BinaryOp):
            return (self._is_loop_invariant(expr.left, modified_vars) and
                    self._is_loop_invariant(expr.right, modified_vars))
        if isinstance(expr, ast.UnaryOp):
            return self._is_loop_invariant(expr.operand, modified_vars)
        if isinstance(expr, ast.Comparison):
            return (self._is_loop_invariant(expr.left, modified_vars) and
                    self._is_loop_invariant(expr.right, modified_vars))
        if isinstance(expr, ast.LogicalOp):
            return (self._is_loop_invariant(expr.left, modified_vars) and
                    self._is_loop_invariant(expr.right, modified_vars))
        if isinstance(expr, ast.NotOp):
            return self._is_loop_invariant(expr.operand, modified_vars)
        if isinstance(expr, ast.Cast):
            return self._is_loop_invariant(expr.expr, modified_vars)
        if isinstance(expr, ast.ArrayAccess):
            return (self._is_loop_invariant(expr.array, modified_vars) and
                    self._is_loop_invariant(expr.index, modified_vars))
        if isinstance(expr, ast.FieldAccess):
            return self._is_loop_invariant(expr.record, modified_vars)
        # Calls, dereferences, address-of are not loop-invariant (side effects/aliasing)
        return False

    def _hoist_invariants(self, stmts: List[ast.Statement],
                         modified_vars: Set[str]) -> Tuple[List[ast.Statement], List[ast.Statement]]:
        """Extract loop-invariant assignments from a statement list.

        Returns (hoisted_stmts, remaining_stmts).
        Only hoists assignments where:
        1. The target is assigned only once in the loop
        2. The value is loop-invariant
        3. The assignment is not in a conditional branch
        """
        hoisted = []
        remaining = []

        # Count how many times each variable is assigned
        assign_counts: Dict[str, int] = {}
        for stmt in stmts:
            if isinstance(stmt, ast.Assignment):
                var = self._get_target_var(stmt.target)
                if var:
                    assign_counts[var] = assign_counts.get(var, 0) + 1

        for stmt in stmts:
            can_hoist = False

            if isinstance(stmt, ast.Assignment):
                var = self._get_target_var(stmt.target)
                if var and assign_counts.get(var, 0) == 1:
                    # Only assigned once, check if value is invariant
                    if self._is_loop_invariant(stmt.value, modified_vars):
                        # Can hoist this assignment
                        can_hoist = True

            if can_hoist:
                hoisted.append(stmt)
            else:
                remaining.append(stmt)

        return hoisted, remaining

    def _try_loop_reversal(self, preceding_stmts: List[ast.Statement], while_stmt: ast.WhileStmt) -> Tuple[int, Optional[ast.Assignment], Optional[ast.WhileStmt]]:
        """Try to reverse a count-up loop to count down for zero flag optimization.

        Transforms:
            i := 0;
            while i < N loop
                ...body... (where i is only used for counting, not indexing)
                i := i + 1;
            end loop;

        Into:
            i := N;
            while i != 0 loop
                i := i - 1;
                ...body...
            end loop;

        Returns (init_index, new_init, new_while) if successful, (-1, None, None) otherwise.
        """
        # First, determine loop variable from condition: var < N or var <= N
        cond = while_stmt.condition
        if not isinstance(cond, ast.Comparison):
            return -1, None, None
        if cond.op not in ('<', '<='):
            return -1, None, None
        # The left side may be just Identifier or Cast(Identifier) due to type widening
        cond_left = cond.left
        if isinstance(cond_left, ast.Cast):
            cond_left = cond_left.expr
        if not isinstance(cond_left, ast.Identifier):
            return -1, None, None
        loop_var = cond_left.name

        limit_val = self._eval_const(cond.right)
        if limit_val is None or limit_val <= 0:
            return -1, None, None

        # Adjust limit for <= vs <
        if cond.op == '<=':
            iteration_count = limit_val + 1
        else:
            iteration_count = limit_val

        # Check the variable is a byte type (uint8 or int8)
        if loop_var in self.tc.global_scope.variables:
            var_info = self.tc.global_scope.variables[loop_var]
        elif hasattr(self.tc, 'current_scope') and self.tc.current_scope and loop_var in self.tc.current_scope.variables:
            var_info = self.tc.current_scope.variables[loop_var]
        else:
            return -1, None, None

        # Extract the actual type from VariableInfo if needed
        var_type = getattr(var_info, 'type', var_info)

        # Check if it's a byte type
        from .types import IntType
        if not isinstance(var_type, IntType) or var_type.size != 1:
            return -1, None, None

        # Find initialization: loop_var := 0 in preceding statements
        # Search backward to find the most recent assignment to loop_var
        init_idx = -1
        for i in range(len(preceding_stmts) - 1, -1, -1):
            stmt = preceding_stmts[i]
            if isinstance(stmt, ast.Assignment) and isinstance(stmt.target, ast.Identifier):
                if stmt.target.name == loop_var:
                    init_val = self._eval_const(stmt.value)
                    if init_val == 0:
                        init_idx = i
                    break  # Stop at first assignment to this var

        if init_idx < 0:
            return -1, None, None

        # Check loop body ends with: var := var + 1
        body = while_stmt.body
        if not body:
            return -1, None, None

        last_stmt = body[-1]
        if not isinstance(last_stmt, ast.Assignment):
            return -1, None, None
        if not isinstance(last_stmt.target, ast.Identifier) or last_stmt.target.name != loop_var:
            return -1, None, None

        # Check it's var := var + 1
        inc_val = self._get_induction_increment(loop_var, last_stmt.value)
        if inc_val != 1:
            return -1, None, None

        # Check loop variable is not used elsewhere in the body (only for counting)
        # This is the key safety check - if var is used for indexing, we can't reverse
        for stmt in body[:-1]:  # Exclude the increment statement
            if self._var_used_in_stmt(loop_var, stmt):
                return -1, None, None

        # Success! Create reversed loop
        loc = while_stmt.location

        # New init: var := iteration_count
        new_init = ast.Assignment(
            location=loc,
            target=ast.Identifier(location=loc, name=loop_var, resolved=None, resolved_type=var_type),
            value=ast.NumberLiteral(location=loc, value=iteration_count)
        )

        # New condition: var != 0
        new_cond = ast.Comparison(
            location=loc,
            left=ast.Identifier(location=loc, name=loop_var, resolved=None, resolved_type=var_type),
            op='!=',
            right=ast.NumberLiteral(location=loc, value=0)
        )

        # New decrement: var := var - 1
        new_decrement = ast.Assignment(
            location=loc,
            target=ast.Identifier(location=loc, name=loop_var, resolved=None, resolved_type=var_type),
            value=ast.BinaryOp(
                location=loc,
                left=ast.Identifier(location=loc, name=loop_var, resolved=None, resolved_type=var_type),
                op='-',
                right=ast.NumberLiteral(location=loc, value=1)
            )
        )

        # New body: decrement first, then rest of original body (without the increment)
        new_body = [new_decrement] + list(body[:-1])

        new_while = ast.WhileStmt(
            location=loc,
            condition=new_cond,
            body=new_body
        )

        if self.debug:
            print(f"  Loop reversal: {loop_var} counting 0 to {limit_val} -> {iteration_count} down to 0")

        return init_idx, new_init, new_while

    def _var_used_in_stmt(self, var_name: str, stmt: ast.Statement) -> bool:
        """Check if a variable is used (read) in a statement."""
        if isinstance(stmt, ast.Assignment):
            # Check if var is used in the value or as an index in target
            if self._var_in_expr(var_name, stmt.value):
                return True
            # Check if var is used as index in array/field access target
            if isinstance(stmt.target, ast.ArrayAccess):
                if self._var_in_expr(var_name, stmt.target.index):
                    return True
                if self._var_in_expr(var_name, stmt.target.array):
                    return True
            elif isinstance(stmt.target, ast.FieldAccess):
                if self._var_in_expr(var_name, stmt.target.record):
                    return True
            elif isinstance(stmt.target, ast.Dereference):
                if self._var_in_expr(var_name, stmt.target.pointer):
                    return True
        elif isinstance(stmt, ast.ExprStmt):
            return self._var_in_expr(var_name, stmt.expr)
        elif isinstance(stmt, ast.IfStmt):
            if self._var_in_expr(var_name, stmt.condition):
                return True
            for s in stmt.then_body:
                if self._var_used_in_stmt(var_name, s):
                    return True
            for cond, body in stmt.elseifs:
                if self._var_in_expr(var_name, cond):
                    return True
                for s in body:
                    if self._var_used_in_stmt(var_name, s):
                        return True
            if stmt.else_body:
                for s in stmt.else_body:
                    if self._var_used_in_stmt(var_name, s):
                        return True
        elif isinstance(stmt, ast.WhileStmt):
            if self._var_in_expr(var_name, stmt.condition):
                return True
            for s in stmt.body:
                if self._var_used_in_stmt(var_name, s):
                    return True
        elif isinstance(stmt, ast.LoopStmt):
            for s in stmt.body:
                if self._var_used_in_stmt(var_name, s):
                    return True
        elif isinstance(stmt, ast.ReturnStmt):
            if hasattr(stmt, 'values') and stmt.values:
                for v in stmt.values:
                    if self._var_in_expr(var_name, v):
                        return True
        return False

    def _var_in_expr(self, var_name: str, expr: ast.Expression) -> bool:
        """Check if a variable appears in an expression."""
        if expr is None:
            return False
        if isinstance(expr, ast.Identifier):
            return expr.name == var_name
        if isinstance(expr, ast.BinaryOp):
            return self._var_in_expr(var_name, expr.left) or self._var_in_expr(var_name, expr.right)
        if isinstance(expr, ast.UnaryOp):
            return self._var_in_expr(var_name, expr.operand)
        if isinstance(expr, ast.Comparison):
            return self._var_in_expr(var_name, expr.left) or self._var_in_expr(var_name, expr.right)
        if isinstance(expr, ast.LogicalOp):
            return self._var_in_expr(var_name, expr.left) or self._var_in_expr(var_name, expr.right)
        if isinstance(expr, ast.NotOp):
            return self._var_in_expr(var_name, expr.operand)
        if isinstance(expr, ast.Cast):
            return self._var_in_expr(var_name, expr.expr)
        if isinstance(expr, ast.ArrayAccess):
            return self._var_in_expr(var_name, expr.array) or self._var_in_expr(var_name, expr.index)
        if isinstance(expr, ast.FieldAccess):
            return self._var_in_expr(var_name, expr.record)
        if isinstance(expr, ast.Dereference):
            return self._var_in_expr(var_name, expr.pointer)
        if isinstance(expr, ast.AddressOf):
            return self._var_in_expr(var_name, expr.operand)
        if isinstance(expr, ast.Call):
            for arg in expr.args:
                if self._var_in_expr(var_name, arg):
                    return True
        return False

    def _find_induction_vars(self, stmts: List[ast.Statement]) -> Dict[str, int]:
        """Find basic induction variables in loop body.

        Returns dict mapping variable name -> increment amount.
        A basic induction variable is one that is:
        1. Assigned exactly once in the loop
        2. The assignment is of the form: i := i + constant or i := i - constant
        """
        induction_vars = {}

        # Count assignments per variable
        assign_counts: Dict[str, int] = {}
        for stmt in stmts:
            if isinstance(stmt, ast.Assignment):
                var = self._get_target_var(stmt.target)
                if var:
                    assign_counts[var] = assign_counts.get(var, 0) + 1

        # Find variables assigned exactly once with i := i +/- const pattern
        for stmt in stmts:
            if isinstance(stmt, ast.Assignment):
                var = self._get_target_var(stmt.target)
                if var and assign_counts.get(var, 0) == 1:
                    increment = self._get_induction_increment(var, stmt.value)
                    if increment is not None:
                        induction_vars[var] = increment

        return induction_vars

    def _get_induction_increment(self, var_name: str, expr: ast.Expression) -> Optional[int]:
        """Check if expr is of form var_name + const or var_name - const.

        Returns the increment (positive or negative) or None if not an induction pattern.
        """
        if not isinstance(expr, ast.BinaryOp):
            return None

        if expr.op == '+':
            # Check for i + const or const + i
            if isinstance(expr.left, ast.Identifier) and expr.left.name == var_name:
                const = self._eval_const(expr.right)
                if const is not None:
                    return const
            if isinstance(expr.right, ast.Identifier) and expr.right.name == var_name:
                const = self._eval_const(expr.left)
                if const is not None:
                    return const

        elif expr.op == '-':
            # Check for i - const
            if isinstance(expr.left, ast.Identifier) and expr.left.name == var_name:
                const = self._eval_const(expr.right)
                if const is not None:
                    return -const

        return None

    def _find_derived_induction_exprs(self, stmts: List[ast.Statement],
                                       induction_vars: Dict[str, int]) -> List[Tuple[str, str, int, int]]:
        """Find expressions of form: v := iv * const + const2

        Returns list of (target_var, induction_var, multiplier, offset).
        These can be strength-reduced to: v := v + (multiplier * increment)
        """
        derived = []

        for stmt in stmts:
            if isinstance(stmt, ast.Assignment):
                target = self._get_target_var(stmt.target)
                if target:
                    result = self._analyze_derived_expr(stmt.value, induction_vars)
                    if result:
                        iv_name, multiplier, offset = result
                        # Only optimize if multiplier > 1 (otherwise not worth it)
                        if multiplier > 1:
                            derived.append((target, iv_name, multiplier, offset))

        return derived

    def _analyze_derived_expr(self, expr: ast.Expression,
                              induction_vars: Dict[str, int]) -> Optional[Tuple[str, int, int]]:
        """Analyze expression to see if it's iv * const or iv * const + const2.

        Returns (iv_name, multiplier, offset) or None.
        """
        # Pattern: iv * const
        if isinstance(expr, ast.BinaryOp) and expr.op == '*':
            if isinstance(expr.left, ast.Identifier) and expr.left.name in induction_vars:
                const = self._eval_const(expr.right)
                if const is not None and const > 1:
                    return (expr.left.name, const, 0)
            if isinstance(expr.right, ast.Identifier) and expr.right.name in induction_vars:
                const = self._eval_const(expr.left)
                if const is not None and const > 1:
                    return (expr.right.name, const, 0)

        # Pattern: (iv * const) + const2 or const2 + (iv * const)
        if isinstance(expr, ast.BinaryOp) and expr.op == '+':
            # Try left side as iv * const
            left_result = self._analyze_derived_expr(expr.left, induction_vars)
            if left_result:
                iv_name, mult, _ = left_result
                offset = self._eval_const(expr.right)
                if offset is not None:
                    return (iv_name, mult, offset)

            # Try right side as iv * const
            right_result = self._analyze_derived_expr(expr.right, induction_vars)
            if right_result:
                iv_name, mult, _ = right_result
                offset = self._eval_const(expr.left)
                if offset is not None:
                    return (iv_name, mult, offset)

        # Pattern: iv << const (same as iv * 2^const)
        if isinstance(expr, ast.BinaryOp) and expr.op == '<<':
            if isinstance(expr.left, ast.Identifier) and expr.left.name in induction_vars:
                shift = self._eval_const(expr.right)
                if shift is not None and shift > 0:
                    return (expr.left.name, 1 << shift, 0)

        return None

    def _apply_induction_var_optimization(self, stmt: ast.WhileStmt) -> Tuple[List[ast.Statement], ast.WhileStmt]:
        """Apply induction variable strength reduction to a while loop.

        Transforms patterns like:
            while i < n loop
                x := i * 5   // expensive multiply
                ...
                i := i + 1
            end loop

        Into:
            _iv_x := 0       // initial value = initial_i * 5
            while i < n loop
                x := _iv_x   // just copy
                ...
                i := i + 1
                _iv_x := _iv_x + 5  // add step instead of multiply
            end loop

        Note: We can only do this if we can compute the initial value.
        For simplicity, we only handle the case where the basic IV starts at 0
        or where we can find the initialization just before the loop.

        Returns (pre_loop_stmts, modified_while_stmt).
        """
        # Find basic induction variables
        induction_vars = self._find_induction_vars(stmt.body)
        if not induction_vars:
            return [], stmt

        # Find derived expressions (iv * const patterns)
        derived = self._find_derived_induction_exprs(stmt.body, induction_vars)
        if not derived:
            return [], stmt

        # For simplicity, we'll use a different approach:
        # Transform the derived expression assignment into an increment
        #
        # If we have:
        #   target := iv * mult + offset
        #   ...
        #   iv := iv + inc
        #
        # We transform to:
        #   target := iv * mult + offset  (keep first computation)
        #   ...
        #   iv := iv + inc
        #   target := target + (mult * inc)  (add increment)
        #
        # But this changes semantics if target is read before assigned.
        # So we need to be careful. Let's do a simpler transformation:
        # Just create an auxiliary variable.

        pre_loop: List[ast.Statement] = []
        new_body: List[ast.Statement] = []

        # Build replacement map: for each derived var, track aux var and step
        replacements: Dict[str, Tuple[str, int, int]] = {}  # target -> (aux_name, step, offset)

        for target, iv_name, multiplier, offset in derived:
            iv_increment = induction_vars[iv_name]
            step = multiplier * iv_increment
            aux_name = f"_iv_{target}"
            replacements[target] = (aux_name, step, offset)

            # Create initialization: aux := offset (assuming iv starts at 0)
            # If iv doesn't start at 0, this is wrong, but we can't know without
            # data flow analysis across basic blocks
            #
            # Actually, for a safer approach, we initialize aux from the first
            # computation of the derived expression. This requires that we
            # keep the first assignment and add increments after the IV update.
            #
            # For now, let's just emit: aux := 0 * mult + offset = offset
            # This is only correct if iv starts at 0.
            init_val = offset  # Assumes iv starts at 0
            pre_loop.append(ast.Assignment(
                location=stmt.location,
                target=ast.Identifier(
                    location=stmt.location,
                    name=aux_name,
                    resolved=None,
                    resolved_type=None
                ),
                value=ast.NumberLiteral(
                    location=stmt.location,
                    value=init_val
                )
            ))

        # Transform loop body
        iv_assignments: List[Tuple[str, ast.Assignment]] = []  # (iv_name, assignment)

        for body_stmt in stmt.body:
            # Check if this is a derived expression assignment we want to transform
            if isinstance(body_stmt, ast.Assignment):
                target_var = self._get_target_var(body_stmt.target)

                # Check if this assigns to a derived variable
                if target_var and target_var in replacements:
                    aux_name, step, offset = replacements[target_var]
                    # Replace: target := iv * mult + offset
                    # With:    target := aux
                    new_body.append(ast.Assignment(
                        location=body_stmt.location,
                        target=body_stmt.target,
                        value=ast.Identifier(
                            location=body_stmt.location,
                            name=aux_name,
                            resolved=None,
                            resolved_type=body_stmt.value.resolved_type
                        )
                    ))
                    self._mark_changed()
                    continue

                # Check if this is an IV assignment
                if target_var and target_var in induction_vars:
                    iv_assignments.append((target_var, body_stmt))

            new_body.append(body_stmt)

        # After IV assignments, add aux increments
        # We need to find where the IV is incremented and add aux increments there
        if iv_assignments and replacements:
            final_body: List[ast.Statement] = []

            for body_stmt in new_body:
                final_body.append(body_stmt)

                # Check if this is an IV increment
                if isinstance(body_stmt, ast.Assignment):
                    target_var = self._get_target_var(body_stmt.target)
                    if target_var and target_var in induction_vars:
                        # Add increments for all aux vars derived from this IV
                        for orig_target, (aux_name, step, offset) in replacements.items():
                            # Check which IV this derived var depends on
                            for dtarget, div_name, dmult, doffset in derived:
                                if dtarget == orig_target and div_name == target_var:
                                    # aux := aux + step
                                    final_body.append(ast.Assignment(
                                        location=body_stmt.location,
                                        target=ast.Identifier(
                                            location=body_stmt.location,
                                            name=aux_name,
                                            resolved=None,
                                            resolved_type=None
                                        ),
                                        value=ast.BinaryOp(
                                            location=body_stmt.location,
                                            op='+',
                                            left=ast.Identifier(
                                                location=body_stmt.location,
                                                name=aux_name,
                                                resolved=None,
                                                resolved_type=None
                                            ),
                                            right=ast.NumberLiteral(
                                                location=body_stmt.location,
                                                value=step
                                            ),
                                            resolved_type=None
                                        )
                                    ))
                                    break

            new_body = final_body

        # Create modified while statement
        new_while = ast.WhileStmt(
            location=stmt.location,
            condition=stmt.condition,
            body=new_body
        )

        return pre_loop, new_while

    def _get_read_vars_in_stmts(self, stmts: List[ast.Statement]) -> Set[str]:
        """Get all variables that are read in a list of statements."""
        read_vars = set()
        for stmt in stmts:
            self._collect_read_vars(stmt, read_vars)
        return read_vars

    def _collect_read_vars(self, stmt: ast.Statement, result: Set[str]):
        """Collect all variables read by a statement."""
        if isinstance(stmt, ast.Assignment):
            # The value is read, but not the target (unless it's an array/field access)
            self._collect_vars(stmt.value, result)
            # For array/field targets, the base is read
            if isinstance(stmt.target, ast.ArrayAccess):
                self._collect_vars(stmt.target.index, result)
            elif isinstance(stmt.target, ast.FieldAccess):
                self._collect_vars(stmt.target.record, result)
        elif isinstance(stmt, ast.MultiAssignment):
            self._collect_vars(stmt.value, result)
        elif isinstance(stmt, ast.VarDecl):
            if stmt.init:
                self._collect_vars(stmt.init, result)
        elif isinstance(stmt, ast.IfStmt):
            self._collect_vars(stmt.condition, result)
            for s in stmt.then_body:
                self._collect_read_vars(s, result)
            for cond, body in stmt.elseifs:
                self._collect_vars(cond, result)
                for s in body:
                    self._collect_read_vars(s, result)
            if stmt.else_body:
                for s in stmt.else_body:
                    self._collect_read_vars(s, result)
        elif isinstance(stmt, ast.WhileStmt):
            self._collect_vars(stmt.condition, result)
            for s in stmt.body:
                self._collect_read_vars(s, result)
        elif isinstance(stmt, ast.LoopStmt):
            for s in stmt.body:
                self._collect_read_vars(s, result)
        elif isinstance(stmt, ast.CaseStmt):
            self._collect_vars(stmt.expr, result)
            for vals, body in stmt.whens:
                for v in vals:
                    self._collect_vars(v, result)
                for s in body:
                    self._collect_read_vars(s, result)
            if stmt.else_body:
                for s in stmt.else_body:
                    self._collect_read_vars(s, result)
        elif isinstance(stmt, ast.ExprStmt):
            self._collect_vars(stmt.expr, result)
        elif isinstance(stmt, ast.ReturnStmt):
            if hasattr(stmt, 'value') and stmt.value:
                self._collect_vars(stmt.value, result)

    def _has_side_effects(self, expr: ast.Expression) -> bool:
        """Check if an expression has side effects (and thus can't be eliminated)."""
        if expr is None:
            return False
        if isinstance(expr, (ast.NumberLiteral, ast.StringLiteral, ast.Identifier)):
            return False
        if isinstance(expr, ast.BinaryOp):
            return self._has_side_effects(expr.left) or self._has_side_effects(expr.right)
        if isinstance(expr, ast.UnaryOp):
            return self._has_side_effects(expr.operand)
        if isinstance(expr, ast.Comparison):
            return self._has_side_effects(expr.left) or self._has_side_effects(expr.right)
        if isinstance(expr, ast.LogicalOp):
            return self._has_side_effects(expr.left) or self._has_side_effects(expr.right)
        if isinstance(expr, ast.NotOp):
            return self._has_side_effects(expr.operand)
        if isinstance(expr, ast.Cast):
            return self._has_side_effects(expr.expr)
        if isinstance(expr, ast.ArrayAccess):
            return self._has_side_effects(expr.array) or self._has_side_effects(expr.index)
        if isinstance(expr, ast.FieldAccess):
            return self._has_side_effects(expr.record)
        # Calls always have potential side effects
        if isinstance(expr, ast.Call):
            return True
        # Dereferences might have side effects (volatile)
        if isinstance(expr, ast.Dereference):
            return True
        # Address-of itself doesn't have side effects
        if isinstance(expr, ast.AddressOf):
            return self._has_side_effects(expr.operand)
        # Assume anything else might have side effects
        return True

    # ========================================================================
    # Statement optimization
    # ========================================================================

    def _optimize_statements(self, stmts: List[ast.Statement],
                              return_vars: Set[str] = None) -> List[ast.Statement]:
        """Optimize a list of statements.

        Args:
            stmts: List of statements to optimize
            return_vars: Set of variable names that are return values (implicitly read)
        """
        if return_vars is None:
            return_vars = set()
        result = []
        last_assignments: Dict[str, ast.Expression] = {}  # var -> last assigned value

        # Dead variable elimination: find all variables that are read
        # Include return_vars as they are implicitly read by the return mechanism
        # Include _outer_read_vars to prevent eliminating assignments used in outer scope
        read_vars = self._get_read_vars_in_stmts(stmts) | return_vars | self._outer_read_vars

        # Save and update outer_read_vars for nested bodies
        saved_outer_read_vars = self._outer_read_vars
        self._outer_read_vars = read_vars

        # Track if we've seen an unconditional control flow statement
        unreachable = False

        for stmt in stmts:
            # Unreachable code elimination: skip statements after return/break/continue
            if unreachable:
                self._mark_changed()
                continue
            # Clear hoisted statements before processing each statement
            self._hoisted_stmts = []

            opt_stmt = self._optimize_stmt(stmt)
            if opt_stmt is None:
                continue

            # Insert any hoisted statements before the loop
            if self._hoisted_stmts:
                for hoisted in self._hoisted_stmts:
                    result.append(hoisted)
                self._hoisted_stmts = []

            # Dead store/variable elimination: remove redundant or useless assignments
            if isinstance(opt_stmt, ast.Assignment):
                target_var = self._get_target_var(opt_stmt.target)
                if target_var:
                    # Dead variable: assigned but never read
                    # Only eliminate if the RHS has no side effects
                    # IMPORTANT: Don't eliminate assignments to global variables,
                    # as they may be read in other subroutines
                    is_global = (self.tc.global_scope and
                                target_var in self.tc.global_scope.variables)
                    if target_var not in read_vars and not is_global:
                        if not self._has_side_effects(opt_stmt.value):
                            self._mark_changed()
                            continue

                    # Check if we're assigning the same value again
                    if target_var in last_assignments:
                        last_val = last_assignments[target_var]
                        curr_val = opt_stmt.value
                        if self._exprs_equal(last_val, curr_val):
                            # Same value assigned again - skip this assignment
                            self._mark_changed()
                            continue

                    # Track this assignment
                    last_assignments[target_var] = opt_stmt.value

            # Any other statement invalidates our tracking for variables it might read/write
            elif isinstance(opt_stmt, (ast.IfStmt, ast.WhileStmt, ast.LoopStmt,
                                       ast.CaseStmt, ast.ExprStmt)):
                last_assignments.clear()

            # Try loop reversal optimization for count-up byte loops
            if isinstance(opt_stmt, ast.WhileStmt) and len(result) > 0:
                init_idx, reversed_init, reversed_while = self._try_loop_reversal(result, opt_stmt)
                if reversed_init is not None:
                    # Replace init statement with reversed init
                    result[init_idx] = reversed_init
                    opt_stmt = reversed_while
                    self._mark_changed()
                    if self.debug:
                        print(f"  Loop reversal applied")

            result.append(opt_stmt)

            # Check if this statement makes subsequent code unreachable
            if isinstance(opt_stmt, (ast.ReturnStmt, ast.BreakStmt, ast.ContinueStmt)):
                unreachable = True

        # Restore outer read vars
        self._outer_read_vars = saved_outer_read_vars
        return result

    def _optimize_stmt(self, stmt: ast.Statement) -> Optional[ast.Statement]:
        """Optimize a single statement. Returns None to remove it."""

        if isinstance(stmt, ast.VarDecl):
            if stmt.init:
                stmt.init = self._optimize_expr(stmt.init)
            return stmt

        elif isinstance(stmt, ast.ConstDecl):
            # Constants are already constant - no optimization needed
            return stmt

        elif isinstance(stmt, ast.Assignment):
            stmt.value = self._optimize_expr(stmt.value)
            # Only optimize complex targets (array access, field access, dereference)
            # Don't optimize simple identifiers - they are targets, not sources
            if not isinstance(stmt.target, ast.Identifier):
                stmt.target = self._optimize_expr(stmt.target)

            # Track assignments for copy/constant propagation
            target_var = self._get_target_var(stmt.target)
            if target_var:
                # Invalidate old tracking for this variable
                self._invalidate_var(target_var)

                # Track constant assignments
                const_val = self._eval_const(stmt.value)
                if const_val is not None:
                    self.var_constants[target_var] = const_val

                # Track copy assignments (y = x)
                if isinstance(stmt.value, ast.Identifier):
                    self.copies[target_var] = stmt.value

                # Register this expression for CSE
                expr_key = self._expr_to_key(stmt.value)
                if expr_key and self._is_cse_candidate(stmt.value):
                    self.available_exprs[expr_key] = target_var

            return stmt

        elif isinstance(stmt, ast.MultiAssignment):
            stmt.value = self._optimize_expr(stmt.value)
            stmt.targets = [self._optimize_expr(t) for t in stmt.targets]
            return stmt

        elif isinstance(stmt, ast.IfStmt):
            stmt.condition = self._optimize_expr(stmt.condition)

            # Check for constant condition - dead code elimination
            const_cond = self._eval_const(stmt.condition)
            if const_cond is not None:
                self._mark_changed()
                if const_cond != 0:
                    # Always true - use then body, eliminate if
                    # NOTE: Don't use _optimize_statements here as it may incorrectly
                    # eliminate assignments to variables read outside this body.
                    # Just return the then_body statements directly; they'll be
                    # optimized by the parent _optimize_statements call.
                    if len(stmt.then_body) == 1:
                        return stmt.then_body[0]
                    elif len(stmt.then_body) == 0:
                        return None
                    # Multiple statements - can't return them directly, keep if
                    pass
                else:
                    # Always false - use else body if present
                    if stmt.else_body:
                        if len(stmt.else_body) == 1:
                            return stmt.else_body[0]
                        elif len(stmt.else_body) == 0:
                            return None
                    elif stmt.elseifs:
                        # Try first elseif
                        pass
                    else:
                        # No else - remove the if entirely
                        return None

            # Control flow diverges - save state, optimize branches, invalidate
            saved_copies = dict(self.copies)
            saved_consts = dict(self.var_constants)

            stmt.then_body = self._optimize_statements(stmt.then_body)
            stmt.elseifs = [(self._optimize_expr(c), self._optimize_statements(b))
                           for c, b in stmt.elseifs]
            if stmt.else_body:
                stmt.else_body = self._optimize_statements(stmt.else_body)

            # After if: can't know which branch was taken, invalidate all
            self._invalidate_all()
            return stmt

        elif isinstance(stmt, ast.WhileStmt):
            # IMPORTANT: Before optimizing the condition, invalidate any variables
            # that are modified in the loop body. Otherwise constant propagation
            # will incorrectly propagate initial values into the condition.
            modified_vars = self._get_modified_vars_in_stmts(stmt.body)
            for var in modified_vars:
                self._invalidate_var(var)

            stmt.condition = self._optimize_expr(stmt.condition)

            # Check for while(0) - dead code
            const_cond = self._eval_const(stmt.condition)
            if const_cond is not None and const_cond == 0:
                self._mark_changed()
                return None  # Remove the entire loop

            # Loop-invariant code motion
            # Also consider variables in the condition as "used before loop"
            hoisted, remaining = self._hoist_invariants(stmt.body, modified_vars)
            if hoisted:
                self._mark_changed()
                stmt.body = remaining
                # Return a list would be ideal, but we'll store hoisted for later
                self._hoisted_stmts = hoisted

            # Induction variable optimization (strength reduction for loops)
            # NOTE: Currently disabled - requires proper variable declaration support
            # iv_pre_loop, stmt = self._apply_induction_var_optimization(stmt)
            # if iv_pre_loop:
            #     self._hoisted_stmts.extend(iv_pre_loop)

            # Loop body may execute multiple times - invalidate all
            self._invalidate_all()
            stmt.body = self._optimize_statements(stmt.body)
            self._invalidate_all()
            return stmt

        elif isinstance(stmt, ast.LoopStmt):
            # Loop-invariant code motion
            modified_vars = self._get_modified_vars_in_stmts(stmt.body)
            hoisted, remaining = self._hoist_invariants(stmt.body, modified_vars)
            if hoisted:
                self._mark_changed()
                stmt.body = remaining
                self._hoisted_stmts = hoisted

            # Loop body may execute multiple times - invalidate all
            self._invalidate_all()
            stmt.body = self._optimize_statements(stmt.body)
            self._invalidate_all()
            return stmt

        elif isinstance(stmt, ast.CaseStmt):
            stmt.expr = self._optimize_expr(stmt.expr)
            # Note: _outer_read_vars propagates automatically via instance variable
            stmt.whens = [([self._optimize_expr(v) for v in vals],
                          self._optimize_statements(body))
                         for vals, body in stmt.whens]
            if stmt.else_body:
                stmt.else_body = self._optimize_statements(stmt.else_body)
            return stmt

        elif isinstance(stmt, ast.ExprStmt):
            stmt.expr = self._optimize_expr(stmt.expr)
            # Function calls may have side effects - invalidate tracking
            if isinstance(stmt.expr, ast.Call):
                self._invalidate_all()
            return stmt

        elif isinstance(stmt, ast.AsmStmt):
            # Inline assembly may modify anything - invalidate all
            self._invalidate_all()
            stmt.parts = [self._optimize_expr(p) if not isinstance(p, str) else p
                         for p in stmt.parts]
            return stmt

        elif isinstance(stmt, ast.NestedSubStmt):
            if stmt.sub.body:
                stmt.sub.body = self._optimize_statements(stmt.sub.body)
            return stmt

        elif isinstance(stmt, ast.SubDecl):
            if stmt.body:
                stmt.body = self._optimize_statements(stmt.body)
            return stmt

        else:
            # BreakStmt, ContinueStmt, ReturnStmt, etc.
            return stmt

    # ========================================================================
    # Expression optimization
    # ========================================================================

    def _optimize_expr(self, expr: ast.Expression) -> ast.Expression:
        """Optimize an expression, applying all optimizations."""
        if expr is None:
            return expr

        # Apply copy/constant propagation first
        expr = self._propagate(expr)

        # Then recursively optimize subexpressions
        expr = self._optimize_subexpressions(expr)

        # Apply optimizations in order
        expr = self._constant_fold(expr)
        expr = self._strength_reduce(expr)
        expr = self._algebraic_simplify(expr)
        expr = self._reassociate(expr)

        # Apply CSE - check if this expression was already computed
        expr = self._apply_cse(expr)

        return expr

    def _apply_cse(self, expr: ast.Expression) -> ast.Expression:
        """Apply Common Subexpression Elimination."""
        if not self._is_cse_candidate(expr):
            return expr

        expr_key = self._expr_to_key(expr)
        if expr_key and expr_key in self.available_exprs:
            var_name = self.available_exprs[expr_key]
            # Only replace if the variable hasn't been modified
            if var_name not in self.modified_vars:
                self._mark_changed()
                # Look up the variable's type info from the type checker
                var_info = self.tc.current_scope.lookup_var(var_name) if self.tc.current_scope else None
                return ast.Identifier(
                    location=expr.location,
                    name=var_name,
                    resolved=var_info,
                    resolved_type=var_info.type if var_info else expr.resolved_type
                )

        return expr

    def _propagate(self, expr: ast.Expression) -> ast.Expression:
        """Apply copy and constant propagation to an expression."""
        if isinstance(expr, ast.Identifier):
            # Check for constant propagation
            if expr.name in self.var_constants:
                self._mark_changed()
                return self._make_number(self.var_constants[expr.name], expr.location)

            # Check for copy propagation
            if expr.name in self.copies:
                copy_src = self.copies[expr.name]
                # Only propagate if source is still valid (not modified)
                if isinstance(copy_src, ast.Identifier):
                    if copy_src.name not in self.modified_vars:
                        self._mark_changed()
                        return ast.Identifier(
                            location=expr.location,
                            name=copy_src.name,
                            resolved=copy_src.resolved,
                            resolved_type=copy_src.resolved_type
                        )

        return expr

    def _optimize_subexpressions(self, expr: ast.Expression) -> ast.Expression:
        """Recursively optimize subexpressions."""

        if isinstance(expr, ast.BinaryOp):
            expr.left = self._optimize_expr(expr.left)
            expr.right = self._optimize_expr(expr.right)

        elif isinstance(expr, ast.UnaryOp):
            expr.operand = self._optimize_expr(expr.operand)

        elif isinstance(expr, ast.Comparison):
            expr.left = self._optimize_expr(expr.left)
            expr.right = self._optimize_expr(expr.right)

        elif isinstance(expr, ast.LogicalOp):
            expr.left = self._optimize_expr(expr.left)
            expr.right = self._optimize_expr(expr.right)

        elif isinstance(expr, ast.NotOp):
            expr.operand = self._optimize_expr(expr.operand)

        elif isinstance(expr, ast.Cast):
            expr.expr = self._optimize_expr(expr.expr)

        elif isinstance(expr, ast.ArrayAccess):
            expr.array = self._optimize_expr(expr.array)
            expr.index = self._optimize_expr(expr.index)

        elif isinstance(expr, ast.FieldAccess):
            expr.record = self._optimize_expr(expr.record)

        elif isinstance(expr, ast.Dereference):
            expr.pointer = self._optimize_expr(expr.pointer)

        elif isinstance(expr, ast.AddressOf):
            # Don't apply constant/copy propagation to address-of operand
            # The operand is an lvalue (location), not a value
            # We still need to optimize index expressions in array accesses though
            if isinstance(expr.operand, ast.ArrayAccess):
                # Only optimize the index, not the array base
                expr.operand.index = self._optimize_expr(expr.operand.index)
            elif isinstance(expr.operand, ast.FieldAccess):
                # Only optimize nested field accesses if the record is complex
                if not isinstance(expr.operand.record, ast.Identifier):
                    expr.operand.record = self._optimize_expr(expr.operand.record)
            # Don't optimize simple identifiers - they're lvalues

        elif isinstance(expr, ast.Call):
            expr.target = self._optimize_expr(expr.target)
            expr.args = [self._optimize_expr(a) for a in expr.args]

        elif isinstance(expr, ast.ArrayInitializer):
            expr.elements = [self._optimize_expr(e) for e in expr.elements]

        elif isinstance(expr, ast.RecordInitializer):
            expr.elements = [self._optimize_expr(e) for e in expr.elements]

        elif isinstance(expr, ast.Next):
            expr.pointer = self._optimize_expr(expr.pointer)

        elif isinstance(expr, ast.Prev):
            expr.pointer = self._optimize_expr(expr.pointer)

        return expr

    # ========================================================================
    # Constant Folding
    # ========================================================================

    def _eval_const(self, expr: ast.Expression) -> Optional[int]:
        """Try to evaluate expression as constant. Uses type checker's eval_const."""
        return self.tc.eval_const(expr)

    def _make_number(self, value: int, location) -> ast.NumberLiteral:
        """Create a number literal with given value."""
        return ast.NumberLiteral(location=location, value=value)

    def _constant_fold(self, expr: ast.Expression) -> ast.Expression:
        """Fold constant expressions."""

        if isinstance(expr, ast.BinaryOp):
            left_val = self._eval_const(expr.left)
            right_val = self._eval_const(expr.right)

            if left_val is not None and right_val is not None:
                result = self._compute_binary(expr.op, left_val, right_val)
                if result is not None:
                    self._mark_changed()
                    return self._make_number(result, expr.location)

        elif isinstance(expr, ast.UnaryOp):
            operand_val = self._eval_const(expr.operand)

            if operand_val is not None:
                result = self._compute_unary(expr.op, operand_val)
                if result is not None:
                    self._mark_changed()
                    return self._make_number(result, expr.location)

        elif isinstance(expr, ast.Comparison):
            left_val = self._eval_const(expr.left)
            right_val = self._eval_const(expr.right)

            if left_val is not None and right_val is not None:
                result = self._compute_comparison(expr.op, left_val, right_val)
                if result is not None:
                    self._mark_changed()
                    return self._make_number(result, expr.location)

        elif isinstance(expr, ast.LogicalOp):
            left_val = self._eval_const(expr.left)
            right_val = self._eval_const(expr.right)

            if left_val is not None and right_val is not None:
                if expr.op == 'and':
                    result = 1 if (left_val != 0 and right_val != 0) else 0
                elif expr.op == 'or':
                    result = 1 if (left_val != 0 or right_val != 0) else 0
                else:
                    return expr
                self._mark_changed()
                return self._make_number(result, expr.location)

        elif isinstance(expr, ast.NotOp):
            operand_val = self._eval_const(expr.operand)
            if operand_val is not None:
                result = 0 if operand_val != 0 else 1
                self._mark_changed()
                return self._make_number(result, expr.location)

        elif isinstance(expr, ast.Cast):
            # Fold cast of constant
            inner_val = self._eval_const(expr.expr)
            if inner_val is not None:
                # Just return the constant (casts don't change numeric value)
                self._mark_changed()
                return self._make_number(inner_val, expr.location)

        return expr

    def _compute_binary(self, op: str, left: int, right: int) -> Optional[int]:
        """Compute result of binary operation."""
        ops = {
            '+': lambda a, b: a + b,
            '-': lambda a, b: a - b,
            '*': lambda a, b: a * b,
            '/': lambda a, b: a // b if b != 0 else None,
            '%': lambda a, b: a % b if b != 0 else None,
            '&': lambda a, b: a & b,
            '|': lambda a, b: a | b,
            '^': lambda a, b: a ^ b,
            '<<': lambda a, b: a << b if b >= 0 and b < 32 else None,
            '>>': lambda a, b: a >> b if b >= 0 and b < 32 else None,
        }
        if op in ops:
            try:
                return ops[op](left, right)
            except:
                return None
        return None

    def _compute_unary(self, op: str, operand: int) -> Optional[int]:
        """Compute result of unary operation."""
        if op == '-':
            return -operand
        elif op == '~':
            return ~operand
        return None

    def _compute_comparison(self, op: str, left: int, right: int) -> Optional[int]:
        """Compute result of comparison (returns 0 or 1)."""
        ops = {
            '==': lambda a, b: a == b,
            '!=': lambda a, b: a != b,
            '<': lambda a, b: a < b,
            '<=': lambda a, b: a <= b,
            '>': lambda a, b: a > b,
            '>=': lambda a, b: a >= b,
        }
        if op in ops:
            return 1 if ops[op](left, right) else 0
        return None

    # ========================================================================
    # Strength Reduction
    # ========================================================================

    def _is_power_of_two(self, n: int) -> Optional[int]:
        """Check if n is a power of 2. Returns the exponent or None."""
        if n <= 0:
            return None
        if n & (n - 1) != 0:
            return None
        exp = 0
        while n > 1:
            n >>= 1
            exp += 1
        return exp

    def _strength_reduce(self, expr: ast.Expression) -> ast.Expression:
        """Apply strength reduction optimizations."""

        if isinstance(expr, ast.BinaryOp):
            left_const = self._eval_const(expr.left)
            right_const = self._eval_const(expr.right)

            # Multiplication strength reduction
            if expr.op == '*':
                # X * 0 = 0
                if right_const == 0:
                    self._mark_changed()
                    return self._make_number(0, expr.location)
                if left_const == 0:
                    self._mark_changed()
                    return self._make_number(0, expr.location)

                # X * 1 = X
                if right_const == 1:
                    self._mark_changed()
                    return expr.left
                if left_const == 1:
                    self._mark_changed()
                    return expr.right

                # X * 2 = X + X
                if right_const == 2:
                    self._mark_changed()
                    return ast.BinaryOp(
                        location=expr.location,
                        op='+',
                        left=expr.left,
                        right=expr.left,
                        resolved_type=expr.resolved_type
                    )
                if left_const == 2:
                    self._mark_changed()
                    return ast.BinaryOp(
                        location=expr.location,
                        op='+',
                        left=expr.right,
                        right=expr.right,
                        resolved_type=expr.resolved_type
                    )

                # X * 2^n = X << n
                if right_const is not None:
                    exp = self._is_power_of_two(right_const)
                    if exp is not None and exp > 1:
                        self._mark_changed()
                        return ast.BinaryOp(
                            location=expr.location,
                            op='<<',
                            left=expr.left,
                            right=self._make_number(exp, expr.location),
                            resolved_type=expr.resolved_type
                        )
                if left_const is not None:
                    exp = self._is_power_of_two(left_const)
                    if exp is not None and exp > 1:
                        self._mark_changed()
                        return ast.BinaryOp(
                            location=expr.location,
                            op='<<',
                            left=expr.right,
                            right=self._make_number(exp, expr.location),
                            resolved_type=expr.resolved_type
                        )

            # Division strength reduction
            elif expr.op == '/':
                # X / 1 = X
                if right_const == 1:
                    self._mark_changed()
                    return expr.left

                # X / 2^n = X >> n (for unsigned)
                if right_const is not None:
                    exp = self._is_power_of_two(right_const)
                    if exp is not None:
                        self._mark_changed()
                        return ast.BinaryOp(
                            location=expr.location,
                            op='>>',
                            left=expr.left,
                            right=self._make_number(exp, expr.location),
                            resolved_type=expr.resolved_type
                        )

            # Modulo strength reduction
            elif expr.op == '%':
                # X % 1 = 0
                if right_const == 1:
                    self._mark_changed()
                    return self._make_number(0, expr.location)

                # X % 2^n = X & (2^n - 1)
                if right_const is not None:
                    exp = self._is_power_of_two(right_const)
                    if exp is not None:
                        self._mark_changed()
                        return ast.BinaryOp(
                            location=expr.location,
                            op='&',
                            left=expr.left,
                            right=self._make_number(right_const - 1, expr.location),
                            resolved_type=expr.resolved_type
                        )

            # Shift strength reduction
            elif expr.op == '<<' or expr.op == '>>':
                # X << 0 = X, X >> 0 = X
                if right_const == 0:
                    self._mark_changed()
                    return expr.left

        return expr

    # ========================================================================
    # Algebraic Simplification
    # ========================================================================

    def _algebraic_simplify(self, expr: ast.Expression) -> ast.Expression:
        """Apply algebraic simplification rules."""

        if isinstance(expr, ast.BinaryOp):
            left_const = self._eval_const(expr.left)
            right_const = self._eval_const(expr.right)

            # Addition identities
            if expr.op == '+':
                # X + 0 = X
                if right_const == 0:
                    self._mark_changed()
                    return expr.left
                # 0 + X = X
                if left_const == 0:
                    self._mark_changed()
                    return expr.right

            # Subtraction identities
            elif expr.op == '-':
                # X - 0 = X
                if right_const == 0:
                    self._mark_changed()
                    return expr.left
                # X - X = 0 (if same identifier)
                if self._exprs_equal(expr.left, expr.right):
                    self._mark_changed()
                    return self._make_number(0, expr.location)

            # Bitwise AND identities
            elif expr.op == '&':
                # X & 0 = 0
                if right_const == 0 or left_const == 0:
                    self._mark_changed()
                    return self._make_number(0, expr.location)
                # X & 0xFFFF = X (for 16-bit)
                if right_const == 0xFFFF or right_const == 0xFF:
                    self._mark_changed()
                    return expr.left
                if left_const == 0xFFFF or left_const == 0xFF:
                    self._mark_changed()
                    return expr.right
                # X & X = X
                if self._exprs_equal(expr.left, expr.right):
                    self._mark_changed()
                    return expr.left

            # Bitwise OR identities
            elif expr.op == '|':
                # X | 0 = X
                if right_const == 0:
                    self._mark_changed()
                    return expr.left
                if left_const == 0:
                    self._mark_changed()
                    return expr.right
                # X | X = X
                if self._exprs_equal(expr.left, expr.right):
                    self._mark_changed()
                    return expr.left

            # Bitwise XOR identities
            elif expr.op == '^':
                # X ^ 0 = X
                if right_const == 0:
                    self._mark_changed()
                    return expr.left
                if left_const == 0:
                    self._mark_changed()
                    return expr.right
                # X ^ X = 0
                if self._exprs_equal(expr.left, expr.right):
                    self._mark_changed()
                    return self._make_number(0, expr.location)

        elif isinstance(expr, ast.UnaryOp):
            # Double negation: --X = X
            if expr.op == '-' and isinstance(expr.operand, ast.UnaryOp):
                if expr.operand.op == '-':
                    self._mark_changed()
                    return expr.operand.operand

            # Double complement: ~~X = X
            if expr.op == '~' and isinstance(expr.operand, ast.UnaryOp):
                if expr.operand.op == '~':
                    self._mark_changed()
                    return expr.operand.operand

        elif isinstance(expr, ast.Comparison):
            left_const = self._eval_const(expr.left)
            right_const = self._eval_const(expr.right)

            # X == X -> 1 (always true)
            if self._exprs_equal(expr.left, expr.right):
                if expr.op in ('==', '<=', '>='):
                    self._mark_changed()
                    return self._make_number(1, expr.location)
                elif expr.op in ('!=', '<', '>'):
                    self._mark_changed()
                    return self._make_number(0, expr.location)

            # Normalize comparisons: put constants on the right
            if left_const is not None and right_const is None:
                inv_op = {'<': '>', '>': '<', '<=': '>=', '>=': '<=',
                         '==': '==', '!=': '!='}
                if expr.op in inv_op:
                    self._mark_changed()
                    return ast.Comparison(
                        location=expr.location,
                        op=inv_op[expr.op],
                        left=expr.right,
                        right=expr.left,
                        resolved_type=expr.resolved_type
                    )

        elif isinstance(expr, ast.NotOp):
            # Double NOT: not(not X) = X
            if isinstance(expr.operand, ast.NotOp):
                self._mark_changed()
                return expr.operand.operand

            # NOT of comparison - invert the comparison
            if isinstance(expr.operand, ast.Comparison):
                inverse = self._invert_comparison(expr.operand.op)
                if inverse:
                    self._mark_changed()
                    return ast.Comparison(
                        location=expr.location,
                        op=inverse,
                        left=expr.operand.left,
                        right=expr.operand.right,
                        resolved_type=expr.operand.resolved_type
                    )

        elif isinstance(expr, ast.LogicalOp):
            left_const = self._eval_const(expr.left)
            right_const = self._eval_const(expr.right)

            if expr.op == 'and':
                # X and 0 = 0
                if left_const == 0 or right_const == 0:
                    self._mark_changed()
                    return self._make_number(0, expr.location)
                # X and 1 = X (if 1 is truthy)
                if left_const is not None and left_const != 0:
                    self._mark_changed()
                    return expr.right
                if right_const is not None and right_const != 0:
                    self._mark_changed()
                    return expr.left

            elif expr.op == 'or':
                # X or 1 = 1 (if 1 is truthy)
                if left_const is not None and left_const != 0:
                    self._mark_changed()
                    return self._make_number(1, expr.location)
                if right_const is not None and right_const != 0:
                    self._mark_changed()
                    return self._make_number(1, expr.location)
                # X or 0 = X
                if left_const == 0:
                    self._mark_changed()
                    return expr.right
                if right_const == 0:
                    self._mark_changed()
                    return expr.left

        return expr

    def _invert_comparison(self, op: str) -> Optional[str]:
        """Return the inverse comparison operator."""
        inverses = {
            '==': '!=',
            '!=': '==',
            '<': '>=',
            '<=': '>',
            '>': '<=',
            '>=': '<',
        }
        return inverses.get(op)

    def _exprs_equal(self, e1: ast.Expression, e2: ast.Expression) -> bool:
        """Check if two expressions are definitely equal."""
        # Simple case: same identifier
        if isinstance(e1, ast.Identifier) and isinstance(e2, ast.Identifier):
            return e1.name == e2.name
        # Both are the same constant
        v1 = self._eval_const(e1)
        v2 = self._eval_const(e2)
        if v1 is not None and v2 is not None:
            return v1 == v2
        return False

    # ========================================================================
    # Expression Reassociation
    # ========================================================================

    def _reassociate(self, expr: ast.Expression) -> ast.Expression:
        """Reassociate expressions to group constants together.

        Transforms: (a + 1) + 2 -> a + 3
                    (a * 2) * 3 -> a * 6
        """
        if not isinstance(expr, ast.BinaryOp):
            return expr

        # Only handle associative operations
        if expr.op not in ('+', '*', '&', '|', '^'):
            return expr

        # Collect all terms/factors in the chain
        terms: List[ast.Expression] = []
        constants: List[int] = []
        self._collect_terms(expr, expr.op, terms, constants)

        # If we found multiple constants, we can optimize
        if len(constants) >= 2:
            # Fold all constants
            if expr.op == '+':
                const_val = sum(constants)
            elif expr.op == '*':
                const_val = 1
                for c in constants:
                    const_val *= c
            elif expr.op == '&':
                const_val = constants[0]
                for c in constants[1:]:
                    const_val &= c
            elif expr.op == '|':
                const_val = constants[0]
                for c in constants[1:]:
                    const_val |= c
            elif expr.op == '^':
                const_val = 0
                for c in constants:
                    const_val ^= c
            else:
                return expr

            self._mark_changed()

            # Build the result expression
            if not terms:
                # All constants - just return the value
                return self._make_number(const_val, expr.location)

            # Start with first non-constant term
            result = terms[0]
            for term in terms[1:]:
                result = ast.BinaryOp(
                    location=expr.location,
                    op=expr.op,
                    left=result,
                    right=term,
                    resolved_type=expr.resolved_type
                )

            # Add the folded constant (skip if identity element)
            skip_const = False
            if expr.op == '+' and const_val == 0:
                skip_const = True
            elif expr.op == '*' and const_val == 1:
                skip_const = True
            elif expr.op == '&' and const_val == 0xFFFF:
                skip_const = True
            elif expr.op == '|' and const_val == 0:
                skip_const = True
            elif expr.op == '^' and const_val == 0:
                skip_const = True

            if not skip_const:
                result = ast.BinaryOp(
                    location=expr.location,
                    op=expr.op,
                    left=result,
                    right=self._make_number(const_val, expr.location),
                    resolved_type=expr.resolved_type
                )

            return result

        return expr

    def _collect_terms(self, expr: ast.Expression, op: str,
                       terms: List[ast.Expression], constants: List[int]) -> None:
        """Recursively collect terms from an associative chain."""
        if isinstance(expr, ast.BinaryOp) and expr.op == op:
            self._collect_terms(expr.left, op, terms, constants)
            self._collect_terms(expr.right, op, terms, constants)
        else:
            const_val = self._eval_const(expr)
            if const_val is not None:
                constants.append(const_val)
            else:
                terms.append(expr)


def optimize_program(program: ast.Program, type_checker: TypeChecker,
                     debug: bool = False) -> int:
    """Optimize a program's AST. Returns number of changes made."""
    optimizer = ASTOptimizer(type_checker)
    optimizer.debug = debug
    return optimizer.optimize(program)
