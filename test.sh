#!/bin/bash
# Test script for ucow compiler

set -e

UCOW_DIR="$(dirname "$0")"
cd "$UCOW_DIR"

echo "=== ucow compiler test ==="
echo

# Test tokenizer
echo "Testing lexer on hello.cow..."
python3 ucow.py --tokens tests/hello.cow | head -20
echo "..."
echo

# Test parser
echo "Testing parser on hello.cow..."
python3 ucow.py --ast tests/hello.cow 2>&1 | head -30
echo "..."
echo

# Test compilation
echo "Compiling hello.cow..."
python3 ucow.py tests/hello.cow -o tests/hello.mac

if [ -f tests/hello.mac ]; then
    echo "Generated assembly:"
    head -50 tests/hello.mac
    echo "..."
    echo
    echo "Success! Assembly written to tests/hello.mac"
else
    echo "Failed to generate assembly"
    exit 1
fi

# If um80 is available, try assembling
if command -v um80 &> /dev/null; then
    echo
    echo "Assembling with um80..."
    # Copy runtime to tests dir for include
    cp lib/runtime.mac tests/
    cd tests
    um80 hello.mac -o hello.rel
    echo "Linking with ul80..."
    ul80 hello.rel -o hello.com
    echo "Generated hello.com"

    # If cpmemu is available, run it
    if [ -f ~/cl/cpmemu/src/cpmemu ]; then
        echo
        echo "Running in cpmemu..."
        ~/cl/cpmemu/src/cpmemu hello.com
    fi
fi
