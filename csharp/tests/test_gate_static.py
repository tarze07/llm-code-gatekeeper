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
from gatekeeper_core.core.orchestrator import run_gates
from gatekeeper_core.core.policy import Policy
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

    assert result.status == "pass", result.message
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

    assert result.status == "fail", result.message
    assert any(f.rule_id == "dotnet.CS0029" for f in result.findings)
    assert result.facts["static.dotnet_available"] is True


@requires_dotnet
@pytest.mark.parametrize(("case", "status"), [
    ("valid", "pass"), ("type_error", "fail"),
    ("missing_package", "error"), ("build_error", "error"),
])
def test_orkiestrator_sprawdza_csharp_w_swiezej_kopii(repo, case, status):
    repo.write(".gitignore", "bin/\nobj/\n")
    project = ('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
               '<TargetFramework>net8.0</TargetFramework></PropertyGroup>{}</Project>')
    repo.write("Demo.csproj", project.format(""))
    repo.write("Calc.cs", 'public class Calc { public int Value() => 1; }\n')
    repo.commit("base: project")
    repo.checkout("feature", create=True)
    value = '"bad type"' if case == "type_error" else "2"
    repo.write("Calc.cs", f"public class Calc {{ public int Value() => {value}; }}\n")
    if case == "missing_package":
        repo.write("Demo.csproj", project.format(
            '<ItemGroup><PackageReference Include="Gatekeeper.Review.Missing.Package" '
            'Version="0.0.0" /></ItemGroup>'
        ))
    elif case == "build_error":
        repo.write("Demo.csproj", project.format(
            '<Target Name="ReviewFailure" BeforeTargets="CoreCompile">'
            '<Error Text="review build failure" /></Target>'
        ))
    repo.commit("change")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    # Domyślna konfiguracja również musi zgłaszać awarię kompilacji, a nie pass.
    result = run_gates(change, Policy(), gates=[StaticGuard({})], max_workers=1)

    gate = result.gate_results[0]
    assert gate.status == status, gate.message
    if case == "type_error":
        assert any(f.rule_id == "dotnet.CS0029" for f in gate.findings)
    if case == "missing_package":
        assert "NU1100" in gate.message
    if case == "build_error":
        assert "review build failure" in gate.message
    # Restore i build dotyczą prywatnej kopii, a nie repozytorium operatora.
    assert not (repo.path / "obj").exists()
    assert repo.git("status", "--porcelain") == ""
