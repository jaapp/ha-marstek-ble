#!/bin/bash
# Comprehensive test script for test_marstek_standalone.py
# Tests all combinations of arguments to validate the test script itself

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_SCRIPT="${SCRIPT_DIR}/test_marstek_standalone.py"
STATS_ITERATIONS=2  # Quick iterations for testing

# Proxy configuration (optional)
PROXY_HOST="${PROXY_HOST:-192.168.7.44}"
PROXY_KEY="${PROXY_KEY:-istH+Pnjbxgury0LoTU4UBzqchEbp70upkgwQHb9bBQ=}"

# Test counters
TOTAL_TESTS=0
PASSED_TESTS=0
FAILED_TESTS=0

# Function to print section header
print_header() {
    echo ""
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
}

# Function to run a test
run_test() {
    local test_name="$1"
    shift
    local args=("$@")

    TOTAL_TESTS=$((TOTAL_TESTS + 1))

    echo -e "${YELLOW}[Test $TOTAL_TESTS]${NC} $test_name"
    echo -e "  Command: python3 test_marstek_standalone.py ${args[*]}"
    echo ""

    if python3 "$TEST_SCRIPT" "${args[@]}"; then
        PASSED_TESTS=$((PASSED_TESTS + 1))
        echo -e "${GREEN}✓ PASSED${NC}: $test_name"
    else
        FAILED_TESTS=$((FAILED_TESTS + 1))
        echo -e "${RED}✗ FAILED${NC}: $test_name"
    fi

    echo ""
    echo "─────────────────────────────────────────────────────────────"
    echo ""
}

# Print banner
echo -e "${BLUE}"
cat << "EOF"
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║     MARSTEK BLE TEST SCRIPT - COMPREHENSIVE TEST SUITE    ║
║                                                            ║
╚════════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

echo "Testing script: $TEST_SCRIPT"
echo "Stats iterations: $STATS_ITERATIONS"
echo "Proxy host: $PROXY_HOST"
echo ""
echo "This will test all combinations of:"
echo "  - Mode: normal vs stats"
echo "  - Execution: sequential vs parallel"
echo "  - Connection: direct BLE vs proxy"
echo ""
echo "Total combinations: 8"
echo ""
read -p "Press Enter to start tests (or Ctrl+C to cancel)..."

# ============================================================================
# DIRECT BLE TESTS (no proxy)
# ============================================================================

print_header "DIRECT BLE TESTS (via Mac's Bluetooth Radio)"

# Test 1: Normal mode, sequential, direct BLE
run_test "Normal + Sequential + Direct BLE" \
    # No flags = normal mode, sequential, direct BLE

# Test 2: Normal mode, parallel, direct BLE
run_test "Normal + Parallel + Direct BLE" \
    --parallel

# Test 3: Stats mode, sequential, direct BLE
run_test "Stats + Sequential + Direct BLE" \
    --stats --iterations "$STATS_ITERATIONS"

# Test 4: Stats mode, parallel, direct BLE
run_test "Stats + Parallel + Direct BLE" \
    --stats --parallel --iterations "$STATS_ITERATIONS"

# ============================================================================
# PROXY TESTS (via ESPHome)
# ============================================================================

print_header "PROXY TESTS (via ESPHome Bluetooth Proxy)"

echo -e "${YELLOW}NOTE:${NC} Proxy tests require ESPHome device at $PROXY_HOST"
echo "If proxy is not available, these tests will fail."
echo ""

# Test 5: Normal mode, sequential, proxy
run_test "Normal + Sequential + Proxy" \
    --proxy "$PROXY_HOST" --proxy-key "$PROXY_KEY"

# Test 6: Normal mode, parallel, proxy
run_test "Normal + Parallel + Proxy" \
    --parallel --proxy "$PROXY_HOST" --proxy-key "$PROXY_KEY"

# Test 7: Stats mode, sequential, proxy
run_test "Stats + Sequential + Proxy" \
    --stats --iterations "$STATS_ITERATIONS" \
    --proxy "$PROXY_HOST" --proxy-key "$PROXY_KEY"

# Test 8: Stats mode, parallel, proxy
run_test "Stats + Parallel + Proxy" \
    --stats --parallel --iterations "$STATS_ITERATIONS" \
    --proxy "$PROXY_HOST" --proxy-key "$PROXY_KEY"

# ============================================================================
# SUMMARY
# ============================================================================

print_header "TEST SUMMARY"

echo "Total Tests:  $TOTAL_TESTS"
echo -e "Passed:       ${GREEN}$PASSED_TESTS${NC}"
echo -e "Failed:       ${RED}$FAILED_TESTS${NC}"
echo ""

if [ $FAILED_TESTS -eq 0 ]; then
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║                                                            ║${NC}"
    echo -e "${GREEN}║                  ALL TESTS PASSED! ✓                       ║${NC}"
    echo -e "${GREEN}║                                                            ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    exit 0
else
    echo -e "${RED}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║                                                            ║${NC}"
    echo -e "${RED}║                  SOME TESTS FAILED! ✗                      ║${NC}"
    echo -e "${RED}║                                                            ║${NC}"
    echo -e "${RED}╚════════════════════════════════════════════════════════════╝${NC}"
    exit 1
fi
