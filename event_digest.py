import os, json, hashlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
API_URL = "https://metaforge.app/api/arc-raiders/event-timers"

# You asked for CST. Use America/Chicago (handles CST/CDT correctly).
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


def parse_dt(val):
    """
    Tries to parse common timestamp shapes:
    - epoch seconds / ms
    - ISO 8601 strings (with or without Z)
    Returns an aware datetime in UTC, or None.
    """
    if val is None:
        return None

    # epoch number
    if isinstance(val, (int, float)):
        # guess ms vs s
        if val > 1_000_000_000_000:
            val = val / 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc)

    if isinstance(val, str):
        s = val.strip()
        # tolerate Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # try ISO
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None


def to_local(dt_utc: datetime):
    return dt_utc.astimezone(TZ)


def fmt_local_time(dt_utc: datetime):
    dt = to_local(dt_utc)
    # Example: 8:05 PM CST
    return dt.strftime("%-I:%M %p %Z")


def fmt_in(delta_seconds: int):
    if delta_seconds < 0:
        delta_seconds = 0
    m, s = divmod(delta_seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"in {h}h {m}m"
    return f"in {m}m"


def extract_occurrences(payload):
    """
    MetaForge's API schema may evolve. This function tries to robustly extract
    "occurrences" like: {event, map, active, start, end}

    It handles:
    - list of events, each with maps/instances/schedule arrays
    - fields named start/startAt/startsAt, end/endAt/endsAt, active/isActive
    - map name fields map/mapName/location
    """
    occurrences = []

    if not isinstance(payload, list):
        return occurrences

    for ev in payload:
        if not isinstance(ev, dict):
            continue

        event_name = ev.get("name") or ev.get("event") or ev.get("title") or "Unknown Event"

        # Candidate containers for per-map schedule entries
        candidates = []
        for k in ("maps", "instances", "schedule", "timers", "occurrences"):
            v = ev.get(k)
            if isinstance(v, list):
                candidates.append(v)

        # If no obvious container, treat event dict itself as one occurrence
        if not candidates:
            candidates = [[ev]]

        for arr in candidates:
            for item in arr:
                if not isinstance(item, dict):
                    continue

                map_name = (
                    item.get("mapName")
                    or item.get("map")
                    or item.get("location")
                    or item.get("zone")
                    or ev.get("maps")  # fallback if event-level is weird
                )
                if isinstance(map_name, dict):
                    map_name = map_name.get("name") or map_name.get("id")

                # Active flags can be named differently
                active = item.get("active")
                if active is None:
                    active = item.get("isActive")
                if active is None:
                    active = item.get("currentlyActive")

                # Times can be named differently
                start = (
                    parse_dt(item.get("start"))
                    or parse_dt(item.get("startAt"))
                    or parse_dt(item.get("startsAt"))
                    or parse_dt(item.get("nextStart"))
                )
                end = (
                    parse_dt(item.get("end"))
                    or parse_dt(item.get("endAt"))
                    or parse_dt(item.get("endsAt"))
                    or parse_dt(item.get("nextEnd"))
                )

                occurrences.append(
                    {
                        "event": str(event_name),
                        "map": str(map_name) if map_name else "Unknown Map",
                        "active": bool(active) if active is not None else None,
                        "start": start,
                        "end": end,
                        "raw": item,
                    }
                )

    # Remove obvious junk duplicates
    cleaned = []
    seen = set()
    for o in occurrences:
        key = (o["event"], o["map"], o["start"].isoformat() if o["start"] else None, o["end"].isoformat() if o["end"] else None)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(o)

    return cleaned


def build_digest(occurrences):
    now = datetime.now(timezone.utc)

    current = []
    upcoming = []

    for o in occurrences:
        start = o["start"]
        end = o["end"]
        active_flag = o["active"]

        is_current = False
        if active_flag is True:
            is_current = True
        elif start and end and start <= now <= end:
            is_current = True

        if is_current:
            # compute "ends in"
            if end:
                ends_in = int((end - now).total_seconds())
                current.append((ends_in, o))
            else:
                current.append((10**9, o))
        else:
            # upcoming = next start in the future
            if start and start > now:
                starts_in = int((start - now).total_seconds())
                upcoming.append((starts_in, o))

    current.sort(key=lambda x: x[0])
    upcoming.sort(key=lambda x: x[0])

    lines = []
    lines.append("**events:**")
    lines.append("")
    lines.append("**current**")
    if not current:
        lines.append("- (none)")
    else:
        for ends_in, o in current[:10]:
            end = o["end"]
            end_str = f"ends {fmt_local_time(end)} ({fmt_in(ends_in)})" if end else "active"
            lines.append(f"- {o['event']} ({o['map']}) — {end_str}")

    lines.append("")
    lines.append("**upcoming**")
    if not upcoming:
        lines.append("- (none)")
    else:
        for starts_in, o in upcoming[:10]:
            start = o["start"]
            start_str = f"{fmt_local_time(start)} ({fmt_in(starts_in)})" if start else "soon"
            lines.append(f"- {o['event']} ({o['map']}) — {start_str}")

    return "\n".join(lines).strip()


def main():
    r = requests.get(API_URL, timeout=20)
    r.raise_for_status()
    payload = r.json()

    occurrences = extract_occurrences(payload)
    digest = build_digest(occurrences)

    # Only post if digest changed
    state = load_state()
    digest_hash = hashlib.sha256(digest.encode("utf-8")).hexdigest()

    if state.get("last_digest_hash") != digest_hash:
        send_discord(digest)
        state["last_digest_hash"] = digest_hash
        save_state(state)
    else:
        # still save in case state file doesn't exist yet
        save_state(state)


if __name__ == "__main__":
    main()
