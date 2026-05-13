"""Cowgol parser.

After the v3 uplox migration this module is no longer a hand-written
recursive-descent parser — it's a thin translator from the
uplox-generated AST (``uplox_cowgol``) into the ucow ``ast`` class
hierarchy. The public API (``parse_string``, ``parse_file``,
``ParseError``, ``Parser``) is preserved so downstream consumers
keep working unchanged.

What this gives us:

* The LR(1) tables come from ``examples/cowgol_ast.uplox`` in the
  uplox repo, regenerated whenever the grammar changes. No more
  hand-rolled recursive-descent code to maintain.
* The lexer and parser layers (~1300 lines combined) collapse into
  this translator. The ``ast.py`` schema remains the consumer-facing
  shape; the v3 AST is structurally similar enough that translation
  is a mechanical walk.
* Source positions are preserved by computing each ucow ``Node.location``
  from the v3 ``pos`` spans the generator emits.

Notable structural differences the translator bridges:

* v3 ``Program.items`` is a flat list of top-level items; ucow splits
  into ``declarations: List[Declaration]`` and ``statements: List[Statement]``.
* v3 splits sub-decl into ``SubDecl`` / ``SubForwardDecl`` / ``SubImpl``;
  ucow folds them into one ``SubDecl`` with ``is_decl`` / ``is_impl``
  discriminators.
* v3 splits scalar types per keyword (``Int8Type``, ``UInt8Type``, …);
  ucow uses a single ``ScalarType`` with a ``name: str`` field.
* v3 emits ``Negate`` / ``BitNot`` etc; ucow uses ``UnaryOp(op='-')``.
* v3 list children carry typed AST kinds (``ElseIf`` nodes); ucow uses
  raw tuples like ``elseifs: List[tuple]``.
"""

from __future__ import annotations

from typing import Any, List, Optional, Union

from . import ast as _ast
from .tokens import SourceLocation

# Import the generated uplox module. Vendored alongside this file.
from . import uplox_cowgol as _ucg


# ---- Public API: error type kept for compatibility --------------------------


class ParseError(Exception):
    """Error during parsing.

    Mirrors the pre-uplox class so existing consumers' ``except``
    blocks don't need to change. The underlying parser is now
    ``uplox.parse.runtime``; this wrapper translates its ``ParseError``
    into the ucow-shaped one with a ``location: SourceLocation``.
    """

    def __init__(self, message: str, location: SourceLocation):
        self.location = location
        super().__init__(f"{location}: {message}")


# ---- Public entry points ----------------------------------------------------


def parse_file(filename: str) -> _ast.Program:
    """Parse a Cowgol source file."""
    with open(filename, "r", encoding="utf-8") as fh:
        source = fh.read()
    return parse_string(source, filename)


def parse_string(source: str, filename: str = "<input>") -> _ast.Program:
    """Parse a Cowgol source string.

    Calls the uplox-generated parser, then walks the resulting v3 AST
    to produce ucow's ``ast.Program``. Raises :class:`ParseError` on
    syntax or lexical errors (both translated from the uplox runtime).
    """
    # ScanError lives in uplox.lex.scanner; the generated module
    # doesn't re-export it, so we look it up via the runtime import.
    from uplox.lex.scanner import ScanError

    try:
        v3_root = _ucg.parse(source)
    except _ucg.ParseError as e:
        tok = getattr(e, "token", None)
        if tok is not None:
            loc = SourceLocation(filename, tok.line, tok.column)
        else:
            loc = SourceLocation(filename, 1, 1)
        raise ParseError(str(e), loc) from e
    except ScanError as e:
        loc = SourceLocation(filename, getattr(e, "line", 1), getattr(e, "column", 1))
        raise ParseError(str(e), loc) from e
    return _translate(v3_root, filename)


