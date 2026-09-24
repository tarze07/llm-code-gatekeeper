# Plan: G2.cross_verify / G2.test_sanity / G2.diff_coverage dla TS/JS

> **Status (2026-09-04): zaimplementowane.** `gatekeeper_ts/testing/` + `gatekeeper_ts/tools/helper.cjs`, zarejestrowane pod `gatekeeper.test_toolchains`, zweryfikowane realnym vitest **i** realnym jest (nie mockami), z przypadkiem kalibracyjnym `test-bez-dowodu`. Dokument zostaje jako zapis decyzji projektowych — kod jest źródłem prawdy o szczegółach implementacji.

Dokument towarzyszący [`README.md`](README.md), pisany w stylu [`csharp/PLAN-G2.md`](../csharp/PLAN-G2.md): interfejs, algorytm, pułapki, koszt. README mówi, co ten pack dziś dostarcza; ten dokument mówi, **dlaczego tak**.

Rozpoznanie zweryfikowane technicznie (nie z pamięci) 2026-09 na `node@22.22.3`, `typescript@7.0.2`, `@typescript-eslint/parser@8.69.0`, `vitest@3`/`@5`, `jest@29`.

## 0. Streszczenie: jedno założenie planu okazało się nieaktualne

Dotychczasowe dokumenty (`csharp/PLAN-G2.md` §0, `PODSUMOWANIE.md`, `core/PLAN.md`) zapowiadały dla TS „native helper na **TypeScript Compiler API**" — analogia do helpera Roslyn dla C#. To założenie **nie przeżyło kontaktu z rzeczywistością**:

```console
$ node -e "const ts=require('typescript'); console.log(ts.version, typeof ts.createSourceFile)"
7.0.2 undefined
```

Od TypeScript 7 (port natywny, Go) pakiet npm `typescript` nie eksponuje już w JS ani `createSourceFile`, ani `SyntaxKind`, ani `forEachChild`. Plan zakładał API, które w aktualnej wersji narzędzia nie istnieje.

Zamiennik: **ESTree z `@typescript-eslint/parser`**. To nie jest kompromis „na już":

- parser jest **już** zależnością tego pack'a — `G1.complexity` używa go od pierwszego dnia (`adapters/complexity.py`), więc liczba wymaganych narzędzi nie rośnie;
- ESTree jest stabilnym, wersjonowanym kontraktem, niezależnym od przepisania kompilatora TS na Go;
- obsługuje `.ts`, `.tsx`, `.js`, `.jsx` jednym wywołaniem, z `jsx` włączanym per rozszerzenie (dla `.ts` **musi** być wyłączony, inaczej `<T>expr` czyta się jako element JSX i plik nie parsuje się wcale — jest na to test).

Czego **nie** tracimy: discovery i linter jakości potrzebują wyłącznie warstwy składniowej, bez modelu typów — dokładnie jak helper C# świadomie nie buduje `Compilation` (`csharp/PLAN-G2.md` §2).

## 1. Co się reużywa 1:1, co wymaga nowego kodu

| Element (python/csharp) | Los w TS/JS |
|---|---|
| `discovery.py` (`ast.parse` / Roslyn `discover`) | **nowy kod** — `helper.cjs discover`, ESTree |
| `quality.py` (5 reguł atrap) | **nowy kod** — `helper.cjs lint`, te same `rule_id` |
| `pytest_runner.py` / `trx_runner.py` | **nowy kod** — `runner.py`, ale **jeden** parser na oba runnery (§3) |
| `parse_junit` / `parse_trx` | zastąpione `parse_report` (JSON jesta) |
| `toolchain.py::_overlay_tests` | **bez zmian koncepcyjnych** — kopiowanie plików testowych |
| `toolchain.py::_assert_isolation` | **inna logika** — nie `pip install -e .`, tylko dowiązania w `node_modules` (§4) |
| `core.diffcover.run_diff_cover_on_report` | **bez zmian** — `diff-cover` konsumuje Cobertorę niezależnie od języka |

Bilans: trzy nowe pliki Pythona (~350 linii), jeden plik JS (~470 linii), zero nowych zależności binarnych ponad to, co pack już wymagał.

## 2. Helper: plik w kole, nie osobna instalacja

Helper C# jest projektem .NET, który trzeba osobno zbudować i zainstalować (`dotnet tool install --global gatekeeper-cs-helper`) — to realny koszt wdrożeniowy, widoczny w `requires_tools` przypadków kalibracyjnych csharp-packu.

