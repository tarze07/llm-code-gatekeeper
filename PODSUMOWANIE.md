# Podsumowanie: podział llm-code-gatekeeper na 4 repozytoria

> **Status (2026-09-03): fizyczny podział cofnięty, podział logiczny został.** Cztery repozytoria opisane niżej zostały scalone z powrotem w jedno (`github.com/tarze07/llm-code-gatekeeper`) — `core/`, `python/`, `ts/`, `csharp/` są dziś katalogami, nie osobnymi remote'ami. Wszystko, co ten dokument mówi o **architekturze** (dwupoziomowe entry points, jeden gate ID niezależnie od liczby packów, granica core↔pack), zostaje w mocy bez zmian: packi nadal są osobnymi pakietami Pythona, nadal instalowanymi osobno, a core nadal nie importuje żadnego z nich. Zmieniła się wyłącznie liczba repozytoriów na GitHubie i URL instalacyjny (`git+…/llm-code-gatekeeper.git#subdirectory=<pack>`). Historia każdego z czterech repo zachowana w scaleniu (`git log -- core/`).

## Punkt wyjścia

`llm-code-gatekeeper` było jednym monorepo Pythona obsługującym trzy języki docelowe (Python, TS/JS, C#) przez wspólny silnik oceny PR-ów (`gatekeeper run` → `PASS`/`PASS-WITH-REVIEW`/`BLOCK`). Padło pytanie: czy da się to rozdzielić na osobne repozytoria per język, bez rozbijania jednego logicznego zestawu bramek (`G0.*`–`G3.*`) na duplikaty.

## Decyzja architektoniczna

**4 repozytoria**, nie 3: wspólny rdzeń (`llm-code-gatekeeper-core`) + trzy cienkie pack'i językowe. Złożone przez dwupoziomowy mechanizm [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) (`importlib.metadata`), nie przez import wprost:

- **Poziom 1** (`gatekeeper.gates`) — core rejestruje przez tę grupę własne 11 bramek jako klasy. Pack językowy mógłby w przyszłości dodać nową bramkę bez patcha w core.
- **Poziom 2** (`gatekeeper.static_checkers`, `gatekeeper.dep_ecosystems`, `gatekeeper.test_toolchains`, `gatekeeper.semgrep_rule_packs`) — dostawcy *wewnątrz* jednej logicznej bramki-agregatora (np. `G1.static` pętla po zainstalowanych `StaticChecker`ach). Dzięki temu `G1.static`/`G1.deps`/`G3.sca`/`G3.sast`/`G2.*` zostają **jednym** gate ID niezależnie od liczby zainstalowanych pack'ów — wymóg `policy/gates.yaml` i orchestratora, które referencują gate ID jako stringi.

Odkrycie po drodze, które zmieniło pierwotny plan: manifest+rejestr+typosquat+SCA dla PyPI/npm/NuGet (`deps/*`) nie mają w sobie żadnej logiki specyficznej dla kompilatora/testów danego języka — to czysta infrastruktura bramek `G1.deps`/`G3.sca`, które same są core-owe. Rozbicie tego na 3 kopie po pack'ach tylko duplikowałoby te same parsery manifestów. Zostało w całości w core.

## Wynik

| Repo | Zawartość | Pakiet Python |
|---|---|---|
| [`llm-code-gatekeeper-core`](core/) | silnik, CLI, 11 bramek jako dispatch, PyPI/npm/NuGet | `gatekeeper_core` |
| [`llm-code-gatekeeper-python`](python/) | ruff/mypy, testy przez `ast` (G2.*), reguły SAST Pythona | `gatekeeper_python` |
| [`llm-code-gatekeeper-ts`](ts/) | tsc/eslint, reguły SAST TS/JS | `gatekeeper_ts` |
| [`llm-code-gatekeeper-csharp`](csharp/) | `dotnet build`, reguły SAST C# | `gatekeeper_csharp` |

Historia git zachowana (`git filter-repo`, nie snapshot) dla każdego pliku, który miał realnych poprzedników w monolicie — i przeżyła też późniejsze scalenie z powrotem do jednego repo (patrz nota na górze). Wszystkie cztery leżą jako rodzeństwo w [README.md](README.md).

## Weryfikacja

Zainstalowane osobno **i** wszystkie 4 naraz w jednym środowisku (świeże klony z GitHuba, nie z dysku roboczego):

- każde repo z osobna: `ruff` + `mypy --strict` + `pytest` + `gatekeeper policy lint` + `gatekeeper calibrate` — zielone
- core dodatkowo: `semgrep --test` (reguła uniwersalna) + 5/5 przypadków kalibracyjnych obejmujących PyPI i npm
- python/ts/csharp: `semgrep --test` na regułach SAST specyficznych dla języka — 6/4/4 zielone
- wszystkie 4 razem: `entry_points(group="gatekeeper.gates")` daje spójny zestaw 11 gate ID; diff mieszający Python+TS+C# w jednym PR-ze przechodzi przez `gatekeeper run` z wynikiem PASS, `G1.static`/`G3.sast` realnie uruchamiają checker/regułę każdego języka jednym wywołaniem

## Znane luki naprawione po drodze (nie zamiecione pod dywan)

- `adapters/semgrep.py::RULES_DIR` i `deps/typosquat.py::popular_packages()` wspinały się po `__file__.parent` — nie przeżyłyby instalacji z wheela. Zamienione na `importlib.resources`.
- Pierwsza ekstrakcja core'a pominęła `tests/test_gate_deps.py`/`test_gate_sca.py` (wyglądały na „mieszane", a są w pełni core-owe) i dane typosquatu (`gatekeeper/data/*.txt`) — dodane, core ma teraz 62 testy zamiast 40.

## Świadomie odłożone (nie jest to dług, tylko zakres poza tą fazą)

Native helpery `G2.cross_verify`/`test_sanity`/`diff_coverage` dla TS (TypeScript Compiler API) i C# (Roslyn) — w chwili pisania tego dokumentu (koniec Fazy 1) cała ta rodzina bramek istniała wyłącznie dla Pythona. Brak zarejestrowanego `TestToolchain` dla danego języka daje `skipped`, nie błąd.

> **Aktualizacja (ten sam dzień, po Fazie 1):** helper Roslyn dla C# (`gatekeeper-cs-helper`, `csharp/tools/`) został zbudowany — `G2.*` ma dziś odpowiednik dla Pythona i C#, TS/JS zostaje jedynym świadomie odłożonym. Powstała też nowa bramka **`G1.complexity`** (złożoność cyklomatyczna McCabe) z odpowiednikiem we wszystkich trzech pack'ach jednocześnie od startu — plan: `core/PLAN-G1-complexity.md`.

## Otwarty dług

Po scaleniu repo (nota na górze) cztery workflow'y `ci.yml` per pack zostały zastąpione jednym korzeniowym `.github/workflows/ci.yml` z czterema jobami. Dług został ten sam: token `gh` używany w tych sesjach nie ma scope `workflow`, którego GitHub wymaga do push'a zmieniającego cokolwiek pod korzeniowym `.github/workflows/`, więc commit z tym plikiem czeka lokalnie. Odblokowanie: `gh auth refresh -h github.com -s workflow` (logowanie przez przeglądarkę), potem `git push`.
