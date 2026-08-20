# Changelog

Notable changes to ucow, a Cowgol compiler targeting 8080/Z80 CP/M.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

Repository only — no release. Neither the wheel nor the sdist contains
`run_tests.sh`, `tests/`, or the file removed below, so the distributed
package is byte-identical to 0.4.3 and there is nothing to publish.

### Fixed

- **`run_tests.sh` can assemble again, and it runs the right runtime.**
  It assembled from `$TESTS_DIR`, where the generated
  `INCLUDE 'runtime.mac'` resolves to nothing, so every test failed at
  the assemble step. It now passes `-I "$UCOW_DIR/lib"`.

  The include path also decides *which* runtime, which mattered more
  than the missing one. A stale `runtime.mac` sat at the repo root whose
  `print_i16` took its argument on the stack at `SP+8`, while codegen
  has passed it in `HL` for a long time. Anything that assembled from
  the repo root therefore linked cleanly and printed garbage —
  `hello.cow` emitted `C~` rather than `Hello, World!` — with no
  diagnostic anywhere. That copy has one commit against it and never
  received the print-combining work, so it also lacked `print_i16_nl`,
  `print_a_nl` and `print_de_nl`, which the post-assembly optimizer
  emits whenever a `print` is followed by a `print_nl`. It is deleted.
  `lib/runtime.mac` is the real one: it is what `CLAUDE.md` documents,
  what the wheel ships, what `test.sh` copies, it takes its argument in
  `HL`, and it has had all three combined helpers since before 0.3.0.

  Assembling from the repo root now fails with "Cannot find include
  file: runtime.mac" instead of producing a wrong program quietly. Pass
  `-I lib`.

- **`run_tests.sh` finds cpmemu.** The path was hardcoded to one Linux
  developer checkout, so the script could not run anywhere else. It
  takes `cpmemu` from `PATH`, where `pip install cpmemu` puts it, and
  `$CPMEMU` still overrides for a build that is not installed. Missing
  is now a clear message rather than a failure attributed to each test
  in turn.

  All 13 tests pass.

- **`tests/asm_test.cow` is written in Z80 mnemonics.** It carried the
  8080 spellings `lxi h, 42`, `shld`, `mvi e, 65` and `mvi c, 2`, but
  codegen emits a `.Z80` directive at the top of every module, so um80
  assembles in Z80 mode and rejects them: `Unknown instruction or
  directive: LXI`. The test was added in the first commit and `.Z80`
  arrived later, so it had never once assembled — it was invisible for
  as long as no test in the suite could assemble at all.

  `ld hl, 42`, `ld (`value`), hl`, `ld e, 65` and `ld c, 2` now, with
  the BDOS `call 5` unchanged. The multi-part `@asm "ld (", value,
  "), hl"` form emits the variable's symbol between the two text
  pieces. Both halves of what the test intends are exercised: it prints
  `Value set via asm: 42`, so the inline store reached the variable and
  `print_i16` read it back, and `Direct BDOS call: A`, so the raw BDOS
  console-output call ran.

### Documentation

- **The docs no longer describe the deleted front end.** `CLAUDE.md`'s
  Code Structure listed `src/lexer.py` and stopped at six modules; it
  now names the twelve that exist, says which one is generated and must
  not be hand-edited, and leads with the fact that there is no
  hand-written lexer. Its Testing recipe was broken three ways — it
  compiled a `tests/test.cow` that does not exist, assembled without the
  `-I` that resolves `runtime.mac`, and predated `run_tests.sh` working
  — and is replaced by a sequence that was run as written.

- **`OPTIMIZATION_STRATEGY.md`'s file tree is marked as the plan it
  is.** Seven of the twelve modules it lists were never built, and it
  omits seven that were, so read as a description it was wrong about
  more than the lexer. Each entry now says what became of it.
  `lexer.py` is annotated as built and deleted in 0.4.0, which is what
  separates it from `cfg.py` or `regalloc.py`, which never existed.

