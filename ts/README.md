# llm-code-gatekeeper-ts

Pack TS/JS dla [`llm-code-gatekeeper-core`](../core/README.md) — instaluje się razem z core i rejestruje się przez entry points, bez patcha w core.

Dostarcza:

- `TsJsStaticChecker` (`gatekeeper.static_checkers`, `checker_id="ts_js"`) — `tsc --noEmit` (kontrola typów, dla TS/JS to samo co `mypy` dla Pythona) + `eslint` (reguły „problem”, nie styl). Konsumowany przez `G1.static` (core).
- `TsComplexityAnalyzer` (`gatekeeper.complexity_analyzers`, `analyzer_id="ts"`) — złożoność cyklomatyczna (McCabe) przez regułę `complexity` eslinta (próg 1, żeby wymusić raport per funkcja). Konsumowany przez `G1.complexity` (core). Wymaga `@typescript-eslint/parser` dla plików `.ts`/`.tsx` (adnotacje typów, których domyślny parser eslinta nie rozumie).
- `TsRulePack` (`gatekeeper.semgrep_rule_packs`, `pack_id="ts"`) — reguły „nigdy” specyficzne dla TS/JS (`no-dangerous-html-unsanitized`, `no-eval-on-input-js`, `no-shell-true-js`, `no-tls-verify-disabled-js`). Konsumowany przez `G3.sast` (core).
- `TsTestToolchain` (`gatekeeper.test_toolchains`, `languages=("typescript", "javascript")`) — discovery testów i linter ich jakości na drzewie ESTree (`tools/helper.cjs`, helper w Node), weryfikacja krzyżowa przez **vitest albo jest**, pokrycie różnicowe przez ich raport Cobertura + `diff-cover`. Konsumowany przez `G2.cross_verify`, `G2.test_sanity` i `G2.diff_coverage` (wszystkie core-owe). Architektura i uzasadnienia: [PLAN-G2.md](PLAN-G2.md).

`TsTestToolchain` jest jedynym toolchainem deklarującym dwa języki naraz (`languages`, nie `language`): jeden przebieg vitesta/jesta obsługuje `.ts` i `.js` i produkuje jeden raport pokrycia, więc rozbicie go na dwóch dostawców oznaczałoby dwa przebiegi po tym samym repo.

Manifest+rejestr+typosquat+SCA dla npm (`G1.deps`/`G3.sca`) **nie** jest tu — `NpmEcosystem` żyje w core (`llm-code-gatekeeper-core`) i działa nawet bez tego pack'a zainstalowanego, patrz README core-a, sekcja „Architektura pluginów”.

## Szybki start

```bash
pip install -e ".[dev]"
npm install --global typescript eslint @typescript-eslint/parser   # binarki/parsery, nie zależności Pythona
pytest -q
```

## Testy SAST

```bash
semgrep --test --config gatekeeper_ts/rules/semgrep rules/semgrep/tests
```
