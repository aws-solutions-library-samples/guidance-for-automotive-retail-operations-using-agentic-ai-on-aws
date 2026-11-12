"""Text lint — no raw code reaches a person.

Six rehearsal rounds reported the same class of gap under different names: `A7:U-US-00007→U-US-00002`
in an activity feed, `objection:payment_too_high · A3`, `DEALER_LaborOps:LOP-BRAKE-F` on a repair-order
line, `MER-VOLT-C` as a vehicle, "Keep Cpo", "(synthetic)" in an incentive name, "12v battery",
"the front brake pads is". Each was fixed where it was seen, and the next screen leaked the next
code, because nothing checked the class: a field a person reads must be prose.

This walks the read routes behind every demo screen, as every demo persona, and checks the fields
the console renders as text (names, titles, summaries, bodies, reasons, notes…) against the
patterns that mean "a machine value escaped": table names, agent/user composites, ids, snake_case
tokens, lowercase acronyms, placeholder markers, doubled words, template braces. Identifier fields
(`*_id`, `*_ref`, `vin`, `status`, `stage`, `kind`, `type`, `action`, `source`…) are exempt — the
console maps those through label tables — so a hit is a real leak, not a false alarm on a key.

    python -m app.demo.textlint            # exit 1 on any leak
    python -m app.demo.textlint --json

Runs the API handler in-process against the stack (like readiness), read-only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any

# field names the console shows as text to a person
PROSE_KEYS = frozenset({
    "name", "title", "summary", "text", "body", "reason", "reasons", "detail", "details", "note", "notes", "label",
    "opening", "message", "headline", "description", "insight", "insights", "narrative", "question", "questions",
    "display", "vehicle_name", "vehicle", "customer_name", "model_name", "trim_name", "dealer_name", "part", "parts",
    "comments", "comment", "evidence", "why", "recommendation_label", "action_label", "to_name", "actor_name", "from_name",
    "subject", "preview", "greeting", "closing", "explanation", "rationale", "next_step", "next_steps", "tip",
    "what_to_say", "talk_track", "script", "basis", "type_label", "lender", "item", "service", "services",
    "intro", "caption", "hint", "placeholder_text", "empty", "status_text", "display_name", "vehicle_label",
})
# identifier-ish keys are never prose (the console maps them through label tables)
ID_KEY = re.compile(r"(^|_)(id|ids|ref|refs|key|code|codes|vin|vins|token|url|path|arn|sk|pk|hash|sha256)$"
                    r"|^(status|stage|kind|type|action|source|role|roles|market_id|currency|severity|channel|"
                    r"disposition|recommendation|decision|powertrain|tier|unit|lane|audience|scope|flag|flags|labor_op|labor_op_id|part_number|"
                    r"template_id|event_type|tool|agent|agent_id|actor|from_user|to_user|user_id|customer_id)$")

# what a leaked machine value looks like, with the gap round that first reported it
LEAKS: list[tuple[str, re.Pattern[str], str]] = [
    ("table-name", re.compile(r"\b(OEM|DEALER|CUST|PLAT)_[A-Z][A-Za-z]+"), "r6.6 DEALER_LaborOps:LOP-BRAKE-F"),
    ("agent-user-composite", re.compile(r"\bA[1-7]:[A-Z]"), "r6.6 A7:U-US-00007"),
    ("user-id", re.compile(r"\bU-(US|DE)-\d{5}\b|\bU-ADMIN-\w+"), "r6.6 → U-US-00002"),
    ("record-id", re.compile(r"\b(SJ|RO|LD|DL|CFG|REC|APR|EST|PO|VAL|INV|AP|TD|HO|TASK|LEAD|INS|DLV|CMP|ACC|BRIEF|TK|NTF|PR|FO|ALT)-[A-Z]{2}-[A-Z0-9-]{3,}\b|"
                             r"\bGR-\d{3}\b|\bINS-[A-Z0-9-]{4,}\b"), "r7 INS-… ids, GR-001"),
    ("iso-timestamp", re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"), "r7 'Prepared 2026-11-09T03:57Z'"),
    ("agent-tool-ref", re.compile(r"\bA[1-7]\.[a-z_]+\b"), "r7 'A7.build_estimate (…)' on a customer page"),
    ("developer-words", re.compile(r"(^|\s)/[a-z]+/[a-z-]+\b|\bAPI\b|\bplaceholder\b|\bconnector\b|\bread model|\bendpoint\b|"
                                   r"\bBedrock\b|\bQuickSight\b|\bdeterministic\b|\bfallback\b"), "r7 '/oem/ask API', 'embed placeholder'"),
    ("half-humanized-id", re.compile(r"\b(Mer|Nor|Kes|Vrd) [a-z]+ [a-z](?: t\d)?\b"), "r7 'Mer volt c t1'"),
    ("labor-op-id", re.compile(r"\bLOP-[A-Z][A-Z-]+\b"), "r6.6 LOP-BRAKE-F"),
    ("model-id", re.compile(r"\b(MER|NOR|KES|VRD)-[A-Z]{3,}-[A-Z0-9]+\b"), "r6.6 MER-VOLT-C"),
    ("snake-case", re.compile(r"(?<![\w/.-])[a-z]+(?:_[a-z0-9]+)+(?![\w/.-])"), "r6.6 payment_too_high, keep_cpo"),
    ("lowercase-acronym", re.compile(r"\b(12v|ev|dtc|mpi|apr|vin|ro|dms|crm|oem)\b(?![-.])"), "r6.6 12v battery"),
    ("placeholder-marker", re.compile(r"\((synthetic|generic template|placeholder|stub|fake|todo|sandbox)\)|"
                                      r"\b(sandbox|synthetic|lorem|TODO|FIXME|dummy)\b|\[object Object\]|\bundefined\b|"
                                      r"\bNaN\b|\bnull\b|\bNone\b"), "r6.6 (synthetic), sandbox valuation connector"),
    ("template-brace", re.compile(r"\{[a-z_]+\}"), "an unfilled template"),
    ("doubled-word", re.compile(r"\b(the|a|an|to|of|is|and) \1\b", re.IGNORECASE), "r5 'the the vehicle'"),
    ("plural-is", re.compile(r"\b(pads|tires|filters|brakes|wipers|rotors) (is|was|has)\b", re.IGNORECASE), "r6 'the front brake pads is'"),
]

# the read routes behind the demo screens, per persona token — the console's own calls
ROUTES: list[tuple[str, str]] = [
    # dealer floor
    ("user:U-US-00002", "/journeys/board"), ("user:U-US-00002", "/dealer/leads"), ("user:U-US-00002", "/leads/queue"),
    ("user:U-US-00005", "/dealer/leads"), ("user:U-US-00040", "/dealer/delivery"), ("user:U-US-00040", "/dealer/cockpit"),
    ("user:U-US-00040", "/dealer/cockpit/funnel"), ("user:U-US-00040", "/dealer/cockpit/sales"),
    ("user:U-US-00044", "/inventory"), ("user:U-US-00044", "/trades/dispositions"), ("user:U-US-00044", "/inventory/acquisitions"),
    ("user:U-US-00044", "/trade-in/prospects"), ("user:U-US-00006", "/fni/queue"),
    # service
    ("user:U-US-00007", "/service/board"), ("user:U-US-00007", "/service/attention"), ("user:U-US-00007", "/service/arrivals"),
    ("user:U-US-00007", "/service/lane"), ("user:U-US-00007", "/service/predictive"), ("user:U-US-00056", "/service/cockpit"),
    ("user:U-US-00055", "/service/parts/desk"), ("user:U-US-00007", "/service/declined"),
    # oem
    ("user:U-US-00044", "/oem/today"), ("user:U-US-00044", "/oem/alerts"), ("user:U-US-00044", "/oem/accounts"),
    ("user:U-US-00049", "/marketing/campaigns"), ("user:U-US-00049", "/oem/campaigns"), ("user:U-US-00050", "/config/nurture"),
    ("user:U-US-00048", "/approvals"),
    # customer
    ("cust:C-US-J1", "/garage/ownership"), ("cust:C-US-J1", "/garage/builds"), ("cust:C-US-J1", "/garage/finance"),
    ("cust:C-US-J1", "/garage/service"), ("cust:C-US-J1", "/garage/appointments"), ("cust:C-US-J1", "/garage/journey"),
    ("cust:C-US-J1", "/me/home"),
    # admin
    ("user:U-US-00051", "/admin/overview"), ("user:U-US-00051", "/admin/agents/health"), ("user:U-US-00051", "/admin/audit"),
]
# per-persona dynamic routes: the first N items of a list route feed their detail routes
DETAIL: list[tuple[str, str, str, str]] = [
    # (token, list route, json path to ids, detail route with {id})
    ("user:U-US-00002", "/journeys/board", "lanes.*.*.journey_id", "/journeys/{id}/activity"),
    ("user:U-US-00002", "/journeys/board", "lanes.*.*.journey_id", "/journeys/{id}/assist"),
    ("user:U-US-00007", "/service/board", "cards.*.service_journey_id", "/service/{id}/state"),
    ("user:U-US-00002", "/dealer/leads", "leads.*.lead_id", "/leads/{id}/script"),
    ("user:U-US-00005", "/dealer/leads", "leads.*.lead_id", "/leads/{id}/script"),
]
_DETAIL_PER_LIST = 4


@dataclass
class Leak:
    route: str
    token: str
    path: str          # json path to the field
    rule: str
    value: str
    gap: str


@dataclass
class Report:
    routes: int = 0
    fields: int = 0
    leaks: list[Leak] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _claims_for(token: str) -> dict[str, str]:
    """The authorizer context for a development token — the deployed authorizer's own resolution."""
    from app.services.api import authorizer
    return {k: str(v) for k, v in authorizer.resolve_principal(f"Bearer {token}").items()}


