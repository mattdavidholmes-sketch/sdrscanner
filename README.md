# SDR Scanner

A radio scanner for one RTL-SDR dongle on a Mac, with a web dashboard that works on a phone, an iPad and a desktop browser at the same time.

It does three jobs with the same dongle:

- **Sweeps** VHF/UHF/700/800 MHz in the background, ranks what it hears, and keeps a bank of active channels with names from the ISED licence register.
- **Listens** to analog channels (NFM, AM airband, WFM) with squelch, tone detection, noise suppression, recording and speech-to-text.
- **Decodes digital** systems (P25 Phase 1/2, DMR, NXDN) by driving [SDRTrunk](https://github.com/DSheirer/sdrtrunk) and streaming its audio to the browser through a local Icecast.

The channel tables are built around Toronto (City of Toronto P25, Metrolinx, Pearson and Billy Bishop airband, GTA amateur repeaters). Everything else is general.

## Hardware

- A Mac (developed on an Apple Silicon Mac mini)
- One RTL-SDR dongle (developed with a NooElec NESDR SMArt v5) and an antenna

One dongle sees about 2.4 MHz at a time, so it does one thing at a time: sweep, or one analog window, or one digital system. A second dongle or a wider receiver removes that limit.

## Install

```
_launchers/setup.command                      # Homebrew packages: rtl-sdr, sox, python deps
_launchers/Install SDRTrunk.command           # digital decoding (optional)
_launchers/Install SDRTrunk streaming (Icecast).command
_launchers/Install transcription.command      # speech-to-text (optional)
```

Then double-click `Dashboard.command` and open `http://localhost:8765`. The phone page is `http://<the Mac's address>:8765/m`.

Set your own location (used for aircraft distance, licence lookups and the snow watch) by creating `home_location.json` next to the scripts:

```
{"lat": 43.6532, "lon": -79.3832}
```

## Layout

| File | What it is |
| --- | --- |
| `dashboard2.py` | Web server and the single-page dashboard (HTML/CSS/JS inline) |
| `dashboard.py` | Back end: player, squelch, recorder, dongle hand-off, monitor banks |
| `audio.py` | rtl_fm to sox audio chain, squelch, tone decode, noise suppression |
| `channelizer.py` | Many analog channels at once from one 2.4 MHz window |
| `fastscan.py`, `sdr_scan.py` | Background sweep and channel ranking |
| `sdrtrunk_playlist.py` | Builds the SDRTrunk playlist (sites, talkgroup names, stream) |
| `mobile_page_v7.html` | The phone page served at `/m` |
| `_launchers/` | Double-click installers and scan presets |
| `_helpers/` | Maintenance scripts |

Runtime data (channel bank, history, recordings, logs, scan folders) stays local and is not part of the repository.

## Notes

- The dashboard listens on the local network only. `_launchers/Set up remote access (Cloudflare).command` sets up a password-protected tunnel if you want it reachable from outside.
- Encrypted talkgroups (police, most of the "other" traffic on the Toronto system) cannot be decoded. They show as silent calls.
- Listening rules differ by country. In Canada, receiving is generally permitted; divulging or using what you hear is restricted under the Radiocommunication Act. Check the rules where you live.
- `licences_gta.csv` is an extract of ISED Canada's public spectrum licence data.
