#!/bin/bash
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python isn't installed. Get it from https://www.python.org/downloads/ then run this again."
  read -r -p "Press Enter to close."; exit 1
fi
if [ ! -x ".venv/bin/python" ]; then
  echo "Setting up SocietyScout for the first time. This takes a minute or two..."
  python3 -m venv .venv || { read -r -p "Setup failed. Press Enter to close."; exit 1; }
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt || {
  echo "Couldn't install the parts SocietyScout needs. Check your internet connection and try again."
  read -r -p "Press Enter to close."; exit 1; }
echo
echo "SocietyScout is starting and will open in your browser."
echo "Keep this window open while you use it. Close it to stop SocietyScout."
(sleep 4; open "http://localhost:8501") &
.venv/bin/python -m streamlit run app.py
