"""Testy G1.static (dispatcher core-owy, `gatekeeper_core.gates.g1_static`)
na żywym SDK .NET — integracja, nie golden file. Adapter ma już testy na
zapisanym wyjściu (`test_adapters_dotnet.py`); tu sprawdzamy, że
`CsharpStaticChecker` faktycznie dogfooduje się przez entry points
`gatekeeper.static_checkers` i że filtrowanie do zmienionych linii, decyzja
`pass`/`fail`, obsługa braku narzędzia działają na żywo. Testy pomijane bez
`dotnet`.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g1_static import StaticGuard

requires_dotnet = pytest.mark.skipif(
    shutil.which("dotnet") is None, reason="dotnet niedostępny (.NET SDK)"
)


@requires_dotnet
def test_csharp_bez_csproj_przechodzi_bez_wolania_dotneta(repo):
    repo.checkout("feature", create=True)
    repo.write("Loose.cs", "namespace X;\npublic class Loose {}\n")
    repo.commit("feat: cs bez projektu")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "pass"
    assert result.facts["static.csharp_files_checked"] == 1
    assert result.facts["static.csproj_found"] is False


@requires_dotnet
def test_wymyslone_wywolanie_api_w_csharp_blokuje(repo, tmp_path):
    def dotnet_(*args):
        subprocess.run(["dotnet", *args], cwd=repo.path, check=True, capture_output=True)

    dotnet_("new", "classlib", "-n", "Demo", "-o", ".")
    repo.commit("baza: szkielet projektu")
    dotnet_("restore", "-v", "quiet")

    repo.checkout("feature", create=True)
    repo.write(
        "Calc.cs",
        "namespace Demo;\n\npublic class Calc\n{\n"
        "    public int Add(int a, string b)\n    {\n        return a + b;\n    }\n}\n",
    )
    repo.commit("feat: cs z bledem typu")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = StaticGuard({}).run(change)

    assert result.status == "fail"
    assert any(f.rule_id == "dotnet.CS0029" for f in result.findings)
    assert result.facts["static.dotnet_available"] is True
