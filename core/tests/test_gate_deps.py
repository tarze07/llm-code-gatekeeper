from __future__ import annotations

import json

import pytest

from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.policy import Policy
from gatekeeper_core.deps.manifests import NPM, NUGET, PYPI
from gatekeeper_core.deps.registries import RegistryUnavailable
from gatekeeper_core.gates import gate_config_errors
from gatekeeper_core.gates.g1_deps import DepGuard
from tests.conftest import FakeRegistry

KNOWN = {
    "requests": {"age_days": 4000},
    "httpx": {"age_days": 2000},
    "fastapi": {"age_days": 2500},
}

NUGET_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>{deps}</ItemGroup></Project>"""


def csproj(*names: str) -> str:
    refs = "".join(f'<PackageReference Include="{n}" Version="1.0.0" />' for n in names)
    return NUGET_CSPROJ.format(deps=refs)


def build(repo, before: str, after: str, filename: str = "pyproject.toml") -> ChangeContext:
    repo.write(filename, before)
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write(filename, after)
    repo.commit("feat: nowa zależność")
    return ChangeContext.from_git(repo.path, "main", "HEAD")


def gate(
    packages: dict | None = None,
    nuget_packages: dict | None = None,
    npm_packages: dict | None = None,
    **config,
) -> DepGuard:
    return DepGuard(
        config or {},
        registries={
            PYPI: FakeRegistry(PYPI, packages if packages is not None else KNOWN),
            NPM: FakeRegistry(NPM, npm_packages or {}),
            NUGET: FakeRegistry(NUGET, nuget_packages or {}),
        },
    )


def manifest(*deps: str) -> str:
    body = ", ".join(f'"{d}"' for d in deps)
    return f'[project]\nname = "demo"\ndependencies = [{body}]\n'


def package_json(deps: dict[str, str]) -> str:
    return json.dumps({"dependencies": deps})


def test_halucynowany_pakiet_jest_blokowany(repo):
    change = build(repo, manifest("requests"), manifest("requests", "httpx-turbo-client"))
    result = gate().run(change)

    assert result.status == "fail"
    assert result.facts["deps.unknown_package"] is True
    finding = next(f for f in result.findings if f.rule_id == "deps.unknown_package")
    assert "httpx-turbo-client" in finding.title
    assert finding.severity == "critical"


def test_typosquat_jest_blokowany(repo):
    change = build(repo, manifest("requests"), manifest("requests", "requsts"))
    result = gate({"requsts": {"age_days": 3}}).run(change)

    assert result.facts["deps.typosquat_suspect"] is True
    finding = next(f for f in result.findings if f.rule_id == "deps.typosquat_suspect")
    assert "requests" in finding.evidence["similar_to"]


def test_stary_pakiet_o_podobnej_nazwie_to_tylko_informacja(repo):
    """Ustabilizowany pakiet nie jest podszyciem tylko dlatego, że nazywa się podobnie."""
    change = build(repo, manifest("requests"), manifest("requests", "requsts"))
    result = gate({"requsts": {"age_days": 2000}}).run(change)

    assert result.facts["deps.typosquat_suspect"] is False
    assert {f.rule_id for f in result.findings} == {"deps.similar_name"}


def test_mlody_pakiet_daje_znalezisko_high(repo):
    change = build(repo, manifest("requests"), manifest("requests", "brand-new-lib"))
    result = gate({"brand-new-lib": {"age_days": 12}}).run(change)

    assert result.facts["deps.too_young"] is True
    finding = next(f for f in result.findings if f.rule_id == "deps.too_young")
    assert finding.severity == "high"


def test_istniejaca_zaleznosc_nie_jest_sprawdzana_ponownie(repo):
    change = build(repo, manifest("requests"), manifest("requests", "httpx"))
    result = gate().run(change)

    assert result.facts["deps.new_packages"] == ["httpx"]
    assert result.status == "pass"


def test_pakiety_wewnetrzne_sa_pomijane(repo):
    change = build(repo, manifest("requests"), manifest("requests", "acme-internal-utils"))
    result = gate(internal_prefixes=["acme-"]).run(change)

    assert result.facts["deps.new_packages"] == []
    assert result.status == "pass"


def test_zmiana_bez_manifestu_nie_uruchamia_sieci(repo):
    repo.checkout("feature", create=True)
    repo.write("src/app.py", "def hello():\n    return 'x'\n")
    repo.commit("zmiana kodu")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    registry = FakeRegistry(PYPI, KNOWN)
    result = DepGuard({}, registries={PYPI: registry}).run(change)

    assert result.status == "pass"
    assert registry.calls == []


def test_niedostepny_rejestr_daje_blad_a_nie_zielona_bramke(repo):
    class Broken:
        ecosystem = PYPI

        def fetch(self, name):
            raise RegistryUnavailable("timeout")

    change = build(repo, manifest("requests"), manifest("requests", "cokolwiek"))
    result = DepGuard({}, registries={PYPI: Broken()}).run(change)

    assert result.status == "error"
    assert "nieosiągalny" in result.message


def test_requirements_txt_tez_jest_obslugiwany(repo):
    change = build(repo, "requests==2.31.0\n", "requests==2.31.0\nzmyslony-pakiet==1.0\n",
                   filename="requirements.txt")
    result = gate().run(change)

    assert result.facts["deps.unknown_package"] is True


# -------------------------------------------------------------------- nuget


def test_halucynowany_pakiet_nuget_jest_blokowany(repo):
    repo.write("Demo.csproj", csproj("Newtonsoft.Json"))
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("Demo.csproj", csproj("Newtonsoft.Json", "Halucynowany.Pakiet.Ktorego.Nie.Ma"))
    repo.commit("feat: nowy pakiet NuGet")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = gate(nuget_packages={"newtonsoft.json": {"age_days": 4000}}).run(change)

    assert result.status == "fail"
    assert result.facts["deps.unknown_package"] is True
    finding = next(f for f in result.findings if f.rule_id == "deps.unknown_package")
    assert "Halucynowany.Pakiet.Ktorego.Nie.Ma" in finding.title
    assert finding.file == "Demo.csproj"


def test_mlody_pakiet_nuget_daje_znalezisko_high(repo):
    repo.write("Demo.csproj", csproj("Newtonsoft.Json"))
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("Demo.csproj", csproj("Newtonsoft.Json", "Brand.New.Lib"))
    repo.commit("feat: mlody pakiet NuGet")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = gate(
        nuget_packages={
            "newtonsoft.json": {"age_days": 4000},
            "brand.new.lib": {"age_days": 10},
        }
    ).run(change)

    assert result.facts["deps.too_young"] is True
    finding = next(f for f in result.findings if f.rule_id == "deps.too_young")
    assert finding.severity == "high"


def test_directory_packages_props_jest_obslugiwany(repo):
    props = '<Project><ItemGroup>{deps}</ItemGroup></Project>'
    repo.write(
        "Directory.Packages.props",
        props.format(deps='<PackageVersion Include="Newtonsoft.Json" Version="13.0.3" />'),
    )
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write(
        "Directory.Packages.props",
        props.format(
            deps='<PackageVersion Include="Newtonsoft.Json" Version="13.0.3" />'
            '<PackageVersion Include="Nieznany.Pakiet" Version="1.0.0" />'
        ),
    )
    repo.commit("feat: cpm")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = gate(nuget_packages={"newtonsoft.json": {"age_days": 4000}}).run(change)

    assert result.facts["deps.unknown_package"] is True


@pytest.mark.parametrize("status", ["pass", "fail"])
def test_fakty_sa_zawsze_kompletne(repo, status):
    after = manifest("requests", "httpx") if status == "pass" else manifest("requests", "nie-ma")
    change = build(repo, manifest("requests"), after)
    result = gate().run(change)

    for fact in DepGuard.facts:
        assert fact in result.facts, f"brak faktu {fact} — polityka odwoła się do pustki"


# ----------------------------------------------------------- allow_packages


def test_allow_packages_gole_imie_z_kropka_dziala_dla_nuget(repo):
    """REVIEW.md P1: allow_packages szedł kiedyś zawsze przez normalizację PyPI
    (PEP 503 zjada `.` → `-`), więc gołe `"Acme.Tools"` nigdy nie trafiało w
    `provider.normalize` NuGet-owego `"Acme.Tools"` (tylko lowercase, kropka
    zostaje). Po naprawie wpis normalizuje się per ekosystem w miejscu
    porównania, więc dopasowanie działa."""
    repo.write("Demo.csproj", csproj("Newtonsoft.Json"))
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("Demo.csproj", csproj("Newtonsoft.Json", "Acme.Tools"))
    repo.commit("feat: wewnetrzny pakiet nuget")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = gate(
        nuget_packages={"newtonsoft.json": {"age_days": 4000}},
        allow_packages=["Acme.Tools"],
    ).run(change)

    assert result.facts["deps.new_packages"] == []
    assert result.status == "pass"


def test_allow_packages_nuget_jest_case_insensitive(repo):
    repo.write("Demo.csproj", csproj("Newtonsoft.Json"))
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("Demo.csproj", csproj("Newtonsoft.Json", "Acme.Tools"))
    repo.commit("feat: wewnetrzny pakiet nuget")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    # wpis małymi literami, pakiet w manifeście wielkimi — NuGet nie dba o wielkość liter
    result = gate(
        nuget_packages={"newtonsoft.json": {"age_days": 4000}},
        allow_packages=["acme.tools"],
    ).run(change)

    assert result.facts["deps.new_packages"] == []
    assert result.status == "pass"


def test_allow_packages_npm_scoped_bare_entry(repo):
    repo.write("package.json", package_json({}))
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("package.json", package_json({"@acme/cli": "^1.0.0"}))
    repo.commit("feat: wewnetrzny pakiet npm scoped")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = gate(allow_packages=["@acme/cli"]).run(change)

    assert result.facts["deps.new_packages"] == []
    assert result.status == "pass"


def test_allow_packages_forma_kwalifikowana_dziala_tylko_w_swoim_ekosystemie(repo):
    """`"npm:foo-tool"` zezwala na `foo-tool` w npm, ale NIE w PyPI — ta sama
    goła nazwa w dwóch ekosystemach to dwa różne pakiety różnych autorów."""
    repo.write("package.json", package_json({}))
    repo.write("pyproject.toml", manifest("requests"))
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("package.json", package_json({"foo-tool": "^1.0.0"}))
    repo.write("pyproject.toml", manifest("requests", "foo-tool"))
    repo.commit("feat: foo-tool w dwoch ekosystemach")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = gate(
        npm_packages={"foo-tool": {"age_days": 4000}},
        allow_packages=["npm:foo-tool"],
    ).run(change)

    # npm:foo-tool jest wewnetrzny i odpada z listy, pypi:foo-tool zostaje
    # i trafia do sprawdzenia (nieznany w `KNOWN`, więc bramka go blokuje).
    assert result.facts["deps.new_packages"] == ["foo-tool"]
    assert result.status == "fail"
    assert result.facts["deps.unknown_package"] is True


def test_allow_packages_dziala_naraz_dla_dwoch_ekosystemow_w_jednym_pr(repo):
    """Gole imie w `allow_packages` ma dzialac w KAZDYM ekosystemie naraz —
    mieszany PR (Python + npm) nie ma dostawac polowicznej allowlisty."""
    repo.write("package.json", package_json({}))
    repo.write("pyproject.toml", manifest("requests"))
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("package.json", package_json({"shared-name": "^1.0.0"}))
    repo.write("pyproject.toml", manifest("requests", "shared-name"))
    repo.commit("feat: shared-name w dwoch ekosystemach")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = gate(allow_packages=["shared-name"]).run(change)

    assert result.facts["deps.new_packages"] == []
    assert result.status == "pass"


def test_allow_packages_nieznany_prefiks_ekosystemu_jest_bledem_konfiguracji():
    with pytest.raises(ValueError, match="npmm"):
        gate(allow_packages=["npmm:left-pad"])


def test_allow_packages_puste_imie_po_prefiksie_jest_bledem_konfiguracji():
    with pytest.raises(ValueError):
        gate(allow_packages=["npm:"])


@pytest.mark.parametrize(
    ("filename", "before", "broken"),
    [
        ("package.json", package_json({"left-pad": "1.0.0"}), '{"dependencies": {"evil": '),
        ("app.csproj", csproj("Newtonsoft.Json"), "<Project><ItemGroup>"),
        ("pyproject.toml", manifest("requests"), '[project\ndependencies = ["evil"]'),
    ],
)
def test_zepsuty_manifest_jest_bledem_a_nie_brakiem_nowych_pakietow(
    repo, filename, before, broken
):
    change = build(repo, before, broken, filename=filename)
    result = gate().run(change)

    assert result.status == "error"
    assert filename in result.message
    assert result.facts["deps.manifests_changed"] == 1


def test_nowy_manifest_bez_wersji_bazowej_nadal_jest_czytany(repo):
    repo.write("README.md", "x\n")
    repo.commit("baza")
    repo.checkout("feature", create=True)
    repo.write("package.json", package_json({"left-pad": "1.0.0"}))
    repo.commit("feat: package.json")
    change = ChangeContext.from_git(repo.path, "main", "HEAD")

    result = gate(npm_packages={"left-pad": {"age_days": 3000}}).run(change)

    assert result.status == "pass"
    assert result.facts["deps.new_packages"] == ["left-pad"]


def test_lint_wylapuje_literowke_w_prefiksie_allow_packages():
    policy = Policy.from_dict(
        {
            "version": 1,
            "gates": {"G1.deps": {"allow_packages": ["acme", "npmm:@acme/cli", 7]}},
        }
    )
    errors = gate_config_errors(policy)

    assert len(errors) == 2
    assert all(e.startswith("`gates.G1.deps`:") for e in errors)
    assert any("npmm" in e for e in errors)
    assert DepGuard.config_errors({"allow_packages": ["acme", "nuget:Acme.Tools"]}) == []
