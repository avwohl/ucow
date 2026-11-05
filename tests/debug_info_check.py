#!/usr/bin/env python3
"""Tests for `ucow -g` (docs/debug_info_g.md).

run_tests.sh runs this file.  Exit status 0 if every check passed, else 1.

1. The D2 rule: a program has the same .COM bytes with and without -g.  Each
   program of tests/ and examples/ is built with and without -g under four
   option sets.  The .mac text without its marker lines is also the same.
2. The markers: the .dbg file of tests/debug_test.cow names the files and the
   routines, has the inline stack, and has a line for each tagged statement.
3. Coverage: each byte of a routine of a program has a source line.
4. debugmarks.run_clean: the order of the markers, a ;#noopt region, and the
   rule that a tail is not merged into another routine.
5. A live udb80 session against cpmemu --gdb (needs both; UCOW_REQUIRE_UDB80=1
   makes a missing one a failure instead of a skip).
6. Variables (phase 8): the ;#var and ;#type records of tests/vars_test.cow
   and tests/vars_ws/ in prog.dbg, the statistics over the whole suite, and a
   live udb80 session that prints each kind of variable at a known value.
"""

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, 'tests')
LIB = os.path.join(ROOT, 'lib')
UCOW = os.path.join(ROOT, 'ucow')
sys.path.insert(0, ROOT)

from src import debugmarks  # noqa: E402
from src.postopt import optimize_asm  # noqa: E402

OPTION_SETS = [[], ['-O0'], ['--no-post-opt'], ['-O0', '--no-post-opt']]

failures = []
passed = 0


def check(ok, what):
    global passed
    if ok:
        passed += 1
    else:
        failures.append(what)
        print(f"FAIL {what}")


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def build(work, srcs, flags, name, lib=False):
    """Compile, assemble and link SRCS (paths in WORK).  Returns
    (mac path, rel path, com path or None, error text)."""
    mac = os.path.join(work, name + '.mac')
    cmd = [sys.executable, UCOW] + srcs + ['-o', mac, '-I', work] + flags
    if lib:
        cmd.append('-L')
    r = run(cmd, work)
    if r.returncode:
        return mac, None, None, 'ucow: ' + r.stderr[-300:]
    debug = ['--debug-info'] if '-g' in flags else []
    r = run(['um80'] + debug + ['-I', LIB, name + '.mac'], work)
    rel = os.path.join(work, name + '.rel')
    if r.returncode or not os.path.exists(rel):
        return mac, None, None, 'um80: ' + (r.stdout + r.stderr)[-300:]
    if lib:
        return mac, rel, None, ''
    com = os.path.join(work, name + '.com')
    r = run(['ul80'] + debug + [name + '.rel', '-o', name + '.com'], work)
    if r.returncode or not os.path.exists(com):
        return mac, rel, None, 'ul80: ' + (r.stdout + r.stderr)[-300:]
    return mac, rel, com, ''


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


MARKER = re.compile(
    r'^[ \t]*;[ \t]*#(file|line|inline|endinline|func|endfunc|noopt|var|type)\b',
    re.I)
# -g also writes the labels ??VSn and ??VEn (the range of a parameter): a
# label writes no byte, and the .com is compared, but the .mac text is not.
VAR_LABEL = re.compile(r'^\?\?V[SE]\d+:$')


def without_markers(path):
    with open(path) as f:
        return [ln for ln in f.read().split('\n')
                if not MARKER.match(ln) and not VAR_LABEL.match(ln)]


def programs(work):
    """The programs of the suite: (name, [sources], is_library)."""
    out = []
    for f in sorted(os.listdir(TESTS)):
        if f.endswith('.cow'):
            shutil.copy(os.path.join(TESTS, f), work)
        elif f.endswith('.coh'):
            shutil.copy(os.path.join(TESTS, f), work)
    for f in sorted(os.listdir(work)):
        if f.endswith('.cow'):
            out.append((f[:-4], [f], False))
    mf = os.path.join(ROOT, 'examples', 'multifile')
    for f in os.listdir(mf):
        if f.endswith('.cow'):
            shutil.copy(os.path.join(mf, f), work)
    out.append(('mf_div', ['test_div.cow'], False))
    out.append(('mf_print', ['test_print.cow'], False))
    ws = os.path.join(TESTS, 'workspace_test')
    shutil.copy(os.path.join(ws, 'main.cow'), os.path.join(work, 'ws_main.cow'))
    shutil.copy(os.path.join(ws, 'lib.cow'), os.path.join(work, 'ws_lib.cow'))
    shutil.copy(os.path.join(ws, 'lib.coh'), work)
    out.append(('ws', ['ws_main.cow', 'ws_lib.cow'], False))
    vw = os.path.join(TESTS, 'vars_ws')
    shutil.copy(os.path.join(vw, 'main.cow'), os.path.join(work, 'vw_main.cow'))
    shutil.copy(os.path.join(vw, 'lib.cow'), os.path.join(work, 'vw_lib.cow'))
    shutil.copy(os.path.join(vw, 'lib.coh'), os.path.join(work, 'lib.coh'))
    out.append(('vw', ['vw_main.cow', 'vw_lib.cow'], False))
    return out


