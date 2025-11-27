"""Preprocessor for Cowgol - handles includes and other directives."""

from pathlib import Path
from typing import List, Set, Optional
from . import ast
from .lexer import Lexer
from .parser import Parser


class PreprocessorError(Exception):
    """Preprocessor error."""
    pass


class Preprocessor:
    """Handle include directives and other preprocessing."""

    def __init__(self, include_paths: List[str] = None):
        self.include_paths = include_paths or []
        self.included_files: Set[str] = set()  # Prevent circular includes

    def add_include_path(self, path: str) -> None:
        """Add a directory to the include search path."""
        self.include_paths.append(path)

    def find_include(self, filename: str, from_file: str) -> Optional[Path]:
        """Find an include file.

        Search order:
        1. Relative to the including file
        2. In each include path directory
        """
        from_dir = Path(from_file).parent

        # Try relative to current file
        candidate = from_dir / filename
        if candidate.exists():
            return candidate.resolve()

        # Try include paths
        for inc_path in self.include_paths:
            candidate = Path(inc_path) / filename
            if candidate.exists():
                return candidate.resolve()

        return None

    def process_file(self, filepath: str) -> ast.Program:
        """Process a file and all its includes.

        Returns a merged AST with all includes resolved.
        """
        filepath = str(Path(filepath).resolve())
        return self._process_file_impl(filepath)

    def _process_file_impl(self, filepath: str) -> ast.Program:
        """Implementation of file processing."""
        # Check for circular include
        if filepath in self.included_files:
            raise PreprocessorError(f"Circular include detected: {filepath}")

        self.included_files.add(filepath)

        # Read and parse
        source = Path(filepath).read_text()
        lexer = Lexer(source, filepath)
        parser = Parser(lexer)
        program = parser.parse()

        # Process items in source order - interleave declarations and statements
        # to maintain proper ordering (e.g., const before include that uses it)
        merged_decls = []
        merged_stmts = []

        # Combine declarations and statements, sorted by source location
        all_items = []
        for decl in program.declarations:
            all_items.append(('decl', decl))
        for stmt in program.statements:
            all_items.append(('stmt', stmt))

        # Sort by source location (line, then column)
        all_items.sort(key=lambda x: (x[1].location.line, x[1].location.column))

        # Process in order
        for item_type, item in all_items:
            if isinstance(item, ast.IncludeDecl):
                # Find and process the included file
                inc_path = self.find_include(item.path, filepath)
                if inc_path is None:
                    raise PreprocessorError(
                        f"{item.location}: Cannot find include file: {item.path}"
                    )

                # Recursively process include
                inc_program = self._process_file_impl(str(inc_path))

                # Merge declarations and statements from include
                merged_decls.extend(inc_program.declarations)
                merged_stmts.extend(inc_program.statements)
            elif item_type == 'decl':
                merged_decls.append(item)
            else:
                merged_stmts.append(item)

        return ast.Program(program.location, merged_decls, merged_stmts)


def preprocess_file(filepath: str, include_paths: List[str] = None) -> ast.Program:
    """Preprocess a file, resolving all includes.

    Args:
        filepath: Path to the main source file
        include_paths: List of directories to search for includes

    Returns:
        Merged AST with all includes resolved
    """
    preprocessor = Preprocessor(include_paths)
    return preprocessor.process_file(filepath)
