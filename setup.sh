#!/bin/bash
set -e

echo "============================================"
echo "  Job360 - Setup"
echo "============================================"
echo ""

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "[1/4] Creating virtual environment..."
    python3 -m venv venv
else
    echo "[1/4] Virtual environment already exists"
fi

# Activate and install
echo "[2/4] Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Create data directories
echo "[3/4] Creating data directories..."
mkdir -p data/exports data/reports data/logs

# Create .env if missing
if [ ! -f ".env" ]; then
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
echo "    3. Run: python src/main.py"
echo "    4. Set up cron: bash cron_setup.sh"
echo ""
