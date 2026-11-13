#!/usr/bin/env python3
"""Write a ready-to-load SDRTrunk playlist for the Toronto digital systems the dongle can hear.
    python3 sdrtrunk_playlist.py            -> sdrtrunk/SDR_Scanner.xml  (also called by the dashboard's Digital tab)
SDRTrunk (~/SDRTrunk) decodes P25 Phase II / DMR voice; this app's FM demodulator cannot. The playlist carries:
  - City of Toronto P25 Phase II system (WACN BEE00, system 3C2): one channel per simulcast site with its control
    channels; the two sites nearest the west end (West, South) are enabled, the others ready to switch on.
    Talkgroup aliases name every Toronto Fire and Toronto Paramedics talkgroup. Police is AES-encrypted on the same
    system - SDRTrunk shows the calls but can play nothing, nobody can.
  - DMR channels: Metrolinx / GO, universities, Bearcom and every UHF frequency the sweep saw whose ISED licence is
    digital (from licences_gta.csv + bank.json), each as a conventional DMR channel.
Format follows sdrtrunk's PlaylistV2 / Channel / DecodeConfigP25Phase1 / SourceConfigTunerMultipleFrequency classes.
A trunked P25 system (Phase I or II) is decoded from its CONTROL channel with the P25 Phase 1 decoder (C4FM); SDRTrunk
spawns the Phase 2 TDMA traffic channels itself. The 'P25 Phase 2' decoder is only for a single fixed TDMA channel."""
import csv, json, os, time
from xml.sax.saxutils import quoteattr as qa

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "sdrtrunk"); OUT = os.path.join(OUT_DIR, "SDR_Scanner.xml")
XSI = "http://www.w3.org/2001/XMLSchema-instance"

