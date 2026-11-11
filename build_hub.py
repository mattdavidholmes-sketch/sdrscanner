#!/usr/bin/env python3
"""Builds 'Radio Hub.html' (searchable app), 'sdrpp_all_bookmarks.json' and 'radio_hub_chirp.csv'
from the airport, repeater, net and FRS/GMRS tables."""
import csv, json, datetime as dt
from build_nets import REPEATERS, SIMPLEX, NETS

# ---- Airports (SkyVector / NAV CANADA CFS) ----
AIRPORTS = {
    "YYZ Pearson (~10 km - the one to listen to)": [
        ("ATIS", 120.825), ("Clearance", 121.300), ("Ground N", 121.650), ("Ground S", 121.900), ("Ground", 119.100),
        ("Tower N (primary)", 118.700), ("Tower S", 118.350),
        ("Arrival 1", 124.475), ("Arrival 2", 125.400), ("Arrival 3", 132.800), ("Arrival 4", 134.175),
        ("Departure 1", 127.575), ("Departure 2", 128.800),
        ("Apron", 122.825), ("Apron N", 122.275), ("Apron S", 122.075), ("Pad control", 131.175), ("VFR advisory", 119.300),
    ],
    "YTZ Billy Bishop (~20 km - marginal on a 2m whip)": [
        ("ATIS/AWOS", 133.600), ("Ground", 121.700), ("Tower", 118.200), ("Tower 2", 119.200),
        ("Toronto Arr/Dep", 133.400), ("Porter FBO", 123.350), ("Stolport FBO", 130.570),
    ],
    "YHM Hamilton (~50 km - out of range from Mississauga)": [
        ("ATIS", 128.100), ("Ground", 121.600), ("Tower", 125.000), ("Toronto Terminal Arr/Dep", 119.700),
        ("Cargojet ops", 131.575), ("Hamilton Jetport", 122.350),
    ],
    "YOO Oshawa (~50 km - out of range from Mississauga)": [("ATIS/AWOS", 125.675), ("Ground", 118.400), ("Tower / MF", 120.100), ("Toronto Arr/Dep", 133.400)],
    "CNC3 Brampton (~15 km)": [("ATF / UNICOM", 123.300)],
    "Common": [("Guard / emergency", 121.500), ("London FIC flight plans", 123.150), ("Toronto VFR", 133.400)],
}
FRSGMRS = [(f"FRS {i}", f) for i, f in enumerate([462.5625,462.5875,462.6125,462.6375,462.6625,462.6875,462.7125,467.5625,467.5875,467.6125,467.6375,467.6625,467.6875,467.7125], 1)]
FRSGMRS += [(f"GMRS {i}", f) for i, f in zip(range(15, 23), [462.550,462.575,462.600,462.625,462.650,462.675,462.700,462.725])]
MURS = [("MURS 1", 151.820), ("MURS 2", 151.880), ("MURS 3", 151.940), ("MURS 4", 154.570), ("MURS 5", 154.600)]
# Store / warehouse walkie-talkies (Motorola CLS, RDU, RDM, RMU...): the licence-exempt "dot" channels. US chain programming from
# RadioReference; Canadian stores vary, and many chains have moved to 900 MHz DLR/DTR digital radios that no scanner can hear.
RETAIL = [
    ("Home Depot ch1 (US std) DPL 065", 467.7625), ("Home Depot ch2 DPL 331", 467.8500), ("Home Depot ch3 DPL 263", 467.8375),
    ("Home Depot ch4 DPL 413", 467.8750), ("Home Depot ch5 mgmt DPL 466", 467.9000),
    ("Walmart ch1/6 (VHF)", 154.570), ("Walmart ch2/7 (VHF)", 154.600), ("Walmart ch3 PL136.5", 151.820), ("Walmart ch4 PL136.5", 151.880), ("Walmart ch5 PL136.5", 151.940),
    ("Walmart UHF (some stores)", 463.7625), ("Walmart UHF (some stores)", 461.5875),
    ("Costco PL74.4", 464.500), ("Costco", 461.7625), ("Costco", 468.2125), ("Costco", 468.3625), ("Costco", 468.4125), ("Costco", 468.4625), ("Costco", 468.5625), ("Costco", 468.6125),
    ("Lowe's DMR (digital)", 464.5375), ("Lowe's DMR (digital)", 464.5625), ("Lowe's DMR (digital)", 462.7625), ("Lowe's DMR (digital)", 469.4875),
    # (v199) the UHF colour names were mixed up: Brown dot is 464.500, Yellow dot 464.550, and 467.925 is the Blue star (en.wikipedia.org/wiki/Business_band).
    # Blue dot and Green dot are the VHF pair 154.570 / 154.600 (listed above as Walmart's). 464.5125 has no colour - it is the itinerant channel below.
    ("Dot: Yellow (CLS default 1)", 464.550), ("Dot: Brown", 464.500), ("Dot: Red", 151.625), ("Dot: Purple", 151.955), ("Dot: Silver star", 467.8500), ("Dot: Gold star", 467.8750), ("Dot: Red star", 467.9000), ("Dot: Blue star", 467.9250),
    ("Itinerant J", 467.7625), ("Itinerant K", 467.8125), ("Itinerant 464.4875", 464.4875), ("Itinerant 464.5125", 464.5125), ("Itinerant 469.4875", 469.4875), ("Itinerant 469.5125", 469.5125),
]
MARINE = [("Marine 16 distress/calling", 156.800), ("Marine 9 pleasure calling", 156.450), ("Marine 68", 156.425), ("Marine 71", 156.575), ("Marine 72", 156.625), ("Prescott CG", 161.825)]
UV32_RX = [(108.0, 136.0), (136.0, 174.0), (200.0, 260.0), (350.0, 390.0), (400.0, 520.0)]
def uv32(mhz): return any(lo <= mhz <= hi for lo, hi in UV32_RX)

