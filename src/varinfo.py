"""Variable records (``-g``): the ``;#type`` and ``;#var`` markers.

The format is in ``docs/debug_info.md`` of um80_and_friends (Variables).
The markers are written after the last text pass (the peephole in codegen and
``postopt``), because only the final text shows which labels are left.  A
marker is a comment, and a label that this module adds is a plain label, so
the program is the same bytes as without ``-g`` (D2).

The code generator calls ``note`` when it allocates a variable.  After the
passes, ``add_markers`` writes the records into the final text:

- the ``;#type`` lines and the ``;#var`` lines of the globals, after the last
  ``;#file`` line;
- the ``;#var`` lines of a routine, after its ``;#func`` line.  A variable
  that shares its address with the variable of another routine belongs to
  the routine in whose ``;#func`` to ``;#endfunc`` it stands, so a debugger
  uses the record only in a frame of that routine.
"""

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from . import ast
from .types import (ArrayType, CowType, ForwardRefType, IntType,
                    InterfaceType, PtrType, RecordType, VoidType)

_BASE_NAMES = {(1, True): 'schar', (1, False): 'uchar',
               (2, True): 'int', (2, False): 'uint',
               (4, True): 'long', (4, False): 'ulong'}

_FUNC_MARK = re.compile(r'^[ \t]*;[ \t]*#func[ \t]+(\S+)')
_LINE_MARK = re.compile(r'^[ \t]*;[ \t]*#(line|inline|endfunc)\b')
_ENDFUNC_MARK = re.compile(r'^[ \t]*;[ \t]*#endfunc\b')
_FILE_MARK = re.compile(r'^[ \t]*;[ \t]*#file\b')
_LABEL = re.compile(r'^([A-Za-z_@?$.][A-Za-z0-9_@?$.]*)(:|[ \t]+EQU\b)', re.I)


@dataclass
class VarRecord:
    name: str            # source name
    type_text: str       # grammar of debug_info.md
    kind: str            # global, local or param
    label: str           # the label the code uses (v_x or v_sub_x)
    ws_offset: Optional[int]   # offset in _wsbase (workspace mode) or None
    sub: Optional[str]   # label of the owning routine, None for a global
    decl: Tuple[int, int]      # file number, line
    scope: Optional[Tuple[int, int, int]]  # file number, first line, last line
    param_copy: bool = False   # valid only after the copy in the prologue


class TypeWriter:
    """Writes the type text of a CowType and collects the ``;#type`` lines."""

    def __init__(self, checker, program: ast.Program):
        self.checker = checker
        self.types: "OrderedDict[str, str]" = OrderedDict()
        self._busy: Set[str] = set()
        # typedef name -> AST type of the typedef, and record name -> AST
        self.typedefs: Dict[str, ast.Type] = {}
        self.record_ast: Dict[str, ast.RecordType] = {}
        self._scan(program.declarations)
        self._scan(program.statements)

    def _scan(self, items) -> None:
        for item in items:
            if isinstance(item, ast.TypedefDecl):
                self.typedefs.setdefault(item.name, item.type)
            elif isinstance(item, ast.RecordDecl):
                self.record_ast.setdefault(item.record.name, item.record)
            body = getattr(item, 'body', None)
            if isinstance(body, list):
                self._scan(body)

    def _resolve(self, typ: CowType) -> CowType:
        if isinstance(typ, ForwardRefType):
            real = self.checker.global_scope.types.get(typ.name) \
                if hasattr(self.checker.global_scope, 'types') else None
            if real is None and typ.name in self.checker.records:
                real = RecordType(typ.name)
            return real if real is not None else typ
        return typ

    def text(self, typ: CowType, node: Optional[ast.Type] = None) -> str:
        """The type text.  NODE is the AST type that the source wrote, when
        known: it keeps the name of a typedef."""
        if isinstance(node, (ast.NamedType, ast.ScalarType)) \
                and node.name in self.typedefs:
            tag = 'typedef:' + node.name
            if tag not in self.types:
                self.types[tag] = ''   # keep the order, and stop a loop
                target = self.typedefs[node.name]
                self.types[tag] = (f";#type {tag} target="
                                   f"{self.text(self.checker.resolve_type(target), target)}")
            return tag
        typ = self._resolve(typ)
        if isinstance(typ, IntType):
            return f"{_BASE_NAMES[(typ.size, typ.signed)]}:{typ.size}"
        if isinstance(typ, PtrType):
            sub = node.target if isinstance(node, ast.PointerType) else None
            return '*' + self.text(typ.target, sub)
        if isinstance(typ, ArrayType):
            sub = node.element if isinstance(node, ast.ArrayType) else None
            return f"[{typ.size or ''}]" + self.text(typ.element, sub)
        if isinstance(typ, RecordType):
            return self._record(typ.name)
        if isinstance(typ, InterfaceType):
            return '*func(void)'   # the variable holds the address of a routine
        if isinstance(typ, VoidType):
            return 'void'
        try:
            return f"raw:{self.checker.type_size(typ)}"
        except Exception:
            return 'raw:2'

    def _record(self, name: str) -> str:
        info = self.checker.records.get(name)
        if info is None:
            return 'raw:2'
        members = [(f.name, f.type, f.offset) for f in info.fields]
        union = False
        spans = sorted((off, off + max(self.checker.type_size(t), 1))
                       for _, t, off in members)
        for a, b in zip(spans, spans[1:]):
            if b[0] < a[1]:
                union = True
        tag = f"{'union' if union else 'struct'}:{name}"
        if tag in self._busy or tag in self.types:
            return tag
        self._busy.add(tag)
        own = {}
        rec = self.record_ast.get(name)
        if rec is not None:
            own = {f.name: f.type for f in rec.fields}
        parts = []
        for fname, ftype, off in members:
            parts.append(f"{fname}:{self.text(ftype, own.get(fname))}@{off}")
        self._busy.discard(tag)
        line = f";#type {tag} size={info.size}"
        if parts:
            line += " members=" + ','.join(parts)
        self.types[tag] = line
        return tag


