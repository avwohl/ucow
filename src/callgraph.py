"""Call graph analysis and workspace optimization for ucow.

This module implements static analysis to determine which subroutines can
share local variable storage. If two subroutines are never active at the
same time (neither calls the other directly or transitively), their local
variables can occupy the same memory addresses.

The algorithm:
1. Build a call graph from AST (which sub calls which)
2. Starting from main, do DFS to assign workspace addresses
3. Each sub's locals start after the "watermark" (max address used by callers)
4. Non-concurrent subs (different call tree branches) can share the same space
"""

from typing import Dict, Set, List, Optional, Tuple
from dataclasses import dataclass, field
from . import ast


@dataclass
class SubroutineWorkspace:
    """Workspace information for a subroutine."""
    name: str
    local_size: int = 0  # Bytes needed for locals (params + returns + local vars)
    workspace_offset: int = 0  # Assigned offset in data segment
    called_by: Set[str] = field(default_factory=set)  # Which subs call this one
    calls: Set[str] = field(default_factory=set)  # Which subs this one calls
    is_used: bool = False  # Reachable from main?


class CallGraph:
    """Builds and analyzes call relationships between subroutines."""

    def __init__(self):
        self.subroutines: Dict[str, SubroutineWorkspace] = {}
        self.main_stmts_calls: Set[str] = set()  # Subs called from main statements

    def add_subroutine(self, name: str, local_size: int = 0) -> None:
        """Register a subroutine with its local storage size."""
        if name not in self.subroutines:
            self.subroutines[name] = SubroutineWorkspace(name, local_size)
        else:
            self.subroutines[name].local_size = local_size

    def add_call(self, caller: str, callee: str) -> None:
        """Record that caller calls callee."""
        # Ensure both exist
        if caller not in self.subroutines:
            self.subroutines[caller] = SubroutineWorkspace(caller)
        if callee not in self.subroutines:
            self.subroutines[callee] = SubroutineWorkspace(callee)

        self.subroutines[caller].calls.add(callee)
        self.subroutines[callee].called_by.add(caller)

    def add_main_call(self, callee: str) -> None:
        """Record that main program statements call a subroutine."""
        self.main_stmts_calls.add(callee)
        if callee not in self.subroutines:
            self.subroutines[callee] = SubroutineWorkspace(callee)

    def reaches(self, src: str, dst: str, visited: Set[str] = None) -> bool:
        """Check if src can reach dst via call chain."""
        if visited is None:
            visited = set()
        if src == dst:
            return True
        if src in visited or src not in self.subroutines:
            return False
        visited.add(src)
        for callee in self.subroutines[src].calls:
            if self.reaches(callee, dst, visited):
                return True
        return False

    def can_share(self, a: str, b: str) -> bool:
        """Return True if a and b can share workspace (never concurrent)."""
        if a == b:
            return True
        return not self.reaches(a, b) and not self.reaches(b, a)

    def mark_used(self) -> None:
        """Mark all subroutines reachable from main as used."""
        # Start from main statement calls
        worklist = list(self.main_stmts_calls)
        visited = set()

        while worklist:
            name = worklist.pop()
            if name in visited or name not in self.subroutines:
                continue
            visited.add(name)
            self.subroutines[name].is_used = True
            worklist.extend(self.subroutines[name].calls)

    def compute_workspace_offsets(self, global_size: int = 0) -> int:
        """Assign workspace offsets to minimize total data segment size.

        Args:
            global_size: Size of global variables (placed at start of data segment)

        Returns:
            Total data segment size needed
        """
        self.mark_used()

        # Build a list of used subroutines
        used_subs = [s for s in self.subroutines.values() if s.is_used]

        if not used_subs:
            return global_size

        # Algorithm: DFS from main, assigning offsets based on call depth
        # Subs at the same depth (siblings in call tree) can share space

        # First, compute the "watermark" for each sub - the max offset needed
        # by all its callers

        # Start with main's calls at offset = global_size
        assigned: Dict[str, int] = {}
        max_offset = global_size

        def assign_workspace(name: str, caller_watermark: int) -> int:
            """Assign workspace offset for a sub, return watermark after it."""
            if name not in self.subroutines:
                return caller_watermark

            sub = self.subroutines[name]

            # If already assigned, check if we need a higher offset
            if name in assigned:
                # Already processed - return the watermark after this sub
                return assigned[name] + sub.local_size

            # Assign at caller's watermark
            sub.workspace_offset = caller_watermark
            assigned[name] = caller_watermark

            # Our watermark is after our locals
            our_watermark = caller_watermark + sub.local_size

            # Process callees - they all start at our watermark
            # but siblings can share (they start at the same offset)
            child_max = our_watermark
            for callee in sub.calls:
                callee_end = assign_workspace(callee, our_watermark)
                child_max = max(child_max, callee_end)

            return child_max

        # Process all subs called from main
        main_watermark = global_size
        for name in self.main_stmts_calls:
            end = assign_workspace(name, main_watermark)
            max_offset = max(max_offset, end)

        # Handle any orphan subs (defined but not reachable from main)
        # They can share space with everything (since they're never called)
        for name, sub in self.subroutines.items():
            if name not in assigned:
                sub.workspace_offset = global_size  # Share with first level

        return max_offset

    def get_workspace_offset(self, name: str) -> int:
        """Get the assigned workspace offset for a subroutine."""
        if name in self.subroutines:
            return self.subroutines[name].workspace_offset
        return 0

    def debug_dump(self) -> str:
        """Return a debug string showing the call graph and assignments."""
        lines = ["Call Graph Analysis:"]
        lines.append(f"  Main calls: {sorted(self.main_stmts_calls)}")
        lines.append("")

        for name, sub in sorted(self.subroutines.items()):
            status = "USED" if sub.is_used else "unused"
            lines.append(f"  {name}: {status}")
            lines.append(f"    local_size: {sub.local_size}")
            lines.append(f"    workspace_offset: {sub.workspace_offset}")
            lines.append(f"    calls: {sorted(sub.calls)}")
            lines.append(f"    called_by: {sorted(sub.called_by)}")

        return '\n'.join(lines)