# City services and utilities around west Toronto, from ISED licences (analog FM = hearable here; the UV-32 can tune
# the VHF/UHF ones). Digital (DMR / P25) ones are in DIGITAL below and play through SDRTrunk.
CITY = [
    ("GFL Environmental - garbage trucks (VHF)", 151.0550), ("Miller Waste - garbage/recycling trucks", 453.7000),
    ("Emterra - recycling trucks", 451.7875), ("City of Toronto Solid Waste Management", 464.1625),
    ("City of Toronto Corporate Security", 410.2375), ("City of Toronto Corporate Security", 410.2875), ("City of Toronto Corporate Security", 410.7875),
    ("City of Toronto Corporate Security", 412.4125), ("City of Toronto Corporate Security", 415.2375), ("City of Toronto Corporate Security", 415.2875),
    ("City of Toronto Corporate Security", 415.7875), ("City of Toronto Corporate Security", 417.4125), ("City of Toronto Corporate Security", 451.1875),
    ("City of Toronto Corporate Security", 452.0625), ("City of Toronto Corporate Security", 452.3625), ("City of Toronto Corporate Security", 457.0625),
    ("City of Toronto Corporate Security", 457.3625), ("City of Toronto Corporate Security mobile", 462.8250), ("City of Toronto Corporate Security mobile", 463.8500),
    ("City of Toronto Corporate Security mobile", 467.8250), ("City of Toronto Corporate Security mobile", 468.8500),
    ("St. Lawrence Market (City of Toronto)", 453.4500), ("St. Lawrence Market (City of Toronto)", 453.7250),
    ("TTC (VHF analog licence)", 148.3600), ("TTC (VHF analog licence)", 149.4100), ("TTC (VHF analog licence)", 151.5200), ("TTC (VHF analog licence)", 151.9250),
    ("TTC (VHF analog licence)", 153.6350), ("TTC (VHF analog licence)", 154.8900), ("TTC (VHF analog licence)", 156.1950),
    ("Enbridge Gas (VHF analog)", 158.9850), ("Enbridge Gas (VHF analog)", 162.5850), ("Enbridge Gas (VHF analog)", 162.6900), ("Enbridge Gas (VHF analog)", 165.5100),
    ("Halton Region Waste", 462.2750), ("Halton Region Waste", 467.2750),
    ("MTO highway plows + road crews, GTA 400-series (PL 103.5)", 143.8050), ("MTO maintenance simplex (PL 114.8)", 143.8050),
    ("MTO road crews / traffic control (reported)", 143.2350), ("MTO road crews / traffic control (reported)", 143.1750),
    ("Toronto Fire analog backup - North", 866.2375), ("Toronto Fire analog backup - East", 867.1125), ("Toronto Fire analog backup - West", 867.2625),
    ("Toronto Fire analog backup - South", 867.4625), ("Toronto Fire analog backup - citywide", 867.7375),
]
# Digital voice systems (decoded by SDRTrunk from the dashboard): name, MHz, protocol, note
DIGITAL = [
    ("Toronto Fire + Paramedics - West Simulcast control", 770.58125, "P25 Phase II", "City of Toronto system (WACN BEE00, sys 3C2); Police on it is AES-encrypted"),
    ("Toronto Fire + Paramedics - South Simulcast control", 769.93125, "P25 Phase II", "same system, downtown/south site"),
    ("Toronto Fire + Paramedics - North Simulcast control", 770.43125, "P25 Phase II", "same system, north site"),
    ("Toronto Fire + Paramedics - East Simulcast control", 770.53125, "P25 Phase II", "same system, east site"),
    ("Metrolinx / GO Transit", 410.0625, "DMR", "UHF expansion 2016 licences; many channels 410.06-410.56"),
    ("Metrolinx / GO Transit", 410.2125, "DMR", ""), ("Metrolinx / GO Transit", 410.3375, "DMR", ""), ("Metrolinx / GO Transit", 410.5125, "DMR", ""),
    ("Metrolinx / GO Transit", 411.1625, "DMR", "heard here (hammer tone)"), ("Metrolinx yard", 462.5625, "DMR", ""),
    ("Enbridge Gas Distribution", 414.4625, "DMR", ""), ("Enbridge Gas Distribution", 419.4625, "DMR", ""),
    ("Snow Marines Snow Service", 469.9000, "DMR", "snow contractor"),
    ("Bearcom (CN Tower)", 463.3125, "DMR", "heard here (hammer tone)"),
    ("University (U of T / TMU / York)", 454.3625, "DMR", ""), ("University", 454.2000, "DMR", ""), ("University", 460.1625, "DMR", ""),
    ("City of Toronto Court Services", 72.1400, "DMR", "VHF low band"),
    ("City of Toronto water-meter telemetry", 458.7625, "data", "NOT voice - automatic meter reading network (nothing to hear)"),
    ("City of Toronto water-meter telemetry", 460.3875, "data", "NOT voice - automatic meter reading network"),
    ("Toronto Hydro", 897.6375, "900 MHz trunked", "EDACS/P25 mix - SDRTrunk cannot decode EDACS; listed for reference"),
    ("TTC buses / subway", 0, "TETRA", "not decodable with SDRTrunk"),
]

