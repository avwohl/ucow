#!/usr/bin/env python3
"""ucow - A Cowgol compiler for 8080/Z80."""

import sys
import argparse
from pathlib import Path
from typing import List

from .lexer import Lexer, LexerError
from .parser import Parser, ParseError, parse_file, parse_string
from .preprocessor import Preprocessor, PreprocessorError, preprocess_file
from .types import TypeChecker, TypeError
from .codegen import generate, CodeGenerator
from .optimizer import optimize_program
from .callgraph import CallGraph, CallGraphBuilder, build_call_graph
from .postopt import optimize_asm
from . import ast


def compile_file(input_path: str, output_path: str = None,
                 include_paths: list = None, optimize: bool = True,
                 opt_debug: bool = False, library_mode: bool = False,
                 post_optimize: bool = True) -> bool:
    """Compile a Cowgol source file to 8080 assembly.

    Args:
        input_path: Path to .cow source file
        output_path: Path to output .mac file (default: replace extension)
        include_paths: Additional include search paths

    Returns:
        True if compilation succeeded, False otherwise
    """
    input_path = Path(input_path)

    if output_path is None:
        output_path = input_path.with_suffix('.mac')
    else:
        output_path = Path(output_path)

    try:
        # Preprocess (handles includes) and parse
        program = preprocess_file(str(input_path), include_paths)

        # Type check
        checker = TypeChecker()
        if not checker.check_program(program):
            for error in checker.errors:
                print(f"Error: {error}", file=sys.stderr)
            return False

        # Optimize AST (multi-pass until stable)
        if optimize:
            changes = optimize_program(program, checker, debug=opt_debug)
            if opt_debug:
                print(f"Optimizer: {changes} total changes")

        # Generate code
        asm = generate(program, checker, library_mode=library_mode)

        # Post-assembly optimization (JP->JR, dead code elimination)
        if post_optimize:
            asm, savings = optimize_asm(asm, verbose=opt_debug)
            if opt_debug and savings > 0:
                print(f"Post-optimizer: {savings} bytes saved")

        # Write output
        output_path.write_text(asm)
        print(f"Wrote {output_path}")

        return True

    except LexerError as e:
        print(f"Lexer error: {e}", file=sys.stderr)
        return False

    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return False

    except PreprocessorError as e:
        print(f"Preprocessor error: {e}", file=sys.stderr)
        return False

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