# City of Toronto P25 Phase II (RadioReference sid 7995, Nov 2026). West = Etobicoke, nearest to home.
P25_SITES = [
    ("West Simulcast",  True,  [770.58125, 770.63125, 770.83125, 771.55625]),
    ("South Simulcast", True,  [769.93125, 770.18125, 770.68125, 771.00625]),
    ("North Simulcast", False, [770.43125, 770.50625, 770.75625, 770.80625]),
    ("East Simulcast",  False, [770.53125, 770.60625, 770.78125, 770.88125]),
    ("PATH / TTC Subway", False, [868.1625, 868.225, 868.625, 868.7125]),
    # (v157) West + South + North + East are one P25 trunked system (Toronto Fire + Paramedics, WACN BEE00/3C2) on four simulcast
    # sites; all 16 of their frequencies span only 1.625 MHz, which fits in one 2.4 MHz dongle window - so one channel definition
    # with every site's control/traffic frequency hears whichever site is actually carrying the call, instead of just one site at a
    # time. This does NOT add TTC: TTC's own bus/subway radio is TETRA, a different system SDRTrunk cannot decode at all.
    # (v177) Order matters: SDRTrunk tries these one at a time, in this order, until it finds a control channel, and comes back to the
    # top of the list whenever it loses lock. Sorted by frequency it started on South's channels and took 2-49 s to settle (West alone:
    # 0-1 s). West first (the strongest site here, 770.58125 is its control channel), then South, North, East.
    ("Fire + EMS - All 4 Sites Combined", False, [770.58125, 770.63125, 770.83125, 771.55625, 769.93125, 770.18125, 770.68125, 771.00625,
                                                 770.43125, 770.50625, 770.75625, 770.80625, 770.53125, 770.60625, 770.78125, 770.88125]),
    # (v175) Toronto Fire's main dispatch talkgroup (TFS Disp 1, 1401) is assigned 774.29375 MHz almost every time. That is 2.74 MHz above
    # the highest control channel (771.55625), just outside what one RTL dongle can hold together with the control channel, so SDRTrunk
    # rejected those calls ("tuner unavailable"). This entry listens on 774.29375 directly as a P25 Phase 1 voice channel: dispatch only,
    # no control channel needed. A second dongle (or an Airspy) is what lets the trunked channel follow dispatch as well.
    ("Fire Dispatch direct (TFS Disp 1)", False, [774.29375]),
    # Metrolinx / GO Transit P25 Phase II (RadioReference sid 4579, WACN BEE00 system 1AC): Central Simulcast covers Toronto incl.
    # Willowbrook yard. Control channels first (411.2875 first: a 2.4 MHz window centred there reaches 7 of the 12 site frequencies).
    ("Metrolinx Central", False, [411.2875, 411.0375, 410.2125, 412.2875]),
]
# which P25 system / alias list a site belongs to (default: City of Toronto)
# (v156) Mimico / Lakeshore West coverage on Metrolinx Central, grouped for clarity in the SDRTrunk window only - no
# priority boost, no colour change. Every Metrolinx talkgroup is heard the same way; these just show under a clearer name.
GO_LAKESHORE_GROUPS = {
    "GO Lakeshore West (trains - Mimico / Long Branch / Port Credit...)": {250},
    "GO Rail PA / Dispatch": {211, 240, 241},
    "GO Rail Yard (Willowbrook)": {245, 247, 248},
}
GO_LAKESHORE_TGS = {tg for s in GO_LAKESHORE_GROUPS.values() for tg in s}
SITE_SYSTEM = {"Metrolinx Central": ("Metrolinx / GO Transit", "Metrolinx")}
# Metrolinx talkgroups: RadioReference sid 4579 as read on 11 Nov 2026 - id: (name shown, what it is). (E) = encrypted: shown, silent.
# (v192) The March 2025 table that stood here had the USRC group shifted by one (501 was called "USRC 2"; it is USRC 1, 500 is the
# all-call), placeholder names for the bus and facilities groups ("Bus Ops 13" = supervisors, "Facilities 1" = East Maintenance
# Facility) and no Durham Region Transit, which shares the system.
METROLINX_TG = {
    100: ("Bus All Call", "GO bus operations: announcement to every bus radio"),
    101: ("Bus Southwest", "GO bus operations, southwest region"),
    102: ("Bus West", "GO bus operations, west region"),
    103: ("Bus North", "GO bus operations, north region"),
    104: ("Bus East", "GO bus operations, east region"),
    105: ("Bus Events West", "GO bus: events west, specials, spare"),
    106: ("Bus Events East", "GO bus: events east, Wonderland Express"),
    107: ("Bus Fleet", "GO bus fleet and maintenance"),
    108: ("Bus Northwest", "GO bus operations, northwest region"),
    109: ("Bus Control West", "GO bus control, west"),
    110: ("Bus Control East", "GO bus control, east"),
    111: ("Union Station Bus Terminal", "Union Station Bus Terminal"),
    112: ("Bus Ops Secure (E)", "GO bus operations, encrypted"),
    113: ("Bus Supervisors", "GO bus supervisors and marshals"),
    114: ("Emergency", "Emergency talkgroup"),
    211: ("Rail PA", "On-board public-address announcements on GO trains"),
    212: ("TTR Security York-Bay", "Security at Union Station, York and Bay side (Toronto Terminals Railway)"),
    240: ("GO Dispatch 1", "Commuter train dispatch"),
    241: ("GO Dispatch 2", "Commuter train dispatch"),
    242: ("GO/UP Rail Secure (E)", "Rail operations, encrypted"),
    243: ("Loco Alarms 1", "Locomotive Alarm Reporting System: automated alarm reports from locomotives"),
    244: ("Loco Alarms 2", "Locomotive Alarm Reporting System: automated alarm reports from locomotives"),
    245: ("Willowbrook Yard 1", "Willowbrook Yard, the GO rail yard and shops"),
    247: ("Willowbrook Yard 2", "Willowbrook Yard, the GO rail yard and shops"),
    248: ("Willowbrook Grounds Escort", "Willowbrook Yard: escorts on the yard grounds"),
    250: ("Lakeshore West", "GO trains, Lakeshore West line"),
    251: ("Lakeshore East", "GO trains, Lakeshore East line"),
    252: ("Kitchener", "GO trains, Kitchener line"),
    253: ("Stouffville", "GO trains, Stouffville line"),
    254: ("Richmond Hill", "GO trains, Richmond Hill line"),
    255: ("Barrie", "GO trains, Barrie line"),
    256: ("Milton", "GO trains, Milton line"),
    300: ("Transit Safety All Call", "Transit Safety: announcement to all officers"),
    301: ("Transit Safety Ops 1 (E)", "Transit Safety operations 1, encrypted"),
    302: ("Transit Safety Ops 2 (E)", "Transit Safety operations 2, encrypted"),
    303: ("Transit Safety Ops 3 (E)", "Transit Safety operations 3, encrypted"),
    304: ("Transit Safety Ops 4 (E)", "Transit Safety operations 4, encrypted"),
    305: ("Transit Safety Ops 5", "Transit Safety operations 5"),
    310: ("Security", "Security"),
    500: ("USRC All Call", "Union Station Rail Corridor: announcement to every USRC radio"),
    501: ("USRC 1", "Union Station Rail Corridor 1 - the group that maintains the tracks through and around Union Station"),
    502: ("USRC 2", "Union Station Rail Corridor 2"),
    503: ("USRC 3", "Union Station Rail Corridor 3 (not in the published list - name inferred from its neighbours)"),
    504: ("USRC 4", "Union Station Rail Corridor 4 (not in the published list - name inferred from its neighbours)"),
    505: ("USRC 5", "Union Station Rail Corridor 5"),
    506: ("USRC 6", "Union Station Rail Corridor 6"),
    30:  ("Facilities Ops", "Facilities operations"),
    601: ("East Maintenance Facility", "East Maintenance Facility"),
    602: ("Central Region Ops", "Central region operations"),
    603: ("East Region Ops", "East region operations"),
    604: ("Plant Services Union Stn", "Plant services, Union Station"),
    605: ("Customer Care", "Customer care"),
    606: ("Customer Care Union Stn", "Customer care, Union Station"),
    607: ("West Maintenance Facility", "West Maintenance Facility"),
    608: ("West Region Ops", "West region operations"),
    609: ("Customer Care Platforms", "Customer care on the platforms"),
    610: ("Station Ops East Events", "Station operations, east events"),
    612: ("Station Ops West Events", "Station operations, west events"),
    701: ("UP Express 1", "Union Pearson Express"),
    702: ("UP Express 2", "Union Pearson Express"),
    1:   ("Interop System (E)", "Interoperability with other agencies: system, encrypted"),
    2:   ("Interop Enforcement (E)", "Interoperability: enforcement, encrypted"),
    3:   ("Interop Maintenance of Way (E)", "Interoperability: track maintenance, encrypted"),
    4:   ("Interop Mass Casualty (E)", "Interoperability: mass-casualty incidents, encrypted"),
    400: ("DRT All Call", "Durham Region Transit: announcement to all"),
    401: ("DRT Westney", "Durham Region Transit: Ajax and Pickering"),
    402: ("DRT Raleigh", "Durham Region Transit: Oshawa and Clarington"),
    403: ("DRT Whitby", "Durham Region Transit: Whitby"),
    404: ("DRT Specialized", "Durham Region Transit: specialized services"),
    405: ("DRT Pulse", "Durham Region Transit: Pulse, the Highway 2 rapid bus"),
    406: ("DRT Supervisors", "Durham Region Transit: supervisors"),
    407: ("DRT Events 1", "Durham Region Transit: events 1"),
    408: ("DRT Events 2", "Durham Region Transit: events 2"),
    409: ("DRT Private", "Durham Region Transit: private"),
    411: ("DRT Emergency", "Durham Region Transit: emergency"),
}
METROLINX_TG.update({1000 + i: (f"Radio Service {i}", f"Radio technicians' service channel {i}") for i in range(1, 11)})
METROLINX = {tg: name for tg, (name, _) in METROLINX_TG.items()}
# names this table used before v192, by id - old lines in the call log still carry them
METROLINX_OLD = {500: "USRC 1", 501: "USRC 2", 502: "USRC 3", 503: "USRC 4", 504: "USRC 5", 505: "USRC 6", 506: "USRC 7", 113: "Bus Ops 13", 114: "Bus Ops 14"}
def metrolinx_info(tid, logged_name="", system=""):
    """(v192) What a talkgroup id on the Metrolinx system is: {"name", "desc", "listed"} - or None when the call is not Metrolinx.
    A call is taken as Metrolinx when its log says so (system), or when the name SDRTrunk logged for it is one this table gave it."""
    try: t = int(tid)
    except (TypeError, ValueError): return None
    nm = (logged_name or "").strip("[] ")
    known = METROLINX_TG.get(t)
    mine = system == "Metrolinx" or (known is not None and (nm == known[0] or nm == METROLINX_OLD.get(t) or nm.startswith(("Bus Ops", "Facilities", "Radio Techs", "Customer Care"))))
    if not mine: return None
    if known: return {"name": known[0], "desc": known[1], "listed": True}
    return {"name": "Unlisted talkgroup", "desc": "Heard on the Metrolinx system but not in the published talkgroup list", "listed": False}
