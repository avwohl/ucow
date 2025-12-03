"""
Post-assembly optimization pass for ucow.

This pass operates on the generated assembly file (.mac) after code generation
but before assembly. It performs optimizations that are easier to do at the
text level, particularly cross-procedure optimizations.

Optimizations:
1. JP to JR conversion - convert 3-byte JP to 2-byte JR when target in range
2. Jump to next instruction elimination
3. Dead code after unconditional jumps/returns

Based on uplm80's postopt.py.
"""

import re
from typing import Optional


def get_instr_size(instr: str) -> Optional[int]:
    """Get the size of a Z80 instruction in bytes, or None if unknown."""
    instr = instr.strip()

    # Skip comments and labels
    if not instr or instr.startswith(';') or instr.endswith(':'):
        return 0

    # Handle label: instruction on same line
    if ':' in instr and not instr.endswith(':'):
        colon_pos = instr.index(':')
        instr = instr[colon_pos + 1:].strip()
        if not instr:
            return 0

    # Strip trailing comments
    if ';' in instr:
        instr = instr[:instr.index(';')].strip()

    # Normalize whitespace
    instr = ' '.join(instr.split())

    # 1-byte instructions
    if instr in ('NOP', 'HALT', 'RET', 'EXX', 'EX DE,HL', "EX AF,AF'",
                 'DI', 'EI', 'CCF', 'SCF', 'DAA', 'CPL', 'NEG',
                 'RLCA', 'RRCA', 'RLA', 'RRA'):
        return 1
    if re.match(r'^(INC|DEC)\s+[ABCDEHL]$', instr):
        return 1
    if re.match(r'^(INC|DEC)\s+(BC|DE|HL|SP|IX|IY)$', instr):
        return 1
    if re.match(r'^(PUSH|POP)\s+(AF|BC|DE|HL)$', instr):
        return 1
    if re.match(r'^LD\s+[ABCDEHL],[ABCDEHL]$', instr):
        return 1
    if re.match(r'^(ADD|ADC|SUB|SBC|AND|OR|XOR|CP)\s+A?,?[ABCDEHL]$', instr):
        return 1
    if re.match(r'^(ADD|ADC|SBC)\s+HL,(BC|DE|HL|SP)', instr):
        return 1
    if instr in ('XOR A', 'OR A', 'AND A', 'CP A'):
        return 1
    if instr == 'JP (HL)':
        return 1

    # 2-byte instructions
    if re.match(r'^LD\s+[ABCDEHL],\d+$', instr):
        return 2  # LD r,n
    if re.match(r'^LD\s+[ABCDEHL],0[0-9A-Fa-f]+H$', instr):
        return 2
    if re.match(r'^(ADD|ADC|SUB|SBC|AND|OR|XOR|CP)\s+(A,)?\d+$', instr):
        return 2
    if re.match(r'^(ADD|ADC|SUB|SBC|AND|OR|XOR|CP)\s+(A,)?0[0-9A-Fa-f]+H$', instr):
        return 2
    if re.match(r'^JR\s+', instr):
        return 2
    if re.match(r'^DJNZ\s+', instr):
        return 2

    # 3-byte instructions
    if re.match(r'^LD\s+(BC|DE|HL|SP),\d+$', instr):
        return 3
    if re.match(r'^LD\s+(BC|DE|HL|SP),0[0-9A-Fa-f]+H$', instr):
        return 3
    if re.match(r'^LD\s+(BC|DE|HL|SP),[A-Za-z@$?_]', instr):
        return 3  # LD rr,label
    if re.match(r'^LD\s+\([A-Za-z@$?_0-9]+\),(HL|A)$', instr):
        return 3
    if re.match(r'^LD\s+(HL|A),\([A-Za-z@$?_0-9]+\)$', instr):
        return 3
    if re.match(r'^(JP|CALL)\s+', instr):
        if '(HL)' in instr:
            return 1
        return 3

    # 4-byte instructions (ED prefix + 2 byte addr)
    if re.match(r'^LD\s+\([A-Za-z@$?_0-9]+\),(BC|DE|SP)$', instr):
        return 4
    if re.match(r'^LD\s+(BC|DE|SP),\([A-Za-z@$?_0-9]+\)$', instr):
        return 4

    # Default: assume 3 bytes (conservative for jumps)
    return None


