# Plan: G1.complexity — bramka złożoności cyklomatycznej

Dokument towarzyszący [`PLAN.md`](PLAN.md) i [`uncle-bob-gauntlet.md`](../uncle-bob-gauntlet.md). README mówi, co silnik dziś dostarcza (G0–G3). Ten dokument mówi, jak dodać jeden nowy gate ID: twardy limit kształtu funkcji w diffie, bez czytania implementacji.

Nie jest to CRAP ani mutacja. CRAP wymaga pokrycia (`G2.diff_coverage`) i zostaje osobnym kamieniem (§8). Mutacja jest już w `PLAN.md` §G2.

## 0. Streszczenie

Nowa bramka **`G1.complexity`** w core, jako agregator poziomu 1 (jak `G1.static`). Mierzy **McCabe’a na poziomie metody**, tylko na funkcjach, których ciało przecina diff. Próg z gauntletu Uncle Boba: **M > 10 blokuje** (zakres 11–20 = złożona; 21+ i tak wpada).

Pierwsza dostawa: **Python, stdlib `ast`, zero nowego narzędzia**. TS/C# — `skipped`, nie błąd, dopóki pack nie zarejestruje analizatora (ten sam kontrakt co brak `TestToolchain` dla G2).

Werdykt architektoniczny (odrzuty w §1):

| Kandydat | Werdykt |
|---|---|
| Wpisać w `G1.static` / ruff C901 | **nie** — inny fakt, inna semantyka, nie pokrywa TS/C# |
| `lizard` w core na wszystkie języki | **nie w v1** — parser regexowy, liczby rozjadą się z radon/eslint |
| Nowy gate ID + `ComplexityAnalyzer` per język | **tak** |

## 1. Dlaczego osobna bramka, nie G1.static

`G1.static` łapie defekt (nieistniejące API, martwy import, błąd kompilacji). Złożoność cyklomatyczna **nie jest defektem** — to kształt, który mnoży ścieżki do przetestowania i chowa błędy agenta w zagnieżdżeniu. Uncle Bob trzyma to obok testów i CRAP, nie obok lintera.

Konkretne konflikty przy wrzuceniu do `G1.static`:

- `static.high_severity_count` miesza „mypy error” z „funkcja ma 12 gałęzi”.
- `warn_only: [G1.static]` wyłączyłoby metrykę razem z typami — REVIEW.md i tak ostrzega, że static tylko ostrzega; nowa bramka ma dostać **własną** dźwignię.
- Ruff C901 nie jest w `select` python-packu (`E,F,I,UP,B,SIM`); `C` nie jest w `RUFF_HIGH_PREFIXES`. Włączenie C901 w ruffie i tak nie da faktu `complexity.max` dla polityki.
- `only_changed_lines(..., context=3)` jest za ciasne: zmiana jednej linii w środku 80-liniowego `if`-drzewa musi liczyć **całą funkcję**, nie ±3 linie.

## 2. Architektura

### 2.1 Poziom 1 — gate ID w core

```
gatekeeper.gates  →  G1.complexity  (ComplexityGuard)
```

Pliki core:

- `gatekeeper_core/gates/g1_complexity.py` — agregator, kopia struktury `g1_static.py`
- `gatekeeper_core/core/plugins.py` — nowy protokół
- `core/pyproject.toml` — wpis w `[project.entry-points."gatekeeper.gates"]`
- `core/gatekeeper_core/core/orchestrator.py` — zależność

```
DEPENDENCIES["G1.complexity"] = ("G0.scope", "G0.provenance")
```

Ta sama fala co `G1.static` / `G1.deps` / G3. **G2 nie czeka** na złożoność — to niezależny wymiar. Budżet: **30 s** (czysty AST, bez podprocesu w v1). Docs-only: pomijana przez istniejący `FAST_PATH_GATES`.

Brak zainstalowanego analizatora → `status="skipped"`, faktów zerowych, komunikat `brak ComplexityAnalyzer`. To nie jest `error`: core-only (jak dziś G2 bez python-packu) musi nadal kalibrować sekrety i deps.