FIRE = {1401: "TFS Disp 1", 1251: "TFS S Ops", 1253: "TFS S TAC 1", 1255: "TFS S TAC 2", 1257: "TFS S TAC 3", 1259: "TFS S TAC 4", 1261: "TFS S TAC 5",
        1263: "TFS S TAC 6", 1265: "TFS S Str 1", 1267: "TFS S Str 2", 1269: "TFS S Prev", 1271: "TFS E Ops", 1273: "TFS E TAC 1", 1275: "TFS E TAC 2",
        1277: "TFS E TAC 3", 1279: "TFS E TAC 4", 1281: "TFS E TAC 5", 1283: "TFS E TAC 6", 1285: "TFS E Str 1", 1287: "TFS E Str 2", 1289: "TFS E Prev",
        1291: "TFS N Ops", 1293: "TFS N TAC 1", 1295: "TFS N TAC 2", 1297: "TFS N TAC 3", 1299: "TFS N TAC 4", 1301: "TFS N TAC 5", 1303: "TFS N TAC 6",
        1305: "TFS N Str 1", 1307: "TFS N Str 2", 1309: "TFS N Prev", 1311: "TFS W Ops", 1313: "TFS W TAC 1", 1315: "TFS W TAC 2", 1317: "TFS W TAC 3",
        1319: "TFS W TAC 4", 1321: "TFS W TAC 5", 1323: "TFS W TAC 6", 1325: "TFS W Str 1", 1327: "TFS W Str 2", 1329: "TFS W Prev", 1355: "TFS W / Vaughan Fire",
        1331: "TFS Training 1", 1333: "TFS Training 2", 1335: "TFS Training 3", 1337: "TFS Training 4", 1339: "TFS Training 5", 1341: "TFS Training 6", 1359: "TFS Mech"}
