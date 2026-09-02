# llm-code-gatekeeper-csharp

Pack C# dla [`llm-code-gatekeeper-core`](https://github.com/tarze07/llm-code-gatekeeper-core) — instaluje się razem z core i rejestruje się przez entry points, bez patcha w core.

Dostarcza:

- `CsharpStaticChecker` (`gatekeeper.static_checkers`, `checker_id="csharp"`) — `dotnet build` (Roslyn pełni podwójną rolę ruff+mypy naraz — kontrola typów w trybie ścisłym bez osobnego narzędzia). Konsumowany przez `G1.static` (core).
- `CsharpRulePack` (`gatekeeper.semgrep_rule_packs`, `pack_id="csharp"`) — reguły „nigdy” specyficzne dla C# (`no-tls-verify-disabled-cs`, `no-sql-string-concat-cs`, `no-shell-true-cs`, `no-unsafe-deserialization-cs`). Konsumowany przez `G3.sast` (core).

Manifest+rejestr+typosquat+SCA dla NuGet (`G1.deps`/`G3.sca`) **nie** jest tu — `NuGetEcosystem` żyje w core (`llm-code-gatekeeper-core`) i działa nawet bez tego pack'a zainstalowanego. Odnajdywanie projektów (`find_project_for`/`projects_for`), które `CsharpStaticChecker` też potrzebuje, żyje w `gatekeeper_core.adapters.dotnet_projects` — współdzielone z `NuGetEcosystem.scan_sca`, patrz README core-a.

`G2.cross_verify`/`G2.test_sanity`/`G2.diff_coverage` (weryfikacja krzyżowa testów) nie mają tu odpowiednika — brak zarejestrowanego `TestToolchain` dla C# to `skipped`, nie błąd. Native helper oparty o Roslyn jest zaplanowany jako osobne zlecenie (Faza 3), nie część tego repo dziś — plan architektury i kolejność budowy: 📄 [PLAN-G2.md](PLAN-G2.md).

`calibration/cases.yaml` startuje pusty — zero przypadków C# istniało przed podziałem monolitu; jawny, zaakceptowany dług.

## Szybki start

```bash
pip install -e ".[dev]"
# wymaga .NET SDK 8.0+ zainstalowanego lokalnie (dotnet build)
pytest -q
```

## Testy SAST

```bash
semgrep --test --config gatekeeper_csharp/rules/semgrep rules/semgrep/tests
```
