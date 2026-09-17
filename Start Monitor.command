#!/bin/sh
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
    echo "Project Python environment not found. Follow the first-time setup in README.md."
    printf "Press Return to close."
    read -r _
    exit 1
fi

exec .venv/bin/python desktop.py