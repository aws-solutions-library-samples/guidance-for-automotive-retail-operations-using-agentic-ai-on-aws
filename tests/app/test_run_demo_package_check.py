# ruff: noqa: S101
"""run_demo step 2 (design §10.7): the Sales Package check tolerates a cold
Journey Agent runtime (warm-up, 202 poll on generated_at >= requested_at) and
still fails on a specialist error card. Fake client and fake clock, no sleep."""
from __future__ import annotations

import pytest

from app.demo import run_demo

JID = "J-DEMO-SALE"
POST = ("POST", f"/journeys/{JID}/package")
GET = ("GET", f"/journeys/{JID}/package")


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


class _Client:
    """Answers each (method, path) from a script; the last answer repeats."""

    def __init__(self, script: dict[tuple[str, str], list[tuple[int, dict]]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        self.calls.append((method, path))
        answers = self.script[(method, path)]
        return answers.pop(0) if len(answers) > 1 else answers[0]


def _pkg(**status: bool) -> dict:
    agents = {a: ({"ok": 1} if fine else {"error": f"the {a} card failed"}) for a, fine in status.items()}
    return {"agent_status": status, "agents": agents, "summary": "QUALIFY journey"}


def _building(requested_at: str) -> dict:
    return {"status": "building", "journey_id": JID, "poll": f"/journeys/{JID}/package",
            "requested_at": requested_at, "correlation_id": "corr-1"}


def _check(client: _Client, clock: _Clock) -> bool:
    return run_demo._package_check(client, JID, sleep=clock.sleep, now=clock.now)


def test_200_with_every_agent_ok_passes_and_the_warm_up_is_not_asserted(capsys):
    # the warm-up answers 500; only the second (asserted) build counts
    client = _Client({POST: [(500, {"error": "cold"}), (200, _pkg(A1=True, A3=True, A4=True))]})
    clock = _Clock()
    assert _check(client, clock) is True
    assert client.calls == [POST, POST]                     # one warm-up, one asserted build
    assert clock.sleeps == [1]
    assert "[PASS] package built, all agents ok (200)" in capsys.readouterr().out


def test_200_with_an_error_card_fails_and_prints_the_card(capsys):
    client = _Client({POST: [(200, _pkg(A1=True, A3=True, A4=True)), (200, _pkg(A1=True, A3=True, A4=False))]})
    assert _check(client, _Clock()) is False
    out = capsys.readouterr().out
    assert "[FAIL] package built, all agents ok (200)" in out
    assert "A4: the A4 card failed" in out and "A1:" not in out


def test_202_then_a_fresh_header_passes_with_the_async_note(capsys):
    client = _Client({
        POST: [(202, _building("2026-10-20T10:00:00Z")), (202, _building("2026-10-20T10:00:05Z"))],
        GET: [(200, {"generated_at": "2026-10-20T10:00:01Z"}),          # warm-up's header
              (200, {"generated_at": "2026-10-20T10:00:01Z"}),          # still the old one
              (200, {"generated_at": "2026-10-20T10:00:05Z", "summary": "fresh"})],
    })
    clock = _Clock()
    assert _check(client, clock) is True
    out = capsys.readouterr().out
    assert "built asynchronously; specialist status is not in the header" in out
    assert "summary: fresh" in out
    assert client.calls.count(POST) == 2 and client.calls.count(GET) == 3
    assert clock.sleeps == [1, 2]                           # warm-up pause, then one 2 s poll


def test_202_with_only_older_headers_fails_at_the_30_s_deadline(capsys):
    client = _Client({
        POST: [(200, _pkg(A1=True, A3=True, A4=True)), (202, _building("2026-10-20T10:00:05Z"))],
        GET: [(200, {"generated_at": "2026-10-20T10:00:04Z"})],
    })
    clock = _Clock()
    assert _check(client, clock) is False
    assert clock.t - 1 == pytest.approx(30)                 # polled every 2 s up to the deadline
    assert set(clock.sleeps[1:]) == {2}
    assert "[FAIL] package built (202" in capsys.readouterr().out


def test_any_other_status_fails_and_prints_the_body(capsys):
    client = _Client({POST: [(200, _pkg(A1=True, A3=True, A4=True)),
                             (500, {"error": "the Journey Agent could not build the package", "agent": "A2"})]})
    assert _check(client, _Clock()) is False
    out = capsys.readouterr().out
    assert "[FAIL] package built (500)" in out and "the Journey Agent could not build the package" in out


def test_a_202_warm_up_is_polled_and_its_outcome_ignored():
    # the warm-up never gets a fresh header (deadline passes) but the asserted build is a clean 200
    client = _Client({POST: [(202, _building("2026-10-20T10:00:09Z")), (200, _pkg(A1=True, A3=True, A4=True))],
                      GET: [(404, {"error": "no package yet; POST to build one"})]})
    clock = _Clock()
    assert _check(client, clock) is True
    assert client.calls[0] == POST and client.calls[-1] == POST and GET in client.calls