class Parser:
    """Compatibility shim — wraps :func:`parse_string` over a source string.

    Pre-v3 ucow constructed a ``Parser(Lexer(source, filename))`` and
    called ``.parse()``. The shim keeps that two-step interface working
    for now; new code should call :func:`parse_string` directly.
    """

    def __init__(self, lexer_or_source):
        # Old code passed a Lexer instance; new code can pass a raw string.
        if hasattr(lexer_or_source, "source"):
            self._source = lexer_or_source.source
            self._filename = getattr(lexer_or_source, "filename", "<input>")
        else:
            self._source = str(lexer_or_source)
            self._filename = "<input>"

    def parse(self) -> _ast.Program:
        return parse_string(self._source, self._filename)


# =============================================================================
# Translation: v3 AST -> ucow ast.py classes
# =============================================================================


def _translate(v3: Any, filename: str) -> _ast.Program:
    """Top-level entry: translate the v3 Program into ucow's Program."""
    assert isinstance(v3, _ucg.Program), f"expected Program, got {type(v3).__name__}"
    loc = _loc(filename, v3)
    declarations: list[_ast.Declaration] = []
    statements: list[_ast.Statement] = []
    for item in v3.items:
        tx = _translate_top_item(item, filename)
        if tx is None:
            continue
        if isinstance(tx, _ast.Declaration):
            declarations.append(tx)
        elif isinstance(tx, _ast.Statement):
            statements.append(tx)
        else:
            raise ParseError(
                f"internal: unrecognised top-level item type {type(tx).__name__}",
                _loc(filename, item),
            )
    return _ast.Program(location=loc, declarations=declarations, statements=statements)


# ---- Location helper --------------------------------------------------------


def _loc(filename: str, v3_node: Any) -> SourceLocation:
    """Pull a SourceLocation off a v3 AST node (or a parse-tree Token)."""
    pos = getattr(v3_node, "pos", None)
    if pos is not None:
        return SourceLocation(filename, pos.start_line or 1, pos.start_column or 1)
    line = getattr(v3_node, "line", None)
    col = getattr(v3_node, "column", None)
    if line is not None:
        return SourceLocation(filename, line, col or 1)
    return SourceLocation(filename, 1, 1)


# ---- Top-level dispatch -----------------------------------------------------


def _translate_top_item(item: Any, filename: str) -> Optional[Any]:
    """A top-level item dispatches to a Declaration or Statement."""
    if isinstance(item, _ucg.IncludeDecl):
        return _ast.IncludeDecl(
            location=_loc(filename, item),
            path=_unquote_string(item.path.text),
        )
    if isinstance(item, _ucg.RecordDecl):
        return _translate_record_decl(item, filename)
    if isinstance(item, _ucg.TypedefDecl):
        return _ast.TypedefDecl(
            location=_loc(filename, item),
            name=item.name.text,
            type=_translate_type(item.type, filename),
        )
    if isinstance(item, _ucg.InterfaceDecl):
        return _translate_interface_decl(item, filename)
    if isinstance(item, (_ucg.SubDecl, _ucg.SubForwardDecl, _ucg.SubImpl)):
        return _translate_sub(item, filename)
    return _translate_statement(item, filename)


def _translate_record_decl(rec: Any, filename: str) -> _ast.RecordDecl:
    fields: list[_ast.RecordField] = []
    for f in rec.fields:
        fields.append(_translate_record_field(f, filename))
    record_type = _ast.RecordType(
        location=_loc(filename, rec),
        name=rec.name.text,
        fields=fields,
        base=rec.base.text if rec.base is not None else None,
    )
    return _ast.RecordDecl(location=_loc(filename, rec), record=record_type)


def _translate_record_field(f: Any, filename: str) -> _ast.RecordField:
    return _ast.RecordField(
        location=_loc(filename, f),
        name=f.name.text,
        type=_translate_type(f.type, filename),
        offset=int(f.offset.text) if f.offset is not None else None,
    )


def _translate_interface_decl(iface: Any, filename: str) -> _ast.InterfaceDecl:
    params: list[_ast.Parameter] = []
    if iface.params is not None:
        for p in iface.params:
            params.append(_translate_parameter(p, filename))
    returns: list[_ast.Parameter] = []
    if iface.returns is not None:
        for r in iface.returns:
            returns.append(_translate_parameter(r, filename))
    itype = _ast.InterfaceType(
        location=_loc(filename, iface),
        name=iface.name.text,
        params=params,
        returns=returns,
    )
    return _ast.InterfaceDecl(location=_loc(filename, iface), interface=itype)


