#!/bin/bash
# Test runner for ucow compiler

UCOW_DIR="$(cd "$(dirname "$0")" && pwd)"
CPMEMU="/home/wohl/cl/cpmemu/src/cpmemu"
TESTS_DIR="$UCOW_DIR/tests"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# List of tests to run
TESTS="hello arith loop record record2 typedef_test inherit_test interface_test union_test fwddecl_test asm_test"

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
    if ! (cd "$TESTS_DIR" && um80 "${test}.mac" >/dev/null 2>&1); then
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

    # Run
    output=$("$CPMEMU" "$TESTS_DIR/${test}.cfg" 2>&1)
    if echo "$output" | grep -q "Program exit via JMP 0"; then
        echo -e "${GREEN}PASS${NC}"
        ((PASSED++))
    else
        echo -e "${RED}FAIL${NC} (runtime error)"
        echo "$output"
        ((FAILED++))
    fi
done

echo ""
echo "Results: $PASSED passed, $FAILED failed"
exit $FAILED
