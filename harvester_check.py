import os, json, requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
API_URL = "https://metaforge.app/api/arc-raiders/event-timers"
STATE_FILE = "state.json"

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def send(msg):
    requests.post(WEBHOOK_URL, json={"content": msg}, timeout=10)

def main():
    r = requests.get(API_URL, timeout=15)
    r.raise_for_status()
    data = r.json()

    harvester = next(
        (e for e in data if "harvester" in e.get("name", "").lower()),
        None
    )
    if not harvester:
        return

    active = bool(harvester.get("active", False))
    state = load_state()
    last = state.get("harvester_active", False)

    if not last and active:
        send("🚨 **HARVESTER EVENT IS LIVE** 🚨\nTime to drop, Raider.")

    state["harvester_active"] = active
    save_state(state)

if __name__ == "__main__":
    main()