def entries():
    out = []
    for ap, lst in AIRPORTS.items():
        for name, f in lst:
            out.append(dict(cat="Airport", group=ap, name=name, mhz=f, mode="AM", duplex="", offset=0, tone="", note="Airband - listen only", key=f"{ap} {name}"))
    for k, (f, d, o, t, w) in REPEATERS.items():
        nets = "; ".join(f"{dd} {tt} {nn}" for dd, tt, nn, kk, _ in NETS if kk == k)
        out.append(dict(cat="Ham repeater", group="2m" if f < 200 else "70cm", name=k.split()[0], mhz=f, mode="NFM", duplex=d, offset=o, tone=t, note=w, nets=nets, key=k))
    for k, (f, w) in SIMPLEX.items():
        out.append(dict(cat="Ham simplex", group="2m" if f < 200 else "70cm", name=k, mhz=f, mode="NFM", duplex="", offset=0, tone="", note=w, key=k))
    for name, f in FRSGMRS:
        out.append(dict(cat="FRS/GMRS", group="FRS" if "FRS" in name else "GMRS", name=name, mhz=f, mode="NFM", duplex="", offset=0, tone="", note="Family radio - buddies on bubble-pack radios hear this", key=name))
    for name, f in MURS:
        out.append(dict(cat="MURS", group="MURS", name=name, mhz=f, mode="NFM", duplex="", offset=0, tone="", note="", key=name))
    seen = set()
    for name, f in RETAIL:
        if f in seen: continue
        seen.add(f)
        out.append(dict(cat="Retail", group="Store walkie-talkies", name=name, mhz=f, mode="NFM", duplex="", offset=0, tone="",
                        note="Licence-exempt business channel (Motorola CLS/RDU/RDM class radios). Chain programming is the US standard - confirm at the local store. 1-2 W handhelds carry ~1 km outdoors, so only nearby stores reach a home antenna.", key=name))
    for name, f in MARINE:
        out.append(dict(cat="Marine", group="Marine", name=name, mhz=f, mode="NFM", duplex="", offset=0, tone="", note="Lake Ontario / Muskoka boats", key=name))
    for name, f in CITY:
        grp = "Garbage / recycling" if any(w in name for w in ("GFL", "Miller", "Emterra", "Solid Waste", "Waste")) else ("Fire backup" if "Fire" in name else ("Winter / snow / roads" if "MTO" in name else "City / utilities"))
        tone = "103.5" if "PL 103.5" in name else ("114.8" if "PL 114.8" in name else "")
        note = ("Reported on the RadioReference forum (unverified here): 143.805 is used for echelon plowing on the 400-series in the GTA. Plows are private contractors, so this is busiest in a snowfall." if "MTO" in name
                else "Analog FM licence (ISED register) - hearable here; range depends on the truck being nearby")
        out.append(dict(cat="City services", group=grp, name=name, mhz=f, mode="NFM", duplex="", offset=0, tone=tone, note=note, key=f"{name} {f}"))
    for name, f, proto, note in DIGITAL:
        if not f: continue
        out.append(dict(cat="Digital", group=proto, name=name, mhz=f, mode="P25" if "P25" in proto else proto.upper(), duplex="", offset=0, tone="", note=note or (proto + " - press play: decoded by SDRTrunk"), key=f"{name} {f}", digital=(proto != "data")))
    for e in out: e["uv32"] = uv32(e["mhz"])
    return out

