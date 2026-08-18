#!/usr/bin/env bash
# One-command setup for macOS/Linux:  bash setup.sh
set -e
cd "$(dirname "$0")"

command -v python3 >/dev/null || { echo "Python 3.9+ required"; exit 1; }

echo "Installing gcode..."
python3 -m pip install -e . --quiet

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env"
fi

echo
echo "Installed. gcode is on your PATH."
if grep -q REPLACE_ME .env; then
  echo "Next: put your OpenAI key in $(pwd)/.env"
  echo "      OPENAI_API_KEY=sk-proj-..."
  echo "      GCODE_USER=your-name"
fi
echo
echo "Then, from any project folder:  gcode"