def _call(token: str, path: str) -> tuple[int, Any]:
    from app.services.api import handler
    p, _, qs = path.partition("?")
    ev = {"httpMethod": "GET", "path": p, "headers": {},
          "queryStringParameters": dict(x.split("=", 1) for x in qs.split("&")) if qs else None,
          "requestContext": {"authorizer": _claims_for(token)}}
    res = handler.lambda_handler(ev, None)
    try:
        body = json.loads(res.get("body") or "null")
    except ValueError:
        body = res.get("body")
    return int(res.get("statusCode") or 0), body


# a message template is edited by CX with its placeholders showing — braces and snake_case are the point there
TEMPLATE_KEYS = frozenset({"templates", "template", "template_body", "template_subject"})


def _walk(value: Any, path: str, key: str | None, out: list[tuple[str, str]], in_template: bool = False) -> None:
    """(json path, string) for every prose field value."""
    if isinstance(value, dict):
        for k, v in value.items():
            _walk(v, f"{path}.{k}" if path else k, k, out, in_template or k in TEMPLATE_KEYS)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _walk(v, f"{path}[{i}]", key, out, in_template)
    elif isinstance(value, str) and key and key in PROSE_KEYS and not ID_KEY.search(key) and not in_template:
        out.append((path, value))


def _ids_at(body: Any, spec: str) -> list[str]:
    parts = spec.split(".")

    def go(v: Any, i: int) -> list[str]:
        if i == len(parts):
            return [str(v)] if isinstance(v, str) else []
        p = parts[i]
        if p == "*":
            items = v.values() if isinstance(v, dict) else (v if isinstance(v, list) else [])
            return [x for it in items for x in go(it, i + 1)]
        return go(v.get(p), i + 1) if isinstance(v, dict) and v.get(p) is not None else []
    seen: list[str] = []
    for x in go(body, 0):
        if x not in seen:
            seen.append(x)
    return seen


