# Changelog

Wszystkie istotne zmiany w projekcie llm-code-gatekeeper są dokumentowane w tym pliku.

Format opiera się na [Keep a Changelog 1.1.0](https://keepachangelog.com/pl/1.1.0/),
a projekt przestrzega [Semantic Versioning 2.0.0](https://semver.org/lang/pl/).

## [0.2.0] - 2026-10-29

Wydanie 0.2.0 stanowi ujednoliconą wersję bazową dla całego ekosystemu monorepo
(wszystkie 5 pakietów: `llm-code-gatekeeper-core`, `llm-code-gatekeeper-python`,
`llm-code-gatekeeper-ts`, `llm-code-gatekeeper-csharp`, `llm-code-gatekeeper-web`).

### Dodano (Added)

#### 1. Silnik reguł i bramki jakości G0–G3 (`llm-code-gatekeeper-core`)
- **11 kanonicznych bramek jakości**:
  - `G0.provenance` — weryfikacja pochodzenia i metadanych zmian agenta (trailery w commitach).
  - `G0.scope` — kontrola granic modyfikacji według `scope_map.yaml` i limitów diffa (bez lockfile).
  - `G1.complexity` — limit złożoności cyklomatycznej McCabe dla zmodyfikowanych funkcji/metod.
  - `G1.deps` — ochrona przed typosquattingiem i nieistniejącymi pakietami w rejestrach (PyPI, npm, NuGet).
  - `G1.static` — statyczna analiza kodu, lintery i kompilatory dla ocenianego języka.
  - `G2.cross_verify` — uruchamianie nowych testów na kodzie sprzed zmiany (łapanie pozornych testów).
  - `G2.test_sanity` — linter jakości testów (brak asercji, tautologie, echo mocków, połykanie wyjątków).
  - `G2.diff_coverage` — branch-aware pokrycie różnicowe nowego kodu testami.
  - `G3.sast` — reguły „nigdy” dla krytycznych wzorców podatnościowych.
  - `G3.sca` — audyt podatności znanych bibliotek przez oficjalne narzędzia (pip-audit, npm audit, dotnet list package).
  - `G3.secrets` — skanowanie wycieków poświadczeń i tokenów z użyciem gitleaks.
- **Dwupoziomowa architektura pluginów** oparta na setuptools entry points.
- **Bezpieczeństwo zależności**: weryfikacja typosquattingu, audyt SCA oraz sprawdzanie istnienia w publicznych rejestrach.
- **Podwójny profil polityki**:
  - `policy/gates.yaml` (profil adopcji) — ostrzega (`warn_only`) dla G1.static, G2.*, G3.sast, G3.sca; blokuje sekrety, typosquat i przekroczenie zakresu.
  - `policy/gates.enforcing.yaml` (profil produkcyjny) — pełne blokowanie, brak `warn_only`, restrykcyjne wymaganie narzędzi (`require_*`).
- **Trzy backendy izolacji środowiskowej**:
  - Bubblewrap (`bwrap`) dla systemów Linux.
  - Kontenerowy (`container`) dla Dockera/Podmana.
  - Windows Job Objects dla środowisk Windows.
- **Zunifikowane CLI**: polecenie `gatekeeper` z podkomendami `run`, `policy lint`, `policy facts`, `calibrate`, `verdict`, `incident`, `metrics`.

#### 2. Pakiety językowe (Multi-Language Packs)
- **Pakiet Python (`llm-code-gatekeeper-python`)**:
  - Adaptery linterów i analizatorów: `ruff`, `mypy`.
  - Analiza złożoności cyklomatycznej McCabe przez wbudowany moduł `ast`.
  - Weryfikacja krzyżowa G2 z użyciem `pytest`.
  - Pokrycie różnicowe G2 przez `coverage.py` i `diff-cover`.
  - Zestaw dedykowanych reguł SAST Semgrep dla Pythona.
- **Pakiet TypeScript/JavaScript (`llm-code-gatekeeper-ts`)**:
  - Adaptery statyczne: kompilator `tsc` i linter `eslint`.
  - Kontrola złożoności cyklomatycznej przez eslint complexity.
  - Weryfikacja krzyżowa G2 z obsługą `vitest` oraz `jest`.
  - Weryfikacja poprawności testów AST przez parser pomocniczy ESTree (`helper.cjs`).
  - Pokrycie różnicowe Cobertura + `diff-cover`.
  - Zestaw reguł SAST Semgrep dla ekosystemu JS/TS.
- **Pakiet C# (`llm-code-gatekeeper-csharp`)**:
  - Statyczna analiza i kompilacja przez `dotnet build` oraz Roslyn.
  - Analiza złożoności i linter testów przez narzędzie Roslyn `gatekeeper-cs-helper`.
  - Weryfikacja krzyżowa G2 z runnerem testów `dotnet test` (xUnit).
  - Pokrycie różnicowe przez XPlat Code Coverage i `diff-cover`.
  - Zestaw reguł SAST Semgrep dla języka C#.

#### 3. Panel webowy (`llm-code-gatekeeper-web`)
- Realizacja etapów 0–6 ze specyfikacji `PLAN-WEB-UI.md`:
  - Przeglądarka raportów i audytów z wizualizacją werdyktów bramek.
  - Rejestr projektów i konfiguracji repozytoriów.
  - Kolejka zadań w tle z niezależnym procesem nadzorcy (supervisor).
  - Panel triage'u i rejestracji decyzji człowieka (`verdict`, false-positive).
  - Historyczne metryki skuteczności i statystyki blokowań.
  - Zarządzanie politykami i profilami bramek.
  - Baza danych SQLite z mechanizmem tworzenia kopii zapasowych przez CLI (`gatekeeper-web db backup`).

#### 4. Empiryczny benchmark kalibracyjny (46 PR-ów)
- 46 zweryfikowanych i sklasyfikowanych pull requestów kalibracyjnych (35 natywnych core + 11 w kontenerze).
- Osiągnięte metryki skuteczności:
  - 100% precyzja blokowania (BLOCK precision — brak fałszywych blokad na poprawnym kodzie).
  - 7.1% wskaźnik ucieczki defektów (escape rate).
  - 97.1% ogólna trafność klasyfikacji (accuracy).
- Szczegółowe wyniki udokumentowane w `docs/CALIBRATION.md`.

---

## Polityka wersjonowania w monorepo (Monorepo Unified Versioning Policy)

1. **Wspólny numer wersji**: Wszystkie 5 pakietów w monorepo (`llm-code-gatekeeper-core`, `llm-code-gatekeeper-python`, `llm-code-gatekeeper-ts`, `llm-code-gatekeeper-csharp`, `llm-code-gatekeeper-web`) współdzielą ten sam numer wersji SemVer (obecnie `0.2.0`).
2. **Deklaracja zależności**: Pakiety zależne (`python`, `ts`, `csharp`, `web`) deklarują zależność od rdzenia ze zgodnością do kolejnego wydania minor: `"llm-code-gatekeeper-core>=0.2.0,<0.3.0"`.
3. **Konwencja tagowania Git**: Wydania są oznaczane pojedynczym tagiem w formacie `v<MAJOR>.<MINOR>.<PATCH>` (np. `v0.2.0`). Tag ten wyzwala zautomatyzowany proces budowania i publikacji wszystkich pakietów na PyPI.
