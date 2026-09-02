"""Testy G1.complexity (dispatcher core-owy, `gatekeeper_core.gates.g1_complexity`)
na prawdziwym `gatekeeper-cs-helper complexity` — integracja, nie golden file.
Odpowiednik testów `test_gate_static.py`. Testy pomijane bez `gatekeeper-cs-helper`
(nie wymaga `dotnet build`/`.csproj` — sam parser Roslyn nie kompiluje kodu).
"""

from __future__ import annotations

import shutil

import pytest
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g1_complexity import ComplexityGuard

requires_helper = pytest.mark.skipif(
    shutil.which("gatekeeper-cs-helper") is None, reason="gatekeeper-cs-helper niedostępny"
)


@requires_helper
def test_metoda_powyzej_progu_blokuje(repo):
    repo.checkout("feature", create=True)
    body = (
        "namespace App;\n\npublic class Program\n{\n"
        "    public string Przetworz(bool a, bool b, bool c, bool d, bool e,\n"
        "        bool f, bool g, bool h, bool i, bool j)\n    {\n"
    )
    for v in "abcdefghij":
        body += f"        if ({v})\n        {{\n"
    body += '            return "ok";\n'
    for _ in "abcdefghij":
        body += "        }\n"
    body += '        return "brak";\n    }\n}\n'
    repo.write("src/App.cs", body)
    repo.commit("feat: zbyt zlozona metoda")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = ComplexityGuard({}).run(change)

    assert result.status == "fail"
    assert result.facts["complexity.over_threshold_count"] == 1
    assert result.facts["complexity.max"] > 10
    assert result.findings[0].rule_id == "complexity.too_high"


@requires_helper
def test_prosta_metoda_przechodzi(repo):
    repo.checkout("feature", create=True)
    repo.write(
        "src/App.cs",
        "namespace App;\n\npublic class Program\n{\n"
        "    public int Dodaj(int a, int b) => a + b;\n}\n",
    )
    repo.commit("feat: prosta metoda")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = ComplexityGuard({}).run(change)

    assert result.status == "pass"
    assert result.findings == []
