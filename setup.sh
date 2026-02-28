#!/bin/bash
set -e

echo "============================================"
echo "  Job360 - Setup"
echo "============================================"
echo ""

# Check Python version (3.9+ required for type hints)
PYTHON_CMD="python3"
if ! command -v "$PYTHON_CMD" &>/dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.9+."
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_CMD" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$("$PYTHON_CMD" -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$("$PYTHON_CMD" -c 'import sys; print(sys.version_info.minor)')

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]; }; then
    echo "ERROR: Python 3.9+ required, found $PYTHON_VERSION"
    exit 1
fi
echo "[1/4] Python $PYTHON_VERSION detected"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "[2/4] Creating virtual environment..."
    "$PYTHON_CMD" -m venv venv
else
    echo "[2/4] Virtual environment already exists"
fi

# Always install/upgrade dependencies (idempotent)
echo "[3/4] Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create data directories
mkdir -p data/exports data/reports data/logs

# Create .env if missing
if [ ! -f ".env.example" ]; then
    echo "WARNING: .env.example not found, skipping .env creation"
elif [ ! -f ".env" ]; then
    echo "[4/4] Creating .env from template..."
    cp .env.example .env
    echo ""
    echo "  IMPORTANT: Edit .env with your API keys!"
    echo "  Required for full functionality:"
    echo "    - REED_API_KEY (https://www.reed.co.uk/developers/jobseeker)"
    echo "    - ADZUNA_APP_ID + ADZUNA_APP_KEY (https://developer.adzuna.com/)"
    echo "    - JSEARCH_API_KEY (https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)"
    echo "    - SMTP_EMAIL + SMTP_PASSWORD (Gmail app password)"
    echo "    - NOTIFY_EMAIL (your email address)"
    echo ""
else
    echo "[4/4] .env already exists"
fi

echo ""
echo "============================================"
echo "  Setup complete!"
echo "============================================"
echo ""
echo "  Next steps:"
echo "    1. Edit .env with your API keys"
echo "    2. Run: source venv/bin/activate"
echo "    3. Run: python -m src.cli run"
echo "    4. Dashboard: python -m src.cli dashboard"
echo "    5. Set up cron: bash cron_setup.sh"
echo ""
