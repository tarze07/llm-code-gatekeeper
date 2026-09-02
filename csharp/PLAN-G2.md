# Plan: G2.cross_verify / G2.test_sanity / G2.diff_coverage dla C#

Dokument towarzyszący [`README.md`](README.md). README mówi, co ten pack dziś dostarcza (`G1.static`, `G3.sast`). Ten dokument mówi, jak domknąć brakującą rodzinę bramek — jedyną, której ten pack jeszcze nie ma — analogicznie do stylu [`python/TOOLS.md`](../python/TOOLS.md): interfejs, algorytm, pułapki, koszt.

Rozpoznanie zweryfikowane technicznie (nie z pamięci) 2026-09; źródła w tekście.

## 0. Streszczenie: taniej niż wyglądało

`G2.diff_coverage` dla C# okazuje się być **głównie okablowaniem**, nie nowym narzędziem — `dotnet test --collect:"XPlat Code Coverage"` domyślnie produkuje Cobertura XML z danymi branch-coverage wbudowanymi ([Microsoft Learn](https://learn.microsoft.com/en-us/dotnet/core/testing/unit-testing-code-coverage)), a `diff-cover` (już zależność `python`-packu, `[gates]`) **natywnie konsumuje Cobertura** jako jeden z formatów wejściowych ([PyPI](https://pypi.org/project/diff-cover/), [GitHub](https://github.com/Bachmann1234/diff_cover)). `python/gatekeeper_python/adapters/coverage.py::parse_diff_cover_json` parsuje **własny JSON `diff-cover`**, nie surową Cobertorę — czyli jest już w 100% language-agnostic i da się przenieść bez zmian.

`G2.cross_verify`/`G2.test_sanity` wymagają realnego nowego kodu, bo Python parsuje sam siebie (`ast`), a `gatekeeper_csharp` (pakiet Pythona) nie potrafi parsować C#. Potrzebny jest **jeden nowy komponent**: mały helper .NET oparty o Roslyn (`Microsoft.CodeAnalysis.CSharp`), wołany jako podproces — dokładnie ten sam wzorzec, w którym `adapters/dotnet.py` już dziś woła `dotnet build` jako podproces, tylko to narzędzie piszemy sami.

## 1. Co się reużywa 1:1, co wymaga nowego kodu

| Element Pythona (`python/gatekeeper_python/testing/`) | Los w C# |
|---|---|
| `discovery.py` (`ast.parse`, `collect_tests`, `_body_hash`, markery) | **nowy kod** — helper Roslyn, komenda `discover` |
| `quality.py` (brak asercji, asercja na stałą, echo mocka, połknięty wyjątek) | **nowy kod** — ten sam helper, komenda `lint` |
| `pytest_runner.py::build_env/run_pytest` | zastąpione `dotnet test --filter ... --logger trx` |
| `pytest_runner.py::parse_junit` | **nowy parser** — TRX zamiast JUnit XML (kształt podobny: `Outcome` per test) |
| `toolchain.py::_overlay_tests` | **bez zmian koncepcyjnych** — kopiowanie plików |
| `toolchain.py::_assert_isolation` | **inna logika** — nie ma odpowiednika „editable install”, ryzyko jest gdzie indziej (§4.3) |
| `adapters/coverage.py::run_diff_coverage` (kroki `coverage run`+`coverage xml`) | zastąpione jednym `dotnet test --collect:"XPlat Code Coverage;Format=cobertura"` |
| `adapters/coverage.py::parse_diff_cover_json` | **bez zmian** — ten sam parser, ten sam plik może nawet zostać reużyty przez import z `python`-packu albo przeniesiony do core (patrz §6) |

## 2. Nowy komponent: `gatekeeper-cs-helper`

Mały projekt konsolowy .NET (`net8.0`), zależny wyłącznie od `Microsoft.CodeAnalysis.CSharp` (Roslyn, tylko warstwa syntax — **bez kompilacji, bez semantic model**, więc szybki i nie wymaga, żeby projekt oceniany dał się w ogóle zbudować do samej analizy testów, dokładnie jak `ast.parse` nie wymaga uruchamialnego Pythona).

### Interfejs

```
gatekeeper-cs-helper discover --files <plik1.cs> <plik2.cs> ...
gatekeeper-cs-helper lint     --files <plik1.cs> <plik2.cs> ...
```