def find_labels_and_jumps(lines: list[str]) -> tuple[dict[str, int], list[tuple[int, str, str]]]:
    """
    Find all labels and JP instructions.

    Returns:
        - label_positions: dict mapping label name to line index
        - jp_candidates: list of (line_idx, condition, target_label)
    """
    label_positions = {}
    jp_candidates = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Collect label definitions
        if stripped.endswith(':') and not stripped.startswith(';'):
            label = stripped[:-1]
            label_positions[label] = i

        # Also handle label: on same line as instruction
        if ':' in stripped and not stripped.endswith(':') and not stripped.startswith(';'):
            colon_pos = stripped.index(':')
            label = stripped[:colon_pos].strip()
            if label and not label.startswith(';'):
                label_positions[label] = i

        # Collect JP instructions (unconditional and conditional)
        # Match: JP target, JP Z,target, JP NZ,target, JP C,target, JP NC,target
        # But NOT JP (HL)
        m = re.match(r'^\s*JP\s+(?:(Z|NZ|C|NC|PE|PO|P|M),\s*)?([A-Za-z@$?_][A-Za-z0-9@$?_]*)$', stripped)
        if m:
            condition = m.group(1)  # None for unconditional
            target = m.group(2)
            jp_candidates.append((i, condition, target))

    return label_positions, jp_candidates


def calculate_byte_offset(lines: list[str], from_line: int, to_line: int) -> Optional[int]:
    """
    Calculate the byte offset between two lines.

    Returns None if any instruction size is unknown.
    """
    if from_line == to_line:
        return 0

    total = 0
    start, end = (from_line, to_line) if from_line < to_line else (to_line, from_line)

    for i in range(start, end):
        line = lines[i].strip()

        # Skip empty lines, comments, and directives
        if not line or line.startswith(';'):
            continue
        if line.startswith('.') or line.startswith('EXTRN') or line == 'END':
            continue

        # Handle DS directive
        if line.startswith('DS ') or line.startswith('DS\t'):
            m = re.match(r'DS\s+(\d+)', line)
            if m:
                total += int(m.group(1))
            continue

        # Handle DW directive
        if line.startswith('DW ') or line.startswith('DW\t'):
            parts = line[3:].split(',')
            total += 2 * len(parts)
            continue

        # Handle DB directive
        if line.startswith('DB ') or line.startswith('DB\t'):
            db_content = line[3:].strip()
            byte_count = 0
            in_string = False
            j = 0
            while j < len(db_content):
                c = db_content[j]
                if c == "'":
                    in_string = not in_string
                elif in_string:
                    byte_count += 1
                elif c == ',':
                    pass
                elif c.isalnum():
                    byte_count += 1
                    while j + 1 < len(db_content) and db_content[j+1].isalnum():
                        j += 1
                j += 1
            total += max(1, byte_count)
            continue

        # Skip EQU
        if 'EQU' in line:
            continue

        size = get_instr_size(line)
        if size is None:
            return None
        total += size

    # If jumping backward, offset is negative
    if from_line > to_line:
        total = -total

    return total


