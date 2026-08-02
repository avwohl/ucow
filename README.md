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

- [80un](https://github.com/avwohl/80un) - Unpacker for the CP/M archive and compression formats LBR, ARC, squeeze, crunch, and CrLZH.
- [cpmdroid](https://github.com/avwohl/cpmdroid) - Z80/CP/M emulator for Android phones and tablets. It emulates the RomWBW HBIOS interface and a VT100 terminal.
- [cpmemu](https://github.com/avwohl/cpmemu) - Z80/CP/M emulator for Linux and Windows, with Z80 and 8080 CPU cores. It translates the BDOS and BIOS calls of CP/M 2.2 programs to the host file system.
- [ioscpm](https://github.com/avwohl/ioscpm) - Z80/CP/M emulator for iOS and macOS. It emulates the RomWBW HBIOS interface and runs CP/M 2.2 and CP/M 3.
- [learn-ada-z80](https://github.com/avwohl/learn-ada-z80) - Collection of more than 90 Ada example programs for uada80, the Ada compiler for the Z80 processor and CP/M.
- [mbasic](https://github.com/avwohl/mbasic) - Python interpreter for MBASIC 5.21, the Microsoft BASIC-80 for CP/M. Two compiler backends compile the programs to CP/M .COM files or to JavaScript.
- [mbasic2025](https://github.com/avwohl/mbasic2025) - Reconstruction of the lost source code of MBASIC 5.21, the Microsoft BASIC-80 for CP/M. The MACRO-80 source code assembles to a binary that matches mbasic.com byte for byte.
- [mbasicc](https://github.com/avwohl/mbasicc) - C++17 interpreter for MBASIC 5.21, the Microsoft BASIC-80 for CP/M. It runs on Linux and macOS.
- [mbasicc_web](https://github.com/avwohl/mbasicc_web) - Web browser interpreter for MBASIC 5.21, the Microsoft BASIC-80 for CP/M. Emscripten compiles the mbasicc interpreter to WebAssembly.
- [mpm2](https://github.com/avwohl/mpm2) - Z80 emulator for MP/M II, the multi-user CP/M operating system. Users connect over SSH, and SFTP clients transfer files.
- [romwbw_emu](https://github.com/avwohl/romwbw_emu) - Hardware-level Z80/CP/M emulator for Linux and macOS. It emulates the RomWBW HBIOS interface and switches banks in 512 KB of ROM and 512 KB of RAM.
- [scelbal](https://github.com/avwohl/scelbal) - Floating-point BASIC interpreter for the 8080 processor and CP/M. A translator converts the original 8008 source code to 8080 source code.
- [uada80](https://github.com/avwohl/uada80) - Ada compiler for the Z80 processor and CP/M 2.2. It compiles a subset of Ada 2012 to CP/M .COM files.
- [uc80](https://github.com/avwohl/uc80) - C compiler for the Z80 processor and CP/M. It optimizes for small code size.
- [um80_and_friends](https://github.com/avwohl/um80_and_friends) - Linux toolchain that is compatible with Microsoft MACRO-80. It has an assembler, a linker, a librarian, and a disassembler.
- [upeepz80](https://github.com/avwohl/upeepz80) - Peephole optimizer for Z80 compilers that write lowercase Z80 assembly language. It shortens jumps to jr, builds djnz loops, and removes dead stores.
- [uplm80](https://github.com/avwohl/uplm80) - PL/M-80 compiler for the Z80 processor and CP/M. It writes Intel 8080 and Zilog Z80 assembly language.
- [z80cpmw](https://github.com/avwohl/z80cpmw) - Z80/CP/M emulator for Windows. It emulates the RomWBW HBIOS interface and boots CP/M from disk images.