- **The README's Requirements are accurate.** It asked for "Python 3"
  and did not mention uplox, which the generated parser imports at run
  time; ucow has needed Python 3.11 and `uplox>=3.3.0` since 0.4.0.

This closes 0.4.0's Known regressions. Every entry there is now either
fixed or recorded against the release that fixed it.


## 0.4.3

An unknown escape sequence is an error again, in both string and
character literals. Error reporting and literal validation only: every
`.cow` program under `tests/` compiles to byte-identical `.mac` output
under 0.4.2 and 0.4.3, and all 34 `.cow` / `.coh` sources in the
repository still parse.

### Fixed

- **`"a\qb"` is rejected instead of compiling as `aqb`.**
  `_unquote_string` looked each escape up in a table and fell back to
  the escaped character itself, so any escape outside the valid seven
  silently lost its backslash. 0.3.0 raised
  `Unknown escape sequence \q`, and so does 0.4.3. The valid set is
  0.3.0's, unchanged: `\n`, `\t`, `\r`, `\\`, `\'`, `\"`, `\0`.

  The error names the position of the backslash, not of the literal:

      Lexer error: esc.cow:1:21: Unknown escape sequence \q

- **`'\q'` is rejected instead of evaluating to 113.** `_parse_number`
  had the same fallback for character literals — `.get(ch, ord(ch))` —
  so a typo'd escape quietly became the code of the escaped letter. This
  was worse than the string case and was missed when the string case was
  first written up, because 0.4.0's changelog recorded character
  literals as unaffected, on the grounds that the uplox scanner rejected
  them. It does not: `'\q'` scans, parses, and compiled to 113.

  Both literal kinds now share one escape table, so they cannot drift
  apart again. The seven valid escapes keep their exact values —
  `'\n'` 10, `'\t'` 9, `'\r'` 13, `'\0'` 0, `'\\'` 92, `'\''` 39,
  `'\"'` 34 — and `'\"'` stays valid, which 0.4.x allowed and 0.3.0 did
  not; nothing is narrowed relative to what already worked.

### Added

- `tests/escape_test.cow`, which uses all seven valid escapes in both
  literal kinds. It is in `run_tests.sh`'s list. The bundled sources use
  only `\n`, `\t` and `\r`, which is why nothing caught the fallback.

### Still open from 0.4.0

`CLAUDE.md` and `OPTIMIZATION_STRATEGY.md` still list the deleted
`src/lexer.py`. The two faults older than the migration also stand here
and are fixed under Unreleased above: `run_tests.sh` assembles from
`tests/` without an include path, and the stale `runtime.mac` at the
repo root lacks the combined print helpers.


## 0.4.2

Error reporting only. Every `.cow` program under `tests/` compiles to
byte-identical `.mac` output under 0.4.1 and 0.4.2; the difference is
what ucow prints when it refuses.

### Fixed

- **A syntax error says `Parse error:` again.** 0.4.0 replaced the
  deleted lexer's exception with `LexerError = ParseError`, an alias.
  The two `except` arms in each of the compile paths in `src/main.py`
  then caught the same class, and since `except LexerError` came first,
  the `Parse error:` arm was unreachable — every syntax error in the
  language was announced as a lexer fault. `LexerError` is a real class
  again, deriving from `ParseError` so that `except ParseError` still
  catches a lexical error exactly as it did while the alias stood, and
  `except LexerError` once more catches only lexical ones.

- **A lexical error names the character and gives its position once.**
  It read

      Lexer error: lex.cow:2:6: <input>:2:6: lexical error at byte 0x24

  Two faults in one line. The uplox scanner formats a location into its
  own message, and the generated `parse()` entry point takes no file
  name, so that copy always said `<input>`; ucow's `ParseError` then
  prefixed the real one. And `byte 0x24` is the character's code, not
  its offset, which reads like a file position and is not one. Now:

      Lexer error: lex.cow:2:6: Unexpected character: '$'

  which is what 0.3.0 printed. A character that will not print shows as
  its code instead.

