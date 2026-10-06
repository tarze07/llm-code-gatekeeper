"""G1 — `dep-guard`: weryfikacja nowych zależności.

Klasa defektu, której nie łapie żaden standardowy skaner, bo wszystkie
zakładają, że pakiet wpisany do manifestu istnieje. Agent potrafi wymyślić
nazwę pakietu, a lockfile powstaje razem z kodem — więc CI instaluje dokładnie
to, co agent zmyślił (PLAN.md §1).

Wersja z kamienia 1: **istnienie, wiek, typosquat**. Pobrania, repo źródłowe
i importy niezadeklarowane w manifeście dochodzą w kamieniu 3.

Wykrywanie i parsowanie manifestów idzie przez zainstalowanych dostawców
`gatekeeper.dep_ecosystems` (`EcosystemProvider`, `core/plugins.py`) —
bramka sama nie wie, jak wygląda manifest PyPI/npm/NuGet, tylko że
`provider.is_manifest(path)`/`provider.parse_manifest(...)` istnieją. Nowy
ekosystem to nowy zarejestrowany provider, nie zmiana w tym pliku.

## `allow_packages` — składnia (`policy/gates.yaml`, `gates.G1.deps.allow_packages`)

Lista stringów. Dwie formy, obie akceptowane w jednej liście:

- **gołe imię**, np. `"acme-tools"` — obowiązuje w **każdym** ekosystemie,
  każdy porównywany przez normalizację *tego* ekosystemu (PEP 503 dla PyPI,
  lowercase dla npm/NuGet). Zgodność wsteczna: tak wygląda każdy wpis przed
  tą poprawką.
- **forma kwalifikowana** `"<ekosystem>:<imię>"`, np. `"npm:@acme/cli"` albo
  `"nuget:Microsoft.Extensions.Logging"` — obowiązuje tylko w jednym
  ekosystemie (`pypi`/`npm`/`nuget`). Potrzebna, bo ta sama goła nazwa w
  dwóch ekosystemach to dwa różne pakiety różnych autorów — zezwolenie na
  `foo` w PyPI nie powinno przypadkiem przepuszczać `foo` w npm.

Żadna z trzech obsługiwanych nazw pakietów nie zawiera `:`, więc jego
obecność jednoznacznie wybiera formę kwalifikowaną; nieznany prefiks przed
`:` (literówka typu `npmm:`) jest błędem konfiguracji (`ValueError` przy
starcie bramki), nie cichą dziurą w allowliście.

Każda forma jest normalizowana **per ekosystem w miejscu porównania**, tą
samą funkcją, którą bramka normalizuje nazwę sprawdzanej zależności
(`provider.normalize`, domyślnie `deps.manifests.normalize`) — przed tą
poprawką cała allowlista szła przez normalizację PyPI (PEP 503: `.`/`_`/`-`
są równoważne), co dla NuGet/npm dawało złe dopasowania (np. kropka w
`Microsoft.Extensions.Logging` zjadana na `-`, więc nigdy nie trafiała w
`provider.normalize` tego samego pakietu, który tylko dociąga wielkość liter).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

from ..core.change import ChangeContext
from ..core.finding import Finding, GateResult, Severity
from ..core.plugins import EcosystemProvider
from ..deps import manifests, typosquat
from ..deps.registries import Registry, RegistryUnavailable
from . import Gate, register

DEFAULT_MIN_AGE_DAYS = 90
#: Poniżej tego wieku „podobna nazwa" jest podejrzana. Powyżej — to po prostu
#: ustabilizowany pakiet, który przypadkiem nazywa się podobnie.
TYPOSQUAT_AGE_DAYS = 365

DEP_ECOSYSTEM_GROUP = "gatekeeper.dep_ecosystems"

#: Ekosystemy, które można nazwać w kwalifikowanym wpisie `allow_packages`
#: (`"<ekosystem>:<imię>"`). Te trzy to te, które core sam parsuje
#: (`deps/manifests.py`) — pack językowy nie rejestruje tu niczego nowego,
#: więc nie trzeba tej listy rozszerzać przy nowym packu.
ALLOW_PACKAGES_ECOSYSTEMS = (manifests.PYPI, manifests.NPM, manifests.NUGET)


def installed_ecosystems() -> list[EcosystemProvider]:
    return [ep.load()() for ep in entry_points(group=DEP_ECOSYSTEM_GROUP)]


def _parse_allow_entry(entry: str) -> tuple[str | None, str]:
    """Rozbija jeden wpis `allow_packages` na `(ekosystem | None, imię)`.

    `None` jako ekosystem = wpis gołego imienia, obowiązuje wszędzie.
    Żadna z obsługiwanych nazw pakietów (PyPI/npm/NuGet) nie zawiera `:`,
    więc jego obecność jednoznacznie oznacza formę `"<ekosystem>:<imię>"` —
    a nieznany prefiks jest literówką w konfiguracji, nie nazwą pakietu.
    """
    if ":" not in entry:
        return None, entry
    eco, _, name = entry.partition(":")
    if eco not in ALLOW_PACKAGES_ECOSYSTEMS:
        raise ValueError(
            f"G1.deps: allow_packages — nieznany prefiks ekosystemu {eco!r} "
            f"we wpisie {entry!r} (oczekiwano jednego z: "
            f"{', '.join(ALLOW_PACKAGES_ECOSYSTEMS)})"
        )
    if not name:
        raise ValueError(
            f"G1.deps: allow_packages — brak nazwy pakietu po prefiksie we wpisie {entry!r}"
        )
    return eco, name


@register
class DepGuard(Gate):
    id = "G1.deps"
    name = "Weryfikacja nowych zależności"
    budget_s = 60.0
    facts = (
        "deps.new_packages",
        "deps.new_package_count",
        "deps.new_external_package",
        "deps.unknown_package",
        "deps.typosquat_suspect",
        "deps.too_young",
        "deps.no_source_repo",
        "deps.manifests_changed",
    )

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        registries: dict[str, Registry] | None = None,
    ) -> None:
        super().__init__(config)
        self._registries = registries
        self.min_age_days = float(self.config.get("min_age_days", DEFAULT_MIN_AGE_DAYS))
        self.internal_prefixes: list[str] = list(self.config.get("internal_prefixes", []))
        self._providers: list[EcosystemProvider] | None = None
        self._allow_global: list[str] = []
        self._allow_qualified: dict[str, list[str]] = {}
        for entry in self.config.get("allow_packages", []):
            eco, name = _parse_allow_entry(entry)
            if eco is None:
                self._allow_global.append(name)
            else:
                self._allow_qualified.setdefault(eco, []).append(name)
        #: Allowlista znormalizowana per ekosystem, leniwie i z cache —
        #: patrz `_allowlist_for()`. Nie licz tego tutaj: normalizacja ma
        #: użyć TEJ SAMEJ funkcji, którą `_is_internal()` normalizuje nazwę
        #: sprawdzanej zależności (`provider.normalize`, jeśli provider dla
        #: tego ekosystemu jest zainstalowany), a providery ładują się
        #: leniwie (`self.providers`).
        self._allowlist_cache: dict[str, frozenset[str]] = {}

    @classmethod
    def config_errors(cls, config: dict[str, Any]) -> list[str]:
        entries = config.get("allow_packages", [])
        if not isinstance(entries, list):
            return ["`allow_packages` musi być listą"]
        errors: list[str] = []
        for entry in entries:
            if not isinstance(entry, str):
                errors.append(f"`allow_packages`: wpis {entry!r} nie jest napisem")
                continue
            try:
                _parse_allow_entry(entry)
            except ValueError as exc:
                errors.append(str(exc))
        return errors

    @property
    def providers(self) -> list[EcosystemProvider]:
        if self._providers is None:
            self._providers = installed_ecosystems()
        return self._providers

    @property
    def registries(self) -> dict[str, Registry]:
        if self._registries is None:
            from ..deps.registries import DEFAULT_CACHE_DIR

            self._registries = {
                p.ecosystem: p.build_registry(DEFAULT_CACHE_DIR) for p in self.providers
            }
        return self._registries

    def run(self, change: ChangeContext) -> GateResult:
        started = time.monotonic()
        providers = self.providers
        changed_manifests = [
            f
            for f in change.files
            if f.status != "D" and any(p.is_manifest(f.path) for p in providers)
        ]
        if not changed_manifests:
            return self.result(
                status="pass",
                duration_s=time.monotonic() - started,
                facts=_empty_facts(),
                message="żaden manifest zależności nie został zmieniony",
            )

        new_deps: list[manifests.Dependency] = []
        try:
            for changed in changed_manifests:
                head = change.file_at(change.head_sha, changed.path) or ""
                base = change.file_at(change.base_sha, changed.path) or ""
                after: set[manifests.Dependency] = set()
                before: set[manifests.Dependency] = set()
                for provider in providers:
                    if provider.is_manifest(changed.path):
                        after |= provider.parse_manifest(changed.path, head)
                        before |= provider.parse_manifest(changed.path, base)
                new_deps.extend(manifests.diff_dependencies(before, after))
        except manifests.ManifestUnparseable as exc:
            # Nieczytelny manifest to brak dowodu, nie „brak nowych pakietów”.
            facts = _empty_facts()
            facts["deps.manifests_changed"] = len(changed_manifests)
            return self.result(
                status="error",
                duration_s=time.monotonic() - started,
                facts=facts,
                message=f"nie da się odczytać manifestu: {exc}",
            )

        new_deps = [d for d in new_deps if not self._is_internal(d)]
        findings: list[Finding] = []
        facts = _empty_facts()
        facts["deps.manifests_changed"] = len(changed_manifests)
        facts["deps.new_packages"] = [d.name for d in new_deps]
        facts["deps.new_package_count"] = len(new_deps)
        facts["deps.new_external_package"] = bool(new_deps)

        if not new_deps:
            return self.result(
                status="pass",
                duration_s=time.monotonic() - started,
                facts=facts,
                message="manifest zmieniony, ale bez nowych pakietów",
            )

        try:
            for dep in new_deps:
                findings.extend(self._check(dep, facts))
        except RegistryUnavailable as exc:
            # Świadomie `error`, nie `pass`: brak odpowiedzi rejestru oznacza,
            # że nie sprawdziliśmy niczego, a nie że jest dobrze.
            return self.result(
                status="error",
                duration_s=time.monotonic() - started,
                facts=facts,
                findings=findings,
                message=f"rejestr pakietów nieosiągalny: {exc}",
            )

        return self.result(
            status="fail" if findings else "pass",
            duration_s=time.monotonic() - started,
            facts=facts,
            findings=findings,
            message=f"sprawdzono {len(new_deps)} nowych pakietów",
        )

    # ------------------------------------------------------------------

    def _is_internal(self, dep: manifests.Dependency) -> bool:
        provider = next((p for p in self.providers if p.ecosystem == dep.ecosystem), None)
        normalize: Callable[[str], str] = (
            provider.normalize if provider else (lambda n: manifests.normalize(dep.ecosystem, n))
        )
        name = normalize(dep.name)
        if name in self._allowlist_for(dep.ecosystem, normalize):
            return True
        return any(name.startswith(p.lower()) for p in self.internal_prefixes)

    def _allowlist_for(self, ecosystem: str, normalize: Callable[[str], str]) -> frozenset[str]:
        """Allowlista `allow_packages` znormalizowana dla `ecosystem`, z cache.

        Wpisy gołego imienia (`self._allow_global`) obowiązują w każdym
        ekosystemie — każdy znormalizowany tą samą funkcją, co nazwa
        sprawdzanej zależności. Wpisy kwalifikowane (`self._allow_qualified`)
        dokładają się tylko dla swojego ekosystemu. To naprawia P1 z REVIEW.md
        §5: cała allowlista szła kiedyś przez normalizację PyPI, więc dla
        npm/NuGet porównanie z `provider.normalize(dep.name)` nie mogło się
        zgodzić (PEP 503 zjada `.` w `Microsoft.Extensions.Logging`).
        """
        cached = self._allowlist_cache.get(ecosystem)
        if cached is not None:
            return cached
        names = list(self._allow_global) + self._allow_qualified.get(ecosystem, [])
        cached = frozenset(normalize(n) for n in names)
        self._allowlist_cache[ecosystem] = cached
        return cached

    def _check(self, dep: manifests.Dependency, facts: dict[str, Any]) -> list[Finding]:
        registry = self.registries.get(dep.ecosystem)
        if registry is None:
            return []
        info = registry.fetch(dep.name)
        if not info.exists:
            facts["deps.unknown_package"] = True
            return [
                Finding(
                    gate=self.id,
                    rule_id="deps.unknown_package",
                    severity=Severity.CRITICAL,
                    title=f"Pakiet `{dep.name}` nie istnieje w rejestrze {dep.ecosystem}",
                    failure_scenario=(
                        f"Instalacja zależności na dowolnym środowisku przerwie się błędem "
                        f"„No matching distribution found for {dep.name}”. Jeżeli ktoś "
                        f"opublikuje pakiet o tej nazwie wcześniej niż my zauważymy, "
                        f"zainstalujemy jego kod z uprawnieniami naszego procesu budowania."
                    ),
                    file=dep.manifest,
                    evidence={"snippet": dep.raw, "ecosystem": dep.ecosystem},
                )
            ]

        findings: list[Finding] = []
        neighbours = typosquat.nearest_popular(dep.ecosystem, dep.name)
        age = info.age_days
        young = age is not None and age < self.min_age_days

        if neighbours:
            similar = ", ".join(f"`{n.candidate}`" for n in neighbours)
            suspect = age is None or age < TYPOSQUAT_AGE_DAYS
            if suspect:
                facts["deps.typosquat_suspect"] = True
                findings.append(
                    Finding(
                        gate=self.id,
                        rule_id="deps.typosquat_suspect",
                        severity=Severity.CRITICAL,
                        title=f"Nazwa `{dep.name}` myląco podobna do: {similar}",
                        failure_scenario=(
                            f"Jeżeli `{dep.name}` jest podszyciem pod {similar}, jego kod "
                            f"wykona się przy instalacji i w każdym imporcie — z dostępem do "
                            f"zmiennych środowiskowych procesu budowania, czyli do sekretów CI. "
                            f"Pakiet ma {_age_str(age)}, co nie daje mu żadnej historii."
                        ),
                        file=dep.manifest,
                        evidence={
                            "snippet": dep.raw or dep.name,
                            "similar_to": [n.candidate for n in neighbours],
                            "age_days": age,
                        },
                    )
                )
            else:
                findings.append(
                    Finding(
                        gate=self.id,
                        rule_id="deps.similar_name",
                        severity=Severity.LOW,
                        title=f"Nazwa `{dep.name}` przypomina: {similar}",
                        failure_scenario=(
                            f"Pakiet istnieje od {_age_str(age)}, więc raczej nie jest "
                            f"podszyciem — ale jeżeli agent chciał użyć {similar}, "
                            f"zainstalowaliśmy bibliotekę robiącą coś innego."
                        ),
                        file=dep.manifest,
                        evidence={"snippet": dep.raw or dep.name, "age_days": age},
                    )
                )

        if young:
            facts["deps.too_young"] = True
            findings.append(
                Finding(
                    gate=self.id,
                    rule_id="deps.too_young",
                    severity=Severity.HIGH,
                    title=(
                        f"Pakiet `{dep.name}` ma {_age_str(age)} (próg: {self.min_age_days:g} dni)"
                    ),
                    failure_scenario=(
                        "Pakiet bez historii nie ma za sobą ani przeglądu społeczności, ani "
                        "czasu na wykrycie złośliwego wydania. Jeżeli okaże się porzucony lub "
                        "przejęty, wycofanie go z produkcji wymaga zmiany kodu, nie tylko wersji."
                    ),
                    file=dep.manifest,
                    evidence={"snippet": dep.raw or dep.name, "age_days": age},
                )
            )

        if not info.repo_url:
            facts["deps.no_source_repo"] = True
            findings.append(
                Finding(
                    gate=self.id,
                    rule_id="deps.no_source_repo",
                    severity=Severity.MEDIUM,
                    title=f"Pakiet `{dep.name}` nie podaje repozytorium źródłowego",
                    failure_scenario=(
                        "Przy incydencie bezpieczeństwa nie da się przejrzeć kodu ani historii "
                        "zmian pakietu — jedynym źródłem prawdy jest artefakt z rejestru."
                    ),
                    file=dep.manifest,
                    evidence={"snippet": dep.raw or dep.name},
                )
            )
        return findings


def _empty_facts() -> dict[str, Any]:
    return {
        "deps.new_packages": [],
        "deps.new_package_count": 0,
        "deps.new_external_package": False,
        "deps.unknown_package": False,
        "deps.typosquat_suspect": False,
        "deps.too_young": False,
        "deps.no_source_repo": False,
        "deps.manifests_changed": 0,
    }


def _age_str(age_days: float | None) -> str:
    if age_days is None:
        return "nieznany wiek"
    if age_days < 1:
        return "mniej niż dobę"
    if age_days < 60:
        return f"{age_days:.0f} dni"
    return f"{age_days / 30.4:.0f} mies."
