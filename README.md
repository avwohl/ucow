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
## Related Projects

- [80un](https://github.com/avwohl/80un) - Unpacker for CP/M compression and archive formats (LBR, ARC, squeeze, crunch, CrLZH)
- [cpmdroid](https://github.com/avwohl/cpmdroid) - Z80/CP/M emulator for Android with RomWBW HBIOS compatibility and VT100 terminal
- [cpmemu](https://github.com/avwohl/cpmemu) - CP/M 2.2 emulator with Z80/8080 CPU emulation and BDOS/BIOS translation to Unix filesystem
- [ioscpm](https://github.com/avwohl/ioscpm) - Z80/CP/M emulator for iOS and macOS with RomWBW HBIOS compatibility
- [learn-ada-z80](https://github.com/avwohl/learn-ada-z80) - Ada programming examples for the uada80 compiler targeting Z80/CP/M
- [mbasic](https://github.com/avwohl/mbasic) - Modern MBASIC 5.21 Interpreter & Compilers
- [mbasic2025](https://github.com/avwohl/mbasic2025) - MBASIC 5.21 source code reconstruction - byte-for-byte match with original binary
- [mbasicc](https://github.com/avwohl/mbasicc) - C++ implementation of MBASIC 5.21
- [mbasicc_web](https://github.com/avwohl/mbasicc_web) - WebAssembly MBASIC 5.21
- [mpm2](https://github.com/avwohl/mpm2) - MP/M II multi-user CP/M emulator with SSH terminal access and SFTP file transfer
- [romwbw_emu](https://github.com/avwohl/romwbw_emu) - Hardware-level Z80 emulator for RomWBW with 512KB ROM + 512KB RAM banking and HBIOS support
- [scelbal](https://github.com/avwohl/scelbal) - SCELBAL BASIC interpreter - 8008 to 8080 translation
- [uada80](https://github.com/avwohl/uada80) - Ada compiler targeting Z80 processor and CP/M 2.2 operating system
- [uc80](https://github.com/avwohl/uc80) - ANSI C compiler targeting Z80 processor and CP/M 2.2 operating system
- [um80_and_friends](https://github.com/avwohl/um80_and_friends) - Microsoft MACRO-80 compatible toolchain for Linux: assembler, linker, librarian, disassembler
- [upeepz80](https://github.com/avwohl/upeepz80) - Universal peephole optimizer for Z80 compilers
- [uplm80](https://github.com/avwohl/uplm80) - PL/M-80 compiler targeting Intel 8080 and Zilog Z80 assembly language
- [z80cpmw](https://github.com/avwohl/z80cpmw) - Z80 CP/M emulator for Windows (RomWBW)