EMS = {1601: "EMS A1 NW SB", 1603: "EMS A2 NE SB", 1605: "EMS A3 SW SB", 1607: "EMS A4 SE SB", 1613: "EMS A5 NC SB", 1609: "EMS A6 SC SB", 1667: "EMS A7 Alerts",
       1669: "EMS A8 Spvrs", 1679: "EMS Emerg Revert", 1615: "EMS B1 NW Dest", 1657: "EMS B2 NE Dest", 1675: "EMS B3 SW Dest", 1677: "EMS B4 SE Dest",
       1671: "EMS B8 Spec Op", 1663: "EMS B9 Staging", 1659: "EMS B10 Event 1", 1661: "EMS B11 Event 2", 1673: "EMS B12 Event 3", 1623: "EMS B13 Event 4",
       1625: "EMS B14 Event 5", 1611: "EMS B15 Event 6", 1641: "EMS B16 Event 7", 1617: "EMS C1-C6 Clearing", 1543: "EMS C11 JES", 1545: "EMS C12 JES",
       1547: "EMS C13 JES", 1548: "TEMS / Sick Kids", 463: "Lifeguard (beaches)"}
# Conventional DMR around Toronto (licence register + what the sweep hears)
# (4 Nov research: RadioReference aid 1770 Etobicoke, sid 9038, sid 8964, wiki FleetConnect; ISED licences.) Conventional DMR only -
# trunked systems are in DMR_TRUNKED below. Metrolinx is P25 (P25_SITES), not DMR; hospitals and far-away sites left out on purpose.
DMR_KNOWN = [
    (463.3125, "Bearcom community repeater (CN Tower)"),
    (454.2000, "Humber Polytechnic Lakeshore - security / facilities"),
    (454.3625, "City of Toronto Corporate - 65 Front St W (Union Station)"),
    (452.0625, "Sherway Gardens - custodial"), (452.3625, "Sherway Gardens - security"),
    (452.8125, "Woodbine Centre - security"), (453.7250, "Stockyards (Rio-Can) - security"),
    (453.8875, "Purolator hub"), (451.1250, "Purolator hub 2"), (464.9625, "Purolator hub 3 / St George's G&CC"),
    (451.1875, "Islington 2000/2010 - maintenance"), (462.8250, "Spa at Old Mill"), (464.6375, "Chrysler Casting"),
    (409.0500, "PortsToronto / Billy Bishop airport 1"), (409.4375, "PortsToronto / Billy Bishop airport 2"), (409.9750, "PortsToronto / Billy Bishop airport 3"),
    (442.1750, "Ham DMR VE3WOO (BrandMeister)"), (444.6375, "Ham DMR VE3YYZ (BrandMeister)"), (444.2875, "Ham DMR VE3NUS (BrandMeister)"),
    (442.1875, "Ham DMR VE3WIK (BrandMeister)"), (444.2500, "Ham DMR VA3PMO Mississauga (BrandMeister)"), (441.9500, "Ham DMR VA3XFT (DMR-MARC)"),
]
# DMR Tier III / Capacity Max trunked systems: SDRTrunk follows the control channel and the traffic channels it can reach in the
# dongle's 2.4 MHz window. (RadioReference sid 4104 "Toronto City Services", sid 9038 "Billy Bishop Toronto City Airport".)
DMR_TRUNKED = [
    ("ROADS Toronto (SNOWTO: snow plows, salt trucks, Roads, Solid Waste) - DMR Cap Max, CN Tower", 939.1375,
     [939.1375, 937.0125, 938.5125, 938.5375, 938.5625, 938.5875, 939.1625]),
    ("Billy Bishop airport ops (DMR Tier III) - Toronto Island", 454.0375, [454.0375, 461.2375]),
]
CITY_SERVICES = {200: "Corporate Security 1", 201: "Corporate Security 2", 202: "Corp Sec - Jack Layton Ferry Terminal", 150: "Roads - Signs & Markings",
                 152: "Roads - Toronto/East York Area 1 Hwy Mtc", 153: "Roads 3", 154: "Roads - T/EY Area 1 & 2", 155: "Roads - T/EY Emergency",
                 158: "Roads 8", 159: "Roads - Ellesmere Yard", 160: "Roads - Dump Trucks", 111: "Winter Maintenance Scarborough",
                 100: "Solid Waste District 3 (E of Yonge)", 101: "Solid Waste District 4 Scarborough", 102: "Solid Waste - Parks collections",
                 103: "Solid Waste - Litter bins", 104: "Solid Waste - Over-size"}