### 2.2 Poziom 2 — analizator w packu

Nowa grupa entry points, analogicznie do `gatekeeper.static_checkers`:

```
gatekeeper.complexity_analyzers  →  ComplexityAnalyzer
```

```python
@dataclass
class MethodComplexity:
    file: str
    name: str            # kwalifikowana nazwa, np. "mod.Klasa.metoda"
    lineno: int
    end_lineno: int
    complexity: int      # McCabe M(m)
    nloc: int            # linie niepuste w ciele; fakt poboczny, nie próg v1

@dataclass
class ComplexityOutcome:
    methods: list[MethodComplexity] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

class ComplexityAnalyzer(Protocol):
    analyzer_id: str
    languages: tuple[str, ...]

    def empty_facts(self) -> dict[str, Any]: ...

    def analyze(
        self, change: ChangeContext, config: dict[str, Any], gate_id: str, budget_s: float
    ) -> ComplexityOutcome: ...
```

Agregator:

1. Enumeruje analizatory.
2. Sumuje `methods`.
3. Zostawia tylko metody, których `[lineno, end_lineno]` przecina `change.added_lines(file)` — **nie** `only_changed_lines`.
4. Pomija pliki testowe, chyba że `include_tests: true`.
5. Wystawia fakty i findings.

Python-pack:

```
[project.entry-points."gatekeeper.complexity_analyzers"]
python = "gatekeeper_python.adapters.complexity:PythonComplexityAnalyzer"
```

`languages = ("python",)`. Pliki `.pyi` pomijamy (brak ciała).

## 3. Co liczymy — McCabe, nie slajd „na palcach”

Slajd 10b w `uncle-bob-gauntlet.md` podaje zagnieżdżone `if` jako `comp = 4` i **ignoruje** `&&`. Wzór ze slajdu 10 (`M = E − N + 2P`) oraz radon/mccabe **liczą krótkie spięcie** `and`/`or` jako dodatkowe krawędzie. Ten plan idzie za wzorem, nie za uproszczeniem w przykładzie JS.

Kanoniczna semantyka v1 = **radon `cc_visit` / flake8-mccabe** (to samo, czego użyje CRAP w §8):

| Węzeł AST | ΔM |
|---|---|
| start funkcji / lambdy | 1 |
| `If`, `IfExp`, `For`, `AsyncFor`, `While`, `Assert`, `ExceptHandler` | +1 |
| `BoolOp` (`and`/`or`) | +(n_values − 1) |
| comprehension (`List`/`Set`/`Dict`/`Generator`) | +1 oraz +1 za każdy `if` w generatorze |
| `match` / każdy `case` poza `_` | +1 za `Match`, +1 za każdy nie-wildcardowy case |
| `With` / `AsyncWith` | 0 (jak mccabe — `with` to nie decyzja) |

Zagnieżdżone `FunctionDef` liczymy **osobno**. Lambda dodaje swoją M do otaczającej funkcji (jak mccabe), nie jako osobna metoda.

Fixture ze slajdu 10b, przepisany na Pythona:

```python
def raty(kwota, lata, dochod):
    if kwota > 0:
        if lata >= 5 and lata <= 30:   # If + BoolOp => +2
            if dochod > kwota * 3:
                return kwota / (lata * 12)
    return None
# M = 1 + 3×If + 1×BoolOp = 5
```

Strażnik:

```python
def raty(kwota, lata, dochod):
    if not poprawne_wejscie(kwota, lata, dochod):
        return None
    return kwota / (lata * 12)
# M = 2
```

Golden test **musi** asertować 5 i 2, nie 4 i 1. W komentarzu testu odnotować rozjazd ze slajdem.

## 4. Zakres: tylko zmienione metody produkcyjne

Bez tego pierwszy przebieg na starym repo umiera — ta sama reguła co `only_changed_lines` w G1.static, tylko jednostką jest metoda, nie linia.

Reguła przecięcia:

```
changed = change.added_lines(method.file)
intersects = bool(changed & set(range(method.lineno, method.end_lineno + 1)))
```

