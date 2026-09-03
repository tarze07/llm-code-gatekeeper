# Jak używać poszczególnych rozwiązań — Python / JavaScript (TS) / C#

Ten plik opisuje **praktyczne** użycie bramy dla każdego języka z osobna: co zainstalować, co zostanie sprawdzone, czego brakuje. Zawsze instalujesz `llm-code-gatekeeper-core` — sam z siebie sprawdza rozmiar/pochodzenie zmiany (`G0.*`), sekrety (`G3.secrets`) i nowe zależności PyPI/npm/NuGet (`G1.deps`/`G3.sca`) bez żadnego pack'a. Pack językowy dokłada wyłącznie `G1.static` (build/lint/typy) i część `G3.sast` (reguły „nigdy" specyficzne dla języka).

Pełny opis mechanizmu (entry points, dwa poziomy dispatchu) — [`core/README.md`](core/README.md). Pełna instrukcja krok po kroku, wspólna dla wszystkich języków (polityka, interpretacja raportu, integracja z CI, FAQ) — [`python/USAGE.md`](python/USAGE.md).

---

## Python

```bash
pip install "llm-code-gatekeeper-core @ git+https://github.com/tarze07/llm-code-gatekeeper-core.git"
pip install "llm-code-gatekeeper-python @ git+https://github.com/tarze07/llm-code-gatekeeper-python.git"
pip install ruff mypy   # narzędzia, które G1.static uruchamia jako podproces — nie są zależnością pip pack'a
```

```bash
gatekeeper policy lint --policy policy/gates.yaml
gatekeeper run --repo /ścieżka/do/repo --base origin/main
```

Co dostajesz z pack'a `llm-code-gatekeeper-python`:

| Bramka | Co sprawdza |
|---|---|
| `G1.static` | `ruff check` + `mypy` na zmienionych liniach — łapie halucynacje API (wywołanie metody, której nie ma) |
| `G1.complexity` | złożoność cyklomatyczna (McCabe) przez `ast` — funkcja w diffie z M > 10 blokuje |
| `G3.sast` | reguły „nigdy": `no-tls-verify-disabled`, `no-eval-on-input`, `no-sql-string-concat`, `no-shell-true`, `no-unsafe-deserialization`, `no-hardcoded-bind-all-interfaces` |
| `G2.cross_verify` | nowe testy uruchomione przeciw kodowi **sprzed** zmiany — łapie testy, które niczego nie dowodzą |
| `G2.test_sanity` | linter jakości testów przez `ast`: brak asercji, asercja na stałą, połknięty wyjątek |
| `G2.diff_coverage` | pokrycie różnicowe branch-aware (`coverage.py` + `diff-cover`) |

`G1.deps`/`G3.sca` (manifest `pyproject.toml`/`requirements*.txt`, rejestr PyPI, `pip-audit`) działają **bez** tego pack'a — to core.

`G2.*` wymaga, żeby zależności testowe Twojego projektu (nie samej bramy) dały się zainstalować w tym samym środowisku — bramka uruchamia `pytest` z PR-a na worktree ze starym kodem.

---

## JavaScript / TypeScript

```bash
pip install "llm-code-gatekeeper-core @ git+https://github.com/tarze07/llm-code-gatekeeper-core.git"
pip install "llm-code-gatekeeper-ts @ git+https://github.com/tarze07/llm-code-gatekeeper-ts.git"
npm install --global typescript eslint @typescript-eslint/parser   # binarki/parsery, nie zależności pip
```

```bash
gatekeeper policy lint --policy policy/gates.yaml
gatekeeper run --repo /ścieżka/do/repo --base origin/main
```

Co dostajesz z pack'a `llm-code-gatekeeper-ts`:

