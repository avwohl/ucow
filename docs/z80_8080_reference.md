# Z80 vs 8080 Instruction Reference

This document compares Z80 and 8080 instructions for code generation decisions.
**Key insight**: Z80 mnemonics don't always mean smaller/faster code!

## Notation
- Bytes: instruction size in bytes
- T: T-states (clock cycles)
- 8080 cycles shown in parentheses when different

## Data Movement

| Operation | 8080 | B | T | Z80 | B | T | Notes |
|-----------|------|---|---|-----|---|---|-------|
| Load reg from reg | MOV r,r | 1 | 5 | LD r,r | 1 | 4 | Z80 faster |
| Load reg immediate | MVI r,n | 2 | 7 | LD r,n | 2 | 7 | Same |
| Load A from memory | LDA addr | 3 | 13 | LD A,(addr) | 3 | 13 | Same |
| Store A to memory | STA addr | 3 | 13 | LD (addr),A | 3 | 13 | Same |
| Load HL from memory | LHLD addr | 3 | 16 | LD HL,(addr) | 3 | 16 | Same |
| Store HL to memory | SHLD addr | 3 | 16 | LD (addr),HL | 3 | 16 | Same |
| Load reg pair imm | LXI rp,nn | 3 | 10 | LD rp,nn | 3 | 10 | Same |
| Load A from (HL) | MOV A,M | 1 | 7 | LD A,(HL) | 1 | 7 | Same |
| Store A to (HL) | MOV M,A | 1 | 7 | LD (HL),A | 1 | 7 | Same |
| Load A from (BC) | LDAX B | 1 | 7 | LD A,(BC) | 1 | 7 | Same |
| Load A from (DE) | LDAX D | 1 | 7 | LD A,(DE) | 1 | 7 | Same |
| Exchange DE,HL | XCHG | 1 | 4 | EX DE,HL | 1 | 4 | Same |

## 16-bit Loads (Z80 extended)

| Operation | Z80 | B | T | 8080 equiv | B | T | Notes |
|-----------|-----|---|---|------------|---|---|-------|
| LD BC,(addr) | ED 4B | 4 | 20 | LHLD+moves | 7 | 30 | Z80 better |
| LD DE,(addr) | ED 5B | 4 | 20 | LHLD+XCHG | 4 | 20 | Same |
| LD (addr),BC | ED 43 | 4 | 20 | moves+SHLD | 7 | 30 | Z80 better |
| LD (addr),DE | ED 53 | 4 | 20 | XCHG+SHLD | 4 | 20 | Same |
| LD SP,(addr) | ED 7B | 4 | 20 | LHLD+SPHL | 4 | 22 | Z80 slightly better |

## Arithmetic (8-bit)

| Operation | 8080 | B | T | Z80 | B | T | Notes |
|-----------|------|---|---|-----|---|---|-------|
| ADD r | ADD r | 1 | 4 | ADD A,r | 1 | 4 | Same |
| ADD immediate | ADI n | 2 | 7 | ADD A,n | 2 | 7 | Same |
| SUB r | SUB r | 1 | 4 | SUB r | 1 | 4 | Same |
| SUB immediate | SUI n | 2 | 7 | SUB n | 2 | 7 | Same |
| INC r | INR r | 1 | 5 | INC r | 1 | 4 | Z80 faster |
| DEC r | DCR r | 1 | 5 | DEC r | 1 | 4 | Z80 faster |
| Compare r | CMP r | 1 | 4 | CP r | 1 | 4 | Same |
| Compare imm | CPI n | 2 | 7 | CP n | 2 | 7 | Same |
| Complement A | CMA | 1 | 4 | CPL | 1 | 4 | Same |
| Negate A | (none) | - | - | NEG | 2 | 8 | Z80 only |

## Arithmetic (16-bit)

