import os, json, hashlib
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
API_URL = "https://metaforge.app/api/arc-raiders/event-timers"

# CST/CDT correctly via America/Chicago
TZ = ZoneInfo("America/Chicago")

STATE_FILE = "state.json"


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def send_discord(msg: str):
    requests.post(WEBHOOK_URL, json={"content": msg}, timeout=15).raise_for_status()


def hhmm_to_utc_dt(d: date, hhmm: str) -> datetime:
    """Interpret schedule times as UTC clock times, then return aware UTC datetime."""
    hh, mm = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)


def to_local_str(dt_utc: datetime) -> str:
    return dt_utc.astimezone(TZ).strftime("%-I:%M %p %Z")


def fmt_in(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    m, _ = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"in {h}h {m}m" if h else f"in {m}m"


def build_occurrences(payload) -> list[dict]:
    """
    API shape: {"data":[{"name","map","times":[{"start","end"}, ...]}, ...]}
    We expand to concrete UTC datetimes for today + tomorrow (so 'upcoming' always exists).
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    data = payload.get("data", [])
    occ = []

    for e in data:
        name = e.get("name", "Unknown")
        map_name = e.get("map", "Unknown Map")
        times = e.get("times", []) or []

        for day in (today, tomorrow):
            for t in times:
                start_s = t.get("start")
                end_s = t.get("end")
                if not start_s or not end_s:
                    continue

                start = hhmm_to_utc_dt(day, start_s)
                end = hhmm_to_utc_dt(day, end_s)

                # Handle wrap (e.g., 22:00 -> 00:00)
                if end <= start:
                    end = end + timedelta(days=1)

                occ.append(
                    {
                        "event": name,
                        "map": map_name,
                        "start": start,
                        "end": end,
                    }
                )

    # only keep relevant window (now - 2h, now + 24h)
    window_start = now - timedelta(hours=2)
    window_end = now + timedelta(hours=24)
    occ = [o for o in occ if o["end"] >= window_start and o["start"] <= window_end]

    return occ


def build_digest(occurrences: list[dict]) -> str:
    now = datetime.now(timezone.utc)

    current = []
    upcoming = []

    for o in occurrences:
        if o["start"] <= now <= o["end"]:
            current.append(o)
        elif o["start"] > now:
            upcoming.append(o)

    current.sort(key=lambda x: x["end"])
    upcoming.sort(key=lambda x: x["start"])

    lines = []
    lines.append("**events:**")
    lines.append("")
    lines.append("**current**")
    if not current:
        lines.append("- (none)")
    else:
        for o in current[:10]:
            ends_in = int((o["end"] - now).total_seconds())
            lines.append(f"- {o['event']} ({o['map']}) — ends {to_local_str(o['end'])} ({fmt_in(ends_in)})")

    lines.append("")
    lines.append("**upcoming**")
    if not upcoming:
        lines.append("- (none)")
    else:
        for o in upcoming[:10]:
            starts_in = int((o["start"] - now).total_seconds())
            lines.append(f"- {o['event']} ({o['map']}) — {to_local_str(o['start'])} ({fmt_in(starts_in)})")

    return "\n".join(lines).strip()


def main():
    r = requests.get(API_URL, timeout=20)
    r.raise_for_status()
    payload = r.json()

    occurrences = build_occurrences(payload)
    digest = build_digest(occurrences)

    state = load_state()
    digest_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    if state.get("last_digest_hash") != digest_hash:
        send_discord(digest)
        state["last_digest_hash"] = digest_hash
        save_state(state)
    else:
        save_state(state)


if __name__ == "__main__":
    main()
