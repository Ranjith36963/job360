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
ENV_FILE="$PROJECT_DIR/.env"

# Verify venv exists
if [ ! -f "$PYTHON" ]; then
    echo "ERROR: Virtual environment not found. Run setup.sh first."
    exit 1
fi

# Create log directory
mkdir -p "$PROJECT_DIR/data/logs"

# Build cron command that loads .env properly
CRON_CMD="cd $PROJECT_DIR && $PYTHON -m src.main >> $LOG 2>&1"
CRON_6AM="0 6 * * * TZ='Europe/London' $CRON_CMD"
CRON_6PM="0 18 * * * TZ='Europe/London' $CRON_CMD"

# Remove existing job360 entries and add new ones
(crontab -l 2>/dev/null | grep -v "job360\|Job360\|src\.main" || true; echo "$CRON_6AM"; echo "$CRON_6PM") | crontab -

echo "Cron jobs installed:"
echo "  - 6:00 AM UK time (daily)"
echo "  - 6:00 PM UK time (daily)"
echo ""

# Show notification channels status
echo "Notification channels:"
if [ -f "$ENV_FILE" ]; then
    grep -q "SMTP_EMAIL=." "$ENV_FILE" 2>/dev/null && echo "  ✓ Email configured" || echo "  ✗ Email not configured"
    grep -q "SLACK_WEBHOOK_URL=." "$ENV_FILE" 2>/dev/null && echo "  ✓ Slack configured" || echo "  ✗ Slack not configured"
    grep -q "DISCORD_WEBHOOK_URL=." "$ENV_FILE" 2>/dev/null && echo "  ✓ Discord configured" || echo "  ✗ Discord not configured"
fi
echo ""

echo "View logs: tail -f $LOG"
echo ""
echo "Current crontab:"
crontab -l 2>/dev/null | grep -E "job360|Job360|src\.main" || echo "  (none found)"
echo ""