class CallGraphBuilder:
    """AST visitor that builds a call graph."""

    def __init__(self, graph: CallGraph):
        self.graph = graph
        self.current_sub: Optional[str] = None

    def visit_SubDecl(self, node: ast.SubDecl) -> None:
        """Visit a subroutine declaration."""
        if node.body is None:
            return  # Forward declaration only

        old_sub = self.current_sub
        self.current_sub = node.name

        # Register the sub (size will be computed later during codegen)
        self.graph.add_subroutine(node.name)

        # Visit body to find calls
        for stmt in node.body:
            self.visit(stmt)

        self.current_sub = old_sub

    def visit_Call(self, node: ast.Call) -> None:
        """Visit a call expression."""
        # Record the call
        if isinstance(node.target, ast.Identifier):
            callee = node.target.name
            if self.current_sub:
                self.graph.add_call(self.current_sub, callee)
            else:
                # Called from main program statements
                self.graph.add_main_call(callee)

        # Visit arguments (might contain nested calls)
        for arg in node.args:
            self.visit(arg)

    def visit(self, node) -> None:
        """Generic visit that dispatches to specific handlers."""
        method_name = f'visit_{node.__class__.__name__}'
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node) -> None:
        """Visit all child nodes."""
        if isinstance(node, ast.Expression):
            self._visit_expr_children(node)
        elif isinstance(node, ast.Statement):
            self._visit_stmt_children(node)

    def _visit_expr_children(self, expr) -> None:
        """Visit children of an expression node."""
        if isinstance(expr, ast.BinaryOp):
            self.visit(expr.left)
            self.visit(expr.right)
        elif isinstance(expr, ast.UnaryOp):
            self.visit(expr.operand)
        elif isinstance(expr, ast.Call):
            self.visit_Call(expr)
        elif isinstance(expr, ast.ArrayAccess):
            self.visit(expr.array)
            self.visit(expr.index)
        elif isinstance(expr, ast.FieldAccess):
            self.visit(expr.record)
        elif isinstance(expr, ast.Cast):
            self.visit(expr.expr)
        elif isinstance(expr, ast.SizeOf):
            if expr.expr:
                self.visit(expr.expr)
        elif isinstance(expr, ast.AddressOf):
            self.visit(expr.expr)
        elif isinstance(expr, ast.Dereference):
            self.visit(expr.expr)

    def _visit_stmt_children(self, stmt) -> None:
        """Visit children of a statement node."""
        if isinstance(stmt, ast.Assignment):
            self.visit(stmt.target)
            self.visit(stmt.value)
        elif isinstance(stmt, ast.VarDecl):
            if stmt.init:
                self.visit(stmt.init)
        elif isinstance(stmt, ast.IfStmt):
            self.visit(stmt.condition)
            for s in stmt.then_body:
                self.visit(s)
            for s in stmt.else_body:
                self.visit(s)
        elif isinstance(stmt, ast.WhileStmt):
            self.visit(stmt.condition)
            for s in stmt.body:
                self.visit(s)
        elif isinstance(stmt, ast.LoopStmt):
            for s in stmt.body:
                self.visit(s)
        elif isinstance(stmt, ast.CaseStmt):
            self.visit(stmt.expr)
            for when in stmt.whens:
                for s in when.body:
                    self.visit(s)
        elif isinstance(stmt, ast.ReturnStmt):
            pass
        elif isinstance(stmt, ast.ExprStmt):
            self.visit(stmt.expr)


def build_call_graph(programs: List[ast.Program]) -> CallGraph:
    """Build a call graph from one or more parsed programs.

    Args:
        programs: List of parsed AST programs

    Returns:
        CallGraph with all call relationships
    """
    graph = CallGraph()
    builder = CallGraphBuilder(graph)

    for program in programs:
        # Visit all declarations (subroutines)
        for decl in program.declarations:
            if isinstance(decl, ast.SubDecl):
                builder.visit_SubDecl(decl)

        # Visit main statements
        builder.current_sub = None
        for stmt in program.statements:
            builder.visit(stmt)

    return graph
