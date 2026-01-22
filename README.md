# ch57x Macro Pad GUI (personal tool)

## Setup
1) Ensure the CLI works:
   ch57x-keyboard-tool --help

2) Create a venv (recommended):
   python3 -m venv .venv
   source .venv/bin/activate

3) Install deps:
   pip install -r requirements.txt

## Run
   python3 main.py

## Notes
- The app uses `ch57x-keyboard-tool` from PATH.
- Save/Load preserves the YAML structure but not comments.
- Sequence format is comma-separated chords: 'ctrl-v,ctrl-c'
- Mouse events are entered via raw text (or just type them).
