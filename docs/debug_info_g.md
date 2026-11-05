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

The `.com` file is the same, byte for byte, with and without `-g`. A marker is a whole-line comment. The assembler writes no byte for a comment. The tests build 22 programs four times each. The four builds use `-O0`, `--no-post-opt`, both options, and neither option. The tests compare the `.com` files.

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
- `;#inline F L NAME` and `;#endinline` around the code of a subroutine that ucow inlined. F and L are the line of the call. The lines inside are the lines of the subroutine.

A subroutine has `frame=static`, because the locals are at fixed addresses. `pops` is always 0. The caller removes the stack arguments after the call.

## Tail merging

`tail_merging_pass` merges identical tails of code. Before `-g`, the pass could merge a tail of one routine with a tail of another routine. The first routine then jumped into the middle of the second routine. A debugger cannot show such code under the right routine.

The pass now merges only inside one routine. The rule is the same with and without `-g`, so the `.com` stays the same. The pass merged no tail in the 22 test programs, before or after the change.

## Limits

- A `while` or `loop` has the line of its first line. The `end loop` line has no marker, because the syntax tree has no position for the line. The jump back to the top has the line of the last statement of the body.
- A tail call (`CALL x` and `RET` made into `JP x`) has the lines of the statement and of `end sub`. A breakpoint on either line stops there.
- A statement that makes no code leaves its marker next to the marker of the next statement. The assembler merges them.
- The runtime (`runtime.mac`) has no markers. The assembler gives each of its lines a line of the `.mac` file (assembler level only).
- Variables are not in `prog.dbg`. The workspace optimization (`_wsbase`) puts two locals at one address. Phase 8 of the plan handles variables.
- `-L` with the post optimizer deletes the data of `examples/multifile/b.cow` (measured, with and without `-g`). Use `--no-post-opt` with `-L`.

## Measurements

These results come from the programs in `tests/` and `examples/multifile/`, and from `tests/workspace_test/`.

- 88 builds (22 programs, four option sets) give the same `.com` with and without `-g`. The `.mac` text with its marker lines removed is also the same.
- 5713 of 5713 bytes in the routines of 22 programs have a source line in `prog.dbg`. The share is 100.0 percent for the set and for each program.
- A mutation test turns off the marker removal. Then the `.com` of `test_all` has 4992 bytes and not 4608 bytes. The test therefore can fail.
- The `.com` files of the 22 programs are the same as before the change, under all four option sets.
- `tests/debug_info_check.py` also runs a session of `udb80` against `cpmemu --gdb`. `break` on a line of `Sum3`, `bt`, `step` and `next` work. The test skips when `cpmemu` has no `--gdb` or `um80` has no `udb80`. `UCOW_REQUIRE_UDB80=1` makes a skip a failure.
