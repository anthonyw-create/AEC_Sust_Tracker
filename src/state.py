import json
from pathlib import Path

STATE_PATH = Path("state.json")

def load_state():
    if STATE_PATH.exists():
        with open(STATE_PATH, "r") as f:
            return json.load(f)
    return {"watch_pages": {}}

def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