def build_sdrpp(es, path):
    mode = {"NFM": 0, "WFM": 1, "AM": 2}; bw = {"NFM": 12500, "AM": 10000}
    marks = {f"{e['cat'][:3]} {e['name']} {e['mhz']:.4f}": {"bandwidth": bw.get(e["mode"], 12500), "frequency": int(round(e["mhz"] * 1e6)), "mode": mode[e["mode"]]} for e in es if not e.get("digital") and e["mode"] in mode}       # analog bookmarks only: SDR++ has no P25/DMR mode here
    json.dump({"bookmarks": marks, "showOnWaterfall": True}, open(path, "w"), indent=1)
    return len(marks)

def build_chirp(es, path):
    cols = ["Location","Name","Frequency","Duplex","Offset","Tone","rToneFreq","cToneFreq","DtcsCode","DtcsPolarity","RxDtcsCode","CrossMode","Mode","TStep","Skip","Power","Comment","URCALL","RPT1CALL","RPT2CALL","DVCODE"]
    rows = []
    for e in es:
        if not e["uv32"] or e.get("digital") or e["mode"] not in ("NFM", "AM", "WFM"): continue                      # the UV-32 is analog-only: digital channels never go in the CHIRP file
        nm = (e["name"] if e["cat"] != "Airport" else (e["group"].split()[0] + " " + e["name"]))[:10]
        rows.append([len(rows), nm, f"{e['mhz']:.6f}", e["duplex"], f"{e['offset']:.6f}", "Tone" if e["tone"] else "", e["tone"] or "88.5", e["tone"] or "88.5",
                     "023", "NN", "023", "Tone->Tone", e["mode"], "5.00", "", "10.0W", (e["note"] + (" | " + e.get("nets", "") if e.get("nets") else ""))[:200], "", "", "", ""])
    with open(path, "w", newline="") as f: w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    return len(rows)

