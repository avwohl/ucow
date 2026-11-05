# Debug markers (-g)

`ucow -g` writes debug markers into the assembler text. A debugger uses the markers to find the Cowgol source line of each instruction. The design is in [debugger_plan.md](https://github.com/avwohl/cpmemu/blob/main/docs/debugger_plan.md) of cpmemu. The marker format is in `docs/debug_info.md` of um80_and_friends.

## Use

`ucow` writes the `.mac` file only. You run `um80` and `ul80` yourself. Give both programs the option `--debug-info`.

```
ucow -g prog.cow -o prog.mac
um80 --debug-info -I lib prog.mac
ul80 --debug-info prog.rel -o prog.com
```

`um80` writes `prog.dbm` next to `prog.rel`. `ul80` writes `prog.dbg` next to `prog.com`. The file `prog.dbg` lists the source line of each address, the routines, and the labels. `udb80` reads the file.

```
udb80 prog.dbg --run 'cpmemu prog.com'
```

The file names in the markers are relative to the directory where you run `ucow`. A file outside that directory has its full path. Run `ucow` in the directory of the source file, or give the debugger a source path.

The option works for one file, for several files (workspace mode), and with `-L`.

## The code does not change

The `.com` file is the same, byte for byte, with and without `-g`. A marker is a whole-line comment. The assembler writes no byte for a comment. The tests build 24 programs four times each. The four builds use `-O0`, `--no-post-opt`, both options, and neither option. The tests compare the `.com` files.

The tests also compare the `.mac` text without its marker lines.

The text passes of ucow work on windows of lines. A marker in the middle of a window would stop a pattern and change the code. Two rules prevent this:

- A pass sees the text as it is without `-g`. The module `src/debugmarks.py` takes the markers out before each pass of `peephole_optimize` and of `postopt`. After the pass, each marker goes back in front of the line that it was in front of.
- When a pass replaced or removed that line, the marker goes in front of the new text. The line number can be wrong. The code is the same.

A `;#noopt begin` to `;#noopt end` region is also kept out of each pass, and comes back unchanged. ucow does not write such a region yet.

## What ucow writes

- `;#file N "PATH"` in the header, one for each source file. An included file has its own number.
- `;#func NAME F L frame=static pops=0` after the label of each subroutine, and `;#endfunc NAME` after its `RET`. The main program is the routine `_main`.
- `;#line F L` in front of each statement. F and L are the file and line of the statement.
- `;#line` for the line of the subroutine declaration, in front of the code that copies the parameters. `;#line` for the line of `end sub`, in front of the code that loads the result.
- `;#line` for the condition of each `elseif` and for each value of a `when`.
- `;#type` and `;#var` for the variables. See [Variables](#variables).
- `;#inline F L NAME` and `;#endinline` around the code of a subroutine that ucow inlined. F and L are the line of the call. The lines inside are the lines of the subroutine.

A subroutine has `frame=static`, because the locals are at fixed addresses. `pops` is always 0. The caller removes the stack arguments after the call.

## Variables

`ucow -g` writes a `;#var` marker for each global, parameter and local variable, and a `;#type` marker for each record and typedef that a variable uses. The format is in `docs/debug_info.md` of um80_and_friends (Variables). `udb80` reads the markers from `prog.dbg`.

```
;#type struct:Point size=4 members=x:int:2@0,y:int:2@2
	;#var total uint:2 kind=global at=v_total decl=1:9
	;#func Inner 1 42 frame=static pops=0
	;#var a uchar:1 kind=param at=v_Inner_a decl=1:42 scope=1:42-53 range=??VS1:??VE1
	;#var lpt struct:Point kind=local at=v_Inner_lpt decl=1:46 scope=1:46-53
```

The `;#type` markers and the global markers stand after the `;#file` lines. The markers of a subroutine stand after its `;#func` marker, so a record belongs to one subroutine. ucow writes all of them after the last text pass (the peephole pass and `postopt`). A marker is a comment. The labels `??VSn` and `??VEn` write no byte. The `.com` file is therefore the same with and without `-g`.

### Types

- `int8` and `uint8` are `schar:1` and `uchar:1`, so a byte array shows as a string. `print/d` shows the number. `int16` and `uint16` are `int:2` and `uint:2`. `int32` and `uint32` are `long:4` and `ulong:4`.
- A pointer `[T]` is `*T`. An array `T[N]` is `[N]T`. The size of an array that has no declared size comes from the storage that ucow allocated.
- A record is `struct:NAME`, with `size=` and `members=NAME:TYPE@OFFSET`. The members of the base record come first. A record whose members overlap (`@(0)` fields) is `union:NAME`.
- A typedef keeps its name: `typedef:byte` with `target=uchar:1`. A variable and a record member keep the typedef name that the source wrote. A parameter does not: the type checker keeps no typedef name for a parameter, so the record has the base type.
- An interface variable has the type `*func(void)`. The variable holds the address of a subroutine, so `udb80` prints the address and the name of the target subroutine. The type `func(void)` is wrong here: `udb80` would print the address of the data slot.

### Where each variable lives

- **Global.** `at=v_NAME`.
- **Local, parameter and return value.** `at=v_SUB_NAME`. With workspace optimization (two or more source files), the records have `at=_wsbase+N`. Two subroutines that are never active together can have the same `N`. The record stands inside its own `;#func` marker, and `udb80` uses a record only in a frame of that subroutine.
- **Parameter.** The prologue copies the parameter to its static slot. The record has `range=` from the first statement of the body to the end of the subroutine. Before the copy, `udb80` shows `<not available>`.
- **Return value.** A named return value is a local variable, with no range.
- **No place.** The optimizer can remove the store to a local, and then no instruction uses the slot. The record has `none why=no-storage`. The record also has this text when `postopt` removed the data of a global.

### Scope

`scope=` runs from the line of the declaration to the line of `end sub`. The syntax tree has no end line for an inner block, so a variable stays visible to the end of the subroutine. Two variables of one name in two blocks of one subroutine share one label, and the later declaration wins in the code (read in code, not measured).

### Limits

- The inliner takes only subroutines with no parameter, no return value and no local variable (read in code). An inlined subroutine therefore has no variable that loses its record.
- ucow stores only 16 bits of an `int32` or `uint32` (the upper two bytes stay zero). The record gives the real type, so `udb80` shows the upper half as zero. A value over 65535 shows wrong, and a negative value shows wrong: `-5` in an `int32` prints as 65531 (measured with `gneg32` in `tests/vars_test.cow`). The code is the same with and without `-g`.
- A nested subroutine cannot print a variable of the subroutine that holds the nested subroutine, although the code reads the variable. A record belongs to one subroutine, so `print p` in a nested subroutine gives `No symbol "p"` (measured).
- No record has `reg=`: the 191 records are at an address or `none`. A store that the optimizer moved or removed can leave an old value in the slot, and `udb80` shows the old value.

## Tail merging

`tail_merging_pass` merges identical tails of code. Before `-g`, the pass could merge a tail of one routine with a tail of another routine. The first routine then jumped into the middle of the second routine. A debugger cannot show such code under the right routine.

The pass now merges only inside one routine. The rule is the same with and without `-g`, so the `.com` stays the same. The pass merged no tail in the 22 test programs, before or after the change.

## Limits

- A `while` or `loop` has the line of its first line. The `end loop` line has no marker, because the syntax tree has no position for the line. The jump back to the top has the line of the last statement of the body.
- A tail call (`CALL x` and `RET` made into `JP x`) has the lines of the statement and of `end sub`. A breakpoint on either line stops there.
- A statement that makes no code leaves its marker next to the marker of the next statement. The assembler merges them.
- The runtime (`runtime.mac`) has no markers. The assembler gives each of its lines a line of the `.mac` file (assembler level only).
- `-L` with the post optimizer deletes the data of `examples/multifile/b.cow` (measured, with and without `-g`). Use `--no-post-opt` with `-L`.

## Measurements

These results come from the programs in `tests/` and `examples/multifile/`, and from `tests/workspace_test/`.

- At phase 7, 88 builds (22 programs, four option sets) gave the same `.com` with and without `-g`. The `.mac` text with its marker lines removed is also the same.
- At phase 7, 5713 of 5713 bytes in the routines of 22 programs had a source line in `prog.dbg`. The share is 100.0 percent for the set and for each program.
- A mutation test turns off the marker removal. Then the `.com` of `test_all` has 4992 bytes and not 4608 bytes. The test therefore can fail.
- At phase 7, the `.com` files of the 22 programs were the same as before the change, under all four option sets.
- `tests/debug_info_check.py` also runs a session of `udb80` against `cpmemu --gdb`. `break` on a line of `Sum3`, `bt`, `step` and `next` work. The test skips when `cpmemu` has no `--gdb` or `um80` has no `udb80`. `UCOW_REQUIRE_UDB80=1` makes a skip a failure.

### Variable records, measured

The test programs are the 24 programs of `tests/`, `examples/multifile/`, `tests/workspace_test/` and `tests/vars_ws/`.

- The D2 test now makes 96 builds (24 programs, four option sets). The `.com` is the same with and without `-g` in all 96.
- 191 records: 67 global, 88 local and 36 parameter. 178 are at an address and 13 have `none why=no-storage`. The type split covers the first 188 records, before `gneg32` and `gcb` joined `tests/vars_test.cow`: 139 scalar, 16 array, 10 pointer, 15 record and 8 typedef.
- All 36 parameters have a `range=`. In workspace mode, records of two subroutines have the same address at 6 addresses.
- `tests/debug_info_check.py` runs `udb80` on `tests/vars_test.cow` and prints each kind of variable at a known value, including the variables of a caller in frame 1 and 2. The session prints an interface variable (`print gcb` gives the address and the name `CbTarget`) and a negative `int32` (`-5` prints as 65531, a known limit). It also runs `tests/vars_ws/` and prints the locals of the two subroutines that share one address.
- A mutation test calls `add_markers` before the peephole pass. The `.com` then differs for 28 of the 96 builds, so the D2 test can fail.