Usunięta funkcja (jest w base, nie ma w head) — **nie mierzymy**. Interesuje kształt po zmianie.

Nowa funkcja — zawsze. Istniejąca funkcja, której ciało nie ruszone, nawet w tym samym pliku — cisza. To jest test kalibracyjny `zlozonosc-poza-diffem`.

Testy (`change.is_test_file`) — **domyślnie poza zakresem**. Table-driven test z wieloma `if` nie jest długiem produkcyjnym. Konfig:

```yaml
gates:
  G1.complexity:
    max_method: 10
    include_tests: false
```

`max_method` żyje w configu bramki (żeby finding znał próg), a polityka **dodatkowo** ma próg na fakcie — podwójne źródło prawdy jest świadome: finding potrzebuje liczby do scenariusza awarii, polityka decyduje o BLOCK vs warn.

## 5. Fakty, znaleziska, polityka

### 5.1 Fakty (deklarowane, lintowane)

```
complexity.max                      # max M wśród zmierzonych metod
complexity.over_threshold_count     # ile metod ma M > max_method
complexity.methods_measured
complexity.python_files_checked     # empty_facts() python-analizatora
```

Brak zmierzonych metod (diff bez Pythona, albo same testy) → fakty `0` / `0` / `0`, status `pass`. Próg `max` na zerze nie strzela (`Threshold.violation` wymaga liczby; 0 ≤ 10).

### 5.2 Finding

Jedno znalezisko **per metoda** powyżej progu, nie per plik.

| Pole | Wartość |
|---|---|
| `gate` | `G1.complexity` |
| `rule_id` | `complexity.too_high` |
| `severity` | `high` gdy M ≥ 21, `medium` gdy 11–20 |
| `title` | `{name} ma złożoność cyklomatyczną {M} (próg {max_method})` |
| `failure_scenario` | obowiązkowy, konkretny — patrz niżej |
| `file` / `line` | ścieżka + `lineno` def |
| `evidence` | `{complexity, threshold, nloc, snippet}` — snippet = pierwsza linia sygnatury, **nie** całe ciało (fingerprint bez numeru linii przeżyje rebase; całe ciało by go psuło) |

Scenariusz awarii (wymóg `Finding.__post_init__`):

> Funkcja `{name}` ma {M} liniowo niezależnych ścieżek (próg {T}). Agent łatwo zostawia nieprzetestowaną gałąź w zagnieżdżeniu; rozbij warunki na strażników albo nazwane predykaty, zanim testy i mutacja będą w stanie to pokryć.

### 5.3 Polityka — v1 **egzekwuje**, nie ostrzega

Świadome odejście od `warn_only` na G1.static. Ta bramka jest tania, deterministyczna i ma niski fałszywy alarm przy zakresie z §4. REVIEW.md: „nie budować G4, dopóki G1/G3 nie blokują”. Nie dokładać kolejnej bramki, która tylko mruga.

We **wszystkich** `policy/gates.yaml` (core + python + ts + csharp):

```yaml
thresholds:
  complexity.max:
    max: 10
    message: >-
      Funkcja w diffie przekracza złożoność cyklomatyczną 10 —
      rozbij na strażników (Uncle Bob / G1.complexity).
  complexity.over_threshold_count:
    max: 0
    message: "W diffie są funkcje powyżej progu złożoności."

gates:
  G1.complexity:
    max_method: 10
    include_tests: false
```

`warn_only` **nie** dostaje `G1.complexity`.

Wyjątki: istniejący mechanizm `exceptions.yaml` (właściciel, powód, data, fingerprint). Agent nie dodaje wyjątków — CODEOWNERS na `policy/` już to pokrywa.

Kalibracja Uncle Boba (slajd 22): ścieżki `auth/` / `payments/` nie dostają osobnego progu w v1 — 10 wszędzie. Zaostrzenie do 7 na `paths_match` wymagałoby progu zależnego od ścieżki, którego `Threshold` dziś nie umie. To v1.1, nie blocker.

## 6. Implementacja Pythona (kamień 1)