def d2(work, progs):
    """1. The same bytes with and without -g."""

    def one(job):
        (name, srcs, _), flags = job
        tag = name + '_' + ''.join(c for c in ' '.join(flags) if c.isalnum())
        w = tempfile.mkdtemp(dir=work)
        for f in os.listdir(work):
            p = os.path.join(work, f)
            if os.path.isfile(p):
                shutil.copy(p, w)
        res = {}
        for g in ([], ['-g']):
            mac, rel, com, err = build(w, srcs, flags + g, tag + ('_g' if g else ''))
            res[bool(g)] = (mac, com, err)
        return tag, res

    jobs = [(p, f) for p in progs for f in OPTION_SETS]
    nbuilt = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for tag, res in ex.map(one, jobs):
            plain, dbg = res[False], res[True]
            check(plain[2] == dbg[2] == '' and plain[1] and dbg[1],
                  f"D2 {tag}: build failed: {plain[2] or dbg[2]}")
            if plain[1] and dbg[1]:
                nbuilt += 1
                check(sha(plain[1]) == sha(dbg[1]), f"D2 {tag}: .COM differs with -g")
                check(without_markers(dbg[0]) == without_markers(plain[0]),
                      f"D2 {tag}: .mac text differs with -g apart from markers")
    # A library module (-L) is a .rel file, not a .COM
    for flags in OPTION_SETS:
        if '--no-post-opt' not in flags:
            continue    # postopt drops the data of a library module: fails with and without -g
        rels = []
        for g in ([], ['-g']):
            # The module name is the file name, so the two builds use the same name
            w = tempfile.mkdtemp(dir=work)
            shutil.copy(os.path.join(ROOT, 'examples', 'multifile', 'b.cow'), w)
            rels.append(build(w, ['b.cow'], flags + g, 'b', lib=True)[1])
        check(all(rels) and sha(rels[0]) == sha(rels[1]), f"D2 library {flags}: .rel differs with -g")
    return nbuilt


def dbg_of(work, srcs, name, extra=()):
    mac, rel, com, err = build(work, srcs, ['-g'] + list(extra), name)
    if not com:
        check(False, f"build {name}: {err}")
        return None
    with open(os.path.join(work, name + '.dbg')) as f:
        return json.load(f)


def covered(dbg):
    """(bytes in routines, bytes of them with a source line)."""
    rows = [(r[0], r[1]) for r in dbg['lines']]
    total = have = 0
    for fn in dbg['funcs']:
        if fn['end'] is None:
            continue
        for a in range(fn['start'], fn['end']):
            total += 1
            if any(s <= a < e for s, e in rows):
                have += 1
    return total, have