| Bramka | Co sprawdza |
|---|---|
| `G1.static` | `tsc --noEmit` (kontrola typów — to samo co mypy dla Pythona) + `eslint` (reguły „problem", nie styl) na zmienionych liniach |
| `G1.complexity` | złożoność cyklomatyczna przez regułę `complexity` eslinta (wymaga też `@typescript-eslint/parser` dla plików `.ts`/`.tsx`) |
| `G3.sast` | reguły „nigdy": `no-dangerous-html-unsanitized`, `no-eval-on-input-js`, `no-shell-true-js`, `no-tls-verify-disabled-js` |

`G1.deps`/`G3.sca` (manifest `package.json`, rejestr npm, `npm audit`) działają **bez** tego pack'a — to core.

`tsc` potrzebuje `tsconfig.json` w repo — bez niego `G1.static` przechodzi bez wołania narzędzia (brak dowodu, nie fałszywy alarm). `eslint` analogicznie potrzebuje configu (`eslint.config.js`/`.eslintrc.*`). Bramka woła najpierw `node_modules/.bin/tsc`/`eslint` projektu ocenianego repo, dopiero potem globalną binarkę.

**`G2.cross_verify`/`test_sanity`/`diff_coverage` nie istnieją jeszcze dla TS/JS** — brak zarejestrowanego `TestToolchain` to `skipped` w raporcie, nie błąd. Native helper na TypeScript Compiler API jest zaplanowany, ale nie zbudowany.

---

## C#

```bash
pip install "llm-code-gatekeeper-core @ git+https://github.com/tarze07/llm-code-gatekeeper-core.git"
pip install "llm-code-gatekeeper-csharp @ git+https://github.com/tarze07/llm-code-gatekeeper-csharp.git"
# wymaga .NET SDK 8.0+ w PATH; bramka zakłada, że `dotnet restore` już się odbył
# (sama nie ściąga pakietów — jedyne miejsce w G1 z dostępem do sieci, którego świadomie nie chcemy)

# gatekeeper-cs-helper (Roslyn) — wymagany przez G1.complexity i G2.*, nie tylko G1.static:
cd tools/gatekeeper-cs-helper && dotnet pack -c Release -o /tmp/cs-helper-nupkg && cd -
dotnet tool install --global gatekeeper-cs-helper --add-source /tmp/cs-helper-nupkg
# (dopóki gatekeeper-cs-helper nie jest opublikowany na NuGet.org)
```

```bash
gatekeeper policy lint --policy policy/gates.yaml
gatekeeper run --repo /ścieżka/do/repo --base origin/main
```

Co dostajesz z pack'a `llm-code-gatekeeper-csharp`:

| Bramka | Co sprawdza |
|---|---|
| `G1.static` | `dotnet build` na projekcie (`.csproj`/`.fsproj`) zawierającym zmieniony plik — Roslyn w trybie ścisłym pełni podwójną rolę ruff+mypy naraz |
| `G1.complexity` | złożoność cyklomatyczna przez `gatekeeper-cs-helper complexity` (syntax tree Roslyn, bez kompilacji) |
| `G3.sast` | reguły „nigdy": `no-tls-verify-disabled-cs`, `no-sql-string-concat-cs`, `no-shell-true-cs`, `no-unsafe-deserialization-cs` |
| `G2.cross_verify` | nowe testy `[Fact]`/`[Theory]` (xUnit) uruchomione przeciw kodowi **sprzed** zmiany przez `dotnet test --filter` |
| `G2.test_sanity` | linter jakości testów przez `gatekeeper-cs-helper lint`: brak asercji, asercja na stałą, echo mocka (Moq), połknięty wyjątek |
| `G2.diff_coverage` | pokrycie różnicowe branch-aware (`dotnet test --collect:"XPlat Code Coverage"` + `diff-cover`) |

`G1.deps`/`G3.sca` (manifest `.csproj`/`.fsproj`/`packages.config`/`Directory.Packages.props`, rejestr NuGet, `dotnet list package --vulnerable`) działają **bez** tego pack'a — to core.

Zmieniony plik `.cs` bez żadnego `.csproj` w drzewie katalogów nad nim daje `static.csproj_found: false` — `G1.static` przechodzi bez wołania `dotnet build` (brak projektu, nie defekt); `G1.complexity` mierzy taki plik i tak (nie potrzebuje projektu, tylko syntax tree).

Architektura `G2.*`/`G1.complexity` dla C# (helper Roslyn, decyzje projektowe): [`csharp/PLAN-G2.md`](csharp/PLAN-G2.md).

---

## Kilka języków w jednym repo

Instalujesz core + tyle pack'ów, ile faktycznie masz języków — `gatekeeper run` obsłuży mieszany diff jednym wywołaniem, bez żadnej dodatkowej konfiguracji:

```bash
pip install "llm-code-gatekeeper-core @ git+https://github.com/tarze07/llm-code-gatekeeper-core.git"
pip install "llm-code-gatekeeper-python @ git+https://github.com/tarze07/llm-code-gatekeeper-python.git"
pip install "llm-code-gatekeeper-ts @ git+https://github.com/tarze07/llm-code-gatekeeper-ts.git"
pip install "llm-code-gatekeeper-csharp @ git+https://github.com/tarze07/llm-code-gatekeeper-csharp.git"
```

`G1.static`, `G1.complexity` i `G3.sast` uruchomią checker/analizator/regułę każdego zainstalowanego języka na plikach, które go dotyczą, i zsumują wynik w jednym `GateResult` — jeden raport na PR, niezależnie od tego, ile języków ten PR dotknął.