Nowy moduł `python/gatekeeper_python/adapters/complexity.py`.

- Wejście: `change.effective_files` o `language == "python"`, nie-test, nie-`.pyi`.
- Treść: `change.file_at(change.head_sha, path)` — **head**, nie working tree (spójne z innymi bramkami na SHA).
- `ast.parse`; `SyntaxError` na pojedynczym pliku → finding `complexity.parse_error` wagi `low` i idziemy dalej (G1.static i tak zablokuje składnię, gdy nie jest w `warn_only`). Całkowita awaria analizatora (`error` w outcome) tylko przy wyjątku poza parsowaniem.
- Visitor: jeden `ast.NodeVisitor` z licznikiem na stosie (wejście w `FunctionDef`/`AsyncFunctionDef` pushuje ramkę). Bez rekurencyjnego `ast.walk` przez zagnieżdżone funkcje w ramce rodzica.
- `end_lineno` z AST (3.8+; wymagamy 3.12).

Bez `radon` na `[gates]`. Zależność tylko po to, by dostać jedną liczbę, jest gorsza niż 80 linii visitatora i golden test. Komentarz w module: „zgodne z radon cc_visit; nie importujemy radona”.

Testy jednostkowe visitatora **nie** potrzebują gita — czysta funkcja `measure(source: str) -> list[MethodComplexity]`. Testy bramki używają `Repo` z `python/tests/conftest.py`.

## 7. Testy i kalibracja

### 7.1 Jednostkowe (python-pack)

| Test | Asercja |
|---|---|
| slajd 10b zagnieżdżone `if` + `and` | M = 5 |
| slajd 10b strażnik | M = 2 |
| sam `return 1` | M = 1 |
| `if a or b or c` | M = 3 |
| `match` z dwoma case + `_` | M = 3 (1 + Match + 2 case − wildcard) |
| zagnieżdżona funkcja | dwie metody, osobne M |
| przecięcie z diffem | ruszone `foo` raportowane, nietknięte `bar` w tym samym pliku milczy |
| plik testowy | zero metod przy `include_tests: false` |
| M = 11 | finding + `complexity.max == 11`, status `fail` |
| M = 10 | brak finding, `pass` |
| M = 21 | severity `high` |

Próg jest ostry: **10 przechodzi, 11 nie**. Uncle Bob „1–10 prosta”.

### 7.2 Kalibracja (python-pack `calibration/`)

Trzy fixture’y, wzorowane na `test-bez-asercji` (małe repo `base/` + `head/`):

| Nazwa | Oczekiwanie |
|---|---|
| `zlozonosc-powyzej-progu` | funkcja z 11+ gałęziami w head → `verdict: BLOCK`, `blocking_rules: [complexity.max]` |
| `zlozonosc-straznik` | ta sama logika, strażnik, M ≤ 10 → `PASS` (wymaga gitleaks jak `czysty-pr`) |
| `zlozonosc-poza-diffem` | w pliku siedzi stara funkcja M = 25, diff rusza tylko nową M = 1 → `PASS` |

Core-only `calibration/cases.yaml` **nie** dostaje tych przypadków — bez python-packu bramka jest `skipped` i nie zablokuje.

`czysty-pr` w core musi pozostać PASS: skipped complexity nie psuje werdyktu.

## 8. Świadomie poza v1

| Temat | Powód odłożenia |
|---|---|
| **CRAP** | `CRAP = comp² × (1−cov)³ + comp` potrzebuje `G2.diff_coverage`; dziś coverage jest `warn_only` i tylko Python. Nowa bramka `G2.crap` po zdjęciu `warn_only` z coverage |
| **NLOC / rozmiar funkcji** | Uncle Bob wymienia obok McCabe; fakt `nloc` zbieramy od razu, progu nie włączamy, dopóki nie skalibrujemy na własnych PR-ach (PLAN.md §6) |
| **TS/JS** | eslint `complexity` albo visitor na TypeScript Compiler API — osobne zlecenie, jak G2 dla TS |
| **C#** | ten sam helper Roslyn co `PLAN-G2.md` (`gatekeeper-cs-helper complexity`) — nie budować drugiego parsera C# |
| **Delta M** (było 8, jest 12) | v1 ocenia kształt po zmianie, nie przyrost; delta kusi do gier („rozbijam jedną funkcję na trzy po M=9”) |
| **Próg per ścieżka** | wymaga rozszerzenia `Threshold`; auth/payments zostają na G5 `paths_match` |