- **A syntax error no longer states its position twice.** The uplox
  runtime ends its text with `at line N, column M`, naming the place
  ucow has already prefixed. That clause is dropped when N and M agree
  with the location being printed, and left alone otherwise, so a
  reworded runtime message passes through whole rather than silently
  losing part of itself.

### Still open from 0.4.0

Two entries under 0.4.0's Known regressions stand here. An unknown
escape sequence in a string literal is still accepted silently, where
0.3.0 rejected `"a\qb"` with `Unknown escape sequence \q` — fixed in
0.4.3, which also fixes the character-literal case that entry recorded
as unaffected. And `CLAUDE.md` and `OPTIMIZATION_STRATEGY.md` still list
the deleted `src/lexer.py`.
The two faults older than the migration also stand: `run_tests.sh`
assembles from `tests/` without an include path, so `runtime.mac` at the
repo root is not found, and `runtime.mac` defines neither `print_de_nl`
nor `print_i16_nl`, which the optimizer emits whenever a `print` is
followed by a `print_nl`.


## 0.4.1

The three nested-declaration regressions 0.4.0 shipped are fixed, and so
is `--tokens`. Nothing here changes the code generated for anything that
already compiled: all 14 `.cow` programs directly under `tests/` produce
byte-identical `.mac` output under 0.4.0 and 0.4.1.

The root cause was one omission with three faces. A sub body is a list
of statements, but Cowgol lets a `record`, `typedef` or `interface`
declaration sit in one, and `ast.py` models all three as `Declaration`
rather than `Statement`. The pre-v3 parser simply appended the
Declaration node to the body list and left `types.py` and `codegen.py`
to dispatch on it there, which they have always done. The v3 translator
kept only the nested-`sub` case.

### Fixed

- **`record` and `typedef` declarations nested inside a subroutine body
  translate again.** `_translate_stmt_or_nested` had a branch for a
  nested `sub` and one for a nested `interface`, but none for
  `RecordDecl` or `TypedefDecl`, so both fell through to
  `_translate_statement` and raised
  `internal: cannot translate statement of kind RecordDecl`. That is
  what stopped `cowgol_compat/inssel.coh` — which ships inside the
  wheel — from parsing at all: it declares `record NodeSlot` inside
  `EmitOneInstruction` at line 786.

  The fix puts `ast.RecordDecl` and `ast.TypedefDecl` straight into the
  body list, which is what the pre-v3 recursive-descent parser did
  (`_parse_record` and `_parse_typedef` were appended to `body` from the
  sub-body loop). Nothing downstream needed changing: `types.py`has
  dispatched on both in `check_statement` since long before the
  migration, and `codegen.py` skips them there. `inssel.coh` now parses
  to 92 top-level declarations, and a nested `record` plus `typedef`
  compiles through to assembly that `um80` and `ul80` accept.

- **A nested `interface` declaration keeps its contents.** 0.4.0
  translated it to `ast.NestedSubStmt(sub=None)` as a deliberate
  placeholder, which discarded the interface's name, parameters and
  returns, and left anything reading `.sub` to raise `AttributeError:
  'NoneType' object has no attribute 'is_impl'` rather than report a
  diagnostic — so a sub containing one could not be compiled at all. It
  now translates through the same `_translate_interface_decl` the top
  level uses, producing a real `ast.InterfaceDecl` in the body list, as
  0.3.0 did. `types.py` already dispatched on it in statement position;
  `codegen.py` now names it alongside `RecordDecl` and `TypedefDecl` in
  the branch that emits nothing for a type declaration, which is what it
  was already doing by falling off the end of the chain.

- **`--tokens` works again**, on a new `parser.scan_tokens()` that runs
  the uplox scanner alone. The old handler called
  `Lexer(source, input_files[0])`, and `Lexer` went with `src/lexer.py`
  in 0.4.0, so the flag died with
  `NameError: name 'Lexer' is not defined`.

  The output format is different, because the tokens are. Pre-v3 ucow
  printed a `TokenType` name and the parsed value, `ID(msg)`; the uplox
  scanner yields a terminal name and the raw lexeme, and the dump now
  carries the position as well:

      hello.cow:3:5: IDENT 'msg'

  As before, the file is scanned as written — no preprocessing — and
  whitespace and comments never appear, since the grammar puts them in
  the scanner's skip set. A lexical error is reported through
  `ParseError` and exits 1 instead of raising.

