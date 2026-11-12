"""Screen text lint — the rendered console, as every demo persona, carries no raw code.

app/demo/textlint.py checks what the API sends in prose fields; this checks what a person SEES,
because the console composes some text itself (a filter option built from an id, a template that
prints `model_id` when `model_name` is missing). It signs in with the development tokens, opens the
home screen of every demo persona plus the screens the rehearsals use, reads the page text, and
fails on the same leak patterns.

    .venv/bin/python scripts/demo/screen_textlint.py                 # the deployed console
    .venv/bin/python scripts/demo/screen_textlint.py --url http://localhost:3100

Needs Playwright with Chrome (the rehearsal tooling's prerequisite). Read-only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = json.loads((ROOT / "app" / "infra" / "console-config.json").read_text()).get("url", "").rstrip("/") \
    if (ROOT / "app" / "infra" / "console-config.json").exists() else ""

LEAK = re.compile(
    r"\bU-(US|DE)-\d{5}\b|\bU-ADMIN-\w+|\bA[1-7]:U-|\b(OEM|DEALER|CUST|PLAT)_[A-Z][A-Za-z]+|=service_lane|"
    r"\b[a-z]+(?:_[a-z0-9]+)+\b(?!\.json)|\(synthetic\)|\(generic template\)|\b12v\b|\b(MER|NOR)-[A-Z]{3,}-[A-Z0-9-]+\b|"
    r"\bLOP-[A-Z][A-Z-]+\b|\[object Object\]|\bundefined\b|\bNaN\b|\bthe the\b|\{[a-z_]+\}|"
    r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}|\bA[1-7]\.[a-z_]+\b|\b(INS|DOC|PO|DLV|CMP|ACC|BRIEF|VAL|REC)-[A-Z]{2}-[A-Z0-9-]{3,}\b|\bGR-\d{3}\b|"
    r"(^|\s)/[a-z]+/[a-z-]+\b|\bAPI\b|\bplaceholder\b|\bconnector\b|\bsandbox\b|\bQuickSight\b|\bBedrock\b|"
    r"\b(Mer|Nor|Kes|Vrd) [a-z]+ [a-z](?: t\d)?\b")
# words with underscores that are legitimately on screen (environment names, flags the admin edits)
ALLOWED = re.compile(r"^(ENABLE_[A-Z_]+|RETAIL_OPS_[A-Z_]+|retail-ops|app_bus|[a-z]+_[a-z]+@)$")

SCREENS: list[tuple[str, str]] = [
    ("user:U-US-00002", "/dealer/journeys"), ("user:U-US-00002", "/dealer/advisor/J-US-J5"), ("user:U-US-00005", "/dealer/leads"),
    ("user:U-US-00006", "/dealer/fni"), ("user:U-US-00009", "/dealer/delivery"), ("user:U-US-00001", "/dealer/cockpit"),
    ("user:U-US-00001", "/dealer/cockpit?tab=sales"), ("user:U-US-00044", "/dealer/inventory"),
    ("user:U-US-00044", "/dealer/inventory?tab=trades"), ("user:U-US-00007", "/dealer/service"),
    ("user:U-US-00007", "/dealer/assistant/service"), ("user:U-US-00056", "/dealer/service/cockpit"), ("user:U-US-00055", "/dealer/service/parts"),
    ("user:U-US-00044", "/oem"), ("user:U-US-00044", "/oem/alerts"), ("user:U-US-00049", "/marketing/campaigns"),
    ("user:U-US-00050", "/marketing/journey-ops"), ("user:U-US-00048", "/approvals"), ("user:U-US-00051", "/admin"),
    ("cust:C-US-J1", "/garage"), ("cust:C-US-J5", "/garage"), ("cust:C-US-J1", "/garage/service"),
    ("user:U-US-00007", "/dealer/service/SJ-US-00001"), ("user:U-US-00007", "/dealer/service/SJ-US-00001/checkin"),
    ("user:U-US-00007", "/dealer/service/SJ-US-00001/estimate"), ("cust:C-US-J1", "/garage/service/SJ-US-00001"),
    ("user:U-US-00044", "/oem/accounts/ACC-US-00001"), ("user:U-US-00002", "/dealer/discovery"),
    ("user:U-US-00002", "/dealer/trade-in"), ("user:U-US-00056", "/dealer/service/cockpit"),
    ("user:U-US-00049", "/marketing/campaigns?tab=roi"), ("user:U-US-00044", "/oem/scenario"),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--settle", type=float, default=8.0, help="seconds to let a screen load")
    args = ap.parse_args(argv)
    if not args.url:
        print("no console URL (deploy the console or pass --url)")
        return 2
    from playwright.sync_api import sync_playwright
    leaks: list[tuple[str, str, str]] = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        print(f"=== SCREEN TEXT LINT: {args.url} ===")
        for token, path in SCREENS:
            pg = b.new_context(viewport={"width": 1440, "height": 1100}).new_page()
            pg.set_default_timeout(45000)
            pg.goto(args.url + "/login")
            pg.evaluate("(t)=>{localStorage.setItem('retail-ops.devToken',t);localStorage.removeItem('retail-ops.actAs');}", token)
            pg.goto(args.url + path)
            time.sleep(args.settle)
            try:
                text = pg.locator("main").inner_text(timeout=10000)
            except Exception:  # noqa: BLE001 - a screen that never rendered is reported as such
                leaks.append((path, token, "<no main content rendered>"))
                pg.context.close()
                continue
            hits = sorted({m.group(0) for m in LEAK.finditer(text) if not ALLOWED.match(m.group(0))})
            for h in hits:
                leaks.append((path, token, h))
            print(f"  {'LEAK' if hits else 'ok  '} {path:36s} as {token:18s} {', '.join(hits[:6])}")
            pg.context.close()
        b.close()
    print(f"=== SCREEN TEXT LINT RESULT: {'CLEAN' if not leaks else f'{len(leaks)} LEAKS'} ({len(SCREENS)} screens) ===")
    return 1 if leaks else 0


if __name__ == "__main__":
    sys.exit(main())
