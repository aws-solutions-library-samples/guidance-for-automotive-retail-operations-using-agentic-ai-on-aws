#!/usr/bin/env python3
"""Generate inspection-evidence images for the A7 Service Desk demo (plan §D7).

Two backends, selected by --backend:
  placeholder  (default, zero-cost, always works): a labeled card drawn with Pillow.
  nova         : Amazon Nova Canvas (Bedrock text-to-image) generates a realistic
                 base image, then we overlay the burned-in label.

Every image — whichever backend — carries the D7 burned-in label:
  "<ITEM> · <MEASUREMENT> · VIN …<last6>"   plus an urgency color bar (green/yellow/red).
That label is the demo signal; the generated photo is only the backdrop.

Rules (plan §D7 / spec §3 A7 guardrails):
  - NEVER real vehicles, plates, faces, or people. Prompts describe close-up parts only.
  - Realistic set is generated for the ~15 golden-storyline findings; bulk uses placeholder.

Usage:
  python3 scripts/seed/generate/evidence_images.py --sample            # 3 sample images
  python3 scripts/seed/generate/evidence_images.py --golden            # ~15 golden findings
  python3 scripts/seed/generate/evidence_images.py --sample --backend nova
  python3 scripts/seed/generate/evidence_images.py --golden --backend nova --region us-east-1
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "sample-data" / "generated" / "evidence"

URGENCY_COLOR = {"green": (16, 185, 129), "yellow": (245, 158, 11), "red": (239, 68, 68)}

# The ~15 golden-storyline findings (plan §2.4 S1–S8). Each: id, item, measurement,
# urgency, vin_last6, and a photo prompt (close-up part only — no vehicle/plate/person).
GOLDEN = [
    ("S1-tire-rr",  "REAR TIRE",        "TREAD 4/32",       "yellow", "9F3C21",
     "extreme close-up of a worn car tire tread groove, tread depth gauge, garage lighting, no vehicle, no license plate, no people, product photography"),
    ("S1-wiper",    "WIPER BLADE",      "SPLIT/AGED",       "yellow", "9F3C21",
     "close-up of a cracked worn windshield wiper blade rubber edge, macro detail, workshop, no vehicle body, no plate, no people"),
    ("S2-brake-fl", "FRONT BRAKE PAD",  "2 MM",             "red",    "7A11B8",
     "extreme close-up of a heavily worn disc brake pad with thin friction material next to caliper and rotor, macro, workshop lighting, no vehicle, no plate, no people"),
    ("S5-strut-fl", "FRONT STRUT",      "LEAKING",          "red",    "4D77E2",
     "close-up of an oil-leaking automotive suspension strut assembly, wet fluid film on the shaft, undercar workshop, no vehicle body, no plate, no people"),
    ("S5-tire-fr",  "FRONT TIRE",       "TREAD 3/32",       "red",    "4D77E2",
     "macro close-up of a bald car tire showing wear bars, tread depth gauge, garage floor, no vehicle, no plate, no people"),
    ("S6-hv-batt",  "HV BATTERY SOH",   "94%",              "green",  "1C55A9",
     "close-up of an automotive diagnostic tablet screen showing an EV high-voltage battery state-of-health readout and bar graph, dark UI, no people, no plate"),
    ("S6-charge",   "CHARGE PORT",      "PIN OK",           "green",  "1C55A9",
     "close-up of an electric vehicle charging port connector pins, clean metal contacts, macro, no vehicle body, no plate, no people"),
    ("S1-coolant",  "COOLANT",          "OK LEVEL",         "green",  "9F3C21",
     "close-up of an engine coolant reservoir showing fluid level between min and max marks, engine bay, no plate, no people"),
    ("S2-rotor",    "FRONT ROTOR",      "SCORED",           "yellow", "7A11B8",
     "extreme close-up of a scored grooved brake rotor disc surface, macro metal detail, workshop, no vehicle, no plate, no people"),
    ("S5-tie-rod",  "TIE ROD END",      "PLAY",             "yellow", "4D77E2",
     "close-up of a worn automotive tie rod end joint, undercar inspection, no vehicle body, no plate, no people"),
    ("S8-windshld", "WINDSHIELD",       "CHIP",             "yellow", "3B0099",
     "close-up of a small stone chip crack in automotive windshield glass, macro, no plate, no people, no reflection of faces"),
    ("S8-tire-lr",  "LEFT REAR TIRE",   "TREAD 5/32",       "yellow", "3B0099",
     "macro close-up of a partially worn car tire tread, tread gauge, garage, no vehicle, no plate, no people"),
    ("S1-cabin",    "CABIN FILTER",     "DIRTY",            "yellow", "9F3C21",
     "close-up of a dirty pleated automotive cabin air filter next to a clean one, workshop bench, no people, no plate"),
    ("S6-coolant",  "EV COOLANT LOOP",  "OK",               "green",  "1C55A9",
     "close-up of an EV battery coolant loop hose fittings, clean engine bay, no plate, no people"),
    ("S2-fluid",    "BRAKE FLUID",      "LOW",              "red",    "7A11B8",
     "close-up of a brake fluid reservoir with the level below the minimum mark, engine bay, no plate, no people"),
]

SAMPLE_IDS = {"S1-tire-rr", "S2-brake-fl", "S6-hv-batt"}


def _label_overlay(img, item, measurement, urgency, vin6):
    """Burn the D7 label + urgency bar onto a PIL image (in place returns new)."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    bar_h = max(48, H // 12)
    # translucent banner at the bottom
    draw.rectangle([0, H - bar_h, W, H], fill=(10, 14, 26, 210))
    # urgency chip on the left
    color = URGENCY_COLOR.get(urgency, URGENCY_COLOR["yellow"])
    draw.rectangle([0, H - bar_h, max(10, W // 40), H], fill=color + (255,))
    text = f"{item}  \u00b7  {measurement}  \u00b7  VIN \u2026{vin6}"
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", max(20, bar_h // 2))
    except Exception:
        font = ImageFont.load_default()
    tx = W // 30
    ty = H - bar_h + (bar_h - (max(20, bar_h // 2))) // 2
    draw.text((tx, ty), text, fill=(241, 245, 249, 255), font=font)
    # small SYNTHETIC watermark, top-right
    try:
        wf = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 16)
    except Exception:
        wf = ImageFont.load_default()
    draw.text((W - 150, 10), "SYNTHETIC", fill=(148, 163, 184, 220), font=wf)
    return img


def _placeholder_base(item, urgency):
    from PIL import Image, ImageDraw
    W = H = 768
    bg = tuple(max(0, c - 120) for c in URGENCY_COLOR.get(urgency, (100, 116, 139)))
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 40, W - 40, H - 40], outline=(148, 163, 184), width=3)
    return img


def _control_sketch(item):
    """A rough part-shaped control image for Stability control-structure guidance."""
    from PIL import Image, ImageDraw
    W = H = 768
    img = Image.new("RGB", (W, H), (225, 225, 225))
    d = ImageDraw.Draw(img)
    key = item.upper()
    if "BRAKE PAD" in key:
        d.rectangle([120, 320, 648, 470], fill=(90, 90, 95))
        d.rectangle([120, 320, 648, 362], fill=(150, 120, 80))
        d.ellipse([480, 180, 780, 580], outline=(60, 60, 60), width=10)
    elif "TIRE" in key:
        d.ellipse([120, 120, 648, 648], outline=(40, 40, 40), width=60)
        for x in range(180, 620, 60):
            d.line([x, 180, x, 588], fill=(70, 70, 70), width=10)
    elif "BATTERY SOH" in key or "SOH" in key or "SCREEN" in key:
        d.rectangle([120, 160, 648, 600], fill=(20, 24, 40))
        for i, h in enumerate([180, 260, 320, 300, 240]):
            d.rectangle([180 + i * 90, 560 - h, 240 + i * 90, 560], fill=(0, 180, 210))
    elif "STRUT" in key:
        d.rectangle([340, 120, 430, 600], fill=(120, 120, 130))
        d.ellipse([300, 100, 470, 200], fill=(90, 90, 100))
    elif "FILTER" in key:
        for x in range(160, 620, 40):
            d.line([x, 200, x, 560], fill=(120, 100, 60), width=18)
    elif "WINDSHIELD" in key or "GLASS" in key:
        d.line([300, 300, 460, 460], fill=(120, 120, 130), width=6)
        d.line([380, 280, 360, 500], fill=(120, 120, 130), width=4)
    else:  # generic part
        d.ellipse([220, 220, 548, 548], fill=(110, 110, 120))
    return img


def _stability_base(item, prompt, region):
    """Stability control-structure via inference profile; returns a PIL image."""
    import base64 as _b64
    import boto3
    from PIL import Image
    client = boto3.client("bedrock-runtime", region_name=region)
    ctrl = _control_sketch(item)
    buf = io.BytesIO(); ctrl.save(buf, format="PNG")
    body = {
        "prompt": prompt,
        "image": _b64.b64encode(buf.getvalue()).decode(),
        "output_format": "png",
        "control_strength": 0.6,
        "negative_prompt": "vehicle body, license plate, number plate, person, face, hands, text, watermark, logo",
    }
    resp = client.invoke_model(
        modelId="us.stability.stable-image-control-structure-v1:0", body=json.dumps(body)
    )
    payload = json.loads(resp["body"].read())
    return Image.open(io.BytesIO(_b64.b64decode(payload["images"][0]))).convert("RGB")


def _nova_base(item, measurement, urgency, prompt, region):
    """Call Amazon Nova Canvas (Bedrock) text-to-image; return a PIL image."""
    import boto3
    from PIL import Image
    client = boto3.client("bedrock-runtime", region_name=region)
    body = {
        "taskType": "TEXT_IMAGE",
        "textToImageParams": {
            "text": prompt,
            "negativeText": "vehicle body, license plate, number plate, human, person, face, hands, text, watermark, logo",
        },
        "imageGenerationConfig": {
            "numberOfImages": 1, "height": 768, "width": 768, "cfgScale": 7.0, "seed": 42,
        },
    }
    resp = client.invoke_model(modelId="amazon.nova-canvas-v1:0", body=json.dumps(body))
    payload = json.loads(resp["body"].read())
    b64 = payload["images"][0]
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def generate(which, backend, region):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    items = [g for g in GOLDEN if (which == "golden" or g[0] in SAMPLE_IDS)]
    manifest = []
    for fid, item, meas, urg, vin6, prompt in items:
        if backend in ("nova", "stability"):
            try:
                base = (_stability_base(item, prompt, region) if backend == "stability"
                        else _nova_base(item, meas, urg, prompt, region))
                src = backend
            except Exception as e:
                print(f"  WARN {backend} failed for {fid} ({str(e)[:120]}); falling back to placeholder", file=sys.stderr)
                base = _placeholder_base(item, urg); src = "placeholder-fallback"
        else:
            base = _placeholder_base(item, urg); src = "placeholder"
        img = _label_overlay(base, item, meas, urg, vin6)
        out = OUT_DIR / f"{fid}.png"
        img.save(out)
        manifest.append({"id": fid, "file": out.name, "item": item, "measurement": meas,
                         "urgency": urg, "vin_last6": vin6, "backend": src})
        print(f"  wrote {out.relative_to(ROOT)}  [{src}]")
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\n{len(manifest)} image(s) -> {OUT_DIR.relative_to(ROOT)} (+ manifest.json)")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample", action="store_true", help="3 sample findings")
    g.add_argument("--golden", action="store_true", help="all ~15 golden findings")
    ap.add_argument("--backend", choices=["placeholder", "nova", "stability"], default="placeholder")
    ap.add_argument("--region", default="us-east-1")
    a = ap.parse_args()
    generate("sample" if a.sample else "golden", a.backend, a.region)


if __name__ == "__main__":
    main()
