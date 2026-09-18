"""G2 — pokrycie różnicowe, branch-aware (TOOLS.md §4.5).

Fakt `coverage.diff_ratio`: jaki procent nowych/zmienionych linii **produkcyjnych**
w diffie wykonuje **cały** zestaw testów repo — nie tylko nowe testy, to różni tę
bramkę od `G2.cross_verify`. `if` z pustą gałęzią błędu nie liczy się jako pokryty
(`--branch-coverage` w `diff-cover`, patrz `adapters/coverage.py`).

Bramka jest wyłącznie faktograficzna, jak `G0.scope`/`G0.provenance`: nigdy nie
ustawia `status="fail"`. Próg (`coverage.diff_ratio < 0.80`) to sprawa polityki
(`policy/gates.yaml`), nie osądu bramki — ten sam podział odpowiedzialności co
wszędzie indziej w tym repo (facts osobno od decyzji, TOOLS.md §1.1).

Ta bramka sama nie ma logiki językowej — jak `G2.cross_verify`/`G2.test_sanity`,
jest agregatorem poziomu 1 (`core/plugins.py`) po zainstalowanych
`TestToolchain` (`gatekeeper.test_toolchains`); `produce_coverage_report()`
każdego toolchaina robi całą robotę (uruchamia testy repo pod narzędziem
pokrycia i przecina wynik z diffem — dla Pythona: `coverage.py` + `diff-cover`,
patrz `testing/toolchain.py`).
"""

from __future__ import annotations

import time
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from ..adapters.base import ToolFailed, ToolMissing
from ..core.change import ChangeContext
from ..core.finding import GateResult
from ..core.plugins import TestToolchain, toolchain_languages
from . import Gate, register

TOOLCHAIN_GROUP = "gatekeeper.test_toolchains"


def _installed_toolchains() -> list[TestToolchain]:
    return [ep.load()() for ep in entry_points(group=TOOLCHAIN_GROUP)]


@register
class DiffCoverage(Gate):
    id = "G2.diff_coverage"
    name = "Pokrycie różnicowe (branch-aware)"
    budget_s = 600.0
    facts = (
        "coverage.diff_ratio",
        "coverage.total_lines",
        "coverage.covered_lines",
        "coverage.tool_available",
    )

    def run(self, change: ChangeContext) -> GateResult:
        started = time.monotonic()
        facts = _empty_facts()
        require_tool = bool(self.config.get("require_tool", True))

        toolchains = _installed_toolchains()
        touched_production = False
        covered_total = 0
        lines_total = 0
        notes: list[str] = []

        for toolchain in toolchains:
            languages = toolchain_languages(toolchain)
            production = [
                f
                for f in change.files
                if not f.test and not f.generated and f.status != "D" and f.language in languages
            ]
            if not production:
                continue
            touched_production = True

            try:
                result = toolchain.produce_coverage_report(change, self.config)
            except (ToolMissing, ToolFailed) as exc:
                facts["coverage.tool_available"] = False
                if not require_tool:
                    notes.append(f"narzędzie niedostępne, `require_tool: false`: {exc}")
                    continue
                return self.result(
                    status="error",
                    duration_s=time.monotonic() - started,
                    facts=facts,
                    message=str(exc),
                )

            production_files = {
                path: cov for path, cov in result.files.items() if not change.is_test_file(path)
            }
            covered_total += sum(cov.covered for cov in production_files.values())
            lines_total += sum(cov.total for cov in production_files.values())

        if not touched_production:
            return self.result(
                status="skipped",
                duration_s=time.monotonic() - started,
                facts=facts,
                message=_nothing_to_measure(change, toolchains),
            )

        facts["coverage.covered_lines"] = covered_total
        facts["coverage.total_lines"] = lines_total
        facts["coverage.diff_ratio"] = covered_total / lines_total if lines_total else None

        if lines_total == 0:
            message = (
                "brak zmierzonych linii produkcyjnych w diffie — plik nowy/zmieniony "
                "nie pojawił się w raporcie coverage (prawdopodobnie nieużyty przez "
                "żaden test; patrz uwaga w adapters/coverage.py)"
            )
        else:
            message = (
                f"{covered_total}/{lines_total} nowych linii produkcyjnych pokrytych testami "
                f"({100 * covered_total / lines_total:.0f}%)"
            )
        if notes:
            message += " · " + " · ".join(notes)

        return self.result(
            status="pass",
            duration_s=time.monotonic() - started,
            facts=facts,
            message=message,
        )