# (v152) the talkgroups that carry plows, salt trucks and road crews: priority 1 in the playlist so a plow call always wins over refuse or security traffic
SNOWTO_TGS = {150, 152, 153, 154, 155, 158, 159, 160, 111}
# GO Transit / Metrolinx rail traffic control: NXDN 4800 (RAN 0) on VHF (Metrolinx VHF Radio System Overview, 2021). Odd = crew standby, even = RTC.
NXDN_KNOWN = {160.350: "GO13 Lakeshore West crew", 161.4375: "GO14 Lakeshore West RTC (Willowbrook)", 160.950: "GO11 USRC West crew",
              160.650: "GO12 USRC West RTC", 161.220: "GO20 Oakville/Appleby RTC", 160.770: "GO15 Kitchener crew", 161.520: "GO16 Kitchener RTC"}
BILLY_BISHOP = {10: "Airport Ops", 20: "Ferry", 30: "Security 1", 40: "Command 1", 50: "Security 2", 80: "Fire Tac", 90: "Command 2"}
CITY_P25_EXTRA = {81: "City Corporate Security 1", 82: "City Corporate Security 2", 90: "TCHC Training", 94: "Crisis Response Team 1",
                  95: "Crisis Response Team 2", 96: "Crisis Response Team 3", 97: "Crisis Response Team 4"}

_LIC = {"m": None, "lic": {}}
def digital_licences():
    """{mhz: licensee} for every digital ISED licence - read once, re-read only when the CSV changes."""
    p = os.path.join(HERE, "licences_gta.csv")
    try: m = os.path.getmtime(p)
    except OSError: return {}
    if _LIC["m"] != m:
        lic = {}
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("digital", "").strip().upper().startswith("D"):
                    try: lic.setdefault(round(float(r["mhz"]), 4), r["licensee"])
                    except ValueError: pass
        _LIC.update(m=m, lic=lic)
    return _LIC["lic"]
_DFD = {"m": None, "out": {}}
import re as _re
P25_LICENSEES = _re.compile(r"metrolinx|go transit|city of toronto", _re.I)          # licensees whose UHF/700 channels are P25 trunked (decoded via P25_SITES), never DMR
def p25_site_extras():
    """(v138) Licensed channels of the P25 licensees the sweep saw in UHF -> shown under the Metrolinx Central site as voice channels of the
    same system. One dongle can only decode the ones inside its 2.4 MHz window around the control channel; the rest are listed for honesty."""
    out = []
    try:
        for f, n in digital_licences().items():
            if P25_LICENSEES.search(n or "") and 400 <= f <= 520 and "metrolinx" in (n or "").lower(): out.append((f, n))
    except Exception: pass
    return sorted(out)

def dmr_from_data():
    """UHF channels the sweep has seen whose licence is digital (the hammer tones) -> DMR candidates. Cached per bank.json mtime."""
    out = {}
    try:
        bp = os.path.join(HERE, "bank.json"); bm = os.path.getmtime(bp)
        if _DFD["m"] == bm: return dict(_DFD["out"])
        lic = digital_licences()
        bank = json.load(open(bp))
        for k, e in bank.items():
            try: m = round(float(k), 4)
            except ValueError: continue
            if 400 <= m <= 520 and e.get("ver") not in ("VOICE", "KNOWN"):
                near = [f for f in lic if abs(f - m) <= 0.00626]
                if near:
                    f = min(near, key=lambda f: abs(f - m))
                    if P25_LICENSEES.search(lic[f] or ""): continue                   # (v138) Metrolinx's UHF licences are its P25 Phase II system (Metrolinx Central), not DMR
                    out[f] = lic[f]                                                   # listed at the licensed frequency, not the sweep bin
        _DFD.update(m=bm, out=dict(out))
    except Exception: pass
    return out

def alias(name, group, tg, color, prio=None, lst="Toronto"):
    """prio: -1 = do not monitor (used by the dashboard's hone-in), None = default (hear it)."""
    pr = f'    <id xsi:type="priority" priority="{int(prio)}"/>\n' if prio else ""
    pr += f'    <id xsi:type="broadcastChannel" channel={qa(STREAM_NAME)}/>\n'
    return (f'  <alias color="{color}" group={qa(group)} list={qa(lst)} name={qa(name)}>\n'
            f'    <id xsi:type="talkgroup" protocol="APCO25" value="{tg}"/>\n{pr}  </alias>\n')

