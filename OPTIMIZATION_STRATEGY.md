# ucow Optimization Strategy

Target: 8080/Z80 via um80 assembler → ul80 linker → cpmemu for testing

## Why We Can Do Better Than Self-Hosted Cowgol

The self-hosted Cowgol compiler is **~70KB** running on 8-bit machines. It must:
- Parse in a single pass (forward declarations required)
- Minimize memory for symbol tables and intermediate representation
- Use conservative algorithms that don't require full program analysis

With Python on Linux, we have:
- Gigabytes of RAM for full AST, CFG, and dataflow structures
- Multi-pass compilation
- Whole-program analysis
- Modern algorithms (SSA, graph coloring, etc.)

## Cowgol Properties That Help Optimization

| Property | Optimization Benefit |
|----------|---------------------|
| **No recursion** | Perfect static allocation, trivial call graph, safe inlining |
| **Strong typing** | No type inference/rebinding needed |
| **No heap allocation** | Whole-program memory layout at compile time |
| **Bounded arrays** | Compile-time bounds, loop trip counts |
| **Explicit casts** | No implicit conversions to track |
| **Static variables** | All memory addresses known at compile time |

## Target Architecture: 8080/Z80

### 8080 Register Set
```
A        - 8-bit accumulator (most operations)
B, C     - 8-bit, BC = 16-bit pair
D, E     - 8-bit, DE = 16-bit pair
H, L     - 8-bit, HL = 16-bit pair (memory pointer)
SP       - Stack pointer
Flags    - S, Z, AC, P, CY
```

### Key 8080 Constraints
- Only A can do most arithmetic
- HL is the primary memory pointer
- No index registers (Z80 adds IX, IY)
- 16-bit operations limited (INX, DCX, DAD)
- No multiply/divide instructions

### Cycle Costs (8080)
| Operation | Cycles |
|-----------|--------|
| MOV r,r | 5 |
| MVI r,n | 7 |
| LDA addr | 13 |
| STA addr | 13 |
| ADD r | 4 |
| ADI n | 7 |
| CALL | 17 |
| RET | 10 |
| JMP | 10 |
| JZ/JNZ | 10 |

---

## Phase 1: Foundation (No Optimization)

### 1.1 Lexer & Parser
- Full AST construction (not single-pass)
- Type checking and validation
- Symbol table with scope tracking

### 1.2 Basic Code Generation
- Naive stack-based code gen
- All variables in memory
- All operations through A register
- Function calls via CALL/RET

### 1.3 Testing Infrastructure
```bash
# Compile
python ucow.py test.cow -o test.mac
# Assemble
um80 test.mac -o test.rel
# Link
ul80 test.rel -o test.com
# Run
cpmemu test.com
```

---

## Phase 2: Essential Optimizations

### 2.1 Constant Folding & Propagation
**Priority: HIGH** | **Complexity: LOW**

Evaluate constant expressions at compile time.

```cowgol
# Before
var x := 10 + 20;
var y := x * 2;

# After
var x := 30;
var y := 60;
```

Multi-pass propagation through the AST.

### 2.2 Dead Code Elimination
**Priority: HIGH** | **Complexity: MEDIUM**

Build CFG, compute reachability, remove unreachable code.

```cowgol
# Before
sub Foo() is
    return;
    print("never");  # dead
end sub;

# After
sub Foo() is
    return;
end sub;
```

### 2.3 Common Subexpression Elimination (CSE)
**Priority: HIGH** | **Complexity: MEDIUM**

Track available expressions, reuse computed values.

```cowgol
# Before
var a := x + y;
var b := x + y;  # redundant

# After
var a := x + y;
var b := a;
```

For 8080, this is critical - arithmetic is expensive.

### 2.4 Strength Reduction
**Priority: HIGH** | **Complexity: LOW**

Replace expensive operations with cheaper ones.

| Original | Replacement | Savings |
|----------|-------------|---------|
| `x * 2` | `x + x` | MUL→ADD |
| `x * 4` | `x << 2` | MUL→shifts |
| `x * 2^n` | `x << n` | MUL→shifts |
| `x / 2` | `x >> 1` | DIV→shift |
| `x % 2` | `x & 1` | MOD→AND |