def markers_and_coverage(work, progs):
    """2 and 3."""
    for f in ('debug_test.cow', 'debug_inc.coh'):
        shutil.copy(os.path.join(TESTS, f), work)
    dbg = dbg_of(work, ['debug_test.cow'], 'dt')
    if not dbg:
        return
    files = [os.path.basename(p) for p in dbg['files']]
    check('debug_test.cow' in files and 'debug_inc.coh' in files,
          f"markers: the file table has both source files, has {files}")
    names = {fn['name'] for fn in dbg['funcs']}
    check(names == {'Count', 'Sum3', 'Classify', 'Twice', '_main'},
          f"markers: routines are {sorted(names)}")
    check(all(fn['frame'] == 'static' and fn['pops'] == 0 for fn in dbg['funcs']),
          "markers: every routine is frame=static pops=0")
    check(any(r[3:] and r[3][0][0] == 'Bump' for r in dbg['lines']),
          "markers: the inlined Bump has an inline stack")
    # a line of each tagged statement has code
    tags = {}
    for f in ('debug_test.cow', 'debug_inc.coh'):
        with open(os.path.join(TESTS, f)) as fh:
            for no, ln in enumerate(fh, 1):
                m = re.search(r'# @(\w+)', ln)
                if m:
                    tags[m.group(1)] = (f, no)
    lines = set()
    for r in dbg['lines']:
        for g in r[2]:
            lines.add((os.path.basename(dbg['files'][g[0]]), g[1]))
    for tag, where in tags.items():
        check(where in lines, f"markers: tagged line @{tag} {where} has code")
    # an inlined statement is stepped as the callee line, with the call site on the stack
    bump = tags['bump']
    check(any(os.path.basename(dbg['files'][g[0]]) == bump[0] and g[1] == bump[1]
              and len(r) > 3 for r in dbg['lines'] for g in r[2]),
          "markers: the inlined statement has the inline stack")

    # 3. coverage over the whole suite
    total = have = 0
    for name, srcs, _ in progs:
        d = dbg_of(work, srcs, 'cv_' + name)
        if d:
            t, h = covered(d)
            total += t
            have += h
            check(t == h, f"coverage {name}: {h} of {t} routine bytes have a source line")
    print(f"coverage: {have} of {total} bytes of routines have a source line")


def vrows(dbg):
    """The vars table as dicts."""
    out = []
    for r in dbg.get('vars') or []:
        r = list(r) + [None] * (10 - len(r))
        out.append({'name': r[0], 'type': r[1], 'kind': r[2], 'loc': r[3],
                    'func': r[4], 'scope': r[5], 'ranges': r[6], 'decl': r[7],
                    'why': r[9]})
    return out


def variables(work, progs):
    """6. The records in prog.dbg, and the statistics over the suite."""
    shutil.copy(os.path.join(TESTS, 'vars_test.cow'), work)
    dbg = dbg_of(work, ['vars_test.cow'], 'vt')
    if dbg:
        v = {(r['func'] is not None, r['name']): r for r in vrows(dbg)}
        want = {'g8': 'schar:1', 'gu8': 'uchar:1', 'g16': 'int:2', 'gu16': 'uint:2',
                'g32': 'long:4', 'gu32': 'ulong:4', 'gb': 'typedef:byte',
                'gc': 'typedef:counter', 'gnums': '[4]uint:2', 'gbox': 'struct:Box',
                'gval': 'union:Value', 'gtext': '*uchar:1', 'gptr': '*struct:Point',
                'ginit': '[5]uchar:1', 'res': 'uint:2'}
        for name, typ in want.items():
            r = v.get((False, name))
            check(r is not None and r['type'] == typ and r['kind'] == 'global'
                  and r['loc'] and r['loc'][0] == 'at',
                  f"vars: global {name} has type {typ} at an address, has {r}")
        check(set(dbg['types']) == {'typedef:byte', 'typedef:counter', 'struct:Point',
                                    'struct:Box', 'union:Value'},
              f"vars: the types are {sorted(dbg['types'])}")
        for name, kind in (('a', 'param'), ('b', 'param'), ('c', 'param'),
                           ('r', 'local'), ('loc8', 'local'), ('larr', 'local'),
                           ('lpt', 'local'), ('n', 'param'), ('oloc', 'local'),
                           ('obox', 'local')):
            r = v.get((True, name))
            check(r is not None and r['kind'] == kind and r['loc'],
                  f"vars: {kind} {name} is at an address, has {r}")
        # A parameter is valid after the copy only: it has a range.
        check(all(r['ranges'] for r in vrows(dbg) if r['kind'] == 'param' and r['loc']),
              "vars: each parameter has a range (valid after the copy)")
    # workspace mode: two routines at one address, each record names its routine
    vw = ['vw_main.cow', 'vw_lib.cow']
    dbg = dbg_of(work, vw, 'vw')
    if dbg:
        rows = vrows(dbg)
        funcs = {fn['name']: fn['start'] for fn in dbg['funcs']}
        by = {(r['func'], r['name']): r for r in rows}
        abuf = by.get((funcs['process_a'], 'abuf'))
        bbuf = by.get((funcs['process_b'], 'bbuf'))
        check(abuf and bbuf and abuf['loc'] == bbuf['loc'] and abuf['loc'],
              f"vars ws: abuf and bbuf share an address, have {abuf} {bbuf}")
        check(abuf and bbuf and abuf['func'] != bbuf['func'],
              "vars ws: the two records name two routines")
        a1 = by.get((funcs['process_a'], 'a1'))
        check(a1 and a1['why'] == 'no-storage' and a1['loc'] is None,
              "vars ws: a1 has no place and says why (the optimizer removed the store)")
    # statistics over the suite
    kinds, places, nprog, nrec = {}, {}, 0, 0
    for name, srcs, _ in progs:
        d = dbg_of(work, srcs, 'vs_' + name)
        if not d:
            continue
        nprog += 1
        for r in vrows(d):
            nrec += 1
            kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
            p = 'none' if not r['loc'] else r['loc'][0]
            places[p] = places.get(p, 0) + 1
            check(r['loc'] is not None or r['why'],
                  f"vars {name}: {r['name']} has no place and no reason")
    print(f"vars: {nrec} records in {nprog} programs; kinds {sorted(kinds.items())}; "
          f"places {sorted(places.items())}")


