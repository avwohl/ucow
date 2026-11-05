"""Debug markers (``-g``) in the assembler text.

The markers follow ``docs/debug_info.md`` of um80_and_friends.  A marker is
a whole-line comment: blanks, ``;``, blanks, ``#``, then the kind
(``;#line 1 12``).

The rule of ``-g`` is that the program is the same bytes with and without
the switch.  The text passes of ucow (the peephole in ``codegen`` and the
passes of ``postopt``) are windows over lines of text, so a marker line in
the middle of a window would stop a pattern and change the code.  Each pass
therefore runs on the text as it is without ``-g``:  ``run_clean`` takes
the markers out, runs the pass, and puts each marker back in front of the
line that it was in front of.  When the pass replaced or removed that line,
the marker goes in front of the text that the pass wrote in its place (or in
front of the next line that stayed).  The line number is then possibly not
the right one.  The code is the same.

``run_clean`` also keeps a ``;#noopt begin`` ... ``;#noopt end`` region out
of the pass.  The pass sees a barrier (a label, an instruction that no
pattern knows, a label) in place of the region, and the region comes back
unchanged.
"""

import re
from typing import Callable, Dict, List, Optional, Tuple

# The kinds of docs/debug_info.md.  A comment such as ``; #pragma ...`` has
# another word after the ``#`` and is a plain comment.
_MARKER = re.compile(
    r"^[ \t]*;[ \t]*#(file|line|inline|endinline|func|endfunc|noopt|var|type)"
    r"(?![A-Za-z0-9_])", re.IGNORECASE)
_NOOPT_BEGIN = re.compile(r"^[ \t]*;[ \t]*#noopt[ \t]+begin\b", re.IGNORECASE)
_NOOPT_END = re.compile(r"^[ \t]*;[ \t]*#noopt[ \t]+end\b", re.IGNORECASE)


def is_marker(line: str) -> bool:
    """True for a whole-line ``;#kind`` marker."""
    return '#' in line and bool(_MARKER.match(line))


def marker_kind(line: str) -> str:
    """The lower-case kind of a marker (``line``, ``func``, ...), else ``""``."""
    m = _MARKER.match(line) if '#' in line else None
    return m.group(1).lower() if m else ""


class _Line(str):
    """A line of text that has an identity.  Two lines with the same text are
    two objects, so that ``run_clean`` can tell which line a pass kept."""
    __slots__ = ()


def run_clean(lines: List[str], fn: Callable[[List[str]], List[str]],
              eol: str = '') -> List[str]:
    """Run the text pass ``fn`` on ``lines`` as it runs without ``-g``.

    ``fn`` takes a list of lines and returns a list of lines.  ``eol`` is the
    line end that ``lines`` carry (``''`` or ``'\\n'``).  With no marker in
    ``lines`` this is only ``fn(lines)``.
    """
    if not any(is_marker(ln) for ln in lines):
        return fn(lines)

    clean: List[_Line] = []
    # marks[k]: the lines to write in front of clean[k]
    marks: List[List[str]] = []
    # fake[k]: clean[k] is a barrier line of a noopt region, not text
    fake = set()
    pending: List[str] = []

    i = 0
    n = len(lines)
    region_no = 0
    while i < n:
        ln = lines[i]
        if is_marker(ln) and _NOOPT_BEGIN.match(ln):
            # Collect the region, up to the matching end (or the end of the text).
            depth = 0
            j = i
            while j < n:
                if is_marker(lines[j]):
                    if _NOOPT_BEGIN.match(lines[j]):
                        depth += 1
                    elif _NOOPT_END.match(lines[j]):
                        depth -= 1
                        if depth == 0:
                            break
                j += 1
            region = list(lines[i:j + 1])
            region_no += 1
            for text, carries in ((f"@NOOPT{region_no}B:{eol}", None),
                                  (f"\tNOOPT_REGION{eol}", region),
                                  (f"@NOOPT{region_no}E:{eol}", None)):
                clean.append(_Line(text))
                marks.append(pending + (carries or []))
                pending = []
                fake.add(len(clean) - 1)
            i = j + 1
            continue
        if is_marker(ln):
            pending.append(ln)
        else:
            clean.append(_Line(ln))
            marks.append(pending)
            pending = []
        i += 1
    tail_marks = pending

    fake_text = {str(clean[k]) for k in fake}
    index: Dict[int, int] = {id(x): k for k, x in enumerate(clean)}
    result = fn(list(clean))

    # Which element of the result is a line that stayed, and in what order.
    # A line that moved backwards counts as new text.
    stay: List[Optional[int]] = []
    last = -1
    for x in result:
        k = index.get(id(x)) if isinstance(x, _Line) else None
        if k is not None and k > last:
            stay.append(k)
            last = k
        else:
            stay.append(None)

    # next_stay[j]: the original index of the first staying line at or after j
    next_stay: List[int] = [len(clean)] * (len(result) + 1)
    for j in range(len(result) - 1, -1, -1):
        next_stay[j] = stay[j] if stay[j] is not None else next_stay[j + 1]

    # The text between two staying lines is a gap.  The old lines of a gap
    # (those that the pass removed or rewrote) and the new lines of the gap
    # are matched in order, spread evenly: the marker of the old line number
    # t of nv goes in front of the new line number t*nn//nv of nn.  With as
    # many new lines as old lines (JP rewritten as JR) each marker stays in
    # front of its own line.  With no new line the markers go in front of the
    # next staying line.
    before_new: Dict[int, List[str]] = {}
    placed = set()   # original lines whose markers are in before_new
    cursor = 0   # original lines before cursor have had their markers placed
    j = 0
    while j < len(result):
        if stay[j] is not None:
            cursor = stay[j] + 1
            j += 1
            continue
        run_end = j
        while run_end < len(result) and stay[run_end] is None:
            run_end += 1
        upto = next_stay[j]
        nv, nn = upto - cursor, run_end - j
        for t in range(nv):
            before_new.setdefault(j + t * nn // nv, []).extend(marks[cursor + t])
            placed.add(cursor + t)
        cursor = max(cursor, upto)
        j = run_end

    out: List[str] = []
    cursor = 0
    for j, x in enumerate(result):
        k = stay[j]
        if k is None:
            out.extend(before_new.get(j, ()))
            if x in fake_text:
                raise RuntimeError("a text pass moved a noopt barrier line")
            out.append(str(x))
        else:
            for m in range(cursor, k + 1):
                if m not in placed:
                    out.extend(marks[m])
            cursor = k + 1
            if k not in fake:
                out.append(str(x))
    for m in range(cursor, len(clean)):
        if m not in placed:
            out.extend(marks[m])
    out.extend(tail_marks)
    return out
