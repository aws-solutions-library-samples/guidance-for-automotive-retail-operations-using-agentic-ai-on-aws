#!/usr/bin/env python3
"""Validate SKILL.md files against the repository quality checklist.

Property numbers match QUALITY_CHECKLIST.md (and the awslabs/hcls-agent-skills conventions):

  P1   frontmatter has name, description, usage, version, tags; name == directory; version is semver;
       tags[0] == "skill"
  P12  file is <= 500 lines
  P13  a "## Response Format" or "## Response Structure" section exists
  P14  tags include category:reasoning or category:pipeline
  P15  description contains >= 12 quoted trigger keywords
  P16  reasoning: decision tree (├─/└─) OR >= 10 numbered steps; pipeline: >= 1 fenced code block
  P17  reasoning skills with decision trees: Response Format contains "do not narrate" or "internal reasoning"
  P18  (repo-specific) a "## Common Mistakes" section with >= 8 items
  P19  (repo-specific) a "## References" section exists

Usage:
  python3 tests/validate_skill.py                 # all skills
  python3 tests/validate_skill.py --skill NAME    # one skill
Exit code 1 if any skill fails.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
SKILLS_BUILD = ROOT / "skills-build"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    fm_text = text[4:end]
    body = text[end + 4:]
    fm: dict = {}
    key = None
    for line in fm_text.splitlines():
        m = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2)
            if val == ">" or val == "|":
                fm[key] = ""
            elif val.startswith("[") and val.endswith("]"):
                fm[key] = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
            else:
                fm[key] = val.strip().strip("'\"")
        elif key and line.startswith("  "):
            if isinstance(fm.get(key), str):
                fm[key] = (fm[key] + " " + line.strip()).strip()
            elif isinstance(fm.get(key), list) and line.strip().startswith("- "):
                fm[key].append(line.strip()[2:].strip())
    return fm, body


def validate(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    path = skill_dir / "SKILL.md"
    if not path.exists():
        return [f"{skill_dir.name}: SKILL.md missing"]
    text = path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    # P1
    for k in ("name", "description", "usage", "version", "tags"):
        if k not in fm or not fm[k]:
            errors.append(f"P1 missing frontmatter key: {k}")
    if fm.get("name") != skill_dir.name:
        errors.append(f"P1 name '{fm.get('name')}' != directory '{skill_dir.name}'")
    if not SEMVER.match(str(fm.get("version", ""))):
        errors.append(f"P1 version not semver: {fm.get('version')}")
    tags = fm.get("tags") or []
    if not tags or tags[0] != "skill":
        errors.append("P1 tags[0] must be 'skill'")

    # P12
    n_lines = text.count("\n") + 1
    if n_lines > 500:
        errors.append(f"P12 {n_lines} lines > 500")

    # P13
    has_rf = re.search(r"^## Response (Format|Structure)", body, re.M)
    if not has_rf:
        errors.append("P13 no '## Response Format' / '## Response Structure' section")

    # P14
    category = next((t.split(":", 1)[1] for t in tags if t.startswith("category:")), None)
    if category not in ("reasoning", "pipeline"):
        errors.append("P14 tags must include category:reasoning or category:pipeline")

    # P15
    triggers = re.findall(r'"([^"]+)"', str(fm.get("description", "")))
    if len(triggers) < 12:
        errors.append(f"P15 only {len(triggers)} quoted trigger keywords (< 12)")

    # P16
    has_tree = ("├─" in body) or ("└─" in body)
    steps = len(re.findall(r"^\d+\. ", body, re.M))
    code_blocks = len(re.findall(r"^\s*```", body, re.M)) // 2
    if category == "reasoning" and not (has_tree or steps >= 10):
        errors.append(f"P16 reasoning skill needs a decision tree or >= 10 numbered steps (has {steps})")
    if category == "pipeline" and code_blocks < 1:
        errors.append("P16 pipeline skill needs >= 1 fenced code block")

    # P17
    if category == "reasoning" and has_tree and has_rf:
        rf_start = has_rf.start()
        nxt = re.search(r"^## ", body[rf_start + 3:], re.M)
        rf_text = body[rf_start: rf_start + 3 + (nxt.start() if nxt else len(body))].lower()
        if "do not narrate" not in rf_text and "internal reasoning" not in rf_text:
            errors.append("P17 Response Format must say 'do not narrate' or 'internal reasoning'")

    # P18
    cm = re.search(r"^## Common Mistakes", body, re.M)
    if not cm:
        errors.append("P18 no '## Common Mistakes' section")
    else:
        nxt = re.search(r"^## ", body[cm.end():], re.M)
        cm_text = body[cm.end(): cm.end() + (nxt.start() if nxt else len(body))]
        items = len(re.findall(r"^\d+\. \*\*Wrong", cm_text, re.M))
        if items < 8:
            errors.append(f"P18 Common Mistakes has {items} Wrong/Right items (< 8)")

    # P19
    if not re.search(r"^## References", body, re.M):
        errors.append("P19 no '## References' section")

    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", help="validate a single skill directory name")
    args = ap.parse_args()
    roots = [d for d in (SKILLS, SKILLS_BUILD) if d.exists()]
    if args.skill:
        dirs = [r / args.skill for r in roots if (r / args.skill).exists()]
    else:
        dirs = sorted(p for r in roots for p in r.iterdir() if p.is_dir())
    failed = 0
    for d in dirs:
        errs = validate(d)
        status = "PASS" if not errs else "FAIL"
        n = (d / "SKILL.md").read_text().count("\n") + 1 if (d / "SKILL.md").exists() else 0
        print(f"{status}  {d.name}  ({n} lines)")
        for e in errs:
            print(f"      - {e}")
        failed += bool(errs)
    print(f"\n{len(dirs) - failed}/{len(dirs)} skills pass")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