def _translate_parameter(p: Any, filename: str) -> _ast.Parameter:
    return _ast.Parameter(
        location=_loc(filename, p),
        name=p.name.text,
        type=_translate_type(p.type, filename),
    )


def _translate_sub(sub: Any, filename: str) -> _ast.SubDecl:
    """Collapse the v3 SubDecl/SubForwardDecl/SubImpl trio into ucow's SubDecl."""
    loc = _loc(filename, sub)

    name = sub.name.text
    attrs = list(sub.attrs) if hasattr(sub, "attrs") and sub.attrs else []
    extern_name: Optional[str] = None
    for attr in attrs:
        if isinstance(attr, _ucg.ExternAttr):
            extern_name = _unquote_string(attr.name.text)

    implements: Optional[str] = None
    if getattr(sub, "implements", None) is not None:
        implements = sub.implements.text

    # Params/returns come from the signature on SubDecl/SubForwardDecl;
    # SubImpl inherits from its matching @decl, so we leave them empty
    # and let downstream resolution fill them.
    params: list[_ast.Parameter] = []
    returns: list[_ast.Parameter] = []
    sig = getattr(sub, "sig", None)
    if sig is not None:
        if sig.params is not None:
            for p in sig.params:
                params.append(_translate_parameter(p, filename))
        if sig.returns is not None:
            for r in sig.returns:
                returns.append(_translate_parameter(r, filename))

    body: Optional[list[_ast.Statement]]
    is_decl = False
    is_impl = False
    if isinstance(sub, _ucg.SubForwardDecl):
        body = None
        is_decl = True
    elif isinstance(sub, _ucg.SubImpl):
        body = _translate_stmt_list(sub.body, filename)
        is_impl = True
    else:  # SubDecl
        tail = sub.tail
        if isinstance(tail, _ucg.SubBody):
            body = _translate_stmt_list(tail.body, filename)
        else:  # SubExternal — no body
            body = None

    return _ast.SubDecl(
        location=loc,
        name=name,
        params=params,
        returns=returns,
        body=body,
        extern_name=extern_name,
        implements=implements,
        is_decl=is_decl,
        is_impl=is_impl,
    )


def _translate_stmt_list(items: Any, filename: str) -> list[_ast.Statement]:
    """Map a v3 list of statement-like items (sub_body_item) to ucow Statements."""
    out: list[_ast.Statement] = []
    if items is None:
        return out
    for v in items:
        tx = _translate_stmt_or_nested(v, filename)
        if tx is not None:
            out.append(tx)
    return out


def _translate_stmt_or_nested(v: Any, filename: str) -> Optional[_ast.Statement]:
    """A sub_body_item is either a stmt, a sub_form (nested), or an interface decl."""
    if isinstance(v, (_ucg.SubDecl, _ucg.SubForwardDecl, _ucg.SubImpl)):
        return _ast.NestedSubStmt(
            location=_loc(filename, v),
            sub=_translate_sub(v, filename),
        )
    if isinstance(v, _ucg.InterfaceDecl):
        # No NestedInterfaceStmt in ucow; the pre-uplox parser also
        # admitted nested interface decls but downstream rarely uses
        # them. Emit a marker-style NestedSubStmt with sub=None as the
        # closest match; consumers that don't expect it will fail loudly.
        return _ast.NestedSubStmt(
            location=_loc(filename, v),
            sub=None,  # type: ignore[arg-type]
        )
    return _translate_statement(v, filename)


# ---- Statements -------------------------------------------------------------