def p25_channel(site, enabled, freqs):
    fr = "".join(f"      <frequency>{int(round(f * 1e6))}</frequency>\n" for f in freqs)
    system, lst = SITE_SYSTEM.get(site, ("City of Toronto", "Toronto"))
    label = (system + " - " + site) if site in SITE_SYSTEM else ("Fire + Paramedics - " + site)
    return (f'  <channel system={qa(system)} site={qa(site)} name={qa(label)} enabled="{str(enabled).lower()}">\n'
            f'    <alias_list_name>{lst}</alias_list_name>\n'
            f'    <decode_configuration xsi:type="decodeConfigP25Phase1" modulation="C4FM" ignore_data_calls="true" traffic_channel_pool_size="60"/>\n'
            f'    <aux_decode_configuration xsi:type="auxDecodeConfiguration"/>\n'
            f'    <source_configuration xsi:type="sourceConfigTunerMultipleFrequency" source_type="TUNER_MULTIPLE_FREQUENCIES">\n{fr}    </source_configuration>\n'
            f'    <event_log_configuration xsi:type="eventLogConfiguration"><logger>CALL_EVENT</logger><logger>TRAFFIC_CALL_EVENT</logger></event_log_configuration>\n    <record_configuration xsi:type="recordConfiguration"><recorder>AUDIO</recorder></record_configuration>\n  </channel>\n')

def dmr_trunked_channel(name, cc, freqs, enabled=False, lst="Toronto"):
    fr = "".join(f"      <frequency>{int(round(f * 1e6))}</frequency>\n" for f in [cc] + [f for f in freqs if f != cc])
    return (f'  <channel system="Toronto DMR trunked" site={qa(name[:40])} name={qa(name)} enabled="{str(enabled).lower()}">\n'
            f'    <alias_list_name>{lst}</alias_list_name>\n'
            f'    <decode_configuration xsi:type="decodeConfigDMR" ignore_data_calls="true" traffic_channel_pool_size="12"/>\n'
            f'    <aux_decode_configuration xsi:type="auxDecodeConfiguration"/>\n'
            f'    <source_configuration xsi:type="sourceConfigTunerMultipleFrequency" source_type="TUNER_MULTIPLE_FREQUENCIES">\n{fr}    </source_configuration>\n'
            f'    <event_log_configuration xsi:type="eventLogConfiguration"><logger>CALL_EVENT</logger><logger>TRAFFIC_CALL_EVENT</logger></event_log_configuration>\n    <record_configuration xsi:type="recordConfiguration"><recorder>AUDIO</recorder></record_configuration>\n  </channel>\n')

def nxdn_channel(mhz, name, enabled=False):
    return (f'  <channel system="GO Transit rail (NXDN)" site="VHF" name={qa(f"{mhz:.4f} {name}")} enabled="{str(enabled).lower()}">\n'
            f'    <alias_list_name>Toronto</alias_list_name>\n'
            f'    <decode_configuration xsi:type="decodeConfigNXDN" ignore_data_calls="true" traffic_channel_pool_size="5"/>\n'
            f'    <aux_decode_configuration xsi:type="auxDecodeConfiguration"/>\n'
            f'    <source_configuration xsi:type="sourceConfigTuner" source_type="TUNER" frequency="{int(round(mhz * 1e6))}"/>\n'
            f'    <event_log_configuration xsi:type="eventLogConfiguration"><logger>CALL_EVENT</logger><logger>TRAFFIC_CALL_EVENT</logger></event_log_configuration>\n    <record_configuration xsi:type="recordConfiguration"><recorder>AUDIO</recorder></record_configuration>\n  </channel>\n')

DMR_ALIAS_FILE = os.path.join(HERE, "dmr_aliases.json")
def user_dmr_aliases():
    """(v145) {"422.3000": {"2": "Site crew", "1": "Dispatch"}} - talkgroup names the person gave to a conventional DMR channel.
    Talkgroup numbers are private to each fleet, so every channel with names gets its own SDRTrunk alias list ("DMR 422.3000")."""
    try: return {k: {str(t): n for t, n in v.items() if n} for k, v in json.load(open(DMR_ALIAS_FILE)).items()}
    except Exception: return {}
def dmr_alias_list(mhz):
    return f"DMR {mhz:.4f}" if user_dmr_aliases().get(f"{mhz:.4f}") else "Toronto"
def dmr_channel(mhz, name, enabled=False):
    return (f'  <channel system="Toronto DMR" site="conventional" name={qa(f"{mhz:.4f} {name}")} enabled="{str(enabled).lower()}">\n'
            f'    <alias_list_name>{dmr_alias_list(mhz)}</alias_list_name>\n'
            f'    <decode_configuration xsi:type="decodeConfigDMR" ignore_data_calls="true" traffic_channel_pool_size="5"/>\n'
            f'    <aux_decode_configuration xsi:type="auxDecodeConfiguration"/>\n'
            f'    <source_configuration xsi:type="sourceConfigTuner" source_type="TUNER" frequency="{int(round(mhz * 1e6))}"/>\n'
            f'    <event_log_configuration xsi:type="eventLogConfiguration"><logger>CALL_EVENT</logger><logger>TRAFFIC_CALL_EVENT</logger></event_log_configuration>\n    <record_configuration xsi:type="recordConfiguration"><recorder>AUDIO</recorder></record_configuration>\n  </channel>\n')