Wejście: lista ścieżek do zmienionych plików testowych (bramka w Pythonie już wie, które pliki są testami — `ChangeContext.effective_files` — helper nie zgaduje). Wyjście: JSON na stdout, kod wyjścia `0` niezależnie od znalezisk (błąd parsera to `!= 0` — `dotnet build`/`tsc` już ustanowiły tę konwencję w tym projekcie).

### `discover` — odpowiednik `discovery.py::collect_tests`

Dla każdego pliku: `CSharpSyntaxTree.ParseText(source)`, `GetRoot().DescendantNodes().OfType<MethodDeclarationSyntax>()`, filtr do metod z atrybutem testowym. **Zakres frameworków w pierwszej wersji: wyłącznie xUnit** (`[Fact]`, `[Theory]`) — to domyślny wybór dla nowych projektów .NET w 2026 ([qaskills.sh](https://qaskills.sh/blog/xunit-vs-nunit-vs-mstest-2026)); NUnit (`[Test]`) i MSTest (`[TestMethod]`) to rozszerzenie listy rozpoznawanych atrybutów, nie zmiana architektury — dodać, gdy pojawi się pierwszy realny projekt, który ich potrzebuje.

Wyjście per test — kształt zgodny z `DiscoveryResult` (`core/plugins.py`):

```json
{
  "tests": [
    {
      "file": "Tests/CalcTests.cs",
      "name": "Add_ReturnsSum",
      "nodeid": "Tests/CalcTests.cs::CalcTests.Add_ReturnsSum",
      "lineno": 12,
      "body_hash": "sha256:...",
      "declared_escape": null
    }
  ]
}
```

`nodeid` musi jednoznacznie adresować test dla `dotnet test --filter` — użyj w pełni kwalifikowanej nazwy (`Namespace.Klasa.Metoda`), nie samej ścieżki pliku (C#, w przeciwieństwie do Pythona, nie ma 1:1 między ścieżką pliku a przestrzenią nazw). `declared_escape`: odpowiednik `@pytest.mark.characterization` — konwencja do ustalenia, np. własny atrybut `[Characterization]`/`[TestBackfill]` albo komentarz-marker tuż nad metodą (Roslyn widzi trivia, więc komentarz jest równie łatwy do wykrycia co atrybut).

### `lint` — odpowiednik `quality.py`

Te same reguły, przełożone z `ast` na Roslyn (`InvocationExpressionSyntax` zamiast `ast.Call`, itd.):

| Reguła Pythona | Odpowiednik C# |
|---|---|
| `_has_assert_stmt` (brak `assert`) | brak wywołania `Assert.*`/`Should().*` (FluentAssertions, powszechne w xUnit) w ciele metody |
| `_is_constant_truthy`/`_rule_constant_assertion` | `Assert.True(true)`, `Assert.Equal(1, 1)` — argumenty bez zmiennej z testowanego kodu |
| `_rule_mock_echo` (mock porównywany z własnym `return_value`) | Moq: `mock.Setup(x => x.Foo()).Returns(5); Assert.Equal(5, mock.Object.Foo());` — asercja odtwarza to, co sam test ustawił |
| `_rule_only_smoke` (sam `is not None`) | `Assert.NotNull(x)` jako jedyna asercja w teście |
| `_rule_exception_swallowed` | pusty `catch { }` / `catch (Exception) { }` bez asercji w środku |

Wyjście — kształt zgodny z `QualityIssue` (`core/plugins.py`): `rule_id`, `severity`, `title`, `failure_scenario`, `evidence`, plus wskazanie, którego testu (`nodeid`) dotyczy.

### Dystrybucja

**`dotnet tool install --global gatekeeper-cs-helper`** (publikacja jako .NET tool na NuGet), analogicznie do `npm install --global typescript eslint` w ts-packu — spójność z istniejącym wzorcem „narzędzia projektu ocenianego instalują się osobno, nie są zależnością pip pack'a”. Alternatywa (budowanie helpera z wektorowanego źródła przy pierwszym użyciu) odrzucona: dokłada czas do pierwszego uruchomienia i ryzyko konfliktu wersji SDK helpera z SDK ocenianego projektu.

`CsharpStaticChecker.check()` już dziś zgłasza `static.dotnet_available` gdy brakuje `dotnet` — ten sam wzorzec: brak `gatekeeper-cs-helper` w `PATH` to `error` z jasnym komunikatem, nie cichy brak dowodu.

## 3. `G2.cross_verify`

`CsharpTestToolchain.run_cross_verify()`, sygnatura identyczna z `PythonTestToolchain` (kontrakt `TestToolchain` w `core/plugins.py` nie zmienia się między językami):

1. `change.worktree_at(change.base_sha)` — worktree ze starym kodem (bez zmian, to już robi `ChangeContext`).
2. `_overlay_tests` — kopiuje nowe/zmienione pliki testowe na worktree (bez zmian koncepcyjnych względem Pythona).
3. **Build w izolacji**: `dotnet build --no-incremental -o <tmp_output>` w worktree — `--no-incremental` i świeży `-o` są tu kluczowe (§4.3, nie kosmetyka).
4. `dotnet test --no-build --filter "FullyQualifiedName=A|FullyQualifiedName=B|..." --logger "trx;LogFileName=results.trx"` — filtr złożony z `nodeid`ów przekazanych testów (`|` = OR w składni VSTest).
5. Parser TRX → `dict[nodeid, TestOutcome]`.

### Parser TRX zamiast JUnit

TRX to XML VSTest, wbudowany, bez dodatkowego pakietu NuGet ([qaskills.sh](https://qaskills.sh/blog/xunit-vs-nunit-vs-mstest-2026)). Węzeł `<UnitTestResult>` niesie `outcome` (`Passed`/`Failed`/`NotExecuted`/`Error`) i `testName` — mapowanie na `Outcome` z `pytest_runner.py` jest bezpośrednie:

| TRX `outcome` | `TestOutcome.outcome` (Python) |
|---|---|
| `Passed` | `"passed"` |
| `Failed` | `"failed"` |
| `NotExecuted` | `"skipped"` |
| `Error` | `"error"` |
| (test nie znaleziony w raporcie) | `"missing"` |

To rozróżnienie jest sednem bramki (patrz docstring `g2_crossverify.py`): `Failed` dowodzi czegoś o zachowaniu, `Error` (zwykle błąd kompilacji po overlayu — test odwołuje się do API, którego stary kod jeszcze nie ma) dowodzi mniej i liczy się osobno jako `tests.weak_evidence`.

### 4.3 Izolacja — inny problem niż w Pythonie, nie kopiuj-wklej

Python: ryzykiem jest `pip install -e .` — `import` sięga po kod z katalogu roboczego zamiast z worktree. C# nie ma tego trybu (brak „editable install” dla bibliotek .NET w typowym scenariuszu), więc `IsolationBroken` w tej formie nie ma zastosowania.

Realne ryzyko w C# jest inne: **inkrementalny build**. `obj/`/`bin/` z poprzedniego builda (np. z tego samego worktree użytego wcześniej, albo — gorzej — współdzielonego przez `nuget.config` z lokalnym cache pakietów zbudowanym z HEAD) mogą przeciekać skompilowane artefakty nowego kodu do „starego” builda. Mitigacja: `dotnet build --no-incremental` + świeży katalog `-o` per przebieg (nigdy nie reużywać `obj`/`bin` między worktree'ami), i weryfikacja, że `dotnet test` faktycznie ładuje assembly z tego `-o`, nie z jakiegoś globalnie zainstalowanego pakietu o tej samej nazwie (rzadkie, ale możliwe przy `dotnet pack`+lokalny feed w tym samym repo). Nowa klasa wyjątku analogiczna do `ToolchainIsolationBroken`, ale z innym komunikatem — nie kopiować tekstu o „trybie edytowalnym”, bo wprowadzałoby w błąd.

## 4. `G2.test_sanity`

`CsharpTestToolchain.lint_quality()` woła `gatekeeper-cs-helper lint --files ...` na plikach z `discover_tests()`, mapuje JSON na `list[tuple[DiscoveryResult, QualityIssue]]`. Reszta (agregacja w `gates/g2_test_sanity.py`) jest już core-owa i nie wymaga zmian — bramka od dawna jest dispatcherem.

## 5. `G2.diff_coverage`

`CsharpTestToolchain.produce_coverage_report()`:

```bash
dotnet test --collect:"XPlat Code Coverage;Format=cobertura" \
  --results-directory <tmp>/TestResults
# -> <tmp>/TestResults/**/coverage.cobertura.xml (branch-rate wbudowane)

diff-cover <tmp>/TestResults/**/coverage.cobertura.xml \
  --compare-branch=<base_sha> \
  --branch-coverage \
  --total-percent-float \
  --json-report <tmp>/diffcover.json
```

`parse_diff_cover_json` — **identyczny kod co w Pythonie**, zero zmian, bo parsuje JSON `diff-cover`, nie Cobertorę. Praktyczna decyzja: albo `python`-pack eksportuje tę funkcję jako reużywalną (import `gatekeeper_python.adapters.coverage` z `gatekeeper_csharp` — brzydkie, tworzy zależność między pack'ami równorzędnymi), albo — **lepiej** — wydzielić `parse_diff_cover_json`/`DiffCoverageResult`/`run_diff_cover_on_cobertura` do **core** jako `core.diffcover`, tak jak `testing/toolchain.py` już dziś zapowiada w komentarzu o zakresie. To dokładnie moment, żeby to zrobić: skoro dwa pack'i (python, csharp) potrzebują tej samej logiki, przestaje to być „dopóki jest jeden toolchain” (cytat z `toolchain.py`), tylko realny wspólny mianownik. `run_diff_cover_on_report(xml_path, sandbox, base_sha, ...)` w core, obaj toolchainy wołają tylko krok „wyprodukuj Cobertorę swoim narzędziem”, resztę mają za darmo.

## 6. Kolejność budowy

1. **`gatekeeper-cs-helper` — `discover` (tylko xUnit)** + testy na zapisanych próbkach `.cs` (golden file, ten sam wzorzec co `test_adapters_linters.py`). Bez tego nic innego nie rusza.
2. **`core.diffcover`** (wydzielenie z `python/adapters/coverage.py`) + `produce_coverage_report` w C# — najmniej ryzykowne, najszybciej daje działającą bramkę (`G2.diff_coverage` jako pierwsza żywa).
3. **Parser TRX** + `run_cross_verify` (overlay, build izolowany, `dotnet test --filter`) — `G2.cross_verify`.
4. **`gatekeeper-cs-helper lint`** + `lint_quality` — `G2.test_sanity`.
5. Rejestracja `python = "gatekeeper_csharp.testing.toolchain:CsharpTestToolchain"` pod `gatekeeper.test_toolchains` w `pyproject.toml`, `calibration/cases.yaml` dostaje pierwsze przypadki C# (dziś puste — patrz `README.md`).
6. Rozszerzenie `discover`/`lint` o NUnit/MSTest, jeśli pojawi się taka potrzeba.

## 7. Ryzyka

- **Roslyn to nowa zależność projektu** (nie jest to standardowa biblioteka .NET) — trzeba przypiąć wersję `Microsoft.CodeAnalysis.CSharp` w `.csproj` helpera, tak jak `mypy`/`ruff` mają przypięte minimalne wersje w `pyproject.toml`.
- **`--filter` VSTest ma ograniczenie długości/liczby warunków** przy bardzo dużych PR-ach (setki nowych testów) — do zweryfikowania empirycznie; fallback to podział na paczki filtrów, ten sam problem co `pytest nodeids` na bardzo długiej liście (dziś niesprawdzony limit w Pythonie też).
- **`--collect:"XPlat Code Coverage"` wymaga `coverlet.collector`** jako zależności projektu testowego ocenianego repo (nie samej bramy) — jeśli go brak, `dotnet test` milczy o coverage zamiast raportować błąd; bramka musi to wykryć explicite (brak pliku `*.cobertura.xml` po przebiegu = `coverage.tool_available: false`, ten sam fakt co dziś w `g2_diff_coverage.py`).

## 8. Koszt

Porównywalny z oryginalną budową G2 dla Pythona pod względem *nowego* kodu produkcyjnego, ale nie 1:1 — `G2.diff_coverage` jest wyraźnie tańsze (okablowanie, nie nowe narzędzie), `G2.cross_verify`/`test_sanity` niosą realny nowy komponent (helper Roslyn) plus adaptację logiki izolacji, która **nie jest** przeniesieniem 1:1 (§4.3). Szacunek: helper Roslyn (discover+lint, tylko xUnit) — porównywalny rozmiarem z `discovery.py`+`quality.py` razem (~350 linii w Pythonie, w C#/Roslyn prawdopodobnie więcej ceremonii); reszta (parser TRX, toolchain, wydzielenie `core.diffcover`) — porównywalna z odpowiadającymi plikami Pythona.
