#!/usr/bin/env bash
#
# Test runner for mudyla
#
# Usage:
#   ./run-tests.sh              # Run all tests
#   ./run-tests.sh unit         # Run only unit tests
#   ./run-tests.sh integration  # Run only integration tests
#   ./run-tests.sh --html       # Generate HTML report
#   ./run-tests.sh --parallel   # Run tests in parallel
#   ./run-tests.sh --verbose    # Verbose output
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default options
PYTEST_ARGS=()
TEST_FILTER=""
HTML_REPORT=false
PARALLEL=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        unit)
            TEST_FILTER="-m unit"
            shift
            ;;
        integration)
            TEST_FILTER="-m integration"
            shift
            ;;
        --html)
            HTML_REPORT=true
            shift
            ;;
        --parallel|-n)
            PARALLEL=true
            shift
            ;;
        --verbose|-v)
            PYTEST_ARGS+=("-vv")
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS] [FILTER]"
            echo ""
            echo "Options:"
            echo "  unit              Run only unit tests"
            echo "  integration       Run only integration tests"
            echo "  --html            Generate HTML report"
            echo "  --parallel, -n    Run tests in parallel"
            echo "  --verbose, -v     Verbose output"
            echo "  --help, -h        Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0                    # Run all tests"
            echo "  $0 integration        # Run only integration tests"
            echo "  $0 --html             # Run all tests with HTML report"
            echo "  $0 integration --html # Run integration tests with HTML report"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Add HTML report if requested
if [ "$HTML_REPORT" = true ]; then
    PYTEST_ARGS+=("--html=test-reports/report.html" "--self-contained-html")
    mkdir -p test-reports
fi

# Add parallel execution if requested
if [ "$PARALLEL" = true ]; then
    PYTEST_ARGS+=("-n" "auto")
fi

# Add test filter if specified
if [ -n "$TEST_FILTER" ]; then
    PYTEST_ARGS+=($TEST_FILTER)
fi

# Print configuration
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Mudyla Test Runner${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "Test filter: ${YELLOW}${TEST_FILTER:-all tests}${NC}"
echo -e "HTML report: ${YELLOW}${HTML_REPORT}${NC}"
echo -e "Parallel:    ${YELLOW}${PARALLEL}${NC}"
echo ""

# Run pytest
echo -e "${BLUE}Running tests...${NC}"
echo ""

if nix develop --command pytest "${PYTEST_ARGS[@]}"; then
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}All tests passed!${NC}"
    echo -e "${GREEN}========================================${NC}"

    if [ "$HTML_REPORT" = true ]; then
        echo ""
        echo -e "${BLUE}HTML report generated: test-reports/report.html${NC}"
    fi

    exit 0
else
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}Tests failed!${NC}"
    echo -e "${RED}========================================${NC}"

    if [ "$HTML_REPORT" = true ]; then
        echo ""
        echo -e "${BLUE}HTML report generated: test-reports/report.html${NC}"
    fi

    exit 1
fi
