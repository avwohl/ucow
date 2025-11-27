# Cowgol Language Reference

Cowgol is an Ada-inspired, strongly-typed language designed for small systems (6502, Z80, 80386, etc). It forbids recursion to enable static variable allocation without stack frames.

## Basic Syntax

```cowgol
include "cowgol.coh";       # include files use .coh extension

var i: uint8 := 4;          # variable with initializer
var j: uint8;               # uninitialized variable
var k := expr;              # type inference (RHS must be non-constant)

# Comments start with #

# Number literals
var hex := 0x1234;          # hexadecimal
var dec := 0d1234;          # explicit decimal
var oct := 0o1234;          # octal
var bin := 0b1010;          # binary
var grouped := 12_34;       # underscores ignored

# Character literals
var c: uint8 := 'A';
var newline := '\n';
```

## Types

### Scalar Types
- `int8`, `uint8` - 8-bit signed/unsigned
- `int16`, `uint16` - 16-bit signed/unsigned
- `int32`, `uint32` - 32-bit signed/unsigned
- `intptr` - pointer-sized integer (platform dependent)
- Custom: `int(-8, 7)` - smallest type fitting the range

### Pointers
```cowgol
var p: [uint8];             # pointer to uint8
var pp: [[uint8]];          # pointer to pointer
p := &record.field;         # address-of (only record members, not scalars)
p := nil;                   # nil pointer
[p] := value;               # dereference
p := @next p;               # advance pointer by element size
p := @prev p;               # move pointer back by element size
p := p + 1;                 # pointer arithmetic (always in bytes!)
```

### Arrays
```cowgol
var arr: uint8[42];         # fixed-size array
var arr2: uint8[] := {1,2,3}; # size inferred from initializer
arr[i] := value;            # indexing

@sizeof arr                 # number of elements
@indexof arr                # type suitable for indexing this array
@bytesof arr                # size in bytes
```

### Records (Structs)
```cowgol
record Point is
    x: int16;
    y: int16;
end record;

record Point3D: Point is    # inheritance
    z: int16;
end record;

record Union is
    a @at(0): uint8;        # @at specifies offset (for unions/hardware)
    b @at(0): uint16;
end record;

var p: Point := { 10, 20 }; # initializer
p.x := 5;                   # field access
```

### Type Aliases
```cowgol
typedef MyInt is uint16;
typedef MyArray is uint8[100];
```

## Operators

### Arithmetic (same types required on both sides)
`+` `-` `*` `/` `%` `&` `|` `^` `~` (bitwise not)

### Shifts (RHS must be uint8)
`<<` `>>`

### Comparison (only in conditionals)
`==` `!=` `<` `<=` `>` `>=`

### Logical (short-circuit, only in conditionals)
`and` `or` `not`

### Type Casting
```cowgol
var i: uint8 := 5;
var j: uint16 := i as uint16;   # explicit cast required
```

## Control Flow

### If Statement
```cowgol
if condition then
    # ...
elseif other_condition then
    # ...
else
    # ...
end if;
```

### While Loop
```cowgol
while condition loop
    # ...
end loop;
```

### Infinite Loop
```cowgol
loop
    # ...
    if done then
        break;
    end if;
    continue;       # also available
end loop;
```

### Case Statement
```cowgol
case value is
    when 0: # ...
    when 1: # ...
    when 2, 3: # multiple values (if supported)
    when else: # default
end case;
```

## Subroutines

### Basic Subroutine
```cowgol
sub DoSomething(param1: uint8, param2: uint16) is
    # body
end sub;
```

### Multiple Return Values
```cowgol
sub Swap(a: uint8, b: uint8): (out1: uint8, out2: uint8) is
    out1 := b;
    out2 := a;
end sub;

(x, y) := Swap(x, y);   # calling
```

### Nested Subroutines
```cowgol
sub Outer(x: uint8) is
    sub Inner() is
        print_i8(x);    # can access outer's variables
    end sub;
    Inner();
end sub;
```

### Forward Declarations
```cowgol
@decl sub ForwardDeclared(param: uint8);

# ... later in same scope ...

@impl sub ForwardDeclared is
    # implementation (params from @decl)
end sub;
```

### External Linkage
```cowgol
sub Exported @extern("_exported_name") is
    # ...
end sub;

@decl sub Imported(x: uint8) @extern("_imported_name");
```

## Interfaces (Function Pointers)

```cowgol
interface Comparator(a: [uint8], b: [uint8]): (result: int8);

sub StrCompare implements Comparator is
    # implementation uses interface's parameter names
    result := 0;
end sub;

var cmp: Comparator := StrCompare;
var r := cmp(ptr1, ptr2);           # call through interface
```

## Constants

```cowgol
const MAX_SIZE := 100;
const MASK := 0xFF;
```

## Static Initializers

```cowgol
var arr: uint8[3] := {1, 2, 3};
var rec: Point := { 10, 20 };
var str: [uint8] := "hello";
```

## Special Operators

| Operator | Description |
|----------|-------------|
| `@sizeof` | Number of elements in array |
| `@bytesof` | Size in bytes of type/variable |
| `@indexof` | Index type for array |
| `@next` | Advance pointer by element size |
| `@prev` | Move pointer back by element size |
| `@at(n)` | Specify record field offset |
| `@alias` | Bypass aliasing restrictions |

## Inline Assembly

```cowgol
@asm "mov %eax, %ebx";
@asm "mov ", variable, ", %eax";    # reference simple variables
```

## Key Constraints

1. **No recursion** - enables static variable allocation
2. **No implicit casts** - all type conversions must be explicit
3. **No floating point**
4. **No boolean type** - comparisons only valid in conditionals
5. **Cannot take address of scalar variables** - only record members
6. **Single-pass compiler** - forward declarations needed for mutual references
7. **Variables not auto-initialized** - contain garbage until assigned

## File Extensions

- `.cow` - Cowgol source files
- `.coh` - Cowgol header/include files

## Standard Library Functions (typical)

```cowgol
print(str: [uint8])              # print null-terminated string
print_char(c: uint8)             # print single character
print_nl()                       # print newline
print_i8(v: uint8)               # print 8-bit integer
print_i16(v: uint16)             # print 16-bit integer
print_i32(v: uint32)             # print 32-bit integer
print_hex_i8(v: uint8)           # print 8-bit hex
print_hex_i16(v: uint16)         # print 16-bit hex
print_hex_i32(v: uint32)         # print 32-bit hex
Exit()                           # exit program
ExitWithError()                  # exit with error code
MemSet(ptr: [uint8], val: uint8, len: intptr)
MemZero(ptr: [uint8], size: intptr)
```