| Operation | 8080 | B | T | Z80 | B | T | Notes |
|-----------|------|---|---|-----|---|---|-------|
| ADD HL,rp | DAD rp | 1 | 10 | ADD HL,rp | 1 | 11 | 8080 faster! |
| INC rp | INX rp | 1 | 5 | INC rp | 1 | 6 | 8080 faster! |
| DEC rp | DCX rp | 1 | 5 | DEC rp | 1 | 6 | 8080 faster! |
| ADC HL,rp | (none) | - | - | ADC HL,rp | 2 | 15 | Z80 only |
| SBC HL,rp | (none) | - | - | SBC HL,rp | 2 | 15 | Z80 only |

## Logic

| Operation | 8080 | B | T | Z80 | B | T | Notes |
|-----------|------|---|---|-----|---|---|-------|
| AND r | ANA r | 1 | 4 | AND r | 1 | 4 | Same |
| AND immediate | ANI n | 2 | 7 | AND n | 2 | 7 | Same |
| OR r | ORA r | 1 | 4 | OR r | 1 | 4 | Same |
| OR immediate | ORI n | 2 | 7 | OR n | 2 | 7 | Same |
| XOR r | XRA r | 1 | 4 | XOR r | 1 | 4 | Same |
| XOR immediate | XRI n | 2 | 7 | XOR n | 2 | 7 | Same |

## Rotate/Shift

| Operation | 8080 | B | T | Z80 | B | T | Notes |
|-----------|------|---|---|-----|---|---|-------|
| Rotate A left | RLC | 1 | 4 | RLCA | 1 | 4 | Same |
| Rotate A right | RRC | 1 | 4 | RRCA | 1 | 4 | Same |
| Rotate A left thru C | RAL | 1 | 4 | RLA | 1 | 4 | Same |
| Rotate A right thru C | RAR | 1 | 4 | RRA | 1 | 4 | Same |
| Shift left arith | (none) | - | - | SLA r | 2 | 8 | Z80 only |
| Shift right arith | (none) | - | - | SRA r | 2 | 8 | Z80 only |
| Shift right logic | (none) | - | - | SRL r | 2 | 8 | Z80 only |
| Rotate reg left | (none) | - | - | RL r | 2 | 8 | Z80 only |
| Rotate reg right | (none) | - | - | RR r | 2 | 8 | Z80 only |

## Jumps

| Operation | 8080 | B | T | Z80 | B | T | Notes |
|-----------|------|---|---|-----|---|---|-------|
| Jump unconditional | JMP addr | 3 | 10 | JP addr | 3 | 10 | Same |
| Jump conditional | Jcc addr | 3 | 10 | JP cc,addr | 3 | 10 | Same |
| Jump relative | (none) | - | - | JR e | 2 | 12 | Z80 only, slower! |
| Jump rel cond | (none) | - | - | JR cc,e | 2 | 12/7 | Z80 only |
| Jump to HL | PCHL | 1 | 5 | JP (HL) | 1 | 4 | Z80 faster |
| Decrement & jump | (none) | - | - | DJNZ e | 2 | 13/8 | Z80 only |

