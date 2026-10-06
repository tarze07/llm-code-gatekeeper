"""Przypadki z przeglądu: pełna ścieżka import → zapis → widok i eksport."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import Panel, load_sample

from gatekeeper_web.services.deserialize import run_result_from_payload
from gatekeeper_web.services.reports import parse_report
from gatekeeper_web.services.view import build_run_view
from gatekeeper_web.templating import _fmt_datetime


@pytest.mark.parametrize(("path", "value"), [
    (("gates", 0, "duration_s"), "wrong"),
    (("gates", 0, "duration_s"), True),
    (("gates", 0, "duration_s"), -1),
    (("gates", 0, "facts"), 3),
    (("gates", 0, "facts"), []),
    (("gates", 0, "findings"), {}),
    (("gates", 0, "warn_only"), "false"),
    (("gates", 0, "status"), []),
    (("gates", 0, "message"), {}),
    (("decision", "verdict"), []),
    (("decision", "reasons"), 1),
    (("decision", "warnings"), "text"),
    (("not_checked",), "text"),
    (("repo",), {}),
    (("started_at",), "wrong"),
    (("duration_s",), float("nan")),
    (("duration_s",), float("inf")),
    (("duration_s",), True),
    (("policy_version",), 2**64),
    (("run_id",), "broken/id"),
    (("gates", 0, "findings", 0, "evidence"), 1),
    (("gates", 0, "findings", 0, "file"), []),
    (("gates", 0, "findings", 0, "severity"), {}),
    (("gates", 0, "findings", 0, "confidence"), 2),
    (("gates", 0, "findings", 0, "fingerprint"), []),
    (("decision", "reasons", 0, "source"), []),
    (("decision", "reasons", 0, "gate"), []),
    (("decision", "reasons", 0, "fingerprints"), 3),
])
def test_zly_raport_jest_odrzucony_przed_zapisem(
    panel: Panel, path: tuple[str | int, ...], value: Any,
) -> None:
    data = load_sample("demo-celowe-usterki.json")
    target: Any = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    project = panel.create_project()

    response = panel.import_bytes(json.dumps(data).encode(), project)

    assert response.status_code == 422, response.text
    assert panel.get(f"/api/v1/projects/{project}/runs").json()["total"] == 0
    # Odrzucony wpis nie zajmuje run_id i nie powoduje późniejszego konfliktu.
    accepted = panel.import_sample("demo-celowe-usterki.json", project)
    assert accepted.status_code == 201
    run_id = accepted.json()["run"]["run_id"]
    assert panel.get(f"/projekty/{project}/przebiegi/{run_id}").status_code == 200


@pytest.mark.parametrize("container", [
    {"value": "review_dummy_secret"},
    [{"value": "review_dummy_secret"}],
    {"nested": ["review_dummy_secret"]},
])
def test_zagniezdzony_sekret_nie_trafia_do_zapisu_ani_eksportu(
    panel: Panel, container: Any,
) -> None:
    data = load_sample("demo-celowe-usterki.json")
    data["gates"][0]["facts"]["credentials"] = container
    data["gates"][0]["findings"][0]["evidence"]["authorization"] = container
    project = panel.create_project()
    assert panel.import_bytes(json.dumps(data).encode(), project).status_code == 201
    base = f"/api/v1/projects/{project}/runs/{data['run_id']}"

    stored = panel.get(base).json()["report"]
    assert stored["gates"][0]["facts"]["credentials"] == "[zredagowano]"
    assert "review_dummy_secret" not in json.dumps(stored)
    # Maskowanie nie może zmienić wyniku pomiaru secrets.found na tekst.
    secrets = next(g for g in stored["gates"] if g["gate"] == "G3.secrets")
    assert secrets["facts"]["secrets.found"] is True
    for fmt in ("json", "html", "markdown"):
        response = panel.get(f"{base}/report?format={fmt}")
        assert response.status_code == 200
        assert "review_dummy_secret" not in response.text


@pytest.mark.parametrize("section", ["warnings", "suppressed"])
def test_ostrzezenie_i_wyjatek_nie_staja_sie_blokada(panel: Panel, section: str) -> None:
    data = load_sample("demo-celowe-usterki.json")
    reason = next(r for r in data["decision"]["reasons"] if r["rule"] == "coverage.diff_ratio")
    data["gates"] = [g for g in data["gates"] if g["gate"] == "G2.diff_coverage"]
    data["decision"] = {"verdict": "PASS", "reasons": [], section: [reason]}
    parsed = parse_report(json.dumps(data).encode())
    view = build_run_view(run_result_from_payload(parsed.payload), project_id=1,
                          project_slug="demo", project_name="Demo")
    assert not view.gates[0].blocking_reasons
    assert not view.gates[0].needs_explanation
    assert view.gates[0].reasons[0].section == section
    assert view.gates[0].reasons[0].fact is not None

    project = panel.create_project()
    assert panel.import_bytes(json.dumps(data).encode(), project).status_code == 201
    page = panel.get(f"/projekty/{project}/przebiegi/{data['run_id']}")
    assert page.status_code == 200
    assert "narusza politykę" not in page.text
    assert "coverage.diff_ratio" in page.text


def test_pusty_projekt_w_formularzu_filtruje_wszystkie_projekty(panel: Panel) -> None:
    first = panel.create_project("Pierwszy")
    second = panel.create_project("Drugi")
    panel.import_sample("demo-celowe-usterki.json", first)
    panel.import_sample("bez-znalezisk.json", second)
    params = {"projekt": "", "decyzja": "BLOCK", "od": "", "do": "", "q": ""}
    response = panel.get("/przebiegi", params=params)
    assert response.status_code == 200
    assert "35e102d0d126" in response.text
    assert "0000demo0003" not in response.text
    params.update(projekt=str(second), decyzja="")
    selected = panel.get("/przebiegi", params=params)
    assert selected.status_code == 200
    assert "0000demo0003" in selected.text and "35e102d0d126" not in selected.text
    assert f'value="{second}" selected' in selected.text


@pytest.mark.parametrize("value", ["abc", "-1", str(2**63)])
def test_nieprawidlowy_numer_projektu_jest_odrzucony(panel: Panel, value: str) -> None:
    assert panel.get("/przebiegi", params={"projekt": value}).status_code == 422


@pytest.mark.parametrize("duration", ["missing", None, 0.0])
def test_czas_zachowuje_roznice_miedzy_brakiem_i_zerem(panel: Panel, duration: Any) -> None:
    data = load_sample("bez-znalezisk.json")
    data["gates"] = data["gates"][:1]
    for obj in (data, data["gates"][0]):
        if duration == "missing":
            del obj["duration_s"]
        else:
            obj["duration_s"] = duration
    project = panel.create_project()
    assert panel.import_bytes(json.dumps(data).encode(), project).status_code == 201
    base = f"/api/v1/projects/{project}/runs/{data['run_id']}"
    stored = panel.get(base).json()["report"]
    run = run_result_from_payload(stored)
    expected = 0.0 if duration == 0.0 else None
    assert run.duration_s == run.gate_results[0].duration_s == expected
    assert run.to_dict()["duration_s"] == expected
    assert run.to_dict()["gates"][0]["duration_s"] == expected
    for url in ("/przebiegi", f"/projekty/{project}/przebiegi/{data['run_id']}",
                f"{base}/report?format=html", f"{base}/report?format=markdown"):
        response = panel.get(url)
        assert response.status_code == 200, response.text
        if expected is None:
            assert "brak danych" in response.text
            assert "0,0 s" not in response.text and "0.0s" not in response.text
        else:
            assert "0,0 s" in response.text or "0.0s" in response.text


@pytest.mark.parametrize(("raw", "expected"), [
    ("2026-09-05T12:00:00+02:00", "2026-09-05 10:00 UTC"),
    ("2026-09-05T23:30:00-03:00", "2026-09-06 02:30 UTC"),
    ("2026-09-05T12:00:00Z", "2026-09-05 12:00 UTC"),
    ("2026-09-05T12:00:00", "2026-09-05 12:00"),
])
def test_strefa_czasowa_jest_zachowana(raw: str, expected: str) -> None:
    data = load_sample("bez-znalezisk.json")
    data["started_at"] = raw
    run = run_result_from_payload(parse_report(json.dumps(data).encode()).payload)
    assert _fmt_datetime(raw) == expected
    assert _fmt_datetime(run.started_at) == expected