def build(extra_cc=None, enable=None, prio=None, nxdn_ok=None):
    """extra_cc: control-channel candidates (MHz) the sweep found that are not in the site tables -> an extra 'Found by sweep' channel.
    enable: None -> West + South auto-start (hand use); {"p25": site} or {"dmr": mhz} -> ONLY that channel auto-starts (dashboard ▶)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    if nxdn_ok is None: nxdn_ok = os.path.exists(os.path.expanduser("~/Applications/sdr-trunk-nightly/bin/sdr-trunk")) and not os.path.exists(os.path.join(HERE, "sdrtrunk", "use_stable.txt"))
    x = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<playlist xmlns:xsi="{XSI}" version="4">\n',
         f'  <!-- generated by SDR Scanner {time.strftime("%Y-%m-%d %H:%M")} - City of Toronto P25 Phase II (WACN BEE00 / system 3C2) + Toronto DMR -->\n']
    prio = {int(k): v for k, v in (prio or {}).items() if v}
    for tg, n in FIRE.items(): x.append(alias(n, "Toronto Fire", tg, -65536, prio.get(tg)))
    for tg, n in EMS.items(): x.append(alias(n, "Toronto Paramedics", tg, -16776961, prio.get(tg)))
    def go_group(tg):                                                               # (v156) which Lakeshore West sub-group this talkgroup shows under, if any
        for gname, tgs in GO_LAKESHORE_GROUPS.items():
            if tg in tgs: return gname
        return None
    for tg, n in METROLINX.items():
        x.append(alias(n, go_group(tg) or "Metrolinx / GO", tg, -16744448, prio.get(tg), lst="Metrolinx"))
    for tg, n in CITY_P25_EXTRA.items(): x.append(alias(n, "City of Toronto", tg, -8355712, prio.get(tg)))
    for tg, n in CITY_SERVICES.items():                                              # (v152) roads + winter maintenance = the SNOWTO group: highest priority, own colour
        snow = tg in SNOWTO_TGS
        x.append(alias(("SNOWTO - " if snow and not n.startswith("Winter") else "") + n, "ROADS Toronto - plows + salt (SNOWTO)" if snow else "City services (DMR)", tg,
                       -256 if snow else -8355712, 1 if snow else None, lst="CityDMR").replace('protocol="APCO25"', 'protocol="DMR"'))
    for tg, n in BILLY_BISHOP.items(): x.append(alias(n, "Billy Bishop airport", tg, -8355712, None, lst="BillyBishop").replace('protocol="APCO25"', 'protocol="DMR"'))
    ua = user_dmr_aliases()
    for k, tgs in sorted(ua.items()):                                                 # (v145) the person's own talkgroup names, per channel
        for tg, n in tgs.items():
            try: x.append(alias(n, f"DMR {k}", int(tg), -16744448, None, lst=f"DMR {k}").replace('protocol="APCO25"', 'protocol="DMR"'))
            except ValueError: pass
    for lst, proto in [("Toronto", "APCO25"), ("Metrolinx", "APCO25"), ("Toronto", "DMR"), ("CityDMR", "DMR"), ("BillyBishop", "DMR")] + [(f"DMR {k}", "DMR") for k in sorted(ua)]:   # unnamed talkgroups still stream
        x.append(f'  <alias color="-8355712" group="Other" list={qa(lst)} name="Other talkgroup ({proto})">\n'
                 f'    <id xsi:type="talkgroupRange" protocol="{proto}" min="1" max="65535"/>\n    <id xsi:type="broadcastChannel" channel={qa(STREAM_NAME)}/>\n  </alias>\n')
    x.append(stream_xml())
    for tg, v in sorted(prio.items()):                                  # starred / muted talkgroups that have no name yet get a plain alias
        if tg not in FIRE and tg not in EMS: x.append(alias(f"Talkgroup {tg}", "Other", tg, -8355712, v))
    for site, en, fr in P25_SITES:
        if enable is not None: en = enable.get("p25") == site
        x.append(p25_channel(site, en, fr))
    if enable and enable.get("probe"): x.append(p25_channel(f"Probe {enable['probe']:.4f}", True, [enable["probe"]]))
    known = {f for _, _, fr in P25_SITES for f in fr}
    extra = sorted({round(f, 5) for f in (extra_cc or []) if round(f, 5) not in known and 764 <= f <= 776})
    if extra: x.append(p25_channel("Found by sweep", False, extra))
    en_t = (enable or {}).get("dmrt")
    for name, cc, fr in DMR_TRUNKED:
        lst = "CityDMR" if cc > 900 else "BillyBishop"
        x.append(dmr_trunked_channel(name, cc, fr, enable is not None and en_t is not None and abs(en_t - cc) < 0.0001, lst))
    en_n = (enable or {}).get("nxdn") or set()
    if nxdn_ok:                                                        # only the nightly SDRTrunk knows decodeConfigNXDN; 0.6.1 would refuse the playlist
        for m, n in sorted(NXDN_KNOWN.items()): x.append(nxdn_channel(m, n, m in en_n))
    seen = set()
    for m, n in DMR_KNOWN + sorted(dmr_from_data().items()):
        if m in seen: continue
        en_d = enable.get("dmr") if enable else None
        on = (m in en_d) if isinstance(en_d, (set, list, tuple)) else (en_d == m)
        seen.add(m); x.append(dmr_channel(m, n, enable is not None and on))
    # (v207) a DMR frequency asked for by the dashboard that is in neither list (a channel the reference names as DMR): it gets its
    # channel here, or SDRTrunk would start with nothing to decode
    en_d = (enable or {}).get("dmr")
    for m in sorted(set(en_d) if isinstance(en_d, (set, list, tuple)) else ({en_d} if en_d else set())):
        if m not in seen: seen.add(m); x.append(dmr_channel(m, ((enable or {}).get("names") or {}).get(m, "DMR channel"), True))
    x.append("</playlist>\n")
    with open(OUT, "w", encoding="utf-8") as f: f.write("".join(x))
    return OUT, len(seen), len(FIRE) + len(EMS)

if __name__ == "__main__":
    out, ndmr, ntg = build()
    print(f"{out}\n  P25: {len(P25_SITES)} sites, {ntg} Fire/EMS talkgroups named; DMR: {ndmr} channels")
    print("SDRTrunk: Playlist Editor -> Playlists (top tab) -> Add -> pick this file -> Select; then Channels tab -> Play 'Fire + Paramedics - West Simulcast'.")


GROUP_NAMES = [(409.0, 410.0, "PortsToronto / Billy Bishop ground"), (441.9, 442.5, "Ham DMR 441-442 (BrandMeister / DMR-MARC)"),
               (444.0, 444.7, "Ham DMR 444 (BrandMeister)"), (451.0, 452.9, "Etobicoke west: Sherway, Woodbine, Purolator, Islington"),
               (453.5, 454.5, "Stockyards, Humber Lakeshore, City Corporate (Union)"), (462.8, 465.0, "Bearcom repeater + west business")]
# SDRTrunk -> local Icecast (brew install icecast; the dashboard runs it) -> the app's /sdrt_audio -> any browser, anywhere.
STREAM_NAME = "SDR Scanner"; ICECAST_PORT = 8800; ICECAST_MOUNT = "/sdrtrunk"; ICECAST_PASS = "sdrscanner"
def stream_xml():
    return (f'  <stream xsi:type="icecastHTTPConfiguration" name={qa(STREAM_NAME)} host="127.0.0.1" port="{ICECAST_PORT}" password={qa(ICECAST_PASS)} '
            f'user_name="source" mount_point={qa(ICECAST_MOUNT)} bitrate="32" sample_rate="8000" channels="1" delay="0" enabled="true" '
            f'maximum_recording_age="60000" inline="true" description="SDR Scanner - Toronto digital voice" genre="scanner" public="false">\n'
            f'    <format>MP3</format>\n  </stream>\n')
DMR_SPAN = 2.2       # MHz: channels this close together fit in the dongle's 2.4 MHz window and SDRTrunk decodes them ALL at once
def dmr_groups():
    """Conventional DMR channels bundled into groups SDRTrunk can decode simultaneously (one tuner, many channels).
    [{"name": ..., "mhz": [...], "names": [...]}], groups in frequency order."""
    allc = dict(DMR_KNOWN); allc.update(dmr_from_data())
    fr = sorted(allc); groups = []; cur = []
    for m in fr:
        if cur and m - cur[0] > DMR_SPAN: groups.append(cur); cur = []
        cur.append(m)
    if cur: groups.append(cur)
    out = []
    for g in groups:
        names = [allc[m] for m in g]
        short = []
        for n in names:
            n0 = n.split(" (")[0].split(" - ")[0].split(" / ")[0]
            if n0 not in short: short.append(n0)
        label = short[0] + (f" + {len(short)-1} more" if len(short) > 1 else "") if short else f"{g[0]:.4f}"
        for lo, hi, nm in GROUP_NAMES:
            if lo <= g[0] <= hi: label = nm; break
        out.append({"name": label, "mhz": g, "names": names})
    return out
