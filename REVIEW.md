# Review: llm-code-gatekeeper

Data: 2026-09-02  
Zakres: `core/`, `python/`, `ts/`, `csharp/` (silnik + 3 packi językowe)  
Werdykt: **wartościowy, dobrze przemyślany fundament (G0–G3), nie gotowy produkt produkcyjny**

---

## 1. Co to jest

Bramka jakości dla kodu z agentów LLM. Nie pyta „czy testy są zielone”, tylko **czy zmiana może iść na produkcję** — decyzją `PASS` / `PASS-WITH-REVIEW` / `BLOCK`, z uzasadnieniem, fingerprintami i śladem w SQLite.

Założenie projektowe, na którym stoi całość (i które jest prawdziwe):

> testy, lockfile, konfiguracja i opis PR pochodzą od tego samego autora co kod — więc żadnego z nich nie wolno traktować jako niezależnego dowodu.

To odróżnia system od klasycznego CI. Standardowy pipeline łapie „kod nie działa”. Kod z LLM zwykle działa — i to jest problem (halucynowane pakiety, testy-atrapy, sekrety w fixture’ach, diff poza zakresem).

---

## 2. Ocena skrótowa

| Obszar | Ocena | Komentarz |
|---|---|---|
| Problem i model zagrożeń | ★★★★★ | PLAN.md jest jednym z lepszych dokumentów projektowych w tej klasie |
| Architektura silnika | ★★★★☆ | orchestrator, polityka bez `eval`, fail-closed, pluginy |
| G0 / G1.deps / G3.secrets | ★★★★★ | dojrzałe, skalibrowane, testowane |
| G1.static / G3.sast | ★★★★☆ | działa, ale w domyślnej polityce tylko ostrzega |
| G2 (dowód testów) | ★★★☆☆ | bardzo dobry dla Pythona, nie istnieje dla TS/C# |
| G4–G6 | — | zaplanowane, niezaimplementowane |
| Pack TS / C# | ★★☆☆☆ | cienkie: static + semgrep, zero G2, C# bez kalibracji |
| Gotowość operacyjna | ★★☆☆☆ | `warn_only` na prawie wszystkim, CI nie wypchnięte, brak PyPI |
| Bezpieczeństwo runtime | ★★★☆☆ | świadomy sandbox, ale Linux-only i bez kontenera |

**Największa wartość dziś:** `G1.deps` (halucynacja / typosquat) + `G2.cross_verify` (test, który niczego nie dowodzi) + polityka jako kod.

**Największe ryzyko dziś:** domyślna polityka **nie blokuje** static/SAST/SCA/G2. Zespół może dostać zielony Check Run i uznać, że brama „chroni”, podczas gdy chroni naprawdę tylko sekrety, nieistniejące pakiety, typosquat i rozmiar diffa.

---

## 3. Architektura — co jest zrobione dobrze

### 3.1 Podział 4 repo, nie 3

`core` (silnik + 10 bramek jako dispatch) + cienkie packi per język, złożone przez **dwa poziomy entry points**:

1. `gatekeeper.gates` — całe bramki (core rejestruje własne 10)
2. `gatekeeper.static_checkers` / `dep_ecosystems` / `test_toolchains` / `semgrep_rule_packs` — dostawcy *wewnątrz* jednego gate ID

To jest właściwa decyzja. Polityka i orchestrator referencują `G1.static` jako string — rozbicie na `G1.static.python` / `.ts` / `.csharp` rozjechałoby `gates.yaml`. Agregator pętli po zainstalowanych pluginach i sumuje `GateResult`.

Świadome zostawienie PyPI/npm/NuGet w core (nie w packach) jest poprawne: to infrastruktura manifestów, nie kompilatorów.

### 3.2 Fail-closed, nie fail-open

Powtarzający się, spójny wzorzec:

- brak narzędzia → `error`, nie `pass` (`G3.secrets`, `G1.deps` przy martwym rejestrze, `G3.sast` bez packa)
- brak dowodu → `on_gate_error: review`, nie cichy PASS
- raport jawnie wymienia `not_checked` (PLAN.md §9)
- `Finding` bez `failure_scenario` nie potrafi powstać

