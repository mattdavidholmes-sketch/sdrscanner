#!/usr/bin/env python3
"""Replace nets.json with a checked candidate:   python3 _helpers/nets_update.py diag/nets_candidate.json

Checks the candidate (structure, every entry's fields, times, frequencies, that the schedule engine can work with it), prints
what changed against the current file, keeps the current file in _old_versions/ and only then promotes the candidate.
Nothing is changed when a check fails. The dashboard re-reads nets.json by itself within a minute - no restart."""
import os, sys, json, re, shutil, datetime as dt

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
LIVE = os.path.join(HERE, "nets.json")
CONF = {"site", "rac", "old", "inferred"}
RULE = re.compile(r"^(1st|2nd|3rd|4th|5th|last) (Mon|Tue|Wed|Thu|Fri|Sat|Sun)$")


def problems(d):
    bad = []
    for k in ("checked", "clubs", "sources", "nets", "events"):
        if k not in d: bad.append(f"top level: '{k}' is missing")
    if bad: return bad
    try:
        if dt.date.fromisoformat(d["checked"]) > dt.date.today() + dt.timedelta(days=1): bad.append("'checked' is in the future")
    except ValueError: bad.append("'checked' is not a date (YYYY-MM-DD)")
    if len(d["nets"]) < 10: bad.append(f"only {len(d['nets'])} nets - looks truncated")
    seen = set()
    for kind, rows in (("net", d["nets"]), ("event", d["events"])):
        for e in rows:
            w = f"{kind} '{e.get('id', e.get('name', '?'))}'"
            if not e.get("id") or e["id"] in seen: bad.append(f"{w}: id missing or used twice")
            seen.add(e.get("id"))
            if not e.get("name"): bad.append(f"{w}: no name")
            if e.get("club") not in d["clubs"]: bad.append(f"{w}: club '{e.get('club')}' is not in clubs")
            if e.get("src") not in d["sources"]: bad.append(f"{w}: src '{e.get('src')}' is not in sources")
            if e.get("conf") not in CONF: bad.append(f"{w}: conf must be one of {sorted(CONF)}")
            if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(e.get("start", ""))): bad.append(f"{w}: start must be HH:MM (24 h)")
            if not isinstance(e.get("mins", 60), (int, float)) or not 5 <= e.get("mins", 60) <= 720: bad.append(f"{w}: mins out of range")
            when = [k for k in ("days", "rule", "annual", "date") if e.get(k) not in (None, "", [])]
            if len(when) != 1: bad.append(f"{w}: needs exactly one of days / rule / annual / date (has {when})")
            if "days" in when and not all(isinstance(x, int) and 0 <= x <= 6 for x in e["days"]): bad.append(f"{w}: days must be 0..6 (0 = Monday)")
            for r in ([e["rule"]] if "rule" in when else []) + list(e.get("skip", [])):
                if not RULE.match(str(r)): bad.append(f"{w}: rule '{r}' must look like '2nd Mon'")
            if "annual" in when and not re.match(r"^\d\d-\d\d$", e["annual"]): bad.append(f"{w}: annual must be MM-DD")
            if "date" in when:
                try: dt.date.fromisoformat(e["date"])
                except ValueError: bad.append(f"{w}: date must be YYYY-MM-DD")
            if kind == "net" or e.get("mhz"):
                try:
                    if not 1.0 <= float(e.get("mhz")) <= 1300.0: bad.append(f"{w}: mhz out of range")
                except (TypeError, ValueError): bad.append(f"{w}: mhz missing")
    return bad


def changes(old, new):
    out = []
    for key in ("nets", "events"):
        a = {e["id"]: e for e in old.get(key, [])}; b = {e["id"]: e for e in new.get(key, [])}
        for i in b:
            if i not in a: out.append(f"+ {key[:-1]} added: {b[i]['name']} ({i})")
        for i in a:
            if i not in b: out.append(f"- {key[:-1]} removed: {a[i]['name']} ({i})")
        for i in a:
            if i in b:
                diff = [f"{k}: {a[i].get(k)!r} -> {b[i].get(k)!r}" for k in sorted(set(a[i]) | set(b[i])) if k != "checked" and a[i].get(k) != b[i].get(k)]
                if diff: out.append(f"~ {a[i]['name']} ({i}): " + "; ".join(diff))
    for key in ("clubs", "sources"):
        if old.get(key) != new.get(key): out.append(f"~ {key} changed")
    if old.get("checked") != new.get("checked"): out.append(f"~ checked: {old.get('checked')} -> {new.get('checked')}")
    return out


def main():
    if len(sys.argv) != 2: print(__doc__); return 2
    cand = os.path.join(HERE, sys.argv[1]) if not os.path.isabs(sys.argv[1]) else sys.argv[1]
    try: new = json.load(open(cand, encoding="utf-8"))
    except (OSError, ValueError) as e: print("cannot read the candidate:", e); return 1
    bad = problems(new)
    if not bad:
        try:
            import ham_nets
            v = ham_nets.view(path=cand); ham_nets._CACHE.update(m=0, d=None)
            if not v["week"] or not any(d["items"] for d in v["week"]): bad.append("the schedule engine finds nothing in the next seven days")
        except Exception as e: bad.append(f"the schedule engine cannot use it: {e!r}")
    if bad:
        print("NOT PROMOTED -", len(bad), "problem(s):"); [print("  ", b) for b in bad[:40]]; return 1
    try: old = json.load(open(LIVE, encoding="utf-8"))
    except (OSError, ValueError): old = {}
    ch = changes(old, new)
    if not ch: print("no differences - nets.json left as it is"); return 0
    print(len(ch), "difference(s):"); [print("  ", c) for c in ch]
    if os.path.exists(LIVE):
        os.makedirs(os.path.join(HERE, "_old_versions"), exist_ok=True)
        keep = os.path.join(HERE, "_old_versions", "nets_" + dt.datetime.now().strftime("%Y%m%d-%H%M") + ".json"); shutil.copy2(LIVE, keep)
        print("old file kept as", os.path.relpath(keep, HERE))
    shutil.copyfile(cand, LIVE); print("promoted:", len(new["nets"]), "nets,", len(new["events"]), "events, checked", new["checked"]); return 0


if __name__ == "__main__":
    sys.exit(main())
