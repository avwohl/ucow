# Changelog

Notable changes to ucow, a Cowgol compiler targeting 8080/Z80 CP/M.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

- **`--tokens` crashes.** The flag is still accepted and still
  documented in the README, but its handler in `src/main.py` calls
  `Lexer(source, input_files[0])` and `Lexer` no longer exists, so
  `ucow --tokens foo.cow` dies with
  `NameError: name 'Lexer' is not defined` and a traceback. There is no
  replacement dump; the uplox scanner is reachable only through
  `parse_string`.

- **`record` and `typedef` declarations nested inside a subroutine body
  are rejected.** The grammar admits them — `<sub_body_item>` lists
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
  contents.** 0.3.0 produced an `ast.InterfaceDecl` in the body list;
  0.4.0 produces `NestedSubStmt(sub=None)` as a deliberate placeholder,
  so the interface name, parameters, and returns are all discarded and
  any consumer that dereferences `.sub` gets an `AttributeError` on
  `None` rather than a diagnostic.

- **Every syntax error is labelled `Lexer error:`.** Because
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
  silently.** 0.3.0's lexer rejected `"a\qb"` with
  `Unknown escape sequence \q`. The translator's `_unquote_string`
  maps any escape it does not recognise to the escaped character
  itself, so 0.4.0 compiles the same literal as `aqb` and says nothing.
  Character literals are unaffected — the uplox scanner still rejects
  `'\q'`, as `lexical error at byte 0x27`.

- **Docs still describe the old front end.** `CLAUDE.md` and
  `OPTIMIZATION_STRATEGY.md` list `src/lexer.py` in the source tree, the
  README still documents `--tokens`, and the README's Requirements list
  still says only "Python 3", with no mention of uplox.

---

History before 0.4.0 was not kept in a changelog; see the git log.
