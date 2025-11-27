# ucow

A Python-based Cowgol compiler targeting Z80 CP/M.

## Usage

```bash
# Compile a Cowgol source file to Z80 assembly
python3 -m src.main source.cow -o output.mac

# Assemble with um80
um80 output.mac

# Link with ul80
ul80 output.rel -o output.com
```

## Requirements

- Python 3
- um80 (Z80 assembler)
- ul80 (linker)
- cpmemu (for testing)
