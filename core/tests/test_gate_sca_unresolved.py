"""G3.sca: nierozwiązane pakiety to brak dowodu, nie `pass`.

Wcześniej awaria sandboxa/narzędzia przenosiła pakiety do `unresolved`, a bramka
bez znalezisk zwracała `pass` (polityki nie mają reguły na
`sca.unresolved_package_count`) — fail-open.
"""

from __future__ import annotations

from gatekeeper_core.adapters import sca
from gatekeeper_core.adapters.base import ToolFailed
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.finding import Finding, Severity
from gatekeeper_core.gates.g3_sca import ScaGuard


def _manifest(*deps: str) -> str:
    body = ", ".join(f'"{d}"' for d in deps)
    return f'[project]\nname = "demo"\ndependencies = [{body}]\n'


def _zmiana(repo, *nowe: str) -> ChangeContext:
    repo.write("pyproject.toml", _manifest("requests"))
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("pyproject.toml", _manifest("requests", *nowe))
    repo.commit("feat: nowe zależności")
    return ChangeContext.from_git(repo.path, "main", "HEAD")


def test_wszystkie_pakiety_nierozwiazane_to_blad(repo, monkeypatch):
    def fail(*a, **k):
        raise ToolFailed("sandbox niedostępny")

    monkeypatch.setattr(sca, "run_pip_audit", fail)

    result = ScaGuard({}).run(_zmiana(repo, "urllib3", "six"))

    assert result.status == "error"
    assert result.facts["sca.unresolved_package_count"] == 2
    assert "six" in result.message and "urllib3" in result.message


def test_czesciowa_awaria_zachowuje_znaleziska_i_daje_fail(repo, monkeypatch):
    def partial(repo_path, sandbox, gate, requirements, new_packages, manifest, timeout_s):
        if new_packages == {"six"}:
            raise ToolFailed("nie rozwiązał się")
        return [
            Finding(
                gate=gate,
                rule_id="sca.PYSEC-1",
                severity=Severity.HIGH,
                title="urllib3: PYSEC-1",
                failure_scenario="podatność testowa",
                file=manifest,
                evidence={"package": "urllib3"},
            )
        ]

    monkeypatch.setattr(sca, "run_pip_audit", partial)

    result = ScaGuard({}).run(_zmiana(repo, "urllib3", "six"))

    assert result.status == "fail"
    assert len(result.findings) == 1
    assert result.facts["sca.unresolved_package_count"] == 1
    assert "nierozwiązane: six" in result.message


def test_czesciowa_awaria_bez_znalezisk_to_blad(repo, monkeypatch):
    def partial(repo_path, sandbox, gate, requirements, new_packages, manifest, timeout_s):
        if new_packages == {"six"}:
            raise ToolFailed("nie rozwiązał się")
        return []

    monkeypatch.setattr(sca, "run_pip_audit", partial)

    result = ScaGuard({}).run(_zmiana(repo, "urllib3", "six"))

    assert result.status == "error"
    assert result.facts["sca.unresolved_package_count"] == 1