## 9. Kolejność prac

Jeden PR do core, drugi do python-packu. Core pierwszy — pack nie ma o co zahaczyć entry pointu.

**PR A — core (sam przechodzi testy core bez packa):**

1. Protokół `ComplexityAnalyzer` + `ComplexityOutcome` + `MethodComplexity` w `plugins.py`.
2. `gates/g1_complexity.py` + rejestracja w `pyproject.toml`.
3. `DEPENDENCIES` w orchestratorze; dopisać do `NOT_CHECKED` skreślenie „metryki McCabe” i zostawić CRAP.
4. Domyślna polityka core: progi z §5.3.
5. Test agregatora na fałszywym analizatorze (wstrzyknięcie przez monkeypatch `entry_points` albo testowy plugin w `tests/`) — brak analizatora = skipped; analizator z dwiema metodami = fakty; `error` z analizatora = `GateResult.status=error`.
6. `test_orchestrator` / lint polityki: nowy gate ID i fakty znane.

**PR B — python-pack:**

1. Visitor + `PythonComplexityAnalyzer`.
2. Entry point.
3. Testy §7.1.
4. Fixture’y kalibracyjne §7.2; `policy/gates.yaml` packu — te same progi.
5. `python/README.md` — jedna linia: pack dostarcza też `G1.complexity`.

**PR C — ts/csharp policy only:** skopiować progi do ich `gates.yaml`, żeby mixed-stack nie miał rozjazdów. Analizatorów nie dodawać. `gatekeeper policy lint` musi przejść: fakty `complexity.*` deklaruje core, nie pack.

Nie ruszać `warn_only`. Nie ruszać G4.

## 10. Ryzyka

| Ryzyko | Przeciwdziałanie |
|---|---|
| Fałszywe alarmy na wygenerowanym kodzie | `effective_files` już wycina `*.g.cs`, lockfile, min.js; generated globs zostają |
| Agent obchodzi próg trzema funkcjami M=9 zamiast jednej M=20 | akceptowalne w v1 — to *właśnie* pożądany kształt; gauntlet chce małych funkcji |
| Rozjazd liczb vs ruff C901 vs eslint | v1 nie woła ruffa; golden test vs radon-semantyka; w README bramki jedna tabela „co liczymy” |
| `end_lineno` a dekoratory | przecięcie z diffem od `lineno` (linia `def`), nie od dekoratora — zmiana samego dekoratora nie liczy złożoności ciała, i tak trzeba |
| Składnia 3.12 (`type` params) | `ast.parse` w 3.12 to umie; test z `def f[T](x: T)` M=1 |
| Core testy vs 11. gate ID | miejsca z hardcodowaną dziesiątką (`PODSUMOWANIE.md`, mixed-venv w README, testy `entry_points`) — zaktualizować do 11 |

## 11. Kryteria ukończenia v1

- `gatekeeper policy facts` (core+python) zna `complexity.max`.
- `zlozonosc-powyzej-progu` → `BLOCK`; `zlozonosc-straznik` i `zlozonosc-poza-diffem` → `PASS`.
- Mixed-venv (core+python+ts+csharp): 11 gate ID; diff TS-only nie pada na complexity (skipped/pass).
- `ruff` + `mypy --strict` + `pytest` zielone w `core/` i `python/`.
- Żadnego nowego binarium, żadnego radona/lizard w zależnościach.

Czas: PR A ~0,5 dnia, PR B ~1 dzień, PR C ~godzina. Razem poniżej dwóch dni kalendarzowych — ten sam rząd wielkości co „najmniejszy sensowny wycinek” z PLAN.md §10, nie jak G2 dla C#.
