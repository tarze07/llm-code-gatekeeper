# llm-code-gatekeeper-csharp

Pack C# dla [`llm-code-gatekeeper-core`](https://github.com/tarze07/llm-code-gatekeeper-core) — instaluje się razem z core i rejestruje się przez entry points, bez patcha w core.

Dostarcza:

- `CsharpStaticChecker` (`gatekeeper.static_checkers`, `checker_id="csharp"`) — `dotnet build` (Roslyn pełni podwójną rolę ruff+mypy naraz — kontrola typów w trybie ścisłym bez osobnego narzędzia). Konsumowany przez `G1.static` (core).
- `CsharpRulePack` (`gatekeeper.semgrep_rule_packs`, `pack_id="csharp"`) — reguły „nigdy” specyficzne dla C# (`no-tls-verify-disabled-cs`, `no-sql-string-concat-cs`, `no-shell-true-cs`, `no-unsafe-deserialization-cs`). Konsumowany przez `G3.sast` (core).
- `CsharpTestToolchain` (`gatekeeper.test_toolchains`, `language="csharp"`) — weryfikacja krzyżowa nowych testów, linter jakości testów i pokrycie różnicowe. Konsumowany przez `G2.cross_verify`/`G2.test_sanity`/`G2.diff_coverage` (core). Wymaga [`gatekeeper-cs-helper`](tools/gatekeeper-cs-helper/) (`dotnet tool install --global gatekeeper-cs-helper`) — mały helper .NET oparty o Roslyn, wykrywa i lintuje testy `[Fact]`/`[Theory]` (xUnit; NUnit/MSTest jako rozszerzenie w przyszłości). Architektura i decyzje projektowe: 📄 [PLAN-G2.md](PLAN-G2.md).

Manifest+rejestr+typosquat+SCA dla NuGet (`G1.deps`/`G3.sca`) **nie** jest tu — `NuGetEcosystem` żyje w core (`llm-code-gatekeeper-core`) i działa nawet bez tego pack'a zainstalowanego. Odnajdywanie projektów (`find_project_for`/`projects_for`), które `CsharpStaticChecker`/`CsharpTestToolchain` też potrzebują, żyje w `gatekeeper_core.adapters.dotnet_projects` — współdzielone z `NuGetEcosystem.scan_sca`.

## Szybki start

```bash
pip install -e ".[dev]"
# wymaga .NET SDK 8.0+ lokalnie (dotnet build/test) oraz helpera:
cd tools/gatekeeper-cs-helper && dotnet pack -c Release -o /tmp/cs-helper-nupkg && cd ../..
dotnet tool install --global gatekeeper-cs-helper --add-source /tmp/cs-helper-nupkg
# (dopóki gatekeeper-cs-helper nie jest opublikowany na NuGet.org — patrz PLAN-G2.md)
pytest -q
```

## Testy SAST

```bash
semgrep --test --config gatekeeper_csharp/rules/semgrep rules/semgrep/tests
```

## Testy helpera (Roslyn)

```bash
cd tools/GatekeeperCsHelper.Tests && dotnet test
```
