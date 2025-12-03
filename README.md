# ucow

A Python-based Cowgol compiler targeting Z80 CP/M.

## Usage

### Single File Compilation

```bash
# Compile a Cowgol source file to Z80 assembly
python3 -m src.main source.cow -o output.mac

# Assemble with um80
um80 output.mac

# Link with ul80
ul80 output.rel -o output.com
```

### Multi-File Compilation

Compile multiple Cowgol source files together in a single compiler run:

```bash
# Compile all modules together into one assembly file
python3 -m src.main main.cow utils.cow math.cow -o program.mac

# Assemble and link
um80 program.mac
ul80 program.rel -o program.com
```

This approach enables **workspace optimization**: the compiler analyzes the call graph across all modules to determine which subroutines can never be active simultaneously. Subroutines that don't overlap in the call tree share local variable storage, significantly reducing data segment size.

For example, if `sub_a` and `sub_b` each use 100 bytes of locals but never call each other, they share the same 100 bytes instead of using 200 bytes total.

Use `--graph-debug` to see the call graph analysis:

```bash
python3 -m src.main --graph-debug main.cow lib.cow -o output.mac
```

## Include Paths

Use `-I` to add directories to the include search path:

```bash
python3 -m src.main -I ./lib -I ./include source.cow -o output.mac
```

## Compiler Options

| Option | Description |
|--------|-------------|
| `-o OUTPUT` | Output assembly file name |
| `-I PATH` | Add include search path |
| `-O0` | Disable optimization |
| `--no-post-opt` | Disable post-assembly optimization |
| `--graph-debug` | Show call graph analysis |
| `--tokens` | Dump tokens and exit |
| `--ast` | Dump AST and exit |

## Requirements

- Python 3
- um80 (Z80 assembler)
- ul80 (linker)
- cpmemu (for testing)