def _translate_statement(s: Any, filename: str) -> _ast.Statement:
    loc = _loc(filename, s)
    if isinstance(s, _ucg.VarDecl):
        return _ast.VarDecl(
            location=loc,
            name=s.name.text,
            type=_translate_type(s.type, filename) if s.type is not None else None,
            init=_translate_expression(s.init, filename) if s.init is not None else None,
        )
    if isinstance(s, _ucg.ConstDecl):
        return _ast.ConstDecl(
            location=loc, name=s.name.text, value=_translate_expression(s.value, filename)
        )
    if isinstance(s, _ucg.ReturnStmt):
        return _ast.ReturnStmt(location=loc)
    if isinstance(s, _ucg.BreakStmt):
        return _ast.BreakStmt(location=loc)
    if isinstance(s, _ucg.ContinueStmt):
        return _ast.ContinueStmt(location=loc)
    if isinstance(s, _ucg.IfStmt):
        elseifs: list[tuple] = []
        for clause in s.elseifs:
            elseifs.append(
                (
                    _translate_expression(clause.cond, filename),
                    _translate_stmt_list(clause.body, filename),
                )
            )
        else_body: Optional[list[_ast.Statement]] = None
        if s.otherwise is not None:
            else_body = _translate_stmt_list(s.otherwise, filename)
        return _ast.IfStmt(
            location=loc,
            condition=_translate_expression(s.cond, filename),
            then_body=_translate_stmt_list(s.then_body, filename),
            elseifs=elseifs,
            else_body=else_body,
        )
    if isinstance(s, _ucg.WhileStmt):
        return _ast.WhileStmt(
            location=loc,
            condition=_translate_expression(s.cond, filename),
            body=_translate_stmt_list(s.body, filename),
        )
    if isinstance(s, _ucg.LoopStmt):
        return _ast.LoopStmt(location=loc, body=_translate_stmt_list(s.body, filename))
    if isinstance(s, _ucg.CaseStmt):
        whens: list[tuple] = []
        else_body: Optional[list[_ast.Statement]] = None
        for arm in s.arms:
            if isinstance(arm, _ucg.CaseElse):
                else_body = _translate_stmt_list(arm.body, filename)
            else:  # CaseArm
                values = [_translate_expression(v, filename) for v in arm.values]
                body = _translate_stmt_list(arm.body, filename)
                whens.append((values, body))
        return _ast.CaseStmt(
            location=loc,
            expr=_translate_expression(s.subject, filename),
            whens=whens,
            else_body=else_body,
        )
    if isinstance(s, _ucg.Assignment):
        return _ast.Assignment(
            location=loc,
            target=_translate_expression(s.target, filename),
            value=_translate_expression(s.value, filename),
        )
    if isinstance(s, _ucg.MultiAssignment):
        targets = [_translate_expression(t, filename) for t in s.targets]
        value = _translate_expression(s.value, filename)
        if not isinstance(value, _ast.Call):
            raise ParseError(
                "Multi-assignment requires a call",
                _loc(filename, s.value) if s.value is not None else loc,
            )
        return _ast.MultiAssignment(location=loc, targets=targets, value=value)
    if isinstance(s, _ucg.ExprStmt):
        return _ast.ExprStmt(location=loc, expr=_translate_expression(s.expr, filename))
    if isinstance(s, _ucg.AsmStmt):
        # ucow's asm parts list alternates str and Expression. The leading
        # token is always a literal (the first piece of asm text); the
        # rest come from `more` and may be string literals or expressions.
        parts: list[Union[str, _ast.Expression]] = []
        parts.append(_unquote_string(s.first.text))
        for expr in s.more:
            tx = _translate_expression(expr, filename)
            if isinstance(tx, _ast.StringLiteral):
                parts.append(tx.value)
            else:
                parts.append(tx)
        return _ast.AsmStmt(location=loc, parts=parts)
    raise ParseError(
        f"internal: cannot translate statement of kind {type(s).__name__}", loc
    )


# ---- Expressions ------------------------------------------------------------