On 8080 without MUL/DIV instructions, this is huge.

---

## Phase 3: Control Flow Optimizations

### 3.1 Branch Optimization
**Priority: MEDIUM** | **Complexity: LOW**

```asm
; Before
    JZ  SKIP
    JMP NEXT
SKIP:
    ...
NEXT:

; After
    JNZ NEXT
    ...
NEXT:
```

- Eliminate jumps to jumps
- Invert conditions to remove unconditional jumps
- Thread jumps through empty blocks

### 3.2 Loop-Invariant Code Motion
**Priority: HIGH** | **Complexity: MEDIUM**

Move computations that don't change inside a loop to outside.

```cowgol
# Before
while i < n loop
    var offset := base + stride;  # invariant
    arr[i] := arr[i] + offset;
    i := i + 1;
end loop;

# After
var offset := base + stride;
while i < n loop
    arr[i] := arr[i] + offset;
    i := i + 1;
end loop;
```

### 3.3 Loop Unrolling (Small Loops)
**Priority: MEDIUM** | **Complexity: MEDIUM**

For loops with known small iteration counts (2-8), unroll completely.

```cowgol
# Before
var i: uint8 := 0;
while i < 4 loop
    arr[i] := 0;
    i := i + 1;
end loop;

# After
arr[0] := 0;
arr[1] := 0;
arr[2] := 0;
arr[3] := 0;
```

Eliminates loop overhead (compare, branch, increment).

---

## Phase 4: Register Allocation

### 4.1 Live Variable Analysis
**Priority: HIGH** | **Complexity: MEDIUM**

Backward dataflow to determine which variables are live at each point.

### 4.2 Register Allocation
**Priority: CRITICAL** | **Complexity: HIGH**

The biggest single optimization for 8080.

**Strategy: Linear Scan** (simpler than graph coloring)

1. Compute live ranges for all variables
2. Sort by start position
3. Allocate registers greedily, spill to memory when needed

**8080 Register Priorities:**
```
HL - pointer operations, array access
DE - secondary pointer, 16-bit values
BC - loop counters, 16-bit values
A  - arithmetic (implicit, always available)
```

**Allocation Rules:**
- Pointers → HL preferred
- Loop counters → BC preferred
- 16-bit arithmetic → DE or BC
- 8-bit temps → B, C, D, E

### 4.3 Register Coalescing
**Priority: MEDIUM** | **Complexity: MEDIUM**

Eliminate unnecessary MOVs by giving source and dest the same register.

```asm
; Before
MOV A, B
MOV C, A   ; A is now dead

; After (allocate B=C)
; eliminated entirely
```

---

## Phase 5: Memory & Data Optimizations

### 5.1 Static Variable Overlay
**Priority: HIGH** | **Complexity: MEDIUM**

Cowgol already does this conservatively. With full call-graph analysis, we can do it optimally.

```
sub A() calls B(), C()
sub B() uses x, y
sub C() uses z, w

# x,y and z,w can share memory (B and C never concurrent)
```

### 5.2 Workspace Minimization
**Priority: MEDIUM** | **Complexity: MEDIUM**

Nested subroutines share workspace. Minimize total workspace by optimal variable packing.

### 5.3 Constant Pooling
**Priority: LOW** | **Complexity: LOW**

Share identical constants in memory.

```cowgol
print("Error");
print("Error");  # share the string
```

---

## Phase 6: Inlining

### 6.1 Subroutine Inlining
**Priority: HIGH** | **Complexity: MEDIUM**

Since Cowgol forbids recursion, we can inline aggressively.

**Inline Criteria:**
- Small subroutines (< 20 instructions)
- Called once
- Called in hot loops
- Leaf functions (no calls)

**Benefits:**
- Eliminates CALL/RET overhead (27 cycles)
- Exposes more optimization opportunities (CSE, constant prop)
- Enables register allocation across call sites

### 6.2 Interface Devirtualization
**Priority: MEDIUM** | **Complexity: MEDIUM**

When an interface variable has only one possible implementation, inline it.

```cowgol
interface Cmp(a: uint8, b: uint8): (r: int8);
sub NumCmp implements Cmp is ... end sub;

var cmp: Cmp := NumCmp;
# If NumCmp is the only implementation, inline calls to cmp()
```

