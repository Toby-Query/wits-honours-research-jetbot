#!/bin/zsh
# Double-click in Finder: drives one straight per press of Enter, counting runs.
cd "${0:A:h}"
.venv/bin/python tools/straights.py "$@"
echo; read -k1 "?Press any key to close."
