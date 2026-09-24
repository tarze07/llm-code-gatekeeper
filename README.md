# llm-code-gatekeeper

Brama jakości dla kodu generowanego przez agentów LLM. Jedno repozytorium, pięć pakietów Pythona wydawanych niezależnie:

```
llm-code-gatekeeper/
├── core/     llm-code-gatekeeper-core      (silnik + CLI)
├── python/   llm-code-gatekeeper-python    (pack Python)
├── ts/       llm-code-gatekeeper-ts        (pack TS/JS)
├── csharp/   llm-code-gatekeeper-csharp    (pack C#)
└── web/      llm-code-gatekeeper-web       (panel WWW, opcjonalny)
```

`core` dostarcza silnik (orchestrator, polityka, CLI `gatekeeper`) i 11 bramek jako logikę dispatchu — sam nie zna żadnego konkretnego języka poza PyPI/npm/NuGet (te trzy ekosystemy są język-agnostyczne, więc żyją w core). Każdy pack (`python`/`ts`/`csharp`) dorejestrowuje przez [entry points](https://packaging.python.org/en/latest/specifications/entry-points/) obsługę jednego języka — bez tego mechanizmu core musiałby importować kod każdego pack'a wprost. Wspólne repo nie zmienia tej granicy: packi **nadal** instaluje się osobno i core nadal nie importuje żadnego z nich.

`web` to osobna sprawa niż packi: nie dokłada żadnej bramki ani obsługi języka. Daje przeglądarkę raportów i lokalny panel, który uruchamia **ten sam silnik co CLI** (wspólne `core.service`), z kolejką i osobnym procesem nadzorcy. Zależy od `core` w jedną stronę i nikt nie musi go instalować, żeby używać CLI. Szczegóły: [`web/README.md`](web/README.md).

Pełny opis architektury (dwa poziomy grup entry points, kontrakty pluginów) jest w [`core/README.md`](core/README.md) — to on jest właściwym punktem wejścia do zrozumienia systemu; ten plik to ściągawka „jak z tym pracować".

Uruchamianie narzędzi wymaga **Linuksa i Bubblewrap** (`sudo apt-get install
bubblewrap` na Ubuntu/Debian). Każda bramka analizuje osobną kopię wskazanego
commita i ma egzekwowany limit czasu. Wymagania dotyczące zależności oraz
zakres izolacji opisuje [core/SECURITY.md](core/SECURITY.md).

### Czym bramki się posługują

Bramki wołają zewnętrzne programy jako podprocesy i **nie instalują ich za
Ciebie**. Brak narzędzia daje `error` bramki, czyli „brak dowodu" — nigdy
cichego `pass`. Zakładka **Środowisko** w panelu pokazuje, czego brakuje.

| narzędzie | skąd | czego dotyczy |
|---|---|---|
| `git`, `bwrap` | dystrybucja | zakres zmiany i izolacja — wymagane zawsze |
| `semgrep`, `gitleaks`, `diff-cover` | zależności `core` i packów (`pip`) | `G3.sast`, `G3.secrets`, `G2.diff_coverage` |
| `node`, `npm` | dystrybucja albo menedżer wersji | pack TS/JS |
| `dotnet` (SDK 8+) | instalator Microsoftu | pack C# |
| `gatekeeper-cs-helper` | **osobno**, patrz niżej | `G1.complexity` i `G2.*` w packu C# |

`gatekeeper-cs-helper` (Roslyn) jest jedynym, którego nie dociągnie żaden
`pip install` — to program .NET z [`csharp/tools/`](csharp/tools/):

```bash
cd csharp/tools/gatekeeper-cs-helper
dotnet pack -c Release -o /tmp/cs-helper-nupkg
dotnet tool install --global gatekeeper-cs-helper --add-source /tmp/cs-helper-nupkg
```

Ląduje w `~/.dotnet/tools`, którego `dotnet tool install` **nie dokłada** do
`PATH` — wypisuje instrukcję i zostawia to operatorowi. Gdy .NET stoi
w katalogu domowym (`~/.dotnet`, instalacja bez pakietu dystrybucji), shim
narzędzia potrzebuje dodatkowo `DOTNET_ROOT=$HOME/.dotnet`, inaczej kończy się
„You must install .NET to run this application" mimo działającego `dotnet
--version`. Panel WWW ustawia jedno i drugie sam; CLI bierze to, co zastanie
w powłoce.

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

Od Fazy 1 doszły: nowa bramka **`G1.complexity`** (złożoność cyklomatyczna, McCabe) z odpowiednikiem we wszystkich trzech pack'ach ([`core/PLAN-G1-complexity.md`](core/PLAN-G1-complexity.md)) oraz rodzina **`G2.cross_verify`/`test_sanity`/`diff_coverage`** dla C# (helper Roslyn, [`csharp/PLAN-G2.md`](csharp/PLAN-G2.md)) i dla TS/JS (helper ESTree + vitest/jest, [`ts/PLAN-G2.md`](ts/PLAN-G2.md)).

Od 2026-09-04 **wszystkie trzy języki mają komplet G0–G3** — nie ma już języka, dla którego G2.* dawałoby `skipped` z braku toolchaina. Zakres per język jest jednak różny: Python i C# filtrują pojedyncze testy, TS/JS uruchamia całe pliki testowe (uzasadnienie: `ts/PLAN-G2.md` §3.1), a lista obsługiwanych runnerów to pytest / xUnit / vitest+jest — repo na mocha, NUnit czy MSTest dostanie `error`, nie cichy `pass`.

Panel WWW (`web/`) domyka drogę od świeżej instalacji do pierwszej kontroli:
profil polityki startowej powstaje jednym kliknięciem (panel wozi własną kopię
`core/policy/` w danych pakietu, żeby profil raz aktywowany oceniał tym samym,
czym oceniał wczoraj), projekt zakłada się razem ze ścieżką repozytorium
i profilem, a wersję bazową i ocenianą wybiera się z list wypełnionych
zawartością ocenianego repo — gałęzie lokalne i zdalne, tagi oraz ostatnie
commity. Podgląd zakresu pokazuje commity liczone od **merge-base**, czyli to
samo, co oceniany diff. Panel ma skórkę jasną i ciemną (domyślnie za
ustawieniem systemu, bez JavaScriptu). Czego **nie** ma, mówi wprost
[`web/CONTRACT.md`](web/CONTRACT.md) §11.

Przegląd stanu i znalezisk: [`REVIEW.md`](REVIEW.md). Zapis podziału na packi: [`PODSUMOWANIE.md`](PODSUMOWANIE.md).

CI: `.github/workflows/ci.yml` w korzeniu — cztery joby (`core`, `python`, `ts`, `csharp`), każdy z `working-directory` na swoim katalogu, z core'em instalowanym z checkoutu (`pip install -e ../core`), nie z GitHuba. Dzięki temu PR ruszający core i pack naraz jest testowany razem. Cztery pliki `ci.yml` leżące wcześniej per pack są w monorepo martwe (GitHub Actions czyta wyłącznie korzeń) — zastąpione tym jednym; `python/.github/workflows/gatekeeper.yml` **zostaje**, bo to szablon integracji dla *ocenianego* repo, nie CI tego repo.

Uwaga o pushu: GitHub odrzuca **każdy** push tykający `.github/workflows/`,
gdy token nie ma zakresu `workflow` (`remote rejected … without workflow
scope`). Dotyczy to commitów `d0707a0`, `08409d5` i `1887617`. Gałąź
`feat/panel-web` została wypchnięta kluczem SSH, który temu nie podlega, więc
te commity **są** już na zdalnym. Trwałe odblokowanie drogi po HTTPS:
`gh auth refresh -h github.com -s workflow` albo przestawienie remote'u na SSH
(`git remote set-url origin git@github.com:tarze07/llm-code-gatekeeper.git`).

Znany dług: `@wlasciciel-bramy` w `.github/CODEOWNERS` to nadal placeholder, nie istniejący handle (REVIEW.md §5, P0).
