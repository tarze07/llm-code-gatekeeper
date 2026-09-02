# llm-code-gatekeeper-core

Silnik bramy jakości dla kodu generowanego przez agentów LLM — **rdzeń**, wydzielony z [`llm-code-gatekeeper`](https://github.com/tarze07/llm-code-gatekeeper) tak, żeby jedna poprawka orchestratora/policy/store nie wymagała ręcznej propagacji do wszystkich pack'ów językowych.

Sam w sobie nie ocenia żadnego konkretnego języka — dostarcza:

- `core/` — `ChangeContext` (git), `Finding`/`GateResult`/`Decision`, silnik polityki (`policy/gates.yaml`, bez `eval`), raport do PR, ślad przebiegów w SQLite, metryki, sandbox (izolacja sieci/pamięci/czasu), orchestrator (fale bramek wg zależności, budżety, ścieżka szybka dla docs-only).
- `gates/` — dziesięć bramek: `G0.provenance`, `G0.scope`, `G3.secrets` (w pełni language-agnostic) oraz pięć **agregatorów** (`G1.static`, `G1.deps`, `G3.sca`, `G3.sast`, `G2.cross_verify`/`G2.test_sanity`/`G2.diff_coverage`) — te ostatnie nie mają żadnej logiki językowej, tylko pętlę po zainstalowanych dostawcach poziomu 2.
- `core/plugins.py` — kontrakty pluginów: `StaticChecker`, `EcosystemProvider`, `TestToolchain`, `SemgrepRulePackProvider`.
- CLI (`gatekeeper run/policy/calibrate/verdict/incident/metrics`).

## Architektura pluginów

Dwa poziomy:

1. **`gatekeeper.gates`** (entry points) — rejestruje całe bramki. Ten pakiet rejestruje przez tę grupę własnych dziesięć bramek; pack językowy może w przyszłości dodać nową bramkę bez dotykania tego repo.
2. **Cztery mniejsze grupy** — dostawca *wewnątrz* jednej logicznej bramki, żeby `G1.static` (itd.) zostało jednym gate ID niezależnie od liczby zainstalowanych pack'ów:

   | Grupa | Protokół | Kto odkrywa | Kto dostarcza |
   |---|---|---|---|
   | `gatekeeper.static_checkers` | `StaticChecker` | `gates/g1_static.py` | pack językowy |
   | `gatekeeper.dep_ecosystems` | `EcosystemProvider` | `gates/g1_deps.py`, `gates/g3_sca.py` | **ten pakiet** (`deps/ecosystems.py`: PyPI/npm/NuGet) |
   | `gatekeeper.test_toolchains` | `TestToolchain` | `gates/g2_*.py` | pack językowy |
   | `gatekeeper.semgrep_rule_packs` | `SemgrepRulePackProvider` | `gates/g3_sast.py` | ten pakiet (reguła uniwersalna) + pack językowy |

`dep_ecosystems` jest wyjątkiem od reguły „core nie zna żadnego języka": manifest+rejestr+typosquat+SCA per ekosystem pakietów (`deps/manifests.py`, `deps/registries.py`, `deps/typosquat.py`, `deps/ecosystems.py`, `adapters/sca.py`, `adapters/dotnet_projects.py`) to wyłącznie infrastruktura bramek `G1.deps`/`G3.sca`, które same są core-owe — nie ma tu logiki specyficznej dla kompilatora/testów danego języka (to zostaje w packach), więc rozdzielanie tego na 3 kopie tylko duplikowałoby te same parsery manifestów bez żadnej korzyści.

Bez zainstalowanego pack'a językowego bramki-agregatory nie zgłaszają błędu — dają `skipped`/`pass` bez dowodu (nie ma czego sprawdzić). `G0.provenance`/`G0.scope`/`G3.secrets` i `G1.deps`/`G3.sca` (PyPI/npm/NuGet) działają zawsze, nawet bez żadnego pack'a zainstalowanego.

## Pack'i językowe

- [`llm-code-gatekeeper`](https://github.com/tarze07/llm-code-gatekeeper) — Python (ruff/mypy, testy przez `ast`)
- [`llm-code-gatekeeper-ts`](https://github.com/tarze07/llm-code-gatekeeper-ts) — TS/JS (tsc/eslint)
- [`llm-code-gatekeeper-csharp`](https://github.com/tarze07/llm-code-gatekeeper-csharp) — C# (`dotnet build`)

Każdy instaluje ten pakiet jako zależność i rejestruje swojego `StaticChecker`/`TestToolchain`/`SemgrepRulePackProvider` przez powyższe grupy entry points — bez patcha w tym repo. Dostawców `dep_ecosystems` (PyPI/npm/NuGet) żaden pack nie rejestruje — są już tutaj.

## Szybki start

```bash
pip install -e ".[dev,gates]"
gatekeeper policy lint --policy policy/gates.yaml
gatekeeper calibrate   # 5 przypadków core-only, w tym PyPI i npm (patrz kalibracja/cases.yaml)
```

Pełny zestaw kalibracyjny (typosquat/SCA/SAST/G2 per język) żyje w `calibration/` każdego pack'a — tam, gdzie odpowiedni dostawca faktycznie jest zainstalowany.

📄 [PLAN.md](PLAN.md) — pełny plan i architektura G0–G6 (napisany dla całego systemu, nie tylko core-a).