To jest dojrzałość, której brakuje większości „AI code review” narzędzi.

### 3.3 Polityka bez `eval`

`core/gatekeeper_core/core/policy.py` — mała gramatyka (`fakt OP wartość`), lint literówek w faktach, wyjątki z właścicielem i datą wygaśnięcia, `warn_only` per bramka. Agent nie powinien móc „naprawić” kryteriów oceny — stąd CODEOWNERS na `policy/` i `.github/`.

Fingerprint **bez numeru linii** (`finding.py`) — wyciszenia przeżywają rebase. Rzadko kto o tym pamięta.

### 3.4 Orchestrator

Fale wg grafu zależności, równoległość w fali, budżet czasowy, ścieżka szybka docs-only (ale sekrety zawsze). G4 zadeklarowane w `REQUIRES_GREEN` zanim istnieje — dobra dyscyplina.

### 3.5 Sandbox procesów

Jedyny punkt `subprocess` (`runner.py`):

- scrub zmiennych `TOKEN`/`SECRET`/`PASSWORD`/…
- `unshare --net` bez roota
- RLIMIT pamięci/czasu, zabijanie grupy procesów
- jawny komunikat, gdy izolacji nie ma

Komentarz w module nazywa to granicą bezpieczeństwa, nie detalem. Zgadza się: brama **wykonuje kod z PR-a**.

### 3.6 G2.cross_verify (Python)

Najlepszy stosunek wartości do kosztu w całym systemie:

- nowe testy na worktree ze **starym** kodem
- overlay wyłącznie plików testowych (produkcja nie może wyciec)
- detekcja `pip install -e` (izolacja zepsuta → `error`, nie fałszywy PASS)
- rozróżnienie asercji od błędu importu
- markery `characterization` / `test_backfill` / `refactor_only` z licznikiem nadużyć

Do tego AST-linter jakości testów: brak asercji, `assert True`, echo mocka, `is not None`, połknięty wyjątek. Helper asercji jeden poziom w głąb — świadome ograniczenie, nie naiwny walk.

---

## 4. Pokrycie vs PLAN.md

```
Zaplanowane:  G0 → G1 → G2 → G3 → G4 (LLM) → G5 (człowiek) → G6 (deploy)
Zbudowane:    G0    G1    G2*   G3                         (raport dla człowieka)
```

| Bramka | Python | TS/JS | C# |
|---|---|---|---|
| G0.scope / provenance | core | core | core |
| G1.deps (rejestr, wiek, typosquat) | core (PyPI) | core (npm) | core (NuGet) |
| G1.static | ruff + mypy | tsc + eslint | `dotnet build` |
| G2.cross_verify | tak | **brak** (`skipped`) | **brak** |
| G2.test_sanity | tak (AST) | **brak** | **brak** |
| G2.diff_coverage | tak | **brak** | **brak** |
| G2 mutacje / flaky / kontrakty | brak | brak | brak |
| G3.secrets | gitleaks | gitleaks | gitleaks |
| G3.sast („nigdy”) | 6 reguł | 4 reguły | 4 reguły |
| G3.sca | pip-audit | npm audit | `dotnet list --vulnerable` |
| G3 IaC / licencje | brak | brak | brak |
| G4–G6 | brak | brak | brak |

Kalibracja: 5 przypadków w core (halucynacja PyPI/npm, czysty PR, duży diff, sekret). Python ma własne fixture’y G2/SAST. **C# `calibration/cases.yaml` jest pusty.**

To nie jest zarzut wobec fazy 1 — PLAN.md sam mówi, że G0+G1+G3 to ~60% wartości. Problem: komunikacja na zewnątrz („brama jakości dla kodu z LLM”) sugeruje więcej, niż egzekwuje domyślna polityka.

---

## 5. Znaleziska

### P0 — domyślna polityka prawie nic nie blokuje

`policy/gates.yaml` (core i python) ma w `warn_only`:

- `G1.static`
- `G3.sast`
- `G3.sca`
- `G2.cross_verify`
- `G2.test_sanity`
- `G2.diff_coverage`