def compile_multi_file(input_paths: List[str], output_path: str,
                       include_paths: list = None, optimize: bool = True,
                       opt_debug: bool = False, graph_debug: bool = False) -> bool:
    """Compile multiple Cowgol source files with whole-program workspace optimization.

    This mode parses all input files, builds a cross-module call graph, and
    optimizes the data segment layout so non-concurrent subroutines can share
    local variable storage.

    Args:
        input_paths: List of paths to .cow source files
        output_path: Path to output .mac file
        include_paths: Additional include search paths
        optimize: Enable AST optimization
        opt_debug: Show optimization debug info
        graph_debug: Show call graph analysis debug info

    Returns:
        True if compilation succeeded, False otherwise
    """
    try:
        programs: List[ast.Program] = []
        checkers: List[TypeChecker] = []

        # Phase 1: Parse and type-check all files
        for input_path in input_paths:
            input_path = Path(input_path)
            print(f"Parsing {input_path}...")

            # Preprocess and parse
            program = preprocess_file(str(input_path), include_paths)

            # Type check
            checker = TypeChecker()
            if not checker.check_program(program):
                for error in checker.errors:
                    print(f"Error in {input_path}: {error}", file=sys.stderr)
                return False

            # Optimize AST
            if optimize:
                changes = optimize_program(program, checker, debug=opt_debug)
                if opt_debug:
                    print(f"  Optimizer: {changes} total changes")

            programs.append(program)
            checkers.append(checker)

        # Phase 2: Build cross-module call graph
        print("Building call graph...")
        call_graph = build_call_graph(programs)

        # Phase 3: Compute local sizes for each subroutine
        # We need to scan all subs and compute their local storage needs
        for program, checker in zip(programs, checkers):
            for decl in program.declarations:
                if isinstance(decl, ast.SubDecl) and decl.body:
                    local_size = compute_sub_local_size(decl, checker)
                    call_graph.add_subroutine(decl.name, local_size)

        # Phase 4: Compute global variable size (first file is "main")
        # For now, we compute globals from all files
        global_size = 0
        for program, checker in zip(programs, checkers):
            for stmt in program.statements:
                if isinstance(stmt, ast.VarDecl):
                    var_info = checker.current_scope.lookup_var(stmt.name)
                    if var_info:
                        global_size += checker.type_size(var_info.type)

        # Phase 5: Optimize workspace layout
        total_size = call_graph.compute_workspace_offsets(global_size)

        if graph_debug:
            print(call_graph.debug_dump())
            print(f"\nGlobal variables: {global_size} bytes")
            print(f"Total data segment: {total_size} bytes")

        # Phase 6: Generate code with optimized workspace
        # Merge all programs into one for unified code generation
        merged_program = merge_programs(programs)
        merged_checker = merge_checkers(checkers)

        # Generate with call graph info
        asm = generate_with_callgraph(merged_program, merged_checker, call_graph, global_size)

        # Write output
        output_path = Path(output_path)
        output_path.write_text(asm)
        print(f"Wrote {output_path}")
        print(f"Data segment optimized: {total_size} bytes (was {global_size + sum_all_locals(call_graph)} bytes)")

        return True

    except LexerError as e:
        print(f"Lexer error: {e}", file=sys.stderr)
        return False

    except ParseError as e:
        print(f"Parse error: {e}", file=sys.stderr)
        return False

    except PreprocessorError as e:
        print(f"Preprocessor error: {e}", file=sys.stderr)
        return False

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


def compute_sub_local_size(decl: ast.SubDecl, checker: TypeChecker) -> int:
    """Compute total bytes needed for a subroutine's local storage."""
    size = 0

    # Get params/returns from checker
    sub_info = checker.subroutines.get(decl.name)
    if sub_info:
        # Parameters
        for _, param_type in sub_info.params:
            size += checker.type_size(param_type)
        # Return values
        for _, ret_type in sub_info.returns:
            size += checker.type_size(ret_type)

    # Local variable declarations in body
    if decl.body:
        for stmt in decl.body:
            size += count_local_vars_in_stmt(stmt, checker)

    return size


def count_local_vars_in_stmt(stmt, checker: TypeChecker) -> int:
    """Count bytes used by local variables in a statement."""
    size = 0

    if isinstance(stmt, ast.VarDecl):
        # Resolve the type from the AST directly
        var_type = checker.resolve_type(stmt.type)
        if var_type:
            size += checker.type_size(var_type)
    elif isinstance(stmt, ast.IfStmt):
        for s in stmt.then_body:
            size += count_local_vars_in_stmt(s, checker)
        for s in stmt.else_body:
            size += count_local_vars_in_stmt(s, checker)
    elif isinstance(stmt, ast.WhileStmt):
        for s in stmt.body:
            size += count_local_vars_in_stmt(s, checker)
    elif isinstance(stmt, ast.LoopStmt):
        for s in stmt.body:
            size += count_local_vars_in_stmt(s, checker)
    elif isinstance(stmt, ast.CaseStmt):
        for when in stmt.whens:
            for s in when.body:
                size += count_local_vars_in_stmt(s, checker)

    return size


def merge_programs(programs: List[ast.Program]) -> ast.Program:
    """Merge multiple AST programs into one."""
    from .tokens import SourceLocation

    merged_decls = []
    merged_stmts = []

    for program in programs:
        merged_decls.extend(program.declarations)
        merged_stmts.extend(program.statements)

    # Use first program's location as the merged location
    loc = programs[0].location if programs else SourceLocation("<merged>", 0, 0)
    return ast.Program(location=loc, declarations=merged_decls, statements=merged_stmts)


