#!/usr/bin/env bash
#
# Runs comprehensive type checking across all PlexiChat repositories
#
# This script runs pyright type checking on plexichat, common-utils, and encryption,
# then generates detailed reports organized by category with prioritized recommendations.
#
# Usage:
#   ./scripts/run_type_check.sh              # With submodule update
#   ./scripts/run_type_check.sh --skip-submodules  # Without submodule update

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Get script directory and repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo -e "${CYAN}================================================================================${NC}"
echo -e "${CYAN}PlexiChat Type Check - All Repositories${NC}"
echo -e "${CYAN}================================================================================${NC}"
echo ""

# Parse arguments
SKIP_SUBMODULES=false
for arg in "$@"; do
    case $arg in
        --skip-submodules)
            SKIP_SUBMODULES=true
            shift
            ;;
    esac
done

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"

# Check for pyright
if command -v pyright &> /dev/null; then
    PYRIGHT_VERSION=$(pyright --version 2>&1)
    echo -e "${GREEN}✓ pyright is installed: ${PYRIGHT_VERSION}${NC}"
else
    echo -e "${RED}✗ pyright not found${NC}"
    echo -e "${RED}  Install with: npm install -g pyright${NC}"
    exit 1
fi

# Check for Python
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1)
    echo -e "${GREEN}✓ Python is installed: ${PYTHON_VERSION}${NC}"
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_VERSION=$(python --version 2>&1)
    echo -e "${GREEN}✓ Python is installed: ${PYTHON_VERSION}${NC}"
    PYTHON_CMD="python"
else
    echo -e "${RED}✗ Python not found${NC}"
    exit 1
fi

# Check for virtual environment activation
if [[ -z "${VIRTUAL_ENV}" ]]; then
    echo -e "${YELLOW}⚠ Virtual environment is not activated${NC}"
    echo -e "${YELLOW}  Consider activating: source .venv/bin/activate${NC}"
fi

# Update submodules if not skipped
if [[ "$SKIP_SUBMODULES" == false ]]; then
    echo ""
    echo -e "${YELLOW}Updating git submodules...${NC}"
    cd "$REPO_ROOT"
    if git submodule update --init --recursive; then
        echo -e "${GREEN}✓ Submodules updated${NC}"
    else
        echo -e "${RED}✗ Failed to update submodules${NC}"
        exit 1
    fi
fi

# Run type checking
echo ""
echo -e "${YELLOW}Running type checker...${NC}"
echo ""

cd "$REPO_ROOT"
if $PYTHON_CMD scripts/type_check_all.py; then
    EXIT_CODE=0
else
    EXIT_CODE=$?
fi

# Summary
echo ""
echo -e "${CYAN}================================================================================${NC}"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo -e "${GREEN}✓ Type checking completed successfully!${NC}"
else
    echo -e "${YELLOW}⚠ Type checking completed with issues${NC}"
fi
echo -e "${CYAN}================================================================================${NC}"
echo ""
echo -e "${CYAN}Reports available in: type_check_reports/${NC}"
echo -e "${WHITE}- type_check_summary_consolidated.md (start here)${NC}"
echo -e "${WHITE}- type_check_report_plexichat.md${NC}"
echo -e "${WHITE}- type_check_report_common-utils.md${NC}"
echo -e "${WHITE}- type_check_report_encryption.md${NC}"
echo ""

exit $EXIT_CODE