def _translate_expression(e: Any, filename: str) -> _ast.Expression:
    loc = _loc(filename, e)
    if isinstance(e, _ucg.NumberLiteral):
        return _ast.NumberLiteral(location=loc, value=_parse_number(e.value.text))
    if isinstance(e, _ucg.StringLiteral):
        return _ast.StringLiteral(location=loc, value=_unquote_string(e.value.text))
    if isinstance(e, _ucg.NilLiteral):
        return _ast.NilLiteral(location=loc)
    if isinstance(e, _ucg.Identifier):
        return _ast.Identifier(location=loc, name=e.name.text)
    if isinstance(e, _ucg.BinaryOp):
        return _ast.BinaryOp(
            location=loc,
            op=e.op.text,
            left=_translate_expression(e.lhs, filename),
            right=_translate_expression(e.rhs, filename),
        )
    if isinstance(e, _ucg.LogicalOp):
        return _ast.LogicalOp(
            location=loc,
            op=e.op.text,
            left=_translate_expression(e.lhs, filename),
            right=_translate_expression(e.rhs, filename),
        )
    if isinstance(e, _ucg.Comparison):
        return _ast.Comparison(
            location=loc,
            op=e.op.text,
            left=_translate_expression(e.lhs, filename),
            right=_translate_expression(e.rhs, filename),
        )
    if isinstance(e, _ucg.NotOp):
        return _ast.NotOp(location=loc, operand=_translate_expression(e.operand, filename))
    if isinstance(e, _ucg.Cast):
        return _ast.Cast(
            location=loc,
            expr=_translate_expression(e.target, filename),
            target_type=_translate_type(e.type, filename),
        )
    # v3 splits unary forms per-keyword; ucow folds them into UnaryOp
    # (and AddressOf, Dereference, etc as separate classes).
    if isinstance(e, _ucg.Negate):
        return _ast.UnaryOp(
            location=loc, op="-", operand=_translate_expression(e.operand, filename)
        )
    if isinstance(e, _ucg.BitNot):
        return _ast.UnaryOp(
            location=loc, op="~", operand=_translate_expression(e.operand, filename)
        )
    if isinstance(e, _ucg.AddressOf):
        return _ast.AddressOf(
            location=loc, operand=_translate_expression(e.operand, filename)
        )
    if isinstance(e, _ucg.SizeOf):
        return _ast.SizeOf(
            location=loc, target=_translate_expression(e.operand, filename)
        )
    if isinstance(e, _ucg.BytesOf):
        return _ast.BytesOf(
            location=loc, target=_translate_expression(e.operand, filename)
        )
    if isinstance(e, _ucg.BytesOfType):
        return _ast.BytesOf(location=loc, target=_translate_scalar_kind(e.scalar, filename))
    if isinstance(e, _ucg.IndexOf):
        return _ast.IndexOf(
            location=loc, target=_translate_expression(e.operand, filename)
        )
    if isinstance(e, _ucg.Next):
        return _ast.Next(
            location=loc, pointer=_translate_expression(e.operand, filename)
        )
    if isinstance(e, _ucg.Prev):
        return _ast.Prev(
            location=loc, pointer=_translate_expression(e.operand, filename)
        )
    if isinstance(e, _ucg.Call):
        return _ast.Call(
            location=loc,
            target=_translate_expression(e.callee, filename),
            args=[_translate_expression(a, filename) for a in e.args],
        )
    if isinstance(e, _ucg.ArrayAccess):
        return _ast.ArrayAccess(
            location=loc,
            array=_translate_expression(e.array, filename),
            index=_translate_expression(e.index, filename),
        )
    if isinstance(e, _ucg.FieldAccess):
        return _ast.FieldAccess(
            location=loc,
            record=_translate_expression(e.record, filename),
            field=e.field.text,
        )
    if isinstance(e, _ucg.ArrayInitializer):
        return _ast.ArrayInitializer(
            location=loc,
            elements=[_translate_expression(x, filename) for x in (e.items or [])],
        )
    if isinstance(e, _ucg.Dereference):
        return _ast.Dereference(
            location=loc, pointer=_translate_expression(e.operand, filename)
        )
    raise ParseError(
        f"internal: cannot translate expression of kind {type(e).__name__}", loc
    )


# ---- Types ------------------------------------------------------------------


