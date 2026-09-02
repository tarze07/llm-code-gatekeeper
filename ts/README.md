# llm-code-gatekeeper-ts

Pack TS/JS dla [`llm-code-gatekeeper-core`](https://github.com/tarze07/llm-code-gatekeeper-core) — instaluje się razem z core i rejestruje się przez entry points, bez patcha w core.

Dostarcza:

- `TsJsStaticChecker` (`gatekeeper.static_checkers`, `checker_id="ts_js"`) — `tsc --noEmit` (kontrola typów, dla TS/JS to samo co `mypy` dla Pythona) + `eslint` (reguły „problem”, nie styl). Konsumowany przez `G1.static` (core).
- `TsRulePack` (`gatekeeper.semgrep_rule_packs`, `pack_id="ts"`) — reguły „nigdy” specyficzne dla TS/JS (`no-dangerous-html-unsanitized`, `no-eval-on-input-js`, `no-shell-true-js`, `no-tls-verify-disabled-js`). Konsumowany przez `G3.sast` (core).

Manifest+rejestr+typosquat+SCA dla npm (`G1.deps`/`G3.sca`) **nie** jest tu — `NpmEcosystem` żyje w core (`llm-code-gatekeeper-core`) i działa nawet bez tego pack'a zainstalowanego, patrz README core-a, sekcja „Architektura pluginów”.

`G2.cross_verify`/`G2.test_sanity`/`G2.diff_coverage` (weryfikacja krzyżowa testów) nie mają tu odpowiednika — brak zarejestrowanego `TestToolchain` dla TS/JS to `skipped`, nie błąd. Native helper oparty o TypeScript Compiler API jest zaplanowany jako osobne zlecenie (Faza 2), nie część tego repo dziś.

## Szybki start

```bash
pip install -e ".[dev]"
npm install --global typescript eslint   # tsc/eslint jako binarki, nie zależności Pythona
pytest -q
```

## Testy SAST

```bash
semgrep --test --config gatekeeper_ts/rules/semgrep rules/semgrep/tests
```
