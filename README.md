# llm-code-gatekeeper

Brama jakości dla kodu generowanego przez agentów LLM. Jedno repozytorium, cztery pakiety Pythona wydawane niezależnie:

```
llm-code-gatekeeper/
├── core/     llm-code-gatekeeper-core      (silnik + CLI)
├── python/   llm-code-gatekeeper-python    (pack Python)
├── ts/       llm-code-gatekeeper-ts        (pack TS/JS)
└── csharp/   llm-code-gatekeeper-csharp    (pack C#)
```

`core` dostarcza silnik (orchestrator, polityka, CLI `gatekeeper`) i 11 bramek jako logikę dispatchu — sam nie zna żadnego konkretnego języka poza PyPI/npm/NuGet (te trzy ekosystemy są język-agnostyczne, więc żyją w core). Każdy pack (`python`/`ts`/`csharp`) dorejestrowuje przez [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) obsługę jednego języka — bez tego mechanizmu core musiałby importować kod każdego pack'a wprost. Wspólne repo nie zmienia tej granicy: packi **nadal** instaluje się osobno i core nadal nie importuje żadnego z nich.

Pełny opis architektury (dwa poziomy grup entry points, kontrakty pluginów) jest w [`core/README.md`](core/README.md) — to on jest właściwym punktem wejścia do zrozumienia systemu; ten plik to ściągawka „jak z tym pracować".

## Używanie bramy na cudzym repo (typowy przypadek)

Nie trzeba klonować niczego z tego katalogu — instaluje się z GitHuba w **ocenianym** repozytorium (żaden z pakietów nie jest jeszcze na PyPI):

```bash
pip install "llm-code-gatekeeper-core @ git+https://github.com/tarze07/llm-code-gatekeeper.git#subdirectory=core"

# dołóż pack(i) dla języków, które faktycznie występują w ocenianym repo:
pip install "llm-code-gatekeeper-python @ git+https://github.com/tarze07/llm-code-gatekeeper.git#subdirectory=python"
pip install "llm-code-gatekeeper-ts @ git+https://github.com/tarze07/llm-code-gatekeeper.git#subdirectory=ts"
pip install "llm-code-gatekeeper-csharp @ git+https://github.com/tarze07/llm-code-gatekeeper.git#subdirectory=csharp"

gatekeeper policy lint --policy policy/gates.yaml
gatekeeper run --repo /ścieżka/do/ocenianego/repo --base origin/main
```

`policy/gates.yaml` (i `scope_map.yaml`/`exceptions.yaml`) trzeba mieć w ocenianym repo — kopia startowa jest w [`core/policy/`](core/policy/). Gotowy szablon integracji z GitHub Actions: [`python/.github/workflows/gatekeeper.yml`](python/.github/workflows/gatekeeper.yml) (mimo nazwy katalogu — to uniwersalny workflow, nie coś specyficznego dla Pythona; zależności doinstalowuje pod oceniane repo).

Pełna instrukcja krok po kroku: [`python/USAGE.md`](python/USAGE.md).

## Praca nad samą bramą (rozwój)

Każdy pack ma **własny** `.venv` i własny zestaw testów — nie ma jednego wspólnego środowiska dla całego repo. Do pracy nad jednym pack'iem (np. `python`) z core'em zainstalowanym edytowalnie:

```bash
cd core && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,gates]"
pytest -q && ruff check gatekeeper_core tests && mypy gatekeeper_core --strict

cd ../python && python3 -m venv .venv && source .venv/bin/activate
pip install -e ../core                 # core z dysku, nie z GitHuba
pip install -e ".[dev,gates]"
pytest -q
```

Analogicznie dla `ts`/`csharp` (`pip install -e ../core` + `pip install -e ".[dev]"`).

**Weryfikacja mieszanego stosu** (wszystkie 4 naraz w jednym środowisku — tak działa docelowy użytkownik z Pythonem+TS+C# w jednym repo):

```bash
python3 -m venv /tmp/mixed-venv && source /tmp/mixed-venv/bin/activate
pip install -e core -e python -e ts -e csharp
python -c "from importlib.metadata import entry_points as ep; print(sorted(e.name for e in ep(group='gatekeeper.gates')))"
# -> 11 gate ID niezależnie od tego, ile pack'ów zainstalowanych
```

## Stan projektu

Faza 1 (silnik pluginowy + rozdzielenie na core i trzy packi) jest **ukończona**. Kod żył przez chwilę w czterech osobnych repozytoriach; zostały scalone z powrotem w to jedno, z zachowaniem pełnej historii każdego pliku (`git log -- core/` pokazuje 24 commity core'a, nie jeden merge). Granica architektoniczna między core a packami jest w entry pointach, nie w liczbie repozytoriów.

Od Fazy 1 doszły: `G2.cross_verify`/`test_sanity`/`diff_coverage` dla C# (helper Roslyn, `csharp/tools/gatekeeper-cs-helper`) — istnieją dziś dla Pythona i C#, TS/JS zostaje świadomie odłożone (native helper na TypeScript Compiler API, osobne zlecenie; brak zarejestrowanego `TestToolchain` to `skipped`, nie błąd) — oraz nowa bramka **`G1.complexity`** (złożoność cyklomatyczna, McCabe) z odpowiednikiem we wszystkich trzech pack'ach jednocześnie ([`core/PLAN-G1-complexity.md`](core/PLAN-G1-complexity.md)).

Przegląd stanu i znalezisk: [`REVIEW.md`](REVIEW.md). Zapis podziału na packi: [`PODSUMOWANIE.md`](PODSUMOWANIE.md).

CI: `.github/workflows/ci.yml` w korzeniu — cztery joby (`core`, `python`, `ts`, `csharp`), każdy z `working-directory` na swoim katalogu, z core'em instalowanym z checkoutu (`pip install -e ../core`), nie z GitHuba. Dzięki temu PR ruszający core i pack naraz jest testowany razem. Cztery pliki `ci.yml` leżące wcześniej per pack są w monorepo martwe (GitHub Actions czyta wyłącznie korzeń) — zastąpione tym jednym; `python/.github/workflows/gatekeeper.yml` **zostaje**, bo to szablon integracji dla *ocenianego* repo, nie CI tego repo.

Znany dług: ten korzeniowy workflow (i scalony `.github/CODEOWNERS`) leżą w commicie, którego nie da się wypchnąć tokenem bez scope `workflow` — GitHub blokuje każdy push tykający `.github/workflows/` w korzeniu. Odblokowanie: `gh auth refresh -h github.com -s workflow`, potem `git push`. Drugi, niezależny dług: `@wlasciciel-bramy` w CODEOWNERS to nadal placeholder, nie istniejący handle (REVIEW.md §5, P0).
