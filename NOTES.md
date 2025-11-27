# Current State - ucow optimizations

## Session 2024-11-27:

### Bug fixes:
1. Fixed global variable initialization bug - scalar variables with initializers weren't being initialized
2. Added constant array initialization in data segment (DB/DW directives) - required for cowfe parser tables
3. Fixed constant propagation bug in while loops - variables modified in loop body must be invalidated before optimizing the condition to prevent incorrect loop elimination

### Optimizations added:
1. `PUSH H / LXI H,addr / POP D / DAD D` -> `LXI D,addr / DAD D` (array indexing, ~860 bytes saved)

### Current cowfe size: ~29,945 lines of assembly (down from ~33K initially)
- Code size reduced significantly through peephole optimizations
- Starting from 30,958 lines at session start, saved ~1,013 lines
- Data: ~1.7K lines (parser tables are large)

### Multi-pass AST Optimizer (src/optimizer.py)
Added a semantic-level optimizer that runs in passes until stable (like MBASIC):
- **Constant Folding**: Evaluates constant expressions at compile time
- **Constant Propagation**: Propagates constants from local variable assignments
- **Copy Propagation**: Replace variable copies with their source
- **Strength Reduction**: X*2 -> X+X, X*2^n -> X<<n, X/2^n -> X>>n, X%2^n -> X&(2^n-1)
- **Algebraic Simplification**: X+0 -> X, X*1 -> X, X-X -> 0, X&0 -> 0, etc.
- **Dead Code Elimination**: Removes if(0), while(0), etc.
- **Dead Store Elimination**: Removes redundant assignments of same value
- **Dead Variable Elimination**: Removes assignments to variables never read
- **Unreachable Code Elimination**: Removes code after return/break/continue
- **Expression Reassociation**: (a+1)+2 -> a+3 (groups constants together)
- **Boolean Simplification**: not(not X) -> X, NOT of comparison inverts operator
- **Comparison Simplification**: X==X -> 1, X!=X -> 0, normalize constants to right
- **Common Subexpression Elimination (CSE)**: Reuse previously computed expressions
- **Loop-Invariant Code Motion**: Hoist invariant computations out of loops

Usage: Enabled by default. Disable with `-O0` or `--no-optimize`.
Debug output with `--opt-debug`.

cowfe optimization: 363 AST changes in 3 passes.

### Induction Variable Optimization (partially implemented)
Detection code is in place to find:
- Basic induction variables (i := i + const pattern)
- Derived expressions (i * const + offset pattern)
Transformation is disabled pending proper variable declaration support in the AST.

### Subroutine Inlining
Implemented inlining for SIZE reduction (not speed):
- Analyzes subroutines without parameters/returns, local vars, or loops
- Inlines if total size is reduced: `N*body_size < body_size + 3*N + 1`
- Subs called once are always inlined
- Eliminates CALL/RET overhead for small frequently-called subs

### 16-bit Comparison Optimization
When comparing with a constant, generate `LXI D,const` directly instead of:
```
PUSH H
LXI H,const
XCHG
POP H
```
Saves 4 bytes per constant comparison (common in loops like `while i < 10`).

### Register Tracking (Basic Register Allocation)
Basic register tracking to avoid redundant loads:
- Track what variable is currently in HL and A registers
- Skip LHLD/LDA if the variable is already in the register
- After SHLD/STA, remember that HL/A still contains that variable
- Invalidate tracking on control flow (labels), CALLs, and modifying instructions

### Peephole Optimizations (additional)
- `LXI H,0 / MOV A,L` -> `XRA A` (load zero into A)
- `CALL x / RET` -> `JMP x` (tail call optimization)
- `PUSH H / LXI H,0 / XCHG / POP H / compare` -> `MOV A,H / ORA L` (compare with zero)
- `LXI H,const / DAD H` -> `LXI H,const*2` (constant folding for index*2)
- `LXI H,0 / LXI D,addr / DAD D` -> `LXI H,addr` (0 + addr = addr)
- `LXI D,1 / DAD D` -> `INX H` (add 1 to HL)
- `LXI D,2 / DAD D` -> `INX H / INX H` (add 2 to HL)
- `PUSH H / LHLD x / XCHG / POP H / XCHG` -> `XCHG / LHLD x` (save HL to DE, load x)
- `PUSH H / LHLD x / INX H / XCHG / POP H / XCHG` -> `XCHG / LHLD x / INX H` (same with offset)
- `MOV L,A / MVI H,0 / MOV A,L` -> `MOV L,A / MVI H,0` (A already has value)
- `LXI D,3 / DAD D` -> `INX H / INX H / INX H` (add 3 to HL)
- Byte `var := var + 1` -> `LXI H,var / INR M` (sets Z flag, useful for loops)
- Byte `var := var - 1` -> `LXI H,var / DCR M` (sets Z flag, useful for loops)
- `PUSH H / LXI H,const / XCHG / POP H` -> `LXI D,const` (load DE while preserving HL)

### Byte Comparison Optimization
When comparing a byte variable with a constant 0-255, use `CPI` instead of 16-bit comparison:
```
; Before (16-bit):           ; After (byte):
LDA    v_i                   LDA    v_i
MOV    L,A                   CPI    5
MVI    H,0                   JNC    label
LXI    D,5
MOV    A,H
CMP    D
JNZ    $+6
MOV    A,L
CMP    E
JNC    label
```
Saves ~8 bytes per byte comparison.

### Loop Reversal Optimization
Count-up byte loops that don't use the loop variable in the body are transformed to count down:
```
i := 0;               ->    i := N;
while i < N loop            while i != 0 loop
    ...body...                  i := i - 1;
    i := i + 1;                 ...body...
end loop;                   end loop;
```
Benefits:
- `DCR M` sets zero flag, so condition check becomes `LDA v_i / ORA A / JZ` instead of full 16-bit comparison
- Saves ~7 bytes per loop

### Bug fixes in this session:
- Fixed dead variable elimination for globals - don't eliminate assignments to global variables (they may be read elsewhere)
- Fixed peephole optimizer removing duplicate CALLs - CALLs have side effects and must not be removed

### arith.cow test: All tests pass
- 10 + 3 = 13
- 10 - 3 = 7
- 10 * 3 = 30
- 10 / 3 = 3
- 10 % 3 = 1

## Previous session optimizations:
1. `LHLD x / PUSH H / LHLD y / XCHG / POP H` -> `LHLD y / XCHG / LHLD x`
2. PUSH B / POP B removal
3. XCHG / XCHG removal
4. INX H / DCX H removal
5. LXI H,0 / MOV A,L / ORA H -> XRA A
6. MVI A,0 / ORA A -> XRA A
7. LHLD x / MOV A,L / MOV L,A -> LHLD x / MOV A,L

## To compile cowfe:
```bash
python3 ucow /tmp/cowgol_build/src/cowfe/main.cow \
  -I /tmp/cowgol_build/rt/cpm \
  -I /tmp/cowgol_build/rt \
  -I /tmp/cowgol_build/src/cowfe \
  -I /tmp/cowgol_build \
  -I cowgol_compat \
  -o /tmp/cowfe_opt.mac
```

## Ideas for further size reduction:
1. ~~Inline short subroutines~~ (done)
2. ~~Optimize 16-bit comparison sequences~~ (done)
3. ~~Better register allocation to reduce PUSH/POP~~ (basic version done)
4. ~~Tail call optimization~~ (done via peephole)
5. More aggressive constant folding for array accesses
6. Combine LXI D / DAD D sequences when possible