def unit():
    """4. run_clean and the passes."""
    # The markers keep their order, and the pass sees no marker.
    seen = []

    def spy(lines):
        seen.append(list(lines))
        return [ln for ln in lines if ln.strip() != 'NOP']

    text = ['\tLD\tA,1', ';#line 1 5', '\tNOP', ';#line 1 6', '\tRET']
    out = debugmarks.run_clean(text, spy)
    check(seen[0] == ['\tLD\tA,1', '\tNOP', '\tRET'], "run_clean: the pass sees no marker")
    check(out == ['\tLD\tA,1', ';#line 1 5', ';#line 1 6', '\tRET'],
          f"run_clean: a deleted line leaves its markers in order, got {out}")
    # A line rewritten one to one keeps its own marker
    out = debugmarks.run_clean(
        ['\tJP\tX', ';#line 1 2', '\tJP\tY'],
        lambda ls: [ln.replace('JP', 'JR') for ln in ls])
    check(out == ['\tJR\tX', ';#line 1 2', '\tJR\tY'],
          f"run_clean: a rewritten line keeps its marker, got {out}")
    # No marker, no wrapper
    check(debugmarks.run_clean(['\tNOP'], lambda ls: ['\tRET']) == ['\tRET'],
          "run_clean: no marker, plain pass")
    # A comment that is not a marker is a plain comment
    check(not debugmarks.is_marker('; #pragma once') and debugmarks.is_marker(' ;  #LINE 1 2'),
          "is_marker: kinds and blanks")

    # A ;#noopt region is not changed, and a pass cannot see into it.
    region = [';#noopt begin', '\tPUSH\tHL', '\tPOP\tHL', '\tNOP', ';#noopt end']
    text = ['\tPUSH\tHL', '\tPOP\tHL'] + region + ['\tPUSH\tHL', '\tPOP\tHL', '\tRET']
    # postopt: register tracking drops the second LD A,(v) outside the region only
    r2 = [';#noopt begin', '\tLD\tA,(v)', '\tLD\tA,(v)', '\tNOP', ';#noopt end']
    t2 = ['\tLD\tA,(v)', '\tLD\tA,(v)'] + r2 + ['\tLD\tA,(v)', '\tLD\tA,(v)', '\tRET']
    optimized, _ = optimize_asm(''.join(ln + '\n' for ln in t2))
    got = optimized.split('\n')
    check(got[:-1] == ['\tLD\tA,(v)'] + r2 + ['\tLD\tA,(v)', '\tRET'],
          f"noopt: postopt changes the code outside the region only, got {got}")
    from src.codegen import CodeGenerator
    gen = CodeGenerator.__new__(CodeGenerator)
    out = debugmarks.run_clean(text, gen.peephole_optimize)
    check(out == region + ['\tRET'], f"noopt: the peephole removes PUSH/POP outside the region only, got {out}")

    # Tail merging stays inside one routine.
    two = ['; Subroutine A\n', 'A:\n', '\tLD\tHL,(x)\n', '\tINC\tHL\n', '\tRET\n',
           '; Subroutine B\n', 'B:\n', '\tLD\tHL,(x)\n', '\tINC\tHL\n', '\tRET\n']
    out, _ = optimize_asm(''.join(two))
    check(out == ''.join(two), "tail merging: two routines are not merged")
    same = ['; Subroutine A\n', 'A:\n', '\tLD\tA,(z)\n', '\tJP\tZ,L1\n',
            '\tLD\tHL,(x)\n', '\tINC\tHL\n', '\tRET\n', 'L1:\n',
            '\tLD\tHL,(x)\n', '\tINC\tHL\n', '\tRET\n']
    out, saved = optimize_asm(''.join(same))
    check(saved > 0 and out.count('RET') == 1 and 'JP\tL1' in out,
          f"tail merging: one routine still merges, saved {saved}")
    # Markers do not stop a merge, and the merge does not cross a ;#func
    marked = ['; Subroutine A\n', 'A:\n', ';#func A 1 1\n', '\tLD\tA,(z)\n', ';#line 1 2\n',
              '\tJP\tZ,L1\n', ';#line 1 3\n', '\tLD\tHL,(x)\n', '\tINC\tHL\n', '\tRET\n',
              ';#endfunc A\n', 'L1:\n', '\tLD\tHL,(x)\n', '\tINC\tHL\n', '\tRET\n']
    plain = [ln for ln in marked if not ln.startswith(';#')]
    a, _ = optimize_asm(''.join(marked))
    b, _ = optimize_asm(''.join(plain))
    check([ln for ln in a.split('\n') if not ln.startswith(';#')] == b.split('\n'),
          "optimize_asm: markers do not change the text")
    check([ln for ln in a.split('\n') if ln.startswith(';#')]
          == [ln.strip() for ln in marked if ln.startswith(';#')],
          "optimize_asm: every marker is kept, in order")