def convert_jp_to_jr(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Convert JP to JR where target is within range.

    JR range is -128 to +127 bytes from the end of JR instruction.
    """
    result = lines.copy()
    total_savings = 0

    label_positions, jp_candidates = find_labels_and_jumps(lines)

    for line_idx, condition, target in jp_candidates:
        if target not in label_positions:
            continue

        # JR only supports Z, NZ, C, NC conditions (not P, M, PE, PO)
        if condition and condition not in ('Z', 'NZ', 'C', 'NC'):
            continue

        target_line = label_positions[target]

        # Calculate byte offset
        offset = calculate_byte_offset(lines, line_idx, target_line)
        if offset is None:
            continue

        # Adjust for JR being 2 bytes vs JP being 3 bytes
        if offset > 0:
            jr_offset = offset - 2  # Forward jump
        else:
            jr_offset = offset + 1  # Backward jump

        # Check if in JR range
        if jr_offset < -128 or jr_offset > 127:
            continue

        # Convert JP to JR
        old_line = result[line_idx]
        indent = len(old_line) - len(old_line.lstrip())

        if condition:
            new_instr = f'JR\t{condition},{target}'
        else:
            new_instr = f'JR\t{target}'

        # Preserve any trailing comment
        comment = ''
        if ';' in old_line:
            comment_idx = old_line.index(';')
            comment = '\t' + old_line[comment_idx:].strip()

        result[line_idx] = '\t' + new_instr + comment + '\n'
        total_savings += 1

    if verbose and total_savings > 0:
        print(f"  JP->JR conversions: {total_savings} ({total_savings} bytes saved)")

    return result, total_savings


def eliminate_jump_to_next(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Remove jumps that go to the immediately following instruction.

    Pattern: JP label / label: -> label:
    """
    result = []
    total_savings = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check for JP to label
        m = re.match(r'^JP\s+([A-Za-z@$?_][A-Za-z0-9@$?_]*)$', stripped)
        if m and i + 1 < len(lines):
            target = m.group(1)
            next_line = lines[i + 1].strip()

            # Check if next line is the target label
            if next_line == f'{target}:' or next_line.startswith(f'{target}:'):
                # Skip the JP, keep the label
                i += 1
                total_savings += 3  # JP is 3 bytes
                continue

        result.append(line)
        i += 1

    if verbose and total_savings > 0:
        print(f"  Jump-to-next elimination: {total_savings} bytes saved")

    return result, total_savings


def eliminate_dead_code(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Remove unreachable code after unconditional jumps or returns.

    Pattern: JP/JR/RET followed by non-label instruction -> remove instruction
    """
    result = []
    total_savings = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        result.append(line)

        # Check for unconditional control flow
        is_unconditional = False
        if re.match(r'^(JP|JR)\s+[A-Za-z@$?_]', stripped):
            # Make sure it's not conditional
            if not re.match(r'^(JP|JR)\s+(Z|NZ|C|NC|PE|PO|P|M),', stripped):
                is_unconditional = True
        elif stripped == 'RET':
            is_unconditional = True

        if is_unconditional:
            # Skip following non-label instructions until we hit a label
            i += 1
            while i < len(lines):
                next_line = lines[i]
                next_stripped = next_line.strip()

                # Keep empty lines and comments
                if not next_stripped or next_stripped.startswith(';'):
                    result.append(next_line)
                    i += 1
                    continue

                # Keep labels
                if next_stripped.endswith(':'):
                    break

                # Keep directives (INCLUDE, EQU, assembler directives)
                if (next_stripped.startswith('.') or 'EQU' in next_stripped or
                    next_stripped.startswith('INCLUDE') or next_stripped.startswith('EXTRN') or
                    next_stripped.startswith('PUBLIC') or next_stripped.startswith('CSEG') or
                    next_stripped.startswith('DSEG') or next_stripped == 'END'):
                    result.append(next_line)
                    i += 1
                    continue

                # Skip this unreachable instruction
                size = get_instr_size(next_stripped)
                if size:
                    total_savings += size
                i += 1
            continue

        i += 1

    if verbose and total_savings > 0:
        print(f"  Dead code elimination: {total_savings} bytes saved")

    return result, total_savings


def register_tracking_pass(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Track register contents and eliminate redundant loads.

    Tracks what value is in each register and removes loads that
    would load the same value that's already there.
    """
    result = []
    total_savings = 0

    # Track register contents: reg -> value description (or None if unknown)
    regs = {'A': None, 'B': None, 'C': None, 'D': None, 'E': None, 'H': None, 'L': None}
    hl_value = None  # Track HL as a pair for LHLD/SHLD

    def invalidate_all():
        nonlocal hl_value
        for r in regs:
            regs[r] = None
        hl_value = None

    def invalidate_hl():
        nonlocal hl_value
        regs['H'] = None
        regs['L'] = None
        hl_value = None

    def invalidate_de():
        regs['D'] = None
        regs['E'] = None

    def invalidate_bc():
        regs['B'] = None
        regs['C'] = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith(';'):
            result.append(line)
            i += 1
            continue

        # Labels invalidate tracking (could be jump target)
        if stripped.endswith(':'):
            invalidate_all()
            result.append(line)
            i += 1
            continue

        # Skip directives
        if (stripped.startswith('.') or stripped.startswith('INCLUDE') or
            stripped.startswith('EXTRN') or stripped.startswith('PUBLIC') or
            'EQU' in stripped or stripped.startswith('DS') or
            stripped.startswith('DW') or stripped.startswith('DB')):
            result.append(line)
            i += 1
            continue

        # Parse instruction
        parts = stripped.split(None, 1)
        if not parts:
            result.append(line)
            i += 1
            continue

        opcode = parts[0].upper()
        operands = parts[1].split(';')[0].strip() if len(parts) > 1 else ''

        # Control flow invalidates all tracking
        if opcode in ('JP', 'JR', 'CALL', 'RET', 'DJNZ', 'RST'):
            invalidate_all()
            result.append(line)
            i += 1
            continue

        # LD A,(addr) - track A value
        m = re.match(r'^LD\s+A,\(([^)]+)\)$', stripped)
        if m:
            addr = m.group(1)
            if regs['A'] == f'mem:{addr}':
                # Already have this value - skip
                total_savings += 3  # LD A,(addr) is 3 bytes
                i += 1
                continue
            regs['A'] = f'mem:{addr}'
            result.append(line)
            i += 1
            continue

        # LD (addr),A - A unchanged, track that memory has A's value
        m = re.match(r'^LD\s+\(([^)]+)\),A$', stripped)
        if m:
            result.append(line)
            i += 1
            continue

        # LD HL,(addr) - track HL value
        m = re.match(r'^LD\s+HL,\(([^)]+)\)$', stripped)
        if m:
            addr = m.group(1)
            if hl_value == f'mem:{addr}':
                # Already have this value - skip
                total_savings += 3  # LD HL,(addr) is 3 bytes
                i += 1
                continue
            hl_value = f'mem:{addr}'
            regs['H'] = None
            regs['L'] = None
            result.append(line)
            i += 1
            continue

        # LD (addr),HL - HL unchanged
        m = re.match(r'^LD\s+\(([^)]+)\),HL$', stripped)
        if m:
            addr = m.group(1)
            hl_value = f'mem:{addr}'  # HL still has this value
            result.append(line)
            i += 1
            continue

        # LD HL,const - track HL value
        m = re.match(r'^LD\s+HL,([^(].*)$', stripped)
        if m:
            const = m.group(1).strip()
            if hl_value == f'const:{const}':
                # Already have this value - skip
                total_savings += 3  # LD HL,nn is 3 bytes
                i += 1
                continue
            hl_value = f'const:{const}'
            regs['H'] = None
            regs['L'] = None
            result.append(line)
            i += 1
            continue

        # LD DE,const or LD BC,const
        m = re.match(r'^LD\s+(DE|BC),(.+)$', stripped)
        if m:
            pair, const = m.group(1), m.group(2).strip()
            if pair == 'DE':
                invalidate_de()
            else:
                invalidate_bc()
            result.append(line)
            i += 1
            continue

        # LD r,n - track register value
        m = re.match(r'^LD\s+([ABCDEHL]),(\d+|0[0-9A-Fa-f]+H?)$', stripped)
        if m:
            reg, val = m.group(1), m.group(2)
            if regs[reg] == f'const:{val}':
                # Already have this value - skip
                total_savings += 2  # LD r,n is 2 bytes
                i += 1
                continue
            regs[reg] = f'const:{val}'
            if reg in ('H', 'L'):
                hl_value = None
            result.append(line)
            i += 1
            continue

        # LD r,r - track register copy
        m = re.match(r'^LD\s+([ABCDEHL]),([ABCDEHL])$', stripped)
        if m:
            dst, src = m.group(1), m.group(2)
            if dst == src:
                # LD A,A etc - no-op, skip
                total_savings += 1
                i += 1
                continue
            if regs[dst] == regs[src] and regs[dst] is not None:
                # Same value - skip
                total_savings += 1
                i += 1
                continue
            regs[dst] = regs[src]
            if dst in ('H', 'L'):
                hl_value = None
            result.append(line)
            i += 1
            continue

        # Instructions that modify A
        if opcode in ('ADD', 'ADC', 'SUB', 'SBC', 'AND', 'OR', 'XOR', 'CP'):
            regs['A'] = None
            result.append(line)
            i += 1
            continue

        # INC/DEC register
        m = re.match(r'^(INC|DEC)\s+([ABCDEHL])$', stripped)
        if m:
            reg = m.group(2)
            regs[reg] = None
            if reg in ('H', 'L'):
                hl_value = None
            result.append(line)
            i += 1
            continue

        # INC/DEC register pair
        m = re.match(r'^(INC|DEC)\s+(HL|DE|BC|SP)$', stripped)
        if m:
            pair = m.group(2)
            if pair == 'HL':
                invalidate_hl()
            elif pair == 'DE':
                invalidate_de()
            elif pair == 'BC':
                invalidate_bc()
            result.append(line)
            i += 1
            continue

        # ADD HL,rr
        if opcode == 'ADD' and operands.startswith('HL,'):
            invalidate_hl()
            result.append(line)
            i += 1
            continue

        # EX DE,HL - swap tracking
        if stripped == 'EX\tDE,HL' or stripped == 'EX DE,HL':
            hl_value = None  # Simplify - just invalidate
            regs['H'], regs['D'] = regs['D'], regs['H']
            regs['L'], regs['E'] = regs['E'], regs['L']
            result.append(line)
            i += 1
            continue

        # PUSH/POP
        if opcode == 'POP':
            if operands == 'HL':
                invalidate_hl()
            elif operands == 'DE':
                invalidate_de()
            elif operands == 'BC':
                invalidate_bc()
            elif operands == 'AF':
                regs['A'] = None
            result.append(line)
            i += 1
            continue

        # Rotates modify A
        if opcode in ('RLCA', 'RRCA', 'RLA', 'RRA'):
            regs['A'] = None
            result.append(line)
            i += 1
            continue

        # CPL, DAA, NEG modify A
        if opcode in ('CPL', 'DAA', 'NEG'):
            regs['A'] = None
            result.append(line)
            i += 1
            continue

        # Default: keep the instruction
        result.append(line)
        i += 1

    if verbose and total_savings > 0:
        print(f"  Register tracking: {total_savings} bytes saved")

    return result, total_savings


def print_combining_pass(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Combine CALL print_i16 / CALL print_nl into CALL print_i16_nl

    This saves 3 bytes per occurrence (6 bytes -> 3 bytes).
    """
    result = []
    total_savings = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Check for CALL print_i16 followed by CALL print_nl
        if stripped == 'CALL\tprint_i16':
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].strip()
                if next_stripped == 'CALL\tprint_nl':
                    result.append('\tCALL\tprint_i16_nl\n')
                    total_savings += 3  # 6 bytes -> 3 bytes
                    i += 2
                    continue

        # Also check for JP print_nl after CALL print_i16 (tail calls)
        if stripped == 'CALL\tprint_i16':
            if i + 1 < len(lines):
                next_stripped = lines[i + 1].strip()
                if next_stripped == 'JP\tprint_nl':
                    result.append('\tJP\tprint_i16_nl\n')
                    total_savings += 3  # 6 bytes -> 3 bytes
                    i += 2
                    continue

        result.append(line)
        i += 1

    if verbose and total_savings > 0:
        print(f"  Print combining: {total_savings} bytes saved")

    return result, total_savings


def print_a_combining_pass(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Combine LD L,A / LD H,0 / CALL print_i16_nl into CALL print_a_nl

    This saves 3 bytes per occurrence (6 bytes -> 3 bytes).
    """
    result = []
    total_savings = 0
    i = 0

    while i < len(lines):
        # Check for LD L,A / LD H,0 / CALL print_i16_nl
        if (i + 2 < len(lines) and
            lines[i].strip() == 'LD\tL,A' and
            lines[i+1].strip() == 'LD\tH,0' and
            lines[i+2].strip() == 'CALL\tprint_i16_nl'):
            result.append('\tCALL\tprint_a_nl\n')
            total_savings += 3  # 6 bytes -> 3 bytes
            i += 3
            continue

        # Check for LD L,A / LD H,0 / JP print_i16_nl (tail calls)
        if (i + 2 < len(lines) and
            lines[i].strip() == 'LD\tL,A' and
            lines[i+1].strip() == 'LD\tH,0' and
            lines[i+2].strip() == 'JP\tprint_i16_nl'):
            result.append('\tJP\tprint_a_nl\n')
            total_savings += 3  # 6 bytes -> 3 bytes
            i += 3
            continue

        result.append(lines[i])
        i += 1

    if verbose and total_savings > 0:
        print(f"  Print A combining: {total_savings} bytes saved")

    return result, total_savings


def print_de_combining_pass(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Combine EX DE,HL / CALL print_i16_nl into CALL print_de_nl

    This saves 2 bytes per occurrence (4 bytes -> 3 bytes).
    """
    result = []
    total_savings = 0
    i = 0

    while i < len(lines):
        # Check for EX DE,HL / CALL print_i16_nl
        if (i + 1 < len(lines) and
            lines[i].strip() in ('EX\tDE,HL', 'EX DE,HL') and
            lines[i+1].strip() == 'CALL\tprint_i16_nl'):
            result.append('\tCALL\tprint_de_nl\n')
            total_savings += 1  # 4 bytes -> 3 bytes (EX=1, CALL=3 -> CALL=3)
            i += 2
            continue

        # Check for EX DE,HL / JP print_i16_nl (tail calls)
        if (i + 1 < len(lines) and
            lines[i].strip() in ('EX\tDE,HL', 'EX DE,HL') and
            lines[i+1].strip() == 'JP\tprint_i16_nl'):
            result.append('\tJP\tprint_de_nl\n')
            total_savings += 1  # 4 bytes -> 3 bytes
            i += 2
            continue

        result.append(lines[i])
        i += 1

    if verbose and total_savings > 0:
        print(f"  Print DE combining: {total_savings} bytes saved")

    return result, total_savings


def address_folding_pass(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Fold address calculations: LD HL,addr / INC HL / INC HL -> LD HL,addr+2

    This is common for record field access.
    """
    result = []
    total_savings = 0
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Look for LD HL,symbol (not LD HL,(symbol))
        m = re.match(r'LD\s+HL,([A-Za-z_][A-Za-z0-9_]*)$', stripped)
        if m:
            addr = m.group(1)
            # Count consecutive INC HL
            inc_count = 0
            j = i + 1
            while j < len(lines) and lines[j].strip() == 'INC\tHL':
                inc_count += 1
                j += 1

            # If 2+ INC HL, fold into address
            if inc_count >= 2:
                result.append(f'\tLD\tHL,{addr}+{inc_count}\n')
                # Each INC HL is 1 byte, LD HL,addr+n is same 3 bytes as LD HL,addr
                # So we save inc_count bytes
                total_savings += inc_count  # INC HL is 1 byte each
                i = j
                continue
            elif inc_count == 1:
                # Just 1 INC - not worth changing (LD HL,addr+1 is same size)
                # But we still emit the original
                pass

        result.append(line)
        i += 1

    if verbose and total_savings > 0:
        print(f"  Address folding: {total_savings} bytes saved")

    return result, total_savings


def dead_store_elimination(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Remove dead stores (stores to variables that are immediately overwritten).

    Pattern: LD (var),r followed by LD (var),r without intervening read of var
    """
    result = []
    total_savings = 0

    # Track pending stores: var -> (line_index, size_bytes)
    pending_stores = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith(';'):
            result.append(line)
            i += 1
            continue

        # Labels and control flow reset tracking
        if (stripped.endswith(':') or stripped.startswith('JP') or
            stripped.startswith('JR') or stripped.startswith('CALL') or
            stripped == 'RET' or stripped.startswith('DJNZ')):
            pending_stores = {}
            result.append(line)
            i += 1
            continue

        # Check for store to memory: LD (var),HL or LD (var),A
        store_match = re.match(r'LD\s+\(([^)]+)\),(HL|A)$', stripped)
        if store_match:
            var = store_match.group(1)
            reg = store_match.group(2)
            size = 3 if reg == 'HL' else 3  # LD (nn),HL = 3, LD (nn),A = 3

            if var in pending_stores:
                # Previous store to same var - it's dead!
                prev_idx, prev_size = pending_stores[var]
                # Remove the previous store from result
                # Find and remove it
                for j in range(len(result) - 1, -1, -1):
                    if result[j].strip().startswith(f'LD\t({var})'):
                        result.pop(j)
                        total_savings += prev_size
                        break

            # Record this store as pending
            pending_stores[var] = (len(result), size)
            result.append(line)
            i += 1
            continue

        # Check for read from memory: LD r,(var) or LD HL,(var)
        read_match = re.match(r'LD\s+(A|HL|DE|BC),\(([^)]+)\)$', stripped)
        if read_match:
            var = read_match.group(2)
            # This var is read - remove from pending (store was not dead)
            if var in pending_stores:
                del pending_stores[var]
            result.append(line)
            i += 1
            continue

        # Any other instruction that might use memory indirectly
        # For safety, clear all pending stores on complex instructions
        if 'LDIR' in stripped or 'LDDR' in stripped or '(HL)' in stripped:
            pending_stores = {}

        result.append(line)
        i += 1

    if verbose and total_savings > 0:
        print(f"  Dead store elimination: {total_savings} bytes saved")

    return result, total_savings


def tail_merging_pass(lines: list[str], verbose: bool = False) -> tuple[list[str], int]:
    """
    Merge common instruction sequences at the end of multiple code paths.

    Pattern: If multiple labels are followed by identical RET sequences,
    have earlier ones JP to the last one.

    Also handles: identical trailing sequences before RET
    """
    result = []
    total_savings = 0

    # First pass: find all subroutine end sequences (ending in RET)
    # Map: sequence hash -> list of (start_line_idx, sequence_lines)
    ret_sequences = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Look for RET (end of subroutine)
        if stripped == 'RET':
            # Look back for instructions that could be merged
            # Start with just RET, then look for common prefixes
            seq = [stripped]
            start_idx = i

            # Look back for simple instructions that could be part of common tail
            j = i - 1
            while j >= 0:
                prev = lines[j].strip()
                # Stop at labels, comments, empty lines, or control flow
                if not prev or prev.startswith(';') or prev.endswith(':'):
                    break
                if prev.startswith('JP') or prev.startswith('JR') or prev.startswith('CALL'):
                    break
                # Include simple instructions in sequence
                if (prev.startswith('LD') or prev.startswith('PUSH') or
                    prev.startswith('POP') or prev == 'CALL print_nl' or
                    prev.startswith('INC') or prev.startswith('DEC') or
                    prev.startswith('ADD') or prev.startswith('EX')):
                    seq.insert(0, prev)
                    start_idx = j
                    j -= 1
                else:
                    break

            # Only consider sequences of 2+ instructions
            if len(seq) >= 2:
                seq_key = tuple(seq)
                if seq_key not in ret_sequences:
                    ret_sequences[seq_key] = []
                ret_sequences[seq_key].append((start_idx, i))  # start and end line indices

        i += 1

    # Find sequences that appear multiple times
    merged_ranges = set()
    label_counter = 0

    for seq_key, occurrences in ret_sequences.items():
        if len(occurrences) < 2:
            continue

        # Calculate savings: (n-1) * (seq_bytes - 3) where 3 is JP instruction
        seq_size = sum(get_instr_size(instr) or 2 for instr in seq_key)
        savings_per_merge = seq_size - 3  # JP is 3 bytes

        if savings_per_merge <= 0:
            continue

        # Keep the last occurrence, replace others with JP
        last_start, last_end = occurrences[-1]

        for start_idx, end_idx in occurrences[:-1]:
            # Mark these ranges for modification
            merged_ranges.add((start_idx, end_idx, last_start))
            total_savings += savings_per_merge

        label_counter += 1

    # If no merging opportunities, return unchanged
    if not merged_ranges:
        return lines, 0

    # Second pass: build result with merges applied
    # For simplicity, we'll insert labels at merge targets

    # Find the merge targets (last occurrence of each sequence)
    merge_targets = {}  # start_idx -> label name
    for start_idx, end_idx, target_start in merged_ranges:
        if target_start not in merge_targets:
            # Find a unique label name
            # Look for existing label before target
            j = target_start - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j >= 0 and lines[j].strip().endswith(':'):
                label = lines[j].strip()[:-1]
            else:
                label = f'_tail{len(merge_targets)}'
            merge_targets[target_start] = label

    # Rebuild output
    i = 0
    skip_until = -1

    while i < len(lines):
        # Check if we're in a range to be replaced with JP
        in_merged = False
        for start_idx, end_idx, target_start in merged_ranges:
            if start_idx <= i <= end_idx:
                if i == start_idx:
                    # Replace with JP to the merged tail
                    label = merge_targets[target_start]
                    result.append(f'\tJP\t{label}\n')
                # Skip the rest of the merged sequence
                in_merged = True
                break

        if in_merged:
            # Check if this is the end of a merged range
            for start_idx, end_idx, target_start in merged_ranges:
                if i == end_idx:
                    break
            i += 1
            continue

        # Check if we need to add a label at a merge target
        if i in merge_targets:
            label = merge_targets[i]
            # Check if there's already a label
            if not lines[i].strip().endswith(':'):
                result.append(f'{label}:\n')

        result.append(lines[i])
        i += 1

    if verbose and total_savings > 0:
        print(f"  Tail merging: {total_savings} bytes saved")

    return result, total_savings


def optimize_asm(asm_code: str, verbose: bool = False) -> tuple[str, int]:
    """
    Run post-assembly optimizations on assembly code string.

    Returns (optimized_code, total_bytes_saved).
    """
    lines = asm_code.splitlines(keepends=True)
    # Ensure all lines have newlines
    lines = [line if line.endswith('\n') else line + '\n' for line in lines]

    total_savings = 0

    # Pass 1: Register tracking to eliminate redundant loads
    lines, savings = register_tracking_pass(lines, verbose)
    total_savings += savings

    # Pass 2: Eliminate jumps to next instruction
    lines, savings = eliminate_jump_to_next(lines, verbose)
    total_savings += savings

    # Pass 3: Eliminate dead code after unconditional jumps
    lines, savings = eliminate_dead_code(lines, verbose)
    total_savings += savings

    # Pass 4: Convert JP to JR where possible
    lines, savings = convert_jp_to_jr(lines, verbose)
    total_savings += savings

    # Pass 5: Tail merging (merge common code sequences)
    lines, savings = tail_merging_pass(lines, verbose)
    total_savings += savings

    # Pass 6: Dead store elimination
    lines, savings = dead_store_elimination(lines, verbose)
    total_savings += savings

    # Pass 7: Address folding (LD HL,addr / INC HL * n -> LD HL,addr+n)
    lines, savings = address_folding_pass(lines, verbose)
    total_savings += savings

    # Pass 8: Combine print_i16 + print_nl into print_i16_nl
    lines, savings = print_combining_pass(lines, verbose)
    total_savings += savings

    # Pass 9: Combine LD L,A / LD H,0 / CALL print_i16_nl into CALL print_a_nl
    lines, savings = print_a_combining_pass(lines, verbose)
    total_savings += savings

    # Pass 10: Combine EX DE,HL / CALL print_i16_nl into CALL print_de_nl
    lines, savings = print_de_combining_pass(lines, verbose)
    total_savings += savings

    return ''.join(lines), total_savings


def optimize_file(input_path: str, output_path: Optional[str] = None,
                  verbose: bool = False) -> int:
    """
    Run post-assembly optimizations on a .mac file.

    Returns total bytes saved.
    """
    with open(input_path, 'r') as f:
        asm_code = f.read()

    optimized, savings = optimize_asm(asm_code, verbose)

    # Write output
    if output_path is None:
        output_path = input_path

    with open(output_path, 'w') as f:
        f.write(optimized)

    return savings


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m src.postopt input.mac [output.mac]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Post-assembly optimization: {input_path}")
    savings = optimize_file(input_path, output_path, verbose=True)
    print(f"Total savings: {savings} bytes")