---

## Phase 7: Peephole Optimization

### 7.1 Pattern Matching on Assembly
**Priority: HIGH** | **Complexity: LOW**

After code generation, pattern-match and replace.

| Pattern | Replacement |
|---------|-------------|
| `PUSH r; POP r` | (delete) |
| `MOV A,r; MOV r,A` | (delete second) |
| `LDA addr; STA addr` | (delete STA) |
| `JMP L; L:` | (delete JMP) |
| `XRA A` | (better than `MVI A,0`) |
| `INR A; DCR A` | (delete both) |
| `ADD A` | (same as `RLC` for *2) |

### 7.2 Instruction Selection
**Priority: MEDIUM** | **Complexity: MEDIUM**

Choose optimal instructions during code gen.

```asm
; Load 0 into A
MVI A, 0     ; 7 cycles, 2 bytes
XRA A        ; 4 cycles, 1 byte  ← better

; Compare A to 0
CPI 0        ; 7 cycles, 2 bytes
ORA A        ; 4 cycles, 1 byte  ← better (sets Z flag)

; 16-bit increment
INR L        ; need to handle carry
             ; vs
INX H        ; 5 cycles, 1 byte  ← better
```

---

## Phase 8: Advanced Optimizations (Future)

### 8.1 SSA Form
Convert to Static Single Assignment for:
- Better constant propagation
- Better dead code elimination
- Cleaner dataflow analysis

### 8.2 Instruction Scheduling
Reorder instructions to minimize stalls (less critical on 8080 than modern CPUs).

### 8.3 Profile-Guided Optimization
Run program, collect execution counts, optimize hot paths.

---

## Implementation Order

### Milestone 1: Working Compiler
1. Lexer/Parser → AST
2. Type checker
3. Naive code gen (stack-based)
4. um80/ul80 integration
5. cpmemu test harness

### Milestone 2: Essential Optimizations
6. Constant folding/propagation
7. Dead code elimination
8. Strength reduction
9. Basic peephole

### Milestone 3: Register Allocation
10. Live variable analysis
11. Linear scan allocation
12. Spill code generation

### Milestone 4: Advanced Optimizations
13. CSE with available expressions
14. Loop-invariant code motion
15. Inlining
16. Full peephole suite

### Milestone 5: Polish
17. Workspace optimization
18. Branch optimization
19. Loop unrolling
20. Interface devirtualization

---

## Testing Strategy

### Unit Tests
- Each optimization pass in isolation
- Before/after AST comparison
- Correctness verification

### Integration Tests
- Compile Cowgol test suite from `../cowgol/tests/`
- Run on cpmemu, compare output

### Performance Tests
- Cycle counting in cpmemu (`--progress`)
- Compare against self-hosted compiler output

### Regression Tests
- Golden output files
- Automated CI pipeline

---

## Expected Improvements Over Self-Hosted

| Optimization | Expected Speedup |
|--------------|------------------|
| Register allocation | 2-5x |
| Constant folding | 10-30% |
| CSE | 10-20% |
| Strength reduction | Variable (big for multiply-heavy) |
| Inlining | 10-30% (call-heavy code) |
| Loop optimizations | 20-50% (loop-heavy code) |
| Peephole | 5-15% |

**Overall target: 2-5x faster code than self-hosted compiler for typical programs.**

---

## File Structure

```
ucow/
├── COWGOL_LANGUAGE.md      # Language reference
├── OPTIMIZATION_STRATEGY.md # This document
├── src/
│   ├── lexer.py            # Tokenizer
│   ├── parser.py           # AST builder
│   ├── ast.py              # AST node definitions
│   ├── types.py            # Type system
│   ├── semantic.py         # Type checking, validation
│   ├── cfg.py              # Control flow graph
│   ├── dataflow.py         # Dataflow analysis
│   ├── optimize.py         # Optimization passes
│   ├── regalloc.py         # Register allocation
│   ├── codegen.py          # 8080 code generation
│   ├── peephole.py         # Peephole optimizer
│   └── main.py             # Driver
├── lib/
│   └── runtime.mac         # Runtime support (print, etc.)
└── tests/
    └── ...                 # Test cases
```