def _plural(count: int, forms: tuple[str, str, str]) -> str:
    """`count` z polską odmianą: 1 plik, 2 pliki, 5 plików."""
    if count == 1:
        form = forms[0]
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        form = forms[1]
    else:
        form = forms[2]
    return f"{count} {form}"


def _nothing_to_measure(change: ChangeContext, toolchains: list[TestToolchain]) -> str:
    """Dlaczego naprawdę nie było czego mierzyć.

    Jedno zdanie („zmiana nie dotyka kodu produkcyjnego w żadnym zainstalowanym
    języku") opisywało trzy różne sytuacje naraz: pusty diff, diff złożony
    wyłącznie z testów/plików generowanych/usunięć i diff w języku, którego nie
    obsługuje żaden zainstalowany toolchain. Operator dostawał sugestię, że
    brakuje mu packa językowego, również wtedy, gdy po prostu nie było zmiany.

    Bramka i tak jest `skipped` — rozróżnienie zmienia nie werdykt, tylko to,
    czy da się z komunikatu wyjść z właściwym wnioskiem.
    """
    covered = sorted({lang for tc in toolchains for lang in toolchain_languages(tc)})

    if not change.files:
        return (
            "diff jest pusty — wersja oceniana nie różni się od bazowej, "
            "więc nie ma czego mierzyć"
        )

    if not covered:
        # Nie ma czym mierzyć, a nie: nie ma czego. To jedyny wariant, w którym
        # winna jest instalacja, więc nie mieszamy go z resztą.
        return (
            "nie zainstalowano żadnego packa językowego "
            "(`gatekeeper.test_toolchains` nie ma ani jednego wpisu), "
            "więc nie ma czym mierzyć pokrycia"
        )

    support = f"obsługiwane języki: {', '.join(covered)}"

    skipped_kinds: list[str] = []
    tests = sum(1 for f in change.files if f.test)
    generated = sum(1 for f in change.files if f.generated)
    deleted = sum(1 for f in change.files if f.status == "D" and not f.test and not f.generated)
    if tests:
        skipped_kinds.append(_plural(tests, ("testowy", "testowe", "testowych")))
    if generated:
        skipped_kinds.append(_plural(generated, ("generowany", "generowane", "generowanych")))
    if deleted:
        skipped_kinds.append(_plural(deleted, ("usunięty", "usunięte", "usuniętych")))

    # Pliki produkcyjne, które istnieją, ale żaden toolchain ich nie obsługuje —
    # to jedyna sytuacja, w której doinstalowanie packa cokolwiek zmieni.
    foreign = sorted(
        {
            f.language or f"bez rozpoznanego języka ({Path(f.path).suffix or 'brak rozszerzenia'})"
            for f in change.files
            if not f.test and not f.generated and f.status != "D" and f.language not in covered
        }
    )

    parts = ["w diffie " + _plural(len(change.files), ("plik", "pliki", "plików"))]
    if skipped_kinds:
        parts.append("w tym " + ", ".join(skipped_kinds))
    if foreign:
        parts.append(
            "kod produkcyjny tylko w językach bez zainstalowanego toolchaina: "
            + ", ".join(foreign)
        )
    return (
        "zmiana nie dotyka kodu produkcyjnego w żadnym zainstalowanym języku — "
        f"{'; '.join(parts)} ({support})"
    )


def _empty_facts() -> dict[str, Any]:
    return {
        "coverage.diff_ratio": None,
        "coverage.total_lines": 0,
        "coverage.covered_lines": 0,
        "coverage.tool_available": True,
    }
