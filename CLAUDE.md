# ucow - Cowgol Compiler for 8080/Z80 CP/M

## Project Overview
ucow is a Python-based Cowgol compiler that targets the 8080/Z80 CP/M platform. It compiles Cowgol source code to 8080 assembly, which can then be assembled with um80 and linked with ul80.

## Important Guidelines

### Terminal Safety
- **NEVER output raw binary data to the terminal** - it can put the terminal in graphics mode and make output unreadable
- When displaying binary data, always use hex encoding (e.g., `.hex()` in Python)
- Use `xxd` or similar tools that produce safe ASCII output
- **NEVER run cpmemu or other emulators in background** - they may output control characters
- Use `wc -c` to check file sizes instead of `cat` or `head` on binary files
- Use `xxd file | head` only when necessary and with limited output
- Avoid `head` or `tail` on .rel, .com, or any binary files
- **NEVER use BashOutput to read output from processes that may produce binary**

### Build Commands
```bash
# Compile a Cowgol file
python3 ucow source.cow -o output.mac

# Assemble with um80
um80 output.mac

# Link with ul80
ul80 output.rel -o output.com
```

### Include Paths for Cowgol Compiler
When compiling the Cowgol compiler itself:
```bash
python3 ucow main.cow \
  -I /path/to/cowgol/src/cowfe \
  -I /path/to/ucow/cowgol_compat \
  -I /path/to/cowgol/rt/cpm \
  -I /path/to/cowgol/rt \
  -I /path/to/cowgol
```

### Testing
```bash
# The whole suite
./run_tests.sh

# Or one test by hand. -I ../lib is what resolves the generated
# INCLUDE 'runtime.mac'; without it the assemble step fails.
python3 ucow tests/hello.cow -o tests/hello.mac -I tests
cd tests
um80 -I ../lib hello.mac
ul80 hello.rel -o hello.com
cpmemu hello.com
```

## Code Structure
There is no hand-written lexer. 0.4.0 replaced it and the
recursive-descent parser with an LALR(1) parser uplox generates from
`cowgol_ast.uplox`; `src/parser.py` is now the translator between that
parser's AST and this one's.

- `src/uplox_cowgol.py` - Generated scanner and parser (do not edit; regenerate)
- `src/parser.py` - Translates the generated AST into `src/ast.py` nodes
- `src/ast.py` - AST node definitions
- `src/tokens.py` - `SourceLocation`, all that outlived the old lexer
- `src/preprocessor.py` - Include file handling
- `src/types.py` - Type checker and semantic analysis
- `src/optimizer.py` - AST-level optimization passes, before code generation
- `src/callgraph.py` - Which subroutines can share local storage
- `src/codegen.py` - 8080 assembly code generator
- `src/postopt.py` - Peephole pass over the generated `.mac`
- `src/main.py` - Driver
- `src/__init__.py` - `__version__`, which `[tool.hatch.version]` reads
- `lib/runtime.mac` - Runtime support routines
- `cowgol_compat/` - Generated files for Cowgol compatibility (parser.coh, etc.)

## Name Mangling
- Variables are prefixed with `v_` to avoid conflicts with 8080 register names
- Subroutines named after registers (A, B, C, D, E, H, L, M, SP, PSW) are prefixed with `s_`
- Constants are evaluated at compile time and substituted as literal values

## Optimization Reference
See ~/cl/mbasic/docs/history/compiler_optimizations/OPTIMIZATION_STATUS.md for machine-independent optimizations to implement:
- Constant Folding, Strength Reduction, Algebraic Simplification
- Dead Code Detection, Copy Propagation, CSE
- Expression Reassociation, Boolean Simplification
- These should be done BEFORE register allocation