def defined_labels(lines: List[str]) -> Set[str]:
    out = set()
    for ln in lines:
        m = _LABEL.match(ln)
        if m:
            out.add(m.group(1))
    return out


_WORD = re.compile(r'[A-Za-z_@?$.][A-Za-z0-9_@?$.]*')


def used_labels(lines: List[str]) -> Set[str]:
    """The names that an instruction or a data line uses (a definition and a
    comment do not count)."""
    used = set()
    for ln in lines:
        code = ln.split(';', 1)[0]
        if not code.strip():
            continue
        words = _WORD.findall(code)
        m = _LABEL.match(code)
        if m:
            words = words[1:]
        used.update(words)
    return used


def add_markers(asm: str, records: List[VarRecord], writer: TypeWriter,
                stats: Optional[Dict[str, int]] = None) -> str:
    """Write the ``;#type`` and ``;#var`` lines into the final text ASM."""
    lines = asm.split('\n')
    labels = defined_labels(lines)
    used = used_labels(lines)
    stats = stats if stats is not None else {}

    def bump(key):
        stats[key] = stats.get(key, 0) + 1

    # Routines: label -> index of the ;#func line, and of its ;#endfunc.
    func_at: Dict[str, int] = {}
    end_at: Dict[str, int] = {}
    open_name = None
    for i, ln in enumerate(lines):
        m = _FUNC_MARK.match(ln)
        if m:
            func_at[m.group(1)] = i
            open_name = m.group(1)
        elif _ENDFUNC_MARK.match(ln) and open_name:
            end_at[open_name] = i
            open_name = None

    def where(rec: VarRecord) -> str:
        if rec.label not in labels:
            return 'none why=no-storage'   # the data was removed
        if rec.sub is not None and rec.label not in used:
            return 'none why=no-storage'   # no instruction uses the slot
        if rec.ws_offset is not None:
            return f"at=_wsbase+{rec.ws_offset}"
        return f"at={rec.label}"

    inserts: Dict[int, List[str]] = {}   # index -> lines to put in front of it
    after: Dict[int, List[str]] = {}     # index -> lines to put after it
    n_label = 0
    prologue_end: Dict[str, int] = {}

    header: List[str] = list(writer.types.values())
    for rec in records:
        loc = where(rec)
        text = (f"\t;#var {rec.name} {rec.type_text} kind={rec.kind} {loc}"
                f" decl={rec.decl[0]}:{rec.decl[1]}")
        if rec.scope:
            f, a, b = rec.scope
            text += f" scope={f}:{a}-{b}"
        if rec.sub is None:
            header.append(text)
            bump('global')
            bump('at-label' if loc.startswith('at=') else 'none')
            continue
        idx = func_at.get(rec.sub)
        if idx is None:
            bump('dropped-no-routine')   # the routine has no ;#func
            continue
        if rec.param_copy and loc.startswith('at='):
            # Valid after the copy: from the first marker after the one of
            # the declaration, to the end of the routine.
            if rec.sub not in prologue_end:
                j = idx + 1
                seen_line = False
                while j < len(lines) and not _ENDFUNC_MARK.match(lines[j]):
                    if _LINE_MARK.match(lines[j]):
                        if seen_line:
                            break
                        seen_line = True
                    j += 1
                n_label += 1
                inserts.setdefault(j, []).append(f"??VS{n_label}:")
                inserts.setdefault(end_at.get(rec.sub, j), []).append(
                    f"??VE{n_label}:")
                prologue_end[rec.sub] = (j, n_label)
            n = prologue_end[rec.sub][1]
            text += f" range=??VS{n}:??VE{n}"
        after.setdefault(idx, []).append(text)
        bump(rec.kind)
        bump('at-label' if loc.startswith('at=') else 'none')

    out: List[str] = []
    # The header goes after the last ;#file line (or after .Z80).
    last_file = -1
    for i, ln in enumerate(lines):
        if _FILE_MARK.match(ln):
            last_file = i
    if last_file < 0:
        for i, ln in enumerate(lines):
            if ln.strip().lower() == '.z80':
                last_file = i
                break
    for i, ln in enumerate(lines):
        for extra in inserts.get(i, ()):
            out.append(extra)
        out.append(ln)
        if i == last_file:
            out.extend(header)
        for extra in after.get(i, ()):
            out.append(extra)
    return '\n'.join(out)