def live(work):
    """5. break, step and bt against cpmemu --gdb."""
    cpmemu = os.environ.get('CPMEMU') or shutil.which('cpmemu')
    ok = cpmemu is not None
    why = 'no cpmemu'
    if ok:
        r = run([cpmemu], work)   # no arguments: prints the usage
        if '--gdb' not in (r.stdout + r.stderr):
            ok, why = False, 'cpmemu has no --gdb'
    if ok:
        r = run([sys.executable, '-c', 'import um80.udb80'], work)
        if r.returncode:
            ok, why = False, 'no um80.udb80'
    if not ok:
        if os.environ.get('UCOW_REQUIRE_UDB80') == '1':
            check(False, f"live: {why}, and UCOW_REQUIRE_UDB80=1")
        else:
            print(f"SKIP live udb80 session: {why}")
        return
    for f in ('debug_test.cow', 'debug_inc.coh'):
        shutil.copy(os.path.join(TESTS, f), work)
    mac, rel, com, err = build(work, ['debug_test.cow'], ['-g'], 'live')
    if not com:
        check(False, f"live: build: {err}")
        return
    tags = {}
    for f in ('debug_test.cow', 'debug_inc.coh'):
        with open(os.path.join(TESTS, f)) as fh:
            for no, ln in enumerate(fh, 1):
                m = re.search(r'# @(\w+)', ln)
                if m:
                    tags[m.group(1)] = (f, no)
    sum3 = tags['sum3']
    cmds = [f"break {sum3[0]}:{sum3[1]}", 'continue', 'bt', 'step', 'next', 'bt', 'quit']
    r = run([sys.executable, '-m', 'um80.udb80', 'live.dbg', '--run', f'{cpmemu} live.com',
             '--batch'] + [a for c in cmds for a in ('-ex', c)], work)
    out = r.stdout + r.stderr
    check(r.returncode == 0, f"live: udb80 exit {r.returncode}: {out[-300:]}")
    check(f"Breakpoint 1, Sum3 () at {sum3[0]}:{sum3[1]}" in out,
          f"live: break on {sum3[0]}:{sum3[1]} stops in Sum3, got: {out[-400:]}")
    check(re.search(r'#1\s+0x[0-9a-f]+ in _main \(\) at debug_test\.cow:\d+', out) is not None,
          f"live: bt shows _main as the caller, got: {out[-400:]}")
    live_vars(work, cpmemu)


def tag_lines(path):
    tags = {}
    with open(path) as fh:
        for no, ln in enumerate(fh, 1):
            m = re.search(r'# @(\w+)', ln)
            if m:
                tags[m.group(1)] = no
    return tags


def udb(work, dbg, com, cpmemu, cmds):
    r = run([sys.executable, '-m', 'um80.udb80', dbg, '--run', f'{cpmemu} {com}', '--batch']
            + [a for c in cmds for a in ('-ex', c)], work)
    return r.stdout + r.stderr