`Policy.decide()` degraduje **całą** regułę blocking, jeśli pochodzi z bramki `warn_only`. Skutek: `tests.pass_on_pre_change_code` jest na liście `blocking:`, ale w praktyce to ostrzeżenie.

Naprawdę blokuje tylko:

- sekret w diffie
- nieistniejący pakiet
- typosquat
- diff > 400 linii / 15 plików

Komentarz „USUŃ po tygodniu obserwacji” jest w porządku jako strategia wdrożenia. Jako stan repozytorium po ukończonej Fazie 1 — nie. Konsumujący zespół skopiuje szablon 1:1 i zostanie z bramą-komentatorem.

**Rekomendacja:** szablon produkcyjny vs szablon `warn-only` jako dwa pliki. Albo twardy checklist w USAGE: „zdejmij `warn_only` zanim włączysz Check jako required”.

### P0 — CODEOWNERS to atrapa

```
/policy/  @wlasciciel-bramy
```

Placeholder, nie GitHub handle. Sam plik nic nie egzekwuje (komentarz w pliku to przyznaje). Granica „agent nie zmienia kryteriów oceny” **nie istnieje**, dopóki consuming repo nie włączy branch protection + prawdziwych owners.

### P1 — `allow_packages` normalizuje tylko jako PyPI

```69:71:core/gatekeeper_core/gates/g1_deps.py
        self.allowlist = {
            manifests.normalize(manifests.PYPI, n) for n in self.config.get("allow_packages", [])
        }
```

Dla npm/NuGet allowlista jest zła albo martwa (`lodash` vs `Lodash`, pakiety z `.` w nazwie). Porównywanie idzie przez `provider.normalize(dep.name)` per ekosystem, allowlista — zawsze PEP 503.

### P1 — G2 nie istnieje poza Pythonem

Dla TS/C# trzy bramki, które PLAN.md nazywa „najbardziej wartościową fazą”, dają `skipped`. W mieszanym PR-ze Python+TS brama udowodni testy Pythona i **milcząco odpuści** testy TypeScriptu. Raport ma `not_checked`, ale Check Run i tak może być zielony.

To jest świadomy zakres, nie bug. Jest to jednak dziura w obietnicy „jeden `gatekeeper run` na mieszany stos”.

### P1 — sandbox bez kontenera, Linux-only

Docstring `runner.py` obiecuje opcjonalny `ContainerSandbox`. **Klasy nie ma.** Na macOS/Windows/`unshare` niedostępnym testy z PR-a mają sieć. `describe_isolation()` mówi o tym w raporcie — dobrze — ale `require_isolation: false` jest defaultem.

Brama wykonuje kod agenta obok tokenu CI. To jedyne miejsce, gdzie „fail-closed” jest złamane z wygody.

### P1 — eslint / tsc fail-open przy śmieciowym outputcie

`parse_eslint`: `JSONDecodeError` → `[]` (cisza). Zepsuty eslint wygląda jak czysty przebieg, chyba że `require_eslint: true`. Domyślnie nie jest.

Podobny wzorzec: brak `tsconfig.json` / `.csproj` / configu eslint → bramka **przechodzi** bez dowodu. Uzasadnienie („brak narzędzia konfiguracyjnego ≠ defekt”) jest OK dla adopcji, złe dla bezpieczeństwa. Agent może nie dodać tsconfig i uniknąć tsc.

### P1 — `G1.static` przerywa na pierwszym errorze checkera

Mieszany PR: ruff pada → csharp/ts się nie uruchamiają. Częściowy dowód zostaje w `findings`, ale pozostałe języki nie są sprawdzone. Lepsze: zebrać error per checker, nie abortować fali.

### P2 — podwójna rejestracja bramek

`@register` na klasie **oraz** entry points w `pyproject.toml`. `all_gates()` ładuje EP i skipuje, jeśli ID już jest. Działa, ale dwa źródła prawdy. Pack językowy, który omyłkowo zarejestruje gate ID z core, zostanie zignorowany bez błędu.

### P2 — dryf polityki między 4 repo