def lint_value(route: str, token: str, path: str, value: str) -> list[Leak]:
    out = []
    for rule, pat, gap in LEAKS:
        m = pat.search(value)
        if m:
            out.append(Leak(route, token, path, rule, value[:160], gap))
    return out


def check(routes: list[tuple[str, str]] | None = None) -> Report:
    from app.services.common import ddb
    rep = Report()
    plan = list(routes or ROUTES)
    with ddb.read_memo():
        if routes is None:
            for token, lst, spec, detail in DETAIL:
                code, body = _call(token, lst)
                if code == 200:
                    for i in _ids_at(body, spec)[:_DETAIL_PER_LIST]:
                        plan.append((token, detail.replace("{id}", i)))
        for token, route in plan:
            try:
                code, body = _call(token, route)
            except Exception as e:  # noqa: BLE001 - a route that cannot be read is reported, never skipped silently
                rep.errors.append(f"{route} as {token}: {type(e).__name__}: {e}")
                continue
            if code != 200:
                rep.errors.append(f"{route} as {token}: HTTP {code}")
                continue
            rep.routes += 1
            fields: list[tuple[str, str]] = []
            _walk(body, "", None, fields)
            rep.fields += len(fields)
            for path, value in fields:
                rep.leaks += lint_value(route, token, path, value)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max", type=int, default=40, help="leaks to print")
    args = ap.parse_args(argv)
    rep = check()
    if args.json:
        print(json.dumps({"routes": rep.routes, "fields": rep.fields, "errors": rep.errors,
                          "leaks": [l.__dict__ for l in rep.leaks]}, indent=1))
    else:
        print("=== TEXT LINT: no raw code reaches a person ===")
        by_rule: dict[str, int] = {}
        for l in rep.leaks:
            by_rule[l.rule] = by_rule.get(l.rule, 0) + 1
        for l in rep.leaks[: args.max]:
            print(f"  LEAK {l.rule:20s} {l.route:40s} {l.path:50s} {l.value[:90]!r}")
        if len(rep.leaks) > args.max:
            print(f"  … {len(rep.leaks) - args.max} more")
        for e in rep.errors:
            print(f"  ERROR {e}")
        print(f"=== TEXT LINT RESULT: {'CLEAN' if not rep.leaks and not rep.errors else f'{len(rep.leaks)} LEAKS'} "
              f"({rep.routes} routes, {rep.fields} prose fields" + (f"; by rule {by_rule}" if by_rule else "") + ") ===")
    return 1 if rep.leaks or rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
