"""Szablony polityki: dwa profile i ich kopie w pack'ach nie mogą dryfować.

`core/policy/` jest wzorcem. Kopie żyją w `python/policy/`, `ts/policy/`,
`csharp/policy/` (każdy pack instaluje się osobno przez
`#subdirectory=<pack>`, więc potrzebuje fizycznego pliku, nie symlinku)
i w polityce startowej panelu. Kopie są ręczne — wybraliśmy test zamiast
generatora, bo generator to dodatkowy krok budowania, który da się pominąć,
a test zawodzi w CI sam.

Co musi być identyczne: wszystko, co decyduje o werdykcie (`blocking`,
`thresholds` razem z komunikatami, `human_review_required_when`, `warn_only`,
`on_gate_error`, …) i konfiguracja każdej bramki skonfigurowanej we wzorcu.
Co wolno kopii: komentarze oraz DODATKOWE sekcje `gates:` dla knobs
checkerów danego pack'a, a w `G1.static` (sekcja z natury pack'owa) —
dodatkowe klucze obok tych ze wzorca (np. `mypy_args` w Pythonie).

Profile różnią się tylko `warn_only` i flagami `gates.G1.static.require_*`:
enforcing włącza wszystkie, żeby brak narzędzia albo configu (tsconfig,
eslint, `.csproj`) przy plikach danego języka był `error`, nie `pass`
(REVIEW.md §5 P1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from gatekeeper_core.core.policy import Policy
from gatekeeper_core.gates import known_facts, known_gate_ids

CORE_POLICY = Path(__file__).resolve().parents[1] / "policy"
MONOREPO = CORE_POLICY.parents[1]

ADOPTION = "gates.yaml"
ENFORCING = "gates.enforcing.yaml"
PROFILES = (ADOPTION, ENFORCING)

#: Bramki, które profil adopcji wycisza, a profil enforcing egzekwuje
#: (REVIEW.md §5 P0, §9 pkt 1).
ADOPTION_WARN_ONLY = {
    "G1.static",
    "G2.cross_verify",
    "G2.test_sanity",
    "G2.diff_coverage",
    "G3.sast",
    "G3.sca",
}

#: Kopie wzorca względem korzenia monorepo. Gdy core jest zainstalowany
#: osobno (bez reszty monorepo), brakujące katalogi są pomijane.
COPY_DIRS = (
    "python/policy",
    "ts/policy",
    "csharp/policy",
    "web/gatekeeper_web/polityka_startowa",
)
COPIES = [d for d in COPY_DIRS if (MONOREPO / d).is_dir()]

STATIC = "G1.static"

#: Flagi G1.static, które profil enforcing włącza (REVIEW.md §5 P1). Każdą
#: czyta checker któregoś pack'a — przy plikach jego języka w diffie brak
#: narzędzia/configu kończy bramkę `error`.
ENFORCING_STATIC_FLAGS = {
    "require_ruff",
    "require_mypy",
    "require_tsc",
    "require_eslint",
    "require_dotnet_build",
}

#: Bramki, w których kopia może dołożyć własne klucze obok kluczy wzorca.
PACK_EXTENSIBLE_GATES = {STATIC}

needs_copies = pytest.mark.skipif(
    not COPIES, reason="core poza monorepo — brak kopii polityki do porównania"
)


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    return data


def without(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in data.items() if k not in keys}


def without_profile_knobs(data: dict[str, Any]) -> dict[str, Any]:
    """Profil bez tego, czym profile wolno się różnić: `warn_only`
    i `gates.G1.static.require_*`. Sekcja `G1.static`, która po odjęciu
    flag jest pusta, znika — wzorzec adopcji jej w ogóle nie ma."""
    result = without(data, "warn_only")
    gates = dict(result.get("gates") or {})
    if STATIC in gates:
        static = {
            k: v for k, v in (gates[STATIC] or {}).items() if not k.startswith("require_")
        }
        if static:
            gates[STATIC] = static
        else:
            del gates[STATIC]
    result["gates"] = gates
    return result


# ------------------------------------------------------------ profile we wzorcu


@pytest.mark.parametrize("profile", PROFILES)
def test_profil_przechodzi_lint(profile: str) -> None:
    """To samo, co `gatekeeper policy lint --policy core/policy/<profil>`."""
    policy = Policy.load(CORE_POLICY / profile)
    assert policy.lint(known_facts(), known_gate_ids()) == []
    assert policy.blocking and policy.thresholds


def test_profil_adopcji_wycisza_swieze_bramki() -> None:
    assert set(load(CORE_POLICY / ADOPTION)["warn_only"]) == ADOPTION_WARN_ONLY


def test_profil_enforcing_niczego_nie_wycisza() -> None:
    """Pusty `warn_only` — blokuje też `tests.pass_on_pre_change_code` (G2)."""
    policy = Policy.load(CORE_POLICY / ENFORCING)
    assert policy.warn_only == set()
    for gate_id in ADOPTION_WARN_ONLY:
        assert not policy.is_warn_only(gate_id)


@pytest.mark.parametrize("directory", [str(CORE_POLICY.relative_to(MONOREPO)), *COPIES])
def test_profile_roznia_sie_wylacznie_warn_only_i_require(directory: str) -> None:
    """Drugi profil to nie druga polityka — progi i reguły są wspólne."""
    base = MONOREPO / directory
    adoption, enforcing = load(base / ADOPTION), load(base / ENFORCING)
    assert without_profile_knobs(adoption) == without_profile_knobs(enforcing)


@pytest.mark.parametrize("directory", [str(CORE_POLICY.relative_to(MONOREPO)), *COPIES])
def test_profil_enforcing_wymaga_narzedzi_g1_static(directory: str) -> None:
    """Brak tsconfiga/configu eslinta/`.csproj`/mypy to w produkcji `error`."""
    static = (load(MONOREPO / directory / ENFORCING).get("gates") or {}).get(STATIC) or {}
    assert {k for k, v in static.items() if k.startswith("require_") and v is True} == (
        ENFORCING_STATIC_FLAGS
    )


def test_profil_adopcji_we_wzorcu_nie_wlacza_require() -> None:
    """Adopcja zostaje bez zmian: wzorzec nie ma sekcji `G1.static`, więc
    checkery biorą swoje domyślne (`require_ruff` tak, reszta nie)."""
    assert STATIC not in (load(CORE_POLICY / ADOPTION).get("gates") or {})


# ------------------------------------------------------------ kopie w pack'ach


@needs_copies
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("directory", COPIES)
def test_kopia_ma_te_same_wspolne_klucze(directory: str, profile: str) -> None:
    template = load(CORE_POLICY / profile)
    copy = load(MONOREPO / directory / profile)

    assert set(copy) == set(template), "kopia dodaje albo gubi klucz najwyższego poziomu"
    assert without(copy, "gates") == without(template, "gates")


@needs_copies
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("directory", COPIES)
def test_kopia_nie_zmienia_konfiguracji_bramek_wzorca(directory: str, profile: str) -> None:
    """Pack może dołożyć bramkę do `gates:`, ale nie przestawić knobs core'a."""
    template_gates = load(CORE_POLICY / profile).get("gates") or {}
    copy_gates = load(MONOREPO / directory / profile).get("gates") or {}

    for gate_id, config in template_gates.items():
        if gate_id in PACK_EXTENSIBLE_GATES:
            # Pack dokłada własne knobs obok kluczy wzorca, ale ich nie przestawia.
            copy_config = copy_gates.get(gate_id) or {}
            assert {k: copy_config.get(k) for k in config} == config, gate_id
        else:
            assert copy_gates.get(gate_id) == config, gate_id


@needs_copies
@pytest.mark.parametrize("name", ["exceptions.yaml", "scope_map.yaml"])
@pytest.mark.parametrize("directory", COPIES)
def test_pozostale_pliki_polityki_sa_zgodne(directory: str, name: str) -> None:
    assert load(MONOREPO / directory / name) == load(CORE_POLICY / name)
