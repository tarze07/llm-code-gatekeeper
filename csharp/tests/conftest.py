from __future__ import annotations

import os
import subprocess
import sysconfig
from pathlib import Path

import pytest


def pytest_configure() -> None:
    """Dokłada katalog binarny środowiska testowego na początek `PATH`.

    Bramki wołają `diff-cover` i resztę narzędzi jako podprocesy, przez
    `shutil.which`. Instalują się one **razem z packiem**, do `…/.venv/bin`,
    ale `…/.venv/bin/python -m pytest` *nie* dokłada tego katalogu do `PATH`
    — robi to dopiero `source …/activate`. Zestaw odpalony bez aktywacji
    zgłaszał więc „nie znaleziono programu: diff-cover" dla narzędzia
    leżącego obok własnego interpretera: ten sam haczyk, który panel obchodzi
    w `gatekeeper_web.cli.ensure_tools_on_path`.

    Katalog bierzemy z `sysconfig`, nie z `Path(sys.executable).resolve()`:
    `…/.venv/bin/python` bywa dowiązaniem do `/usr/bin/python3.12`, więc
    rozwinięcie ścieżki wyprowadza **poza** środowisko packa.

    Hook musi być `pytest_configure`, nie fixture: warunki `skipif` w
    `test_gate_diff_coverage.py` liczą się przy imporcie modułu testowego,
    czyli zanim jakikolwiek fixture zdąży się wykonać.
    """
    bindir = sysconfig.get_path("scripts")
    parts = [p for p in os.environ.get("PATH", os.defpath).split(os.pathsep) if p]
    if bindir not in parts:
        os.environ["PATH"] = os.pathsep.join([bindir, *parts])

class Repo:
    """Minimalne repozytorium git do testów bramek."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    def write(self, rel: str, content: str) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def checkout(self, branch: str, create: bool = False) -> None:
        self.git("checkout", "-q", *(["-b"] if create else []), branch)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    path = tmp_path / "repo"
    path.mkdir(parents=True, exist_ok=True)
    r = Repo(path)
    r.write("README.md", "# projekt\n")
    r.write("src/App.cs", "namespace App;\npublic class Program {}\n")
    r.commit("initial")
    return r


#: Minimalne .csproj napisane ręcznie (nie przez `dotnet new`) — testy G2.*
#: wołają prawdziwy `dotnet build`/`dotnet test` wielokrotnie, więc unikanie
#: kosztu scaffoldowania per test naprawdę się liczy (`dotnet new` to ~1-2s
#: samo w sobie, tests/GatekeeperCsHelper.Tests obok tego zresztą pokazuje
#: ten sam kompromis: fixture jako tekst, nie żywy projekt).
_LIB_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>
</Project>
"""

_TEST_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <IsPackable>false</IsPackable>
    <IsTestProject>true</IsTestProject>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="coverlet.collector" Version="6.0.0" />
    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.8.0" />
    <PackageReference Include="xunit" Version="2.5.3" />
    <PackageReference Include="xunit.runner.visualstudio" Version="2.5.3" />
  </ItemGroup>
  <ItemGroup>
    <Using Include="Xunit" />
  </ItemGroup>
  <ItemGroup>
    <ProjectReference Include="..\\..\\src\\Demo\\Demo.csproj" />
  </ItemGroup>
</Project>
"""


@pytest.fixture
def dotnet_repo(tmp_path: Path) -> Repo:
    """Repo z projektem `src/Demo/Demo.csproj` + `tests/Demo.Tests/Demo.Tests.csproj`
    (referencja ustawiona), zawierające jedną klasę `Calc` z metodą `Cena`
    bez rabatu i jeden test-placeholder — baza pod scenariusze G2.*."""
    path = tmp_path / "repo"
    path.mkdir(parents=True, exist_ok=True)
    r = Repo(path)
    r.write(".gitignore", "bin/\nobj/\n")
    r.write("src/Demo/Demo.csproj", _LIB_CSPROJ)
    r.write("src/Demo/Calc.cs", "namespace Demo;\n\npublic class Calc\n{\n"
             "    public int Cena(int x, double rabat = 0.0) => x;\n}\n")
    r.write("tests/Demo.Tests/Demo.Tests.csproj", _TEST_CSPROJ)
    r.write(
        "tests/Demo.Tests/CalcTests.cs",
        "using Xunit;\nusing Demo;\n\nnamespace Demo.Tests;\n\n"
        "public class CalcTests\n{\n"
        "    [Fact]\n    public void Placeholder() { Assert.True(true); }\n}\n",
    )
    r.commit("baza: Calc bez rabatu")
    return r