def live_vars(work, cpmemu):
    """6. print each kind of variable at a known value."""
    shutil.copy(os.path.join(TESTS, 'vars_test.cow'), work)
    mac, rel, com, err = build(work, ['vars_test.cow'], ['-g'], 'lv')
    if not com:
        check(False, f"live vars: build: {err}")
        return
    t = tag_lines(os.path.join(TESTS, 'vars_test.cow'))
    cmds = [f"break vars_test.cow:{t['inner']}", 'continue', 'info args', 'info locals',
            'print g8', 'print gu8', 'print g16', 'print gu16', 'print g32',
            'print gu32', 'print gb', 'print gc', 'print gnums', 'print gbox',
            'print gval', 'print gtext', 'print gptr', 'print *gptr', 'print ginit',
            'print gbox.next->tag', 'print gcb', 'print gneg32', 'print lpt', 'print larr[2]', 'print a + b',
            'frame 1', 'info locals', 'info args', 'print oloc', 'print obox.hi.y',
            'print larr', 'frame 2', 'print res', 'quit']
    out = udb(work, 'lv.dbg', 'lv.com', cpmemu, cmds)
    for want in ('a = 3', 'b = 105', 'c = 7', 'lpt = {x = 11, y = 22}', 'loc16 = 1000',
                 'loc8 = -3', '= -5 ', '= 200 ', '= -1234', '= 54321', '= 30000',
                 '= 60000', '= 4242', '= {10, 20, 30, 40}',
                 '{lo = {x = 1, y = 2}, hi = {x = 3, y = 4}, tag = 5',
                 '{as_word = 4660, as_byte = 52', '"hello"', '(struct Point *) 0x',
                 '{x = 3, y = 4}', '(void (*)(void)) 0x', '<CbTarget>', '= 65531', '= 108', 'oloc = 105', 'n = 5',
                 'No symbol "larr" in the current context'):
        check(want in out, f"live vars: output has {want!r}, got: {out[-1500:]}")
    check(out.count('= 105') >= 3 and re.search(r'= 6\n', out) and '= 3 ' in out,
          "live vars: a caller's variables print in frame 1")
    check(re.search(r'\$\d+ = 0\n', out) is not None,
          "live vars: res is 0 before Outer returns, in frame 2")
    # a parameter is not available before the copy
    out = udb(work, 'lv.dbg', 'lv.com', cpmemu, ['break Inner', 'continue', 'info args', 'quit'])
    check(out.count('<not available>') == 3, f"live vars: params before the copy, got: {out[-300:]}")
    # workspace sharing: the same address, the right name for each routine
    for f in ('vw_main.cow', 'vw_lib.cow', 'lib.coh'):
        shutil.copy(os.path.join(TESTS, 'vars_ws', f.replace('vw_', '')), os.path.join(work, f))
    mac, rel, com, err = build(work, ['vw_main.cow', 'vw_lib.cow'], ['-g'], 'lw')
    if not com:
        check(False, f"live vars ws: build: {err}")
        return
    t = tag_lines(os.path.join(TESTS, 'vars_ws', 'lib.cow'))
    out = udb(work, 'lw.dbg', 'lw.com', cpmemu,
              [f"break vw_lib.cow:{t['a']}", f"break vw_lib.cow:{t['b']}", 'continue',
               'info locals', 'info args', 'print a1', 'print b1', 'continue', 'info locals',
               'print bbuf', 'print a1', 'print counter', 'quit'])
    for want in ('a2 = 20', 'n = 10', 'b1 = 25', 'bbuf = ',
                 'No symbol "b1" in the current context',
                 'No symbol "a1" in the current context', '= 1\n'):
        check(want in out, f"live vars ws: output has {want!r}, got: {out[-1200:]}")


def main():
    with tempfile.TemporaryDirectory() as work:
        if not shutil.which('um80') or not shutil.which('ul80'):
            print("um80 and ul80 are not installed (pip install um80)", file=sys.stderr)
            return 1
        progs = programs(work)
        n = d2(work, progs)
        print(f"D2: {n} builds with and without -g gave the same bytes"
              if not failures else "D2: failures")
        markers_and_coverage(work, progs)
        variables(work, progs)
        unit()
        live(work)
    print(f"debug_info_check: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