def build_html(es, path):
    data = json.dumps(es, separators=(",", ":"))
    nets = json.dumps([dict(day=d, time=t, net=n, key=k, note=x) for d, t, n, k, x in NETS], separators=(",", ":"))
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Radio Hub</title>
<style>
:root{{--bg:#0e1116;--card:#171c24;--ink:#e8edf3;--mute:#8b96a5;--acc:#3fb950;--acc2:#58a6ff;--line:#262d38;--warn:#d29922}}
@media (prefers-color-scheme:light){{:root{{--bg:#f5f7fa;--card:#fff;--ink:#101418;--mute:#5c6673;--line:#e1e6ec}}}}
*{{box-sizing:border-box}}html,body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 -apple-system,system-ui,Helvetica,Arial,sans-serif}}
header{{position:sticky;top:0;background:var(--bg);padding:12px 16px 8px;border-bottom:1px solid var(--line);z-index:5}}
h1{{font-size:1.25rem;margin:0 0 8px}}
.row{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
input[type=search]{{flex:1;min-width:160px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card);color:var(--ink);font-size:16px}}
.chips{{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}}
.chip{{padding:6px 11px;border:1px solid var(--line);border-radius:20px;background:var(--card);color:var(--mute);font-size:.85rem;cursor:pointer;user-select:none}}
.chip.on{{background:var(--acc);color:#04130a;border-color:var(--acc);font-weight:600}}
main{{padding:12px 16px 40px;max-width:1100px;margin:auto}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin-bottom:8px;display:grid;grid-template-columns:1fr auto;gap:4px 10px;align-items:center}}
.f{{font:600 1.25rem/1 ui-monospace,Menlo,monospace}}.n{{font-weight:600}}.m{{color:var(--mute);font-size:.85rem}}
.prog{{font:500 .9rem ui-monospace,Menlo,monospace;color:var(--acc2);text-align:right;white-space:nowrap}}
.tag{{display:inline-block;font-size:.7rem;padding:2px 7px;border-radius:10px;border:1px solid var(--line);color:var(--mute);margin-left:6px;vertical-align:middle}}
.tag.sdr{{border-color:var(--warn);color:var(--warn)}}.tag.hit{{border-color:var(--acc);color:var(--acc)}}
.sig{{grid-column:1/-1;display:flex;align-items:center;gap:8px;font-size:.8rem;color:var(--mute)}}.bar{{width:90px;height:6px;background:var(--line);border-radius:3px}}.bar i{{display:block;height:100%;background:var(--acc);border-radius:3px}}
h2{{font-size:.95rem;color:var(--acc);margin:18px 0 8px;text-transform:uppercase;letter-spacing:.04em}}
.hint{{padding:10px 12px;border-left:3px solid var(--acc);background:var(--card);border-radius:0 10px 10px 0;margin:10px 0;font-size:.9rem}}
.netrow{{display:grid;grid-template-columns:70px 1fr auto;gap:8px;padding:8px 0;border-bottom:1px solid var(--line);font-size:.92rem}}
.netrow .t{{color:var(--mute)}}.netrow .p{{font:500 .85rem ui-monospace,Menlo,monospace;color:var(--acc2);text-align:right;white-space:nowrap}}
.copy{{cursor:pointer}}.copy:active{{opacity:.5}}
label.file{{display:inline-block;padding:8px 12px;border:1px dashed var(--line);border-radius:10px;color:var(--mute);font-size:.85rem;cursor:pointer}}label.file input{{display:none}}
.count{{color:var(--mute);font-size:.85rem;margin:8px 0}}
</style></head><body>
<header>
<h1>Radio Hub <span class="m">GTA airports, ham, FRS/GMRS</span></h1>
<div class="row"><input id="q" type="search" placeholder="Search: VE3SKY, tower, 146.52, GMRS, Mississauga ..." autocomplete="off">
<label class="file">Load scan results.csv<input id="scanfile" type="file" accept=".csv"></label></div>
<div class="chips" id="chips"></div>
</header>
<main>
<div class="hint" id="tuning"><b>Tuning the UV-32:</b> airband = AM, wide, squelch 2-3 (ATC is faint between calls - low squelch or you miss the readbacks). Repeaters = NFM, tone and offset from the line in blue; tap it to copy. FRS/GMRS = NFM, squelch 4-5. Scan the airport group with a 2-3 s dwell so a landing's tower/arrival handoff isn't missed. Load a scan's <code>results.csv</code> above to see which of these the car antenna actually hears.</div>
<div class="count" id="count"></div>
<div id="list"></div>
<h2>Nets this week</h2><div id="nets"></div>
<p class="m" style="margin-top:16px">Built {dt.date.today():%B %d, %Y}. Airports: SkyVector / NAV CANADA. Repeaters: RepeaterBook, yrarc.org. Nets: va3ldm, skywide.ca, ve3we.org. Schedules drift - confirm on air.</p>
</main>
<script>
const DATA={data};const NETS={nets};
const DAYS=["Daily","Mon-Sat","Mon/Wed/Fri","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
const cats=["All","Airport","Ham repeater","Ham simplex","FRS/GMRS","MURS","Retail","Marine","UV-32 only","Heard in scan"];
let cat="All", q="", scan={{}};
const chips=document.getElementById("chips");
cats.forEach(c=>{{const b=document.createElement("span");b.className="chip"+(c==="All"?" on":"");b.textContent=c;b.onclick=()=>{{cat=c;[...chips.children].forEach(x=>x.classList.toggle("on",x.textContent===c));render();}};chips.appendChild(b);}});
document.getElementById("q").oninput=e=>{{q=e.target.value.trim().toLowerCase();render();}};
function prog(e){{let s=e.mhz.toFixed(4);if(e.duplex)s+=" "+e.duplex+e.offset;if(e.tone)s+=" / "+e.tone;s+=" "+e.mode;return s;}}
function match(e){{if(cat==="UV-32 only"&&!e.uv32)return false;if(cat==="Heard in scan"&&!scan[e.mhz.toFixed(4)])return false;
 if(cat!=="All"&&cat!=="UV-32 only"&&cat!=="Heard in scan"&&e.cat!==cat)return false;
 if(!q)return true;const hay=(e.name+" "+e.group+" "+e.cat+" "+e.mhz.toFixed(4)+" "+e.note+" "+(e.nets||"")).toLowerCase();return q.split(/\\s+/).every(w=>hay.includes(w));}}
function render(){{const list=document.getElementById("list");let rows=DATA.filter(match);
 if(Object.keys(scan).length)rows=rows.slice().sort((a,b)=>(scan[b.mhz.toFixed(4)]?.score||-1)-(scan[a.mhz.toFixed(4)]?.score||-1));
 document.getElementById("count").textContent=rows.length+" of "+DATA.length;
 let html="",lastg="";
 for(const e of rows){{const g=e.cat==="Airport"?e.group:e.cat+(e.group&&e.cat!=="Ham simplex"?" - "+e.group:"");if(g!==lastg){{html+="<h2>"+g+"</h2>";lastg=g;}}
  const h=scan[e.mhz.toFixed(4)];
  html+=`<div class="card"><div><span class="f">${{e.mhz.toFixed(4)}}</span><span class="tag">${{e.mode}}</span>${{e.uv32?"":"<span class='tag sdr'>SDR only</span>"}}${{h?"<span class='tag hit'>heard</span>":""}}<br><span class="n">${{e.name}}</span> <span class="m">${{e.note||""}}</span>${{e.nets?"<br><span class='m'>Nets: "+e.nets+"</span>":""}}</div>
  <div class="prog copy" title="tap to copy" onclick="navigator.clipboard&&navigator.clipboard.writeText('${{prog(e)}}')">${{prog(e)}}</div>
  ${{h?`<div class="sig"><div class="bar"><i style="width:${{Math.min(100,h.snr*2.5)}}%"></i></div>${{h.snr}} dB over noise - busy ${{h.busy}}%</div>`:""}}</div>`;}}
 list.innerHTML=html||"<p class='m'>Nothing matches.</p>";}}
function renderNets(){{const byKey={{}};DATA.forEach(e=>byKey[e.key]=e);let html="";
 for(const d of DAYS){{const ns=NETS.filter(n=>n.day===d);if(!ns.length)continue;html+="<h2 style='font-size:.85rem'>"+d+"</h2>";
  for(const n of ns){{const e=byKey[n.key];html+=`<div class="netrow"><span class="t">${{n.time}}</span><span><b>${{n.net}}</b>${{n.note?" <span class='m'>"+n.note+"</span>":""}}<br><span class="m">${{e?e.name+" - "+e.note:n.key}}</span></span><span class="p copy" onclick="navigator.clipboard&&navigator.clipboard.writeText('${{e?prog(e):""}}')">${{e?prog(e):""}}</span></div>`;}}}}
 document.getElementById("nets").innerHTML=html;}}
document.getElementById("scanfile").onchange=ev=>{{const f=ev.target.files[0];if(!f)return;const r=new FileReader();r.onload=()=>{{
 const lines=r.result.split(/\\r?\\n/).filter(Boolean);const hdr=lines[0].split(",");const im=hdr.indexOf("mhz"),isn=hdr.indexOf("snr_db"),ib=hdr.indexOf("active_pct"),isc=hdr.indexOf("score");
 scan={{}};for(const l of lines.slice(1)){{const c=l.split(",");const m=parseFloat(c[im]);if(!isNaN(m))scan[m.toFixed(4)]={{snr:parseFloat(c[isn]),busy:parseFloat(c[ib]),score:parseFloat(c[isc])}};}}
 render();}};r.readAsText(f);}};
render();renderNets();
</script></body></html>"""
    open(path, "w").write(html)

if __name__ == "__main__":
    es = entries()
    build_html(es, "Radio Hub.html")
    n1 = build_sdrpp(es, "sdrpp_all_bookmarks.json")
    n2 = build_chirp(es, "radio_hub_chirp.csv")
    print(f"Radio Hub.html: {len(es)} entries; sdrpp_all_bookmarks.json: {n1}; radio_hub_chirp.csv: {n2} UV-32 channels")
