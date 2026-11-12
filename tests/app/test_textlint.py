"""Text lint (app/demo/textlint.py): the console's read routes, as every demo persona, carry no
raw code in a field a person reads — over the seed, so a new leak fails here before it reaches dev."""
from __future__ import annotations

from app.demo import textlint
from app.services.common import labels


def test_lint_rules_catch_the_reported_leaks():
    hits = {l.rule for l in textlint.lint_value("r", "t", "p", "A7:U-US-00007 → U-US-00002")}
    assert {"agent-user-composite", "user-id"} <= hits
    assert {l.rule for l in textlint.lint_value("r", "t", "p", "DEALER_LaborOps:LOP-BRAKE-F")} >= {"table-name", "labor-op-id"}
    assert {l.rule for l in textlint.lint_value("r", "t", "p", "objection:payment_too_high · A3")} == {"snake-case"}
    assert {l.rule for l in textlint.lint_value("r", "t", "p", "Utility rebate (generic template)")} == {"placeholder-marker"}
    assert {l.rule for l in textlint.lint_value("r", "t", "p", "cabin air filter, 12v battery")} == {"lowercase-acronym"}
    assert {l.rule for l in textlint.lint_value("r", "t", "p", "the front brake pads is due")} == {"plural-is"}
    assert {l.rule for l in textlint.lint_value("r", "t", "p", "the the vehicle")} == {"doubled-word"}
    assert textlint.lint_value("r", "t", "p", "Front brake pads are due around 40,000 mi; the 12V battery too.") == []
    assert textlint.lint_value("r", "t", "p", "Keep as certified pre-owned") == []


def test_words_for_codes():
    assert labels.part_phrase("Front brake pads") == "front brake pads"
    assert labels.part_phrase("12V battery") == "12V battery"
    assert labels.part_phrase("Tires (set)") == "tires"
    assert labels.is_are("Front brake pads") == "are" and labels.is_are("12V battery") == "is"
    assert labels.disposition_label("keep_cpo") == "Keep as certified pre-owned"
    assert labels.actor_label("A7:U-US-00007", {"U-US-00007": "Anthony Nguyen"}) == "Service Agent for Anthony Nguyen"
    assert labels.actor_label({"agent": "A3"}, {}) == "Sales Agent"
    assert labels.actor_label("U-US-99999", {}) == "a colleague"
    assert labels.evidence_label("lead source=service_lane") == "from the service lane"
    assert labels.evidence_label("objection:payment_too_high") == "objection — payment too high"
    assert labels.evidence_label("acquisition_recommended") == "Acquisition recommended"


def test_every_demo_screen_reads_clean_over_the_seed(fake_ddb):
    rep = textlint.check()
    assert not rep.errors, rep.errors
    assert rep.routes >= 40 and rep.fields > 500
    assert not rep.leaks, "\n".join(f"{l.route} {l.path} {l.rule}: {l.value!r}" for l in rep.leaks[:25])