Tutaj helper to **jeden plik `tools/helper.cjs` wysyłany razem z kołem** (`[tool.setuptools.package-data]`). Nie ma kroku budowania, nie ma drugiego artefaktu do wydania, nie ma wersji do zsynchronizowania z pakietem Pythona. Ścieżkę do niego daje `importlib.resources`, nie `__file__.parent` — ta druga forma nie przeżywa instalacji z koła (ta sama pułapka, którą naprawiono w `adapters/semgrep.py` core'a).

**Dlaczego `.cjs`, nie `.mjs`:** `require()` honoruje `NODE_PATH`, `import` nie. Strona Pythona (`gatekeeper_ts/node.py`) dokłada do `NODE_PATH` wynik `npm root -g`, dzięki czemu parser jest znajdowany także wtedy, gdy oceniane repo nie trzyma go u siebie. Rozszerzenie `.cjs` (zamiast `.js`) wymusza CommonJS niezależnie od `"type": "module"` w `package.json` repo, w którego katalogu helper bywa uruchamiany.

Kontrakt CLI jest ten sam co w C#: `helper.cjs <discover|lint> --files <ścieżki względne>`, `cwd` = korzeń repo, JSON na stdout, kod wyjścia 0 niezależnie od znalezisk.

## 3. Runner: dwa frameworki, jeden parser

`detect_runner()` czyta `package.json` (`dependencies`, `devDependencies`, treść `scripts.test`) i zwraca `vitest` albo `jest`.

Kluczowa obserwacja, dzięki której wsparcie dwóch frameworków nie kosztuje podwójnie: **vitest pod `--reporter=json` emituje kształt JSON-a jesta** — `testResults[].assertionResults[]` z `ancestorTitles`, `title`, `status`. Jeden `parse_report()` obsługuje oba.

### 3.1 Dlaczego uruchamiamy całe pliki, nie pojedyncze testy

C# filtruje pojedyncze testy przez `dotnet test --filter FullyQualifiedName~...`, bo tam nazwa testu jest identyfikatorem języka. W TS/JS nazwa testu to **dowolny string** — nawiasy, `$`, znaki regexowe, interpolacja w `it.each`. Filtr `--testNamePattern` jest wyrażeniem regularnym, więc wymagałby escapowania, które i tak nie pokrywa `each`.

Zamiast tego uruchamiamy **pliki** i korelujemy wynik po `nodeid` odtworzonym z `ancestorTitles + title` — tym samym stringiem, który liczy `helper.cjs` (`plik::describe > describe > nazwa`). Obie strony składają go identycznie, więc korelacja jest dokładna.

- Koszt: na kodzie bazowym wykonują się też testy spoza diffa. Ich wyniki są ignorowane (`parse_report` filtruje po `expected`), a czas rośnie o tyle, ile trwa reszta pliku.
- Zysk: zero kruchości filtra i zero ryzyka, że test „zniknie" przez nieudane dopasowanie nazwy — a taki zanik byłby **cichy** (`missing` → `weak_evidence`), czyli najgorszy rodzaj awarii w bramce dowodowej.

### 3.2 Rozróżnienie „poległ" od „nie dał się załadować"

Punkt 3 docstringa `gates/g2_crossverify.py` wymaga, żeby test, który poległ na asercji (dowodzi czegoś o zachowaniu), był liczony osobno od testu, który nie zaimportował nieistniejącego jeszcze modułu (dowodzi znacznie mniej).

W JSON-ie obu runnerów plik, który nie dał się załadować, ma `assertionResults: []` i `message` z błędem. `parse_report` zamienia to na `outcome="error"` dla **wszystkich** oczekiwanych testów z tego pliku — nie na `"missing"`, bo `missing` sugerowałoby literówkę w nodeid, a nie realny brak dowodu.

Uwaga praktyczna z weryfikacji: w vitest ten przypadek okazał się **rzadszy**, niż zakładaliśmy. Vite transpiluje moduł i brakujący eksport nazwany staje się `undefined` w czasie wykonania, więc test pada pojedynczo na `TypeError` (`outcome="failed"`, czyli mocny dowód) zamiast wywracać cały plik. To działa na korzyść bramki — granulacja jest lepsza niż w C#, gdzie brakująca metoda to błąd kompilacji całego projektu.

## 4. Izolacja: `node_modules`, nie `pip install -e .`

Python-pack sprawdza, czy testowany moduł nie importuje się z katalogu roboczego zamiast z worktree (klasyk: `pip install -e .`). C# sprawdza nieświeże `bin/`/`obj/`. W TS/JS ten sam defekt ma trzecią postać.

**Problem 1 — worktree nie ma czym uruchomić testów.** Świeży `git worktree add --detach` nie zawiera `node_modules` (katalog jest gitignored). Kopiowanie go potrafi kosztować gigabajty i minuty, więc podpinamy katalog ocenianego repo **dowiązaniem symbolicznym**. Node rozwiązuje moduły przez `realpath`, więc pakiety ładują się z repo — dla zależności zewnętrznych to poprawne i pożądane.

**Problem 2 — i dokładnie dlatego skrót z punktu 1 wymaga strażnika.** Jeżeli `node_modules` zawiera dowiązanie do katalogu **wewnątrz repo** (npm/pnpm/yarn workspaces, `npm link`), to test uruchomiony na kopii kodu bazowego zaimportuje stamtąd **nowy** kod. Bramka porównywałaby wtedy nowy kod z nowym i dawała zielone „nic nie udowodniono" — awaria cicha, czyli najgorsza.

`_assert_isolation()` przechodzi po `node_modules/<pakiet>` oraz `node_modules/@scope/<pakiet>`, i przerywa `IsolationBroken`, gdy któreś dowiązanie rozwiązuje się do ścieżki wewnątrz repo (pomijając wnętrze samego `node_modules` — `.bin` to dowiązania wewnętrzne). `skip_isolation_check: true` w polityce pozwala świadomie zrezygnować z tego dowodu; jest na to test w obie strony.

## 5. Pokrycie różnicowe: znów tylko okablowanie

Jak w C#: `diff-cover` natywnie konsumuje Cobertorę, a oba runnery potrafią ją wyprodukować.

- vitest: `--coverage --coverage.reporter=cobertura --coverage.reportsDirectory=<dir>` (wymaga `@vitest/coverage-v8` albo `-istanbul` w ocenianym repo)
- jest: `--coverage --coverageReporters=cobertura --coverageDirectory=<dir>`

Oba zapisują `<dir>/cobertura-coverage.xml`, który idzie wprost do `core.diffcover.run_diff_cover_on_report()`. Brak raportu to `ToolMissing` z komunikatem nazywającym brakujący pakiet — nie cichy `pass`.

Przy okazji naprawiona luka: ani `ts`, ani `csharp` nie deklarowały `diff-cover` jako zależności, mimo że oba wołają go przez core. Dodano extra `gates` w obu `pyproject.toml`.

## 6. Zmiana w kontrakcie core: `languages`, nie `language`

`TestToolchain` deklarował jeden `language: str`, a bramki G2 filtrowały pliki produkcyjne przez `f.language == language`. Ten pack obsługuje **dwa** języki: `vitest`/`jest` uruchamiają testy `.ts` i `.js` jednym przebiegiem i produkują jeden raport pokrycia.

Rozbicie na dwa zarejestrowane toolchainy oznaczałoby dwa przebiegi runnera i dwa raporty pokrycia po tym samym repo. Zamiast tego core dostał `plugins.toolchain_languages()`, które czyta `languages` (l. mn.), a gdy go nie ma — spada na `language`. Toolchainy jednojęzyczne (python, csharp) zostają bez zmian; to ta sama konwencja, którą `StaticChecker` i `ComplexityAnalyzer` miały od początku.

## 7. Zakres v1 i co zostaje na potem

**Jest:** vitest, jest; `describe`/`suite` + `it`/`test` z modyfikatorami (`.skip`, `.only`, `.concurrent`, `.each(...)()`), `.ts`/`.tsx`/`.js`/`.jsx`/`.mjs`/`.cjs`; pięć reguł jakości z tymi samymi `rule_id` co Python i C#; markery eskapowe w komentarzu (`// gatekeeper: characterization`).

**Nie ma, świadomie:**

- **mocha / `node:test` / ava.** `detect_runner()` rzuca wtedy `TestRunnerUnavailable`, czyli bramka daje `error`, nie cichy `pass` — fail-closed, zgodnie z zasadą całego systemu. Dopisanie ich to nowa gałąź w `detect_runner` plus parser ich formatu wyniku: zmiana rozmiaru, nie architektury (dokładnie tak, jak helper C# obsługuje na razie sam xUnit, a NUnit/MSTest to rozszerzenie listy atrybutów).
- **Mutacja i CRAP.** Jak w pozostałych packach — `core/PLAN.md` §G2 i `core/PLAN-G1-complexity.md` §8.
- **Analiza przepływu danych w `test.mock_echo`.** Reguła śledzi tekst wyrażenia, nie graf przepływu — to samo uproszczenie co w helperze C#. Pokrywa najczęstszy kształt (`vi.fn()` + `mockReturnValue(X)` + `expect(mock()).toBe(X)`), a fałszywy negatyw jest tu tańszy niż fałszywy pozytyw blokujący poprawny mock wstrzyknięty jako zależność (jest na to test negatywny).

## 8. Koszt uruchomienia

| Krok | Czas | Sieć |
|---|---|---|
| `discover`/`lint` (helper) | ~0,1–0,3 s na plik testowy | nie |
| `run_cross_verify` | jeden przebieg runnera na plikach z diffa | nie (sandbox) |
| `produce_coverage_report` | pełny przebieg zestawu testów + `diff-cover` | nie (sandbox) |

Sandbox dostaje `memory_mb=None` — V8 rezerwuje na starcie kilkugigabajtową przestrzeń adresową (pointer compression cage), więc twardy `RLIMIT_AS` wywraca Node zanim ten cokolwiek uruchomi. To ten sam kwirk, dla którego `CsharpTestToolchain` zdejmuje limit dla CoreCLR; izolacja sieci zostaje w obu przypadkach.
