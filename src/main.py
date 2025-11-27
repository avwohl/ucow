#!/usr/bin/env python3
"""ucow - A Cowgol compiler for 8080/Z80."""

import sys
import argparse
from pathlib import Path

from .lexer import Lexer, LexerError
from .parser import Parser, ParseError, parse_file, parse_string
from .preprocessor import Preprocessor, PreprocessorError, preprocess_file
from .types import TypeChecker, TypeError
from .codegen import generate


def compile_file(input_path: str, output_path: str = None,
                 include_paths: list = None) -> bool:
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

        # Generate code
        asm = generate(program, checker)

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


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="ucow - Cowgol compiler for 8080/Z80"
    )
    parser.add_argument(
        'input',
        help="Input .cow source file"
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

    args = parser.parse_args()

    if args.tokens:
        # Token dump mode
        source = Path(args.input).read_text()
        lexer = Lexer(source, args.input)
        for token in lexer.tokenize():
            print(token)
        return 0

    if args.ast:
        # AST dump mode
        try:
            program = parse_file(args.input)
            import pprint
            pprint.pprint(program)
            return 0
        except (LexerError, ParseError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    # Normal compilation
    success = compile_file(args.input, args.output, args.include)
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