### Added

- `tests/nested_decl_test.cow`, which declares a `record` and a
  `typedef` inside a subroutine and uses both. No program under `tests/`
  exercised the construct, which is why the suite did not catch the
  0.4.0 breakage. It is in `run_tests.sh`'s list.

### Still open from 0.4.0

One entry under 0.4.0's Known regressions stands here and is fixed in
0.4.2: every syntax error is labelled `Lexer error:`, because
`LexerError` is an alias of `ParseError` and its `except` arm comes
first. Two faults older than the migration also stand
and are worth knowing about before running `run_tests.sh`: it assembles
from `tests/` without an include path, so `runtime.mac` at the repo root
is not found, and `runtime.mac` defines neither `print_de_nl` nor
`print_i16_nl`, which the optimizer emits whenever a `print` is followed
by a `print_nl` — `tests/record.cow` hits both.


## 0.4.0

The hand-written lexer and recursive-descent parser are gone. `.cow`
source is now scanned and parsed by an LALR(1) parser that
[uplox](https://github.com/avwohl/uplox) generates from the
`cowgol_ast.uplox` grammar, and `src/parser.py` has become a translator
from uplox's v3 auto-AST into ucow's existing `src/ast.py` classes.
Nothing downstream of the parser was touched — the type checker,
optimizer, code generator, and post-assembly peephole all consume the
same `ast.py` shape they always have. All 14 `.cow` programs directly
under `tests/` compile to byte-identical `.mac` output under 0.3.0 and
0.4.0.

The migration is not free, and this is a breaking release rather than
the patch the version number first suggested. `--tokens` no longer
works, `record` / `typedef` / `interface` declarations nested inside a
subroutine body no longer translate — which breaks the bundled
`cowgol_compat/inssel.coh` — and parse-error text comes from the uplox
runtime instead of ucow. ucow also now requires the `uplox` package at
run time, and with it Python 3.11 or later; 0.3.0 needed neither. Read
the Removed and Known regressions sections before upgrading.

### Changed

- **Parser and lexer replaced by a generated uplox v3 parser.**
  `src/lexer.py` (313 lines) is deleted and the recursive-descent parser
  in `src/parser.py` (942 lines) is replaced by a 660-line translator.
  The grammar is maintained in uplox as `examples/cowgol_ast.uplox` — 78
  terminals, 182 productions, 345 LALR states — and ucow vendors the
  emitted module verbatim as `src/uplox_cowgol.py` (24,388 lines,
  generated; do not edit by hand). The translator is a mechanical walk
  that undoes the shape differences between the two ASTs: the v3
  `SubDecl` / `SubForwardDecl` / `SubImpl` trio folds back into ucow's
  single `SubDecl` with `is_decl` / `is_impl` discriminators; the
  per-keyword scalar kinds (`Int8Type`, `UInt8Type`, …) fold into
  `ScalarType(name='int8')` and friends; `Negate` and `BitNot` fold into
  `UnaryOp(op='-')` and `UnaryOp(op='~')`; `ElseIf`, `CaseArm`, and
  `CaseElse` nodes flatten into the tuple lists `ast.py` expects.
  Numeric and string literals are decoded in the translator
  (`_parse_number`, `_unquote_string`) instead of in a scanner.
  `parse_string`, `parse_file`, and `ParseError` keep their names and
  call signatures; `Parser` keeps its name but is now a shim that takes
  a source string, since the `Lexer` it used to be handed no longer
  exists. `src/preprocessor.py` calls `parse_string()` directly rather
  than building a `Lexer` / `Parser` pair.

- **`uplox` is now a declared run-time dependency, and the Python floor
  moves to 3.11.** `src/uplox_cowgol.py` imports `uplox.lex.scanner`,
  `uplox.parse.runtime` and `uplox.tables` at module level, so
  `src/parser.py` cannot be imported without it and ucow cannot parse
  anything without it. Through the development of this release
  `pyproject.toml` still carried no `dependencies` key, which would have
  produced a compiler that dies at startup with
  `ModuleNotFoundError: No module named 'uplox'` before it reads its
  arguments. `dependencies = ["uplox>=3.3.0"]` is declared as of 0.4.0.
  uplox itself declares `requires-python = ">=3.11"`, so ucow's own
  `requires-python` moves from `>=3.8` to `>=3.11` and the 3.8 / 3.9 /
  3.10 classifiers are dropped — an install on an older interpreter now
  fails in pip with a clear message rather than at first run. 0.3.0 was
  self-contained and installed anywhere; if that matters more to you
  than the new front end, stay on it.

- **Parse-error text now comes from the uplox LR runtime.** Where 0.3.0
  printed `syn.cow:2:20: Expected expression`, 0.4.0 prints
  `syn.cow:2:20: unexpected token 'SEMI' ';' at line 2, column 20;
  expected one of: AMP, AT_BYTESOF, AT_INDEXOF, AT_NEXT, AT_PREV,
  AT_SIZEOF, IDENT, KW_nil, KW_not, LBRACE, LBRACKET, LPAREN, ... +4
  more`. The expected set is genuinely useful, but it is spelled in
  grammar terminal names (`SEMI`, `KW_nil`, `AMP`), not in Cowgol source
  syntax, so anyone reading the message has to map the names back
  themselves.

- **Node source positions moved to the start of the construct.** ucow
  `Node.location` is now computed from the v3 `pos` span rather than
  from wherever the recursive-descent parser's cursor happened to sit,
  and every diagnostic that quotes a node position moves with it. For
  `    x := helper(3);`, 0.3.0 reported `Cannot call non-subroutine` at
  column 16, the opening parenthesis, and 0.4.0 reports it at column
  10, where `helper` begins.

- **`IfStmt.else_body` is `[]` rather than `None` when the `if` has no
  `else`.** This is the only structural difference between the 0.3.0 and
  0.4.0 ASTs across all 20 `.cow` sources bundled under `tests/` and
  `examples/`; everything else compares equal node for node once source
  locations are set aside. It is also what fixes the multi-file crash
  described below.

- **Package metadata points at this repository.** `Homepage` and
  `Repository` in `pyproject.toml` moved from
  `https://github.com/davidgiven/ucow`, which does not exist, to
  `https://github.com/avwohl/ucow`. Upstream Cowgol is
  `davidgiven/cowgol`.

- **PyPI publishing workflow collapsed into one job.**
  `.github/workflows/publish.yml` no longer builds in one job and
  uploads/downloads a `dist` artifact into a second; a single `publish`
  job builds and publishes, with `id-token: write`,
  `attestations: write`, and `contents: read` in the `pypi` environment.

- **README gained a Related Projects section**, listing the sibling
  CP/M and Z80 tools, written in ASD-STE100 Simplified Technical
  English (one canonical name per project, no gerund heads, no `via`).

### Fixed

- **Multi-file and workspace-optimized compiles no longer crash on an
  `if` without an `else`.** `callgraph.py`'s `_visit_stmt_children` and
  `main.py`'s `count_local_vars_in_stmt` both iterate `stmt.else_body`
  unguarded. Under 0.3.0 that field was `None` for an `else`-less `if`,
  so any multi-file build — `ucow a.cow b.cow -o out.mac`, or a single
  file with `--workspace-opt` — died with an unhandled
  `TypeError: 'NoneType' object is not iterable` and a Python traceback
  as soon as one subroutine in the workspace contained a bare `if`.
  Single-file builds were unaffected because they never walk the call
  graph. The translator now always hands over a list, so the walk
  completes. The fix is a side effect of the parser migration, not a
  targeted repair, and the unguarded iterations are still there.

### Removed

- **`src/lexer.py`.** `Lexer`, `LexerError`, and `tokenize_file` no
  longer exist. `src/main.py` keeps the name alive as
  `LexerError = ParseError` purely so its existing `except` blocks still
  compile; see Known regressions for what that alias does to the error
  output.

- **`src/tokens.py` is down to `SourceLocation`** (155 lines to 23).
  `TokenType`, `Token`, and the `KEYWORDS` table are gone. `ast.py`
  references `SourceLocation` on every node, which is why it stayed.

### Known regressions

These are real and confirmed by running 0.3.0 and 0.4.0 side by side.
They are not deliberate removals; treat them as the outstanding cost of
the migration.

- **`--tokens` crashes.** (Fixed in 0.4.1.) The flag is still accepted and still
  documented in the README, but its handler in `src/main.py` calls
  `Lexer(source, input_files[0])` and `Lexer` no longer exists, so
  `ucow --tokens foo.cow` dies with
  `NameError: name 'Lexer' is not defined` and a traceback. There is no
  replacement dump; the uplox scanner is reachable only through
  `parse_string`.

- **`record` and `typedef` declarations nested inside a subroutine body
  are rejected.** (Fixed in 0.4.1.) The grammar admits them — `<sub_body_item>` lists
  `<record_decl>` and `<typedef_decl>` — but the translator's
  `_translate_stmt_or_nested` has no branch for either, so they fall
  through to `_translate_statement` and raise
  `internal: cannot translate statement of kind RecordDecl` (or
  `TypedefDecl`). 0.3.0 parsed both. No `.cow` program under `tests/`
  uses the construct, which is why the test suite did not catch it, but
  `cowgol_compat/inssel.coh` — which ships inside the wheel — declares
  `record NodeSlot` inside `EmitOneInstruction` at line 786, so 0.4.0
  fails on that header where 0.3.0 parsed it.

- **`interface` declarations nested inside a subroutine body lose their
  contents.** (Fixed in 0.4.1.) 0.3.0 produced an `ast.InterfaceDecl` in the body list;
  0.4.0 produces `NestedSubStmt(sub=None)` as a deliberate placeholder,
  so the interface name, parameters, and returns are all discarded and
  any consumer that dereferences `.sub` gets an `AttributeError` on
  `None` rather than a diagnostic.

- **Every syntax error is labelled `Lexer error:`.** (Fixed in 0.4.2.) Because
  `LexerError` is now an alias of `ParseError`, the `except LexerError`
  arm precedes the `except ParseError` arm in both compile paths in
  `src/main.py` (single-file and multi-file), which makes the
  `Parse error:` arm unreachable. Genuine lexical errors also print
  their position twice, with the wrong file name in the inner copy:
  `lex.cow:3:5: <input>:3:5: lexical error at byte 0x24`, because
  `parse_string` prepends its own `SourceLocation` to a uplox
  `ScanError` message that already embeds one, and the generated
  `parse()` entry point takes no file-name argument. 0.3.0 printed
  `lex.cow:3:5: Unexpected character: '$'`.

- **Unknown escape sequences in string literals are now accepted
  silently.** (Fixed in 0.4.3, along with character literals, which
  this entry wrongly recorded as unaffected.) 0.3.0's lexer rejected `"a\qb"` with
  `Unknown escape sequence \q`. The translator's `_unquote_string`
  maps any escape it does not recognise to the escaped character
  itself, so 0.4.0 compiles the same literal as `aqb` and says nothing.
  Character literals are unaffected — the uplox scanner still rejects
  `'\q'`, as `lexical error at byte 0x27`.

- **Docs still describe the old front end.** (Fixed after 0.4.3; see
  Unreleased.) `CLAUDE.md` and
  `OPTIMIZATION_STRATEGY.md` list `src/lexer.py` in the source tree, the
  README still documents `--tokens`, and the README's Requirements list
  still says only "Python 3", with no mention of uplox.

---

History before 0.4.0 was not kept in a changelog; see the git log.