**Important**: JR is 2 bytes but 12 cycles (vs JP's 10 cycles). Only saves 1 byte, costs 2 cycles!
DJNZ is great for loops: 2 bytes vs DCR B + JNZ (4 bytes)

## Calls/Returns

| Operation | 8080 | B | T | Z80 | B | T | Notes |
|-----------|------|---|---|-----|---|---|-------|
| Call | CALL addr | 3 | 17 | CALL addr | 3 | 17 | Same |
| Call conditional | Ccc addr | 3 | 17/11 | CALL cc,addr | 3 | 17/10 | Similar |
| Return | RET | 1 | 10 | RET | 1 | 10 | Same |
| Return conditional | Rcc | 1 | 11/5 | RET cc | 1 | 11/5 | Same |

## Stack

| Operation | 8080 | B | T | Z80 | B | T | Notes |
|-----------|------|---|---|-----|---|---|-------|
| PUSH rp | PUSH rp | 1 | 11 | PUSH rp | 1 | 11 | Same |
| POP rp | POP rp | 1 | 10 | POP rp | 1 | 10 | Same |
| PUSH AF | PUSH PSW | 1 | 11 | PUSH AF | 1 | 11 | Same |
| POP AF | POP PSW | 1 | 10 | POP AF | 1 | 10 | Same |
| EX (SP),HL | XTHL | 1 | 18 | EX (SP),HL | 1 | 19 | 8080 faster! |

## Block Operations (Z80 only)

| Operation | Z80 | B | T | 8080 equiv | Notes |
|-----------|-----|---|---|------------|-------|
| LDI | ED A0 | 2 | 16 | ~6 bytes | Load, inc, dec BC |
| LDIR | ED B0 | 2 | 21/16 | loop | Block copy |
| LDD | ED A8 | 2 | 16 | ~6 bytes | Load, dec, dec BC |
| LDDR | ED B8 | 2 | 21/16 | loop | Block copy backward |
| CPI | ED A1 | 2 | 16 | ~8 bytes | Compare, inc |
| CPIR | ED B1 | 2 | 21/16 | loop | Block search |

**LDIR** is very efficient for memory copies but has overhead per byte.
For small copies (< 4 bytes), unrolled LDI or individual moves may be better.

## Bit Operations (Z80 only)

| Operation | Z80 | B | T | 8080 equiv | Notes |
|-----------|-----|---|---|------------|-------|
| BIT b,r | CB xx | 2 | 8 | ANI + mask | Test bit |
| SET b,r | CB xx | 2 | 8 | ORI + mask | Set bit |
| RES b,r | CB xx | 2 | 8 | ANI + ~mask | Reset bit |
| BIT b,(HL) | CB xx | 2 | 12 | MOV+ANI | Test bit in memory |
| SET b,(HL) | CB xx | 2 | 15 | MOV+ORI+MOV | Set bit in memory |
| RES b,(HL) | CB xx | 2 | 15 | MOV+ANI+MOV | Reset bit in memory |

## Index Registers (Z80 only - IX, IY)

Generally AVOID for generated code:
- All IX/IY instructions have DD/FD prefix = +1 byte, +4 cycles minimum
- LD A,(IX+d) = 3 bytes, 19 cycles vs LD A,(HL) = 1 byte, 7 cycles
- Only useful for accessing stack frames or fixed structures

## Code Generation Guidelines

### Always prefer Z80:
- NEG for negation (2 bytes vs 4 for CMA+INR A... wait, that's 2 bytes too!)
- DJNZ for counted loops (2 bytes vs 4 for DCR+JNZ)
- Block ops (LDIR etc) for copies > 4 bytes
- Bit operations when testing specific bits
- SLA/SRA/SRL for shifts (cleaner than rotate sequences)

### Prefer 8080 style:
- DAD over ADD HL (10 vs 11 cycles)
- INX/DCX over INC/DEC rp (5 vs 6 cycles)
- XTHL over EX (SP),HL (18 vs 19 cycles)
- JP over JR when speed matters (10 vs 12 cycles)

### Situational:
- JR saves 1 byte but costs 2 cycles - use for size optimization only
- LDIR: great for large copies, overhead hurts small copies
- IX/IY: avoid unless accessing complex stack frames

## Register Names

| 8080 | Z80 |
|------|-----|
| A | A |
| B | B |
| C | C |
| D | D |
| E | E |
| H | H |
| L | L |
| M | (HL) |
| SP | SP |
| PSW | AF |

## Condition Codes

| 8080 | Z80 | Meaning |
|------|-----|---------|
| Z | Z | Zero |
| NZ | NZ | Not zero |
| C | C | Carry |
| NC | NC | No carry |
| P | P | Positive (sign=0) |
| M | M | Minus (sign=1) |
| PE | PE | Parity even |
| PO | PO | Parity odd |