def _translate_type(t: Any, filename: str) -> _ast.Type:
    """Translate a v3 Type (base + array_suffixes) into ucow's Type tree.

    Each ``[N]`` suffix wraps the current type in an ``ArrayType``
    outer-to-inner — so ``[uint8][5]`` becomes
    ``ArrayType(PointerType(ScalarType('uint8')), size=5)``.
    """
    if t is None:
        return None  # type: ignore[return-value]
    assert isinstance(t, _ucg.Type), f"expected Type, got {type(t).__name__}"
    base = _translate_base_type(t.base, filename)
    for suffix in t.suffixes or []:
        loc = _loc(filename, suffix)
        if isinstance(suffix, _ucg.ArraySized):
            size_expr = _translate_expression(suffix.size, filename)
            base = _ast.ArrayType(location=loc, element=base, size=size_expr)
        else:  # ArrayInferred
            base = _ast.ArrayType(location=loc, element=base, size=None)
    return base


def _translate_base_type(b: Any, filename: str) -> _ast.Type:
    loc = _loc(filename, b)
    if isinstance(
        b,
        (
            _ucg.Int8Type, _ucg.UInt8Type, _ucg.Int16Type, _ucg.UInt16Type,
            _ucg.Int32Type, _ucg.UInt32Type, _ucg.IntPtrType,
        ),
    ):
        return _translate_scalar_kind(b, filename)
    if isinstance(b, _ucg.PointerType):
        return _ast.PointerType(location=loc, target=_translate_type(b.target, filename))
    if isinstance(b, _ucg.SizeOfType):
        return _ast.SizeOfType(location=loc, target=b.target.text)
    if isinstance(b, _ucg.IndexOfType):
        return _ast.IndexOfType(location=loc, target=b.target.text)
    if isinstance(b, _ucg.NamedType):
        return _ast.NamedType(location=loc, name=b.name.text)
    if isinstance(b, _ucg.RangedIntType):
        return _ast.RangedIntType(
            location=loc,
            min_expr=_translate_expression(b.lo, filename),
            max_expr=_translate_expression(b.hi, filename),
        )
    raise ParseError(
        f"internal: cannot translate base_type of kind {type(b).__name__}", loc
    )


_SCALAR_NAMES = {
    _ucg.Int8Type: "int8",
    _ucg.UInt8Type: "uint8",
    _ucg.Int16Type: "int16",
    _ucg.UInt16Type: "uint16",
    _ucg.Int32Type: "int32",
    _ucg.UInt32Type: "uint32",
    _ucg.IntPtrType: "intptr",
}


def _translate_scalar_kind(b: Any, filename: str) -> _ast.ScalarType:
    name = _SCALAR_NAMES.get(type(b))
    if name is None:
        raise ParseError(
            f"internal: not a scalar kind: {type(b).__name__}", _loc(filename, b)
        )
    return _ast.ScalarType(location=_loc(filename, b), name=name)


# ---- Literal-text helpers ---------------------------------------------------


def _parse_number(text: str) -> int:
    """Parse a Cowgol numeric literal into an int.

    Handles 0x / 0o / 0b / 0d prefixes, underscores within digit runs,
    and single-quoted character literals (``'A'``, ``'\\n'``).
    """
    if not text:
        raise ValueError("empty number literal")
    if text[0] == "'":
        body = text[1:-1]
        if body.startswith("\\"):
            ch = body[1]
            return {"n": 10, "t": 9, "r": 13, "\\": 92, "'": 39, '"': 34, "0": 0}.get(
                ch, ord(ch)
            )
        return ord(body)
    flat = text.replace("_", "")
    if flat.startswith(("0x", "0X")):
        return int(flat[2:], 16)
    if flat.startswith(("0o", "0O")):
        return int(flat[2:], 8)
    if flat.startswith(("0b", "0B")):
        return int(flat[2:], 2)
    if flat.startswith(("0d", "0D")):
        return int(flat[2:], 10)
    return int(flat, 10)


def _unquote_string(text: str) -> str:
    """Strip the surrounding double quotes and process backslash escapes."""
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        body = text[1:-1]
    else:
        body = text
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            esc = body[i + 1]
            out.append(
                {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"', "0": "\0"}.get(
                    esc, esc
                )
            )
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)