`policy/gates.yaml` skopiowany do core/python/ts/csharp. Komentarze już się rozjeżdżają (`ruff/mypy` vs `pip-audit` vs ogólne). Nie ma testu, że szablony są zsynchronizowane.

### P2 — wersje pakietów

`llm-code-gatekeeper-core` 0.1.0, `…-python` **0.2.0**, ts/csharp 0.1.0. Zależność packów: `core>=0.1.0` bez górnego bound. Po pierwszym breaking change w core packi zainstalują się i padną w runtime.

Brak publikacji na PyPI — instalacja z gita. OK na teraz, bolesne w CI (niepinowane commity).

### P2 — budżet orchestratora nie zabija wątku Pythona

Udokumentowane. Po timeoutcie wątek dobiega w tle, może trzymać worktree/sandbox. Przy równoległych PR-ach w tym samym runnerze — wyścig na dysku/sieci.

### P2 — homoglify typosquatu niepełne

Zwijane: kilka cyrylicy + `rn`→`m`. Brakuje m.in. `ο`/`і` w innych pozycjach, `ӏ`, `ɡ`. Dla MVP wystarczy; jako jedyna linia obrony przed slopsquat — nie.

### P2 — testy e2e Pythona importują `core.sequence`

Kompatybilność wsteczna (re-export). Martwy moduł do usunięcia, zanim ktoś zacznie od niego pisać nowy kod.

### P2 — CI/workflow nie na GitHubie

Znany dług: token bez scope `workflow`. Lokalnie pliki są. Na GitHubie — nie. Packi żyją bez CI.

---

## 6. Jakość kodu

Mocne:

- Python 3.12, mypy `--strict`, ruff, dataclasses, Protocols
- komentarze w kodzie tłumaczą *dlaczego*, nie *co* (sandbox CoreCLR/`memory_mb=None`, fingerprint bez linii, lockfile poza limitem diffa)
- testy na zachowanie, nie na mocki narzędzi: golden files semgrep/gitleaks/pip-audit/npm/dotnet, żywe `unshare`, kalibracja na fixture’ach git
- sekrety nie wyciekają do raportu (osobny test)
- `only_changed_lines` — bez tego pierwsze wdrożenie na starym repo zabija projekt

Słabsze:

- Protocol `EcosystemProvider.parse_manifest → set[Any]` — ucieczka od typów
- `TestToolchain` protokoły (`DiscoveryResult`, `CoverageReport`) są luźne; `produce_coverage_report` zwraca gotowy `DiffCoverageResult` Pythona, nie LCOV — utrudni TS/C#
- python/tests nadal dubluje część testów core (kalibracja, e2e polityki) — koszt utrzymania po splicie
- ts: 12 testów, csharp: 5. Packi są cienkie, ale C# nie ma nawet semgrep-gate testu ani kalibracji
- `python/tests/__pycache__` ze starymi testami core (`test_gate_deps`, `test_orchestrator`, …) — śmieci po ekstrakcji

Docstringi są wyjątkowo dobre. PLAN.md / USAGE.md / README warstw są spójne i uczciwe wobec luk. To rzadkość.

---

## 7. Bezpieczeństwo (brama jako atakowany system)

Brama jest ciekawszym celem niż typowy linter: uruchamia kod z PR, czyta sekrety CI, decyduje o merge.

Zrobione dobrze:

- scrub env
- sieć off by default
- testy z PR bez dostępu do sieci (jest test)
- overlay G2 nie kopiuje produkcji do base worktree
- izolacja edytowalnej instalacji
- sekrety redagowane w findings
- gitleaks też na testach/fixture’ach
- `keep_env` jest allowlistą, nie denylistą wyjątków rozsianych po kodzie

Nierozwiązane:

- brak kontenera / read-only mount (obiecywane)
- Windows/macOS = brak netns
- `keep_env` w polityce ocenianego repo — agent z prawem do `policy/` przywraca `GITHUB_TOKEN`
- Semgrep/dotnet z `memory_mb=None` — świadomy wyjątek, ale RLIMIT nie chroni przed fork-bombą (`max_processes` też None, z uzasadnieniem)
- brak pinowania narzędzi (gitleaks w CI ściągany z GitHuba po tagu, OK; `npm install --global typescript eslint` — nie)

