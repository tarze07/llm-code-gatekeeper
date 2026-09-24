import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from gatekeeper_core.cli import app
from gatekeeper_core.core.change import ChangeContext, GitError, write_worktree_file
from gatekeeper_core.core.finding import Finding, GateResult, Severity, Verdict
from gatekeeper_core.core.orchestrator import run_gates
from gatekeeper_core.core.policy import Exemption, Policy
from gatekeeper_core.gates import Gate, build_gates
from gatekeeper_core.gates.g2_crossverify import CrossVerify


def test_wyjatek_nie_wycisza_innego_znaleziska_tej_samej_reguly():
    findings = [
        Finding(
            gate="G3.secrets",
            rule_id="secrets.found_in_diff",
            severity=Severity.CRITICAL,
            title=name,
            failure_scenario="sekret w repo",
            file=name,
        )
        for name in ("old.py", "new.py")
    ]
    facts = {"secrets.found_in_diff": True}
    results = [GateResult(gate="G3.secrets", status="fail", facts=facts, findings=findings)]
    policy = Policy.from_dict({"blocking": ["secrets.found_in_diff"]})
    policy.exemptions = [
        Exemption(
            rule="secrets.found_in_diff",
            owner="team",
            reason="zaakceptowany stary sekret",
            expires=date.today() + timedelta(days=1),
            fingerprints=(findings[0].fingerprint,),
        )
    ]
    decision = policy.decide(facts, results)
    assert decision.verdict == Verdict.BLOCK
    assert decision.reasons[0].fingerprints == (findings[1].fingerprint,)
    assert decision.suppressed[0].fingerprints == (findings[0].fingerprint,)
    policy.exemptions.append(
        Exemption(
            rule="secrets.found_in_diff",
            owner="team",
            reason="drugi osobny wyjątek",
            expires=date.today() + timedelta(days=1),
            fingerprints=(findings[1].fingerprint,),
        )
    )
    assert policy.decide(facts, results).verdict == Verdict.PASS


@pytest.mark.parametrize("names", [["G3.secret"], ["G3.secrets", "G3.secret"]])
def test_nieznana_bramka_jest_bledem(names):
    with pytest.raises(ValueError, match="G3.secret"):
        build_gates(Policy(), only=names)


def test_cli_odrzuca_literowke_przed_analiza_repo(tmp_path):
    policy = tmp_path / "gates.yaml"
    policy.write_text("version: 1\n")
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--base",
            "main",
            "--repo",
            str(tmp_path),
            "--policy",
            str(policy),
            "--gate",
            "G3.secret",
        ],
    )
    assert result.exit_code == 3
    assert "nieznane bramki" in result.output


def change_at_head(repo):
    repo.checkout("feature", create=True)
    repo.write("src/app.py", "committed = True\n")
    repo.commit("change")
    return ChangeContext.from_git(repo.path, "main")


def test_bramki_analizuja_wskazany_commit_i_nie_dziela_zapisu(repo):
    change = change_at_head(repo)
    repo.checkout("main")
    repo.write("src/app.py", "dirty = True\n")

    class Inspect(Gate):
        def __init__(self, name):
            super().__init__()
            self.id = name

        def run(self, snapshot):
            path = snapshot.repo / "src/app.py"
            content = path.read_text()
            path.write_text("mutated by gate\n")
            return self.result(status="pass", facts={self.id: content})

    result = run_gates(change, Policy(), gates=[Inspect("a"), Inspect("b")], max_workers=1)
    assert result.facts == {"a": "committed = True\n", "b": "committed = True\n"}
    assert (repo.path / "src/app.py").read_text() == "dirty = True\n"
    assert result.head_sha == change.head_sha
    assert result.repo == str(repo.path)


@pytest.mark.parametrize("count", [1, 2])
def test_timeout_nie_czeka_na_zawieszona_bramke(repo, count):
    class Hung(Gate):
        budget_s = 0.1

        def __init__(self, name):
            super().__init__()
            self.id = name

        def run(self, change):
            time.sleep(30)
            return self.result(status="pass")

    change = change_at_head(repo)
    started = time.monotonic()
    result = run_gates(change, Policy(), gates=[Hung(str(i)) for i in range(count)])
    assert time.monotonic() - started < 2
    assert all(g.status == "error" for g in result.gate_results)
    assert result.decision.verdict == Verdict.PASS_WITH_REVIEW


@pytest.mark.parametrize("outcome", [None, "missing", "skipped", "error"])
def test_crossverify_bez_dowodu_nie_przechodzi(repo, monkeypatch, outcome):
    class Toolchain:
        language = "python"

        def discover_tests(self, change):
            return [SimpleNamespace(nodeid="test::new", declared_escape=False)]

        def run_cross_verify(self, *args):
            return (
                {} if outcome is None else {"test::new": SimpleNamespace(outcome=outcome)}
            ), "brak dowodu"

    monkeypatch.setattr(
        "gatekeeper_core.gates.g2_crossverify._installed_toolchains", lambda: [Toolchain()]
    )
    result = CrossVerify().run(change_at_head(repo))
    assert result.status == "error"
    assert result.facts["tests.proved"] == 0
    assert result.facts["tests.weak_evidence"] == 1


def test_nakladanie_testow_nie_podaza_za_dowiazaniem(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "test.py").write_text("original")
    (work / "tests").symlink_to(outside, True)
    with pytest.raises(GitError, match="dowiązanie"):
        write_worktree_file(work, "tests/test.py", "replacement")
    assert (outside / "test.py").read_text() == "original"
