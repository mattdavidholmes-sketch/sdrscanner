#!/usr/bin/env python3
"""Is the page's markup balanced? python3 htmlcheck.py dashboard2.py [monitor_panel.html mobile_page_v7.html]
Every tab section must close what it opens; one stray closing tag moves whole tabs out of the main column."""
import ast, sys
from html.parser import HTMLParser
VOID = {"input", "br", "img", "hr", "meta", "link", "source", "col", "wbr", "area", "base", "embed", "param", "track"}
class P(HTMLParser):
    def __init__(s): super().__init__(convert_charrefs=True); s.stack = []; s.bad = []
    def handle_starttag(s, tag, attrs):
        if tag not in VOID: s.stack.append((tag, dict(attrs).get("id", ""), s.getpos()[0]))
    def handle_endtag(s, tag):
        if tag in VOID or tag == "html": return
        if s.stack and s.stack[-1][0] == tag: s.stack.pop(); return
        s.bad.append(f"line {s.getpos()[0]}: </{tag}> does not match (open: {[t[0] + ('#' + t[1] if t[1] else '') for t in s.stack[-3:]]})")
        for i in range(len(s.stack) - 1, -1, -1):
            if s.stack[i][0] == tag: del s.stack[i:]; break
def page_of(path):
    src = open(path, encoding="utf-8").read()
    if not path.endswith(".py"): return src
    for n in ast.parse(src).body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "APP": return ast.literal_eval(n.value)
    raise SystemExit("no APP string in " + path)
rc = 0
for path in sys.argv[1:]:
    html = page_of(path); a = html.find("<body"); body = html[a:] if a >= 0 else html
    import re
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S); body = re.sub(r"<style\b.*?</style>", "", body, flags=re.S)
    p = P(); p.feed(body)
    left = [t for t in p.stack if t[0] not in ("body", "html")]
    ok = not p.bad and not left
    print(("OK  " if ok else "BAD ") + path + ("" if ok else "  " + "; ".join(p.bad[:4]) + (f"; left open: {left[:4]}" if left else "")))
    rc |= not ok
sys.exit(rc)