---

## 8. UX / wdrożenie

Dobrze:

- kody wyjścia 0/1/2/3, `--fail-on block|review|never`
- raport markdown z markerem do edycji komentarza PR (nie spam)
- `gatekeeper policy lint/facts`, `calibrate`, `verdict`, `incident`, `metrics`
- fast path na docs, lockfile nie liczy się do limitu diffa
- jeden `gatekeeper run` na mieszany stos

Źle / tarcie:

- trzy pakiety + gitleaks + semgrep + ruff/mypy albo tsc/eslint albo dotnet SDK — ciężki bootstrap
- nic na PyPI
- szablon workflow w `python/.github/` „uniwersalny mimo nazwy katalogu” — mylące
- G2 wymaga, żeby zależności *ocenianego* projektu dały się zainstalować w env bramy

---

## 9. Co zostawić, co zmienić, co odłożyć

### Zostawić

- dwupoziomowe entry points i gate ID jako stałe stringi
- fail-closed + `not_checked` w raporcie
- polityka bez eval, fingerprint bez linii, wygasające wyjątki
- G2.cross_verify z kontrolą izolacji
- dep-guard (istnienie / wiek / typosquat) w core
- kalibracja jako regresja samej bramy

### Zmienić teraz (mały koszt, duży efekt)

1. Rozdzielić `policy/gates.yaml` na `warn-only` (adopcja) i `enforcing` (produkcja); w enforcing zdjąć `warn_only` z G1.static / G3.sast / G3.sca / G2.*.
2. Naprawić `allow_packages` — normalizacja per ekosystem.
3. Zamieć `JSONDecodeError → []` w parserze eslint (i analogach) na `ToolFailed`.
4. CODEOWNERS: prawdziwe handle albo usunąć plik, żeby nie udawał ochrony.
5. Pin `llm-code-gatekeeper-core>=0.1.0,<0.2` w packach; wyrównać numery wersji.
6. Wypchnąć CI (scope `workflow`) albo udokumentować, że GitHub nie testuje packów.
7. Dwa pliki polityki packów: nie kopiować ręcznie — generować z core albo testować identyczność kluczy `blocking`/`thresholds`.

### Odłożyć (zgodnie z planem, ale nazwać długiem)

- TestToolchain TS (tsc API / vitest) i C# (Roslyn / `dotnet test`) — bez tego „mieszany stos” jest obietnicą
- kalibracja C#
- G4 panel LLM **dopiero** po zdjęciu warn_only z G1–G3 i precyzji >80% na kalibracji (PLAN.md już to mówi — trzymać się tego)
- mutacje, flaky, kontrakty, IaC, licencje
- `ContainerSandbox`
- publikacja PyPI

Nie budować G4, dopóki domyślna polityka nie blokuje G1/G3. Inaczej drogi, niedeterministyczny recenzent przykryje dziury, których tanie bramki już umieją strzec.

---

## 10. Werdykt recenzenta

To nie jest kolejny wrapper na Semgrep. Jest **model defektów specyficznych dla agentów** i silnik, który ten model egzekwuje uczciwie: brak dowodu ≠ pass, luki są nazwane, polityka jest kodem.

Faza 1 (pluginowy core + split 4 repo) jest zrobiona na poziomie, którego nie wstyd pokazać. G2 dla Pythona jest wyróżniające się.

Nie jest to jeszcze brama, której można zaufać jako required check na mieszanym repo produkcyjnym:

- domyślnie nie blokuje static/SAST/SCA/testów,
- TS/C# nie mają dowodu behawioralnego,
- ochrona `policy/` jest papierowa,
- izolacja procesów nie jest twarda.

**Ocena: 7.5/10 jako fundament badawczo-inżynierski; 5/10 jako gotowy gate w CI.**  
Najlepszy następny krok to nie nowa bramka, tylko **włączenie tych, które już są**, plus naprawa P0/P1 z sekcji 5.
