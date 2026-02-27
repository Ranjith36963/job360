#!/bin/bash
set -e

echo "============================================"
echo "  Job360 - Cron Setup"
echo "============================================"
echo ""

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/venv/bin/python"
MAIN="$PROJECT_DIR/src/main.py"
LOG="$PROJECT_DIR/data/logs/cron.log"

# Verify venv exists
if [ ! -f "$PYTHON" ]; then
    echo "ERROR: Virtual environment not found. Run setup.sh first."
    exit 1
fi

# Create log directory
mkdir -p "$PROJECT_DIR/data/logs"

# Cron lines for 6AM and 6PM UK time
CRON_CMD="cd $PROJECT_DIR && $PYTHON $MAIN >> $LOG 2>&1"
CRON_6AM="0 6 * * * TZ='Europe/London' $CRON_CMD"
CRON_6PM="0 18 * * * TZ='Europe/London' $CRON_CMD"

# Remove existing job360 entries and add new ones
(crontab -l 2>/dev/null | grep -v "job360\|Job360\|$MAIN" || true; echo "$CRON_6AM"; echo "$CRON_6PM") | crontab -

echo "Cron jobs installed:"
echo "  - 6:00 AM UK time (daily)"
echo "  - 6:00 PM UK time (daily)"
echo ""
echo "View logs: tail -f $LOG"
echo ""
echo "Current crontab:"
crontab -l 2>/dev/null | grep -E "job360|Job360|$MAIN" || echo "  (none found)"
echo ""
