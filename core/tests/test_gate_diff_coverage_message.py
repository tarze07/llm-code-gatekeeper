"""Powód pominięcia `G2.diff_coverage` — czy da się z niego wyjść z wnioskiem.

Jedno zdanie opisywało wcześniej trzy różne sytuacje naraz i sugerowało
brakujący pack językowy również wtedy, gdy diff był po prostu pusty. Bramka
w każdej z nich jest `skipped`, więc tylko komunikat odróżnia „nie było
zmiany" od „nie mam czym tego zmierzyć".
"""

from __future__ import annotations

from gatekeeper_core.core.change import ChangedFile
from gatekeeper_core.gates.g2_diff_coverage import _nothing_to_measure, _plural


class FakeToolchain:
    def __init__(self, *languages: str) -> None:
        self.languages = languages


class FakeChange:
    def __init__(self, *files: ChangedFile) -> None:
        self.files = list(files)


PYTHON = [FakeToolchain("python")]


def test_pusty_diff_mowi_o_pustym_diffie() -> None:
    """To był przypadek, który mylił najbardziej: brak zmiany, a komunikat
    kierował operatora na brakujący pack językowy."""
    komunikat = _nothing_to_measure(FakeChange(), PYTHON)

    assert "diff jest pusty" in komunikat
    assert "nie różni się od bazowej" in komunikat
    assert "zainstalowan" not in komunikat


def test_brak_packa_jezykowego_mowi_o_instalacji() -> None:
    komunikat = _nothing_to_measure(FakeChange(ChangedFile(path="a.py", status="M")), [])

    assert "nie zainstalowano żadnego packa językowego" in komunikat
    # „nie ma czym", nie „nie ma czego" — winna jest instalacja, nie zmiana.
    assert "nie ma czym mierzyć" in komunikat


def test_sam_diff_testowy_wylicza_co_odpadlo() -> None:
    komunikat = _nothing_to_measure(
        FakeChange(
            ChangedFile(path="tests/test_a.py", status="M", test=True),
            ChangedFile(path="tests/test_b.py", status="A", test=True),
            ChangedFile(path="package-lock.json", status="M", generated=True),
            ChangedFile(path="stare.py", status="D"),
        ),
        PYTHON,
    )

    assert "w diffie 4 pliki" in komunikat
    assert "2 testowe" in komunikat
    assert "1 generowany" in komunikat
    assert "1 usunięty" in komunikat
    assert "obsługiwane języki: python" in komunikat


def test_jezyk_bez_toolchaina_jest_nazwany() -> None:
    """Jedyny wariant, w którym doinstalowanie packa cokolwiek zmieni."""
    komunikat = _nothing_to_measure(
        FakeChange(
            ChangedFile(path="main.go", status="M"),
            ChangedFile(path="skrypt.rb", status="A"),
        ),
        PYTHON,
    )

    assert "bez zainstalowanego toolchaina: go, ruby" in komunikat
    assert "obsługiwane języki: python" in komunikat


def test_plik_bez_rozpoznanego_jezyka_nie_znika_z_komunikatu() -> None:
    komunikat = _nothing_to_measure(FakeChange(ChangedFile(path="Makefile", status="M")), PYTHON)

    assert "bez rozpoznanego języka" in komunikat
    assert "brak rozszerzenia" in komunikat


def test_odmiana_liczebnikow() -> None:
    """Komunikat czyta człowiek, więc `2 plików` jest usterką, nie drobiazgiem."""
    formy = ("plik", "pliki", "plików")

    assert [_plural(n, formy) for n in (1, 2, 5, 12, 22, 25)] == [
        "1 plik",
        "2 pliki",
        "5 plików",
        "12 plików",
        "22 pliki",
        "25 plików",
    ]
