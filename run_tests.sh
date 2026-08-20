#!/bin/bash
# Test runner for ucow compiler

UCOW_DIR="$(cd "$(dirname "$0")" && pwd)"
TESTS_DIR="$UCOW_DIR/tests"

# The runtime the generated `INCLUDE 'runtime.mac'` resolves to. It has
# to be named explicitly: the assembler runs in $TESTS_DIR, and there is
# no runtime.mac there. Point it at lib/, which is the copy the wheel
# ships and the one the compiler is written against.
LIB_DIR="$UCOW_DIR/lib"

# cpmemu installs as a console script (`pip install cpmemu`), so take it
# from PATH. $CPMEMU still overrides, for a build that is not installed.
CPMEMU="${CPMEMU:-$(command -v cpmemu)}"
CPMEMU_TIMEOUT="${CPMEMU_TIMEOUT:-20}"
if [ -z "$CPMEMU" ]; then
    echo "cpmemu not found: install it, or set CPMEMU to the binary" >&2
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# List of tests to run
TESTS="hello simple arith loop record record2 typedef_test inherit_test interface_test union_test fwddecl_test asm_test asm_read_test include_test nested_decl_test escape_test dead_store_test test_all"

PASSED=0
FAILED=0

for test in $TESTS; do
    echo -n "Testing $test... "

    # Compile
    if ! python3 "$UCOW_DIR/ucow" "$TESTS_DIR/${test}.cow" -o "$TESTS_DIR/${test}.mac" -I "$TESTS_DIR" >/dev/null 2>&1; then
        echo -e "${RED}FAIL${NC} (compile error)"
        ((FAILED++))
        continue
    fi

    # Assemble
    if ! (cd "$TESTS_DIR" && um80 -I "$LIB_DIR" "${test}.mac" >/dev/null 2>&1); then
        echo -e "${RED}FAIL${NC} (assemble error)"
        ((FAILED++))
        continue
    fi

    # Link
    if ! (cd "$TESTS_DIR" && ul80 "${test}.rel" -o "${test}.com" >/dev/null 2>&1); then
        echo -e "${RED}FAIL${NC} (link error)"
        ((FAILED++))
        continue
    fi

    # Create config file
    cat > "$TESTS_DIR/${test}.cfg" << EOF
program = $TESTS_DIR/${test}.com
EOF

    # Run. The timeout matters: a miscompiled loop condition runs
    # forever, and cpmemu has no limit of its own, so without it the
    # suite hangs rather than failing.
    output=$(timeout "$CPMEMU_TIMEOUT" "$CPMEMU" "$TESTS_DIR/${test}.cfg" 2>&1)
    if [ $? -eq 124 ]; then
        echo -e "${RED}FAIL${NC} (timed out after ${CPMEMU_TIMEOUT}s)"
        ((FAILED++))
        continue
    fi
    if ! echo "$output" | grep -q "Program exit via JMP 0"; then
        echo -e "${RED}FAIL${NC} (runtime error)"
        echo "$output"
        ((FAILED++))
        continue
    fi

    # Compare what the program printed against a recorded baseline, where
    # one exists. Without this the check above is only a crash test: a
    # miscompile that still exits cleanly passes it. That is exactly what
    # happened -- postopt deleted a live store to a byte array and the
    # suite stayed green for it, because test_all was not in TESTS and
    # nothing diffed its output.
    expected="$TESTS_DIR/${test}_expected.txt"
    # test_all's baseline predates the naming convention.
    [ "$test" = "test_all" ] && expected="$TESTS_DIR/test_expected.txt"

    if [ ! -f "$expected" ]; then
        # Say so. A missing baseline silently downgrades a real check to
        # a crash test, and a crash test passes a miscompile that still
        # exits cleanly -- which is how the dead-store bug survived.
        echo -e "${GREEN}PASS${NC} (no baseline: exit status only)"
        ((PASSED++))
        continue
    fi

    if [ -f "$expected" ]; then
        # Drop cpmemu's own first two lines and its exit notice, and
        # normalise the CR the CP/M program emits.
        printf '%s\n' "$output" \
            | sed -e '1,2d' -e '/^Program exit via JMP 0$/d' \
            | tr -d '\r' > "$TESTS_DIR/${test}.out"
        if ! diff -q <(tr -d '\r' < "$expected") "$TESTS_DIR/${test}.out" >/dev/null; then
            echo -e "${RED}FAIL${NC} (output differs from ${expected##*/})"
            diff <(tr -d '\r' < "$expected") "$TESTS_DIR/${test}.out" | head -20
            ((FAILED++))
            continue
        fi
    fi

    echo -e "${GREEN}PASS${NC}"
    ((PASSED++))
done

echo ""
echo "Results: $PASSED passed, $FAILED failed"
exit $FAILED
