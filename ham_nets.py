#!/usr/bin/env python3
"""Ham nets: the GTA clubs' published schedule (nets.json) worked out against the clock.

view() answers four questions: what is on the air now, what comes next, what the next seven days look like, and which club
dates (meetings, socials, special-event nets) are coming up. It returns plain data only - the dashboard page, the phone page
and a later iPad / iPhone app all read the same answer from /api/nets.

nets.json is the only thing to edit when a club changes its schedule. Each entry says where it came from (src) and how sure
it is (conf): site = on the club's own website on the 'checked' date, rac = the national RAC list, old = an older undated list
that nobody has confirmed, inferred = worked out from the dates a club has published.

When an entry happens:
  days    [0..6]        weekly, 0 = Monday
  rule    "2nd Mon"     monthly (1st 2nd 3rd 4th 5th last + Mon..Sun)
  annual  "11-11"       every year on that month-day
  date    "2026-12-05"  once
  skip    ["1st Tue"]   not on those days;  months [9,10,...] only in those months
  start   "19:30" local time;  mins = how long it usually runs
"""
import os, json, time, bisect, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "nets.json")
_CACHE = {"m": 0, "d": None}
_EMPTY = {"version": 1, "checked": "", "clubs": {}, "sources": {}, "nets": [], "events": []}
_ORD = {"1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}
_WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CONF = {"site": "on the club's own website", "rac": "on the national RAC net list", "old": "from an older list - not confirmed",
        "inferred": "worked out from the club's published dates - not confirmed"}


def load(path=None):
    """nets.json, re-read only when the file changes. A broken file keeps the last good copy."""
    path = path or FILE
    try:
        m = os.path.getmtime(path)
        if m != _CACHE["m"] or _CACHE["d"] is None:
            _CACHE["d"] = json.load(open(path, encoding="utf-8")); _CACHE["m"] = m
    except (OSError, ValueError):
        return _CACHE["d"] or dict(_EMPTY)
    return _CACHE["d"]


def rule_hit(rule, d):
    """'2nd Mon', '3rd Thu', 'last Fri': is date d that day of its month?"""
    try: o, w = str(rule).split()
    except ValueError: return False
    if _WD[d.weekday()] != w: return False
    if o == "last": return (d + dt.timedelta(days=7)).month != d.month
    return (d.day - 1) // 7 + 1 == _ORD.get(o, 0)


def starts_on(e, d):
    """does entry e start on date d?"""
    if e.get("months") and d.month not in e["months"]: return False
    if any(rule_hit(r, d) for r in e.get("skip", [])): return False
    if e.get("date"): return d.isoformat() == e["date"]
    if e.get("annual"): return d.strftime("%m-%d") == e["annual"]
    if e.get("rule"): return rule_hit(e["rule"], d)
    return d.weekday() in e.get("days", [])


def clock(t):
    """datetime -> '7:30 pm'"""
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def _prog(e):
    if not e.get("mhz"): return ""
    s = f"{e['mhz']:.4f}"
    if e.get("duplex"): s += f" {e['duplex']}{e.get('offset', 0):g}"
    elif e.get("rpt") == "simplex": s += " simplex"
    if e.get("tone"): s += f" / {e['tone']}"
    return s


def occurrence(e, d, data, kind=None):
    """one dated occurrence of entry e on date d, as the page / app wants it"""
    hh, mm = (int(x) for x in e.get("start", "00:00").split(":"))
    t0 = dt.datetime(d.year, d.month, d.day, hh, mm); t1 = t0 + dt.timedelta(minutes=int(e.get("mins", 60)))
    club = data.get("clubs", {}).get(e.get("club", ""), {})
    src = data.get("sources", {}).get(e.get("src", ""), {})
    return {"id": e.get("id", ""), "name": e.get("name", ""), "club": e.get("club", ""), "club_name": club.get("name", ""), "home": bool(club.get("home")),
            "kind": kind or e.get("kind", "net"), "start": int(time.mktime(t0.timetuple())), "end": int(time.mktime(t1.timetuple())),
            "date": d.isoformat(), "day": _WD[d.weekday()], "time": clock(t0), "until": clock(t1),
            "mhz": e.get("mhz"), "mode": e.get("mode", "fm" if e.get("mhz") else ""), "rpt": e.get("rpt", ""), "duplex": e.get("duplex", ""),
            "offset": e.get("offset", 0), "tone": e.get("tone", ""), "prog": _prog(e), "where": e.get("where", ""), "note": e.get("note", ""),
            "rx": bool(e.get("mhz")) and e.get("rx", True), "conf": e.get("conf", ""), "conf_text": CONF.get(e.get("conf", ""), ""),
            "src": src.get("label", ""), "src_url": src.get("url", ""), "checked": e.get("checked", data.get("checked", ""))}


def on_date(d, data):
    """everything that starts on date d, in time order"""
    out = [occurrence(e, d, data, "net") for e in data.get("nets", []) if starts_on(e, d)]
    out += [occurrence(e, d, data) for e in data.get("events", []) if starts_on(e, d)]
    return sorted(out, key=lambda o: (o["start"], not o["home"], o["name"]))


def _label(d, today):
    n = (d - today).days
    return ("Today" if n == 0 else "Tomorrow" if n == 1 else d.strftime("%A")) + d.strftime(" · %b ") + str(d.day)


def view(now=None, days=7, ahead=75, heard=None, path=None):
    """The whole answer. now = a datetime (local) for testing; heard = optional function(mhz) -> unix time a signal was last
    seen on that frequency (0 = never), used to say whether a net that should be on is actually being received."""
    data = load(path); now = now or dt.datetime.now(); ts = int(time.mktime(now.timetuple())); today = now.date()
    span = {}                                                  # date -> occurrences, yesterday (a net can run past midnight) to `ahead` days out
    for i in range(-1, ahead + 1):
        d = today + dt.timedelta(days=i); span[d] = on_date(d, data)
    flat = [o for d in sorted(span) for o in span[d]]
    for o in flat:
        o["state"] = "on" if o["start"] <= ts < o["end"] else ("done" if o["end"] <= ts else "later")
        o["in_min"] = max(0, round((o["start"] - ts) / 60)); o["left_min"] = max(0, round((o["end"] - ts) / 60)) if o["state"] == "on" else 0
        if heard and o["rx"] and o["kind"] == "net" and o["state"] == "on":
            try: h = int(heard(o["mhz"]) or 0)
            except Exception: h = 0
            o["heard_ts"] = h; o["heard_in_window"] = h >= o["start"]; o["heard_ago_min"] = round((ts - h) / 60) if h else None
    on = [o for o in flat if o["state"] == "on"]
    nxt = [o for o in flat if o["state"] == "later" and o["start"] - ts <= 24 * 3600][:8]
    week = [{"date": d.isoformat(), "label": _label(d, today), "items": [o for o in span[d] if o["kind"] == "net"]}
            for d in (today + dt.timedelta(days=i) for i in range(days))]
    events = [o for o in flat if o["kind"] != "net" or o["id"] in {e.get("id") for e in data.get("events", [])}]
    events = [o for o in events if o["state"] != "done"][:12]
    try: age = (today - dt.date.fromisoformat(data.get("checked", ""))).days
    except ValueError: age = None
    return {"server_time": ts, "tz": time.strftime("%Z"), "checked": data.get("checked", ""), "age_days": age, "stale": age is None or age > 21,
            "now": on, "next": nxt, "week": week, "events": events, "clubs": data.get("clubs", {}), "sources": data.get("sources", {}),
            "conf": CONF, "counts": {"nets": len(data.get("nets", [])), "events": len(data.get("events", []))}}


def heard_lookup(bank):
    """bank (dict of channel dicts with 'mhz' and 'last_heard') -> function(mhz) giving the newest last_heard within 6 kHz"""
    rows = sorted((float(e.get("mhz", 0)), float(e.get("last_heard", 0) or 0)) for e in bank.values() if isinstance(e, dict))
    fs = [r[0] for r in rows]
    def f(mhz):
        i = bisect.bisect_left(fs, mhz - 0.0061); best = 0
        while i < len(rows) and rows[i][0] <= mhz + 0.0061:
            best = max(best, rows[i][1]); i += 1
        return best
    return f


if __name__ == "__main__":
    import sys
    v = view(dt.datetime.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None)
    print(f"schedule checked {v['checked']} ({v['age_days']} days ago) - {v['counts']['nets']} nets, {v['counts']['events']} club dates")
    for o in v["now"]: print(f"  ON NOW  {o['name']} ({o['club_name']}) {o['prog']} until {o['until']}")
    for o in v["next"]: print(f"  next    {o['day']} {o['time']:>8}  {o['name']} ({o['club_name']}) {o['prog']}")
    for o in v["events"]: print(f"  date    {o['date']} {o['time']:>8}  {o['name']} - {o['where'] or o['prog']}")