def merge_checkers(checkers: List[TypeChecker]) -> TypeChecker:
    """Merge type checkers into one with combined symbol tables."""
    merged = TypeChecker()

    for checker in checkers:
        # Merge subroutines
        merged.subroutines.update(checker.subroutines)
        # Merge records
        merged.records.update(checker.records)
        # Merge constants
        merged.constants.update(checker.constants)
        # Merge interfaces
        merged.interfaces.update(checker.interfaces)
        # Merge variable info from global scope
        if hasattr(checker.global_scope, 'variables'):
            merged.global_scope.variables.update(checker.global_scope.variables)
        if hasattr(checker.global_scope, 'types'):
            merged.global_scope.types.update(checker.global_scope.types)

    return merged


def sum_all_locals(call_graph: CallGraph) -> int:
    """Sum up all local storage without optimization (for comparison)."""
    return sum(s.local_size for s in call_graph.subroutines.values())


def generate_with_callgraph(program: ast.Program, checker: TypeChecker,
                            call_graph: CallGraph, global_size: int) -> str:
    """Generate assembly using optimized workspace offsets from call graph."""
    gen = CodeGenerator(checker)
    gen.call_graph = call_graph
    gen.global_data_offset = global_size
    return gen.gen_program(program)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="ucow - Cowgol compiler for 8080/Z80"
    )
    parser.add_argument(
        'input',
        nargs='+',
        help="Input .cow source file(s). Multiple files enable workspace optimization."
    )
    parser.add_argument(
        '-o', '--output',
        help="Output .mac assembly file"
    )
    parser.add_argument(
        '-I', '--include',
        action='append',
        default=[],
        help="Add include search path"
    )
    parser.add_argument(
        '--tokens',
        action='store_true',
        help="Dump tokens and exit"
    )
    parser.add_argument(
        '--ast',
        action='store_true',
        help="Dump AST and exit"
    )
    parser.add_argument(
        '-O0', '--no-optimize',
        action='store_true',
        help="Disable optimization"
    )
    parser.add_argument(
        '--opt-debug',
        action='store_true',
        help="Show optimization debug info"
    )
    parser.add_argument(
        '--graph-debug',
        action='store_true',
        help="Show call graph analysis debug info"
    )
    parser.add_argument(
        '-L', '--library',
        action='store_true',
        help="Library mode: no main entry point or runtime (for multi-file linking)"
    )
    parser.add_argument(
        '--workspace-opt',
        action='store_true',
        help="Enable workspace optimization (automatic with multiple files)"
    )
    parser.add_argument(
        '--no-post-opt',
        action='store_true',
        help="Disable post-assembly optimization (JP->JR conversion, dead code elimination)"
    )

    args = parser.parse_args()

    # Determine if we have multiple files or single file mode
    input_files = args.input
    is_multi_file = len(input_files) > 1 or args.workspace_opt

    if args.tokens:
        # Token dump mode (single file only)
        source = Path(input_files[0]).read_text()
        lexer = Lexer(source, input_files[0])
        for token in lexer.tokenize():
            print(token)
        return 0

    if args.ast:
        # AST dump mode (single file only)
        try:
            program = parse_file(input_files[0])
            import pprint
            pprint.pprint(program)
            return 0
        except (LexerError, ParseError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    if is_multi_file:
        # Multi-file compilation with workspace optimization
        if args.output is None:
            # Default output name from first input
            args.output = Path(input_files[0]).with_suffix('.mac')

        success = compile_multi_file(
            input_files, args.output, args.include,
            optimize=not args.no_optimize,
            opt_debug=args.opt_debug,
            graph_debug=args.graph_debug
        )
    else:
        # Single file compilation (original mode)
        success = compile_file(
            input_files[0], args.output, args.include,
            optimize=not args.no_optimize,
            opt_debug=args.opt_debug,
            library_mode=args.library,
            post_optimize=not args.no_post_opt
        )
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
