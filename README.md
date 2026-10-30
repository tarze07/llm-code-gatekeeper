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

Uruchamianie narzędzi wymaga izolacji: na Linuksie **Bubblewrap**
(`sudo apt-get install bubblewrap`), na Windows 11 (i opcjonalnie na Linuksie)
**kontenera** — Docker albo Podman z obrazem `gatekeeper-tools`:

```bash
docker build -t gatekeeper-tools:latest -f container/Dockerfile .
GATEKEEPER_SANDBOX=container gatekeeper container check   # na Windows to domyślny backend
gatekeeper container init --repo /ścieżka/do/repo          # obraz projektu z zależnościami
```

Każda bramka analizuje osobną kopię wskazanego commita i ma egzekwowany limit
czasu. Wymagania dotyczące zależności oraz
zakres izolacji opisuje [core/SECURITY.md](core/SECURITY.md).

### Czym bramki się posługują

Bramki wołają zewnętrzne programy jako podprocesy i **nie instalują ich za
Ciebie**. Brak narzędzia daje `error` bramki, czyli „brak dowodu" — nigdy
cichego `pass`. Zakładka **Środowisko** w panelu pokazuje, czego brakuje.

| narzędzie | skąd | czego dotyczy |
|---|---|---|
| `git` + `bwrap` albo Docker/Podman | dystrybucja / Docker Desktop | zakres zmiany i izolacja — wymagane zawsze |
| `semgrep`, `diff-cover` | zależności `core` i packów (`pip`) | `G3.sast`, `G2.diff_coverage` |
| `gitleaks` | binarka z [wydań gitleaks](https://github.com/gitleaks/gitleaks/releases) — `pip` jej nie instaluje | `G3.secrets` |
| `node`, `npm` | dystrybucja albo menedżer wersji | pack TS/JS |
| `dotnet` (SDK 8+) | instalator Microsoftu | pack C# |
| `gatekeeper-cs-helper` | **osobno**, patrz niżej | `G1.complexity` i `G2.*` w packu C# |

Z backendem `container` wszystkie narzędzia z tabeli poza `git` są w obrazie
`gatekeeper-tools` — na hoście potrzebny jest tylko git i Docker/Podman.

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

Nie trzeba klonować niczego z tego katalogu — instaluje się pakiety wydane na PyPI (lub lokalnie w trybie edytowalnym) w **ocenianym** repozytorium:

```bash
pip install llm-code-gatekeeper-core

# dołóż pack(i) dla języków, które faktycznie występują w ocenianym repo:
pip install llm-code-gatekeeper-python
pip install llm-code-gatekeeper-ts
pip install llm-code-gatekeeper-csharp

# alternatywnie praca bezpośrednio z repozytorium (tryb edytowalny):
# pip install -e ./core -e ./python

gatekeeper policy lint --policy policy/gates.yaml
gatekeeper run --repo /ścieżka/do/ocenianego/repo --base origin/main
```

`policy/gates.yaml` (i `scope_map.yaml`/`exceptions.yaml`) trzeba mieć w ocenianym repo — kopia startowa jest w [`core/policy/`](core/policy/), w dwóch profilach: `gates.yaml` (**adopcja** — `G1.static`, `G2.*`, `G3.sast`, `G3.sca` tylko ostrzegają; naprawdę blokuje wtedy sekret, nieistniejący pakiet, typosquat, za duży diff i złożoność) oraz `gates.enforcing.yaml` (**produkcja** — pusty `warn_only`, blokuje wszystko; do tego `G1.static.require_*` zamienia brak tsc/eslinta/mypy/`dotnet build` albo ich configu — tsconfig, config eslinta, `.csproj` — przy plikach danego języka w diffie w `error`, zamiast cichego `pass`; repo bez plików danego języka nie potrzebuje jego configu). Profil adopcji to tydzień obserwacji; **zanim Check Run stanie się required, skopiuj `gates.enforcing.yaml` jako `policy/gates.yaml`**. Kopie w pack'ach i w panelu muszą zgadzać się z `core/policy/` — pilnuje tego `core/tests/test_policy_templates.py`. Gotowy szablon integracji z GitHub Actions: [`templates/github/gatekeeper.yml`](templates/github/gatekeeper.yml) (uniwersalny workflow, nie coś specyficznego dla Pythona; zależności doinstalowuje pod oceniane repo).

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

Wydanie **0.2.0** unifikuje cały ekosystem monorepo (szczegółowy opis zmian w [`CHANGELOG.md`](CHANGELOG.md)).

- **Silnik reguł i bramki jakości (G0–G3)**: 11 kanonicznych bramek w `core/`, podwójny profil polityki (`gates.yaml` adopcja / `gates.enforcing.yaml` produkcja), izolacja procesów (Bubblewrap, kontenery Docker/Podman `gatekeeper-tools`, Windows Job Objects).
- **Pakiety językowe**: Pełna obsługa Python (`python/`), TypeScript/JavaScript (`ts/`) oraz C# (`csharp/`) z narzędziami statycznymi, regułami SAST i weryfikacją krzyżową G2.* (pytest / vitest+jest / xUnit).
- **Panel WWW (`web/`)**: Przeglądarka raportów, kolejka zadań, rejestr repozytoriów, panel triage'u werdyktów człowieka i zarządzanie profilami polityki (zgodnie ze specyfikacją [`docs/PLAN-WEB-UI.md`](docs/PLAN-WEB-UI.md)).
- **Kalibracja empiryczna**: Zestaw 46 pull requestów benchmarkowych z precyzją blokowania 100% i raportem skuteczności per bramka ([`docs/CALIBRATION.md`](docs/CALIBRATION.md)).
- **Archiwum i prezentacje**: Dokumenty robocze, audyty wdrożeń i prezentacje projektowe zebrane w [`docs/archive/`](docs/archive/) oraz [`docs/presentations/`](docs/presentations/).

Przegląd stanu i znalezisk: [`REVIEW.md`](REVIEW.md). Zapis podziału na packi: [`PODSUMOWANIE.md`](PODSUMOWANIE.md).

CI: `.github/workflows/ci.yml` w korzeniu — cztery joby (`core`, `python`, `ts`, `csharp`), każdy z `working-directory` na swoim katalogu, z core'em instalowanym z checkoutu (`pip install -e ../core`), nie z GitHuba. Dzięki temu PR ruszający core i pack naraz jest testowany razem. Cztery pliki `ci.yml` leżące wcześniej per pack są w monorepo martwe (GitHub Actions czyta wyłącznie korzeń) — zastąpione tym jednym; uniwersalny szablon integracji dla *ocenianego* repo znajduje się w [`templates/github/gatekeeper.yml`](templates/github/gatekeeper.yml).

Uwaga o pushu: GitHub odrzuca **każdy** push tykający `.github/workflows/`,
gdy token nie ma zakresu `workflow` (`remote rejected … without workflow
scope`). Dotyczy to commitów `d0707a0`, `08409d5` i `1887617`. Gałąź
`feat/panel-web` została wypchnięta kluczem SSH, który temu nie podlega, więc
te commity **są** już na zdalnym. Trwałe odblokowanie drogi po HTTPS:
`gh auth refresh -h github.com -s workflow` albo przestawienie remote'u na SSH
(`git remote set-url origin git@github.com:tarze07/llm-code-gatekeeper.git`).

To repozytorium celowo nie ma `CODEOWNERS`. Przy jednym właścicielu plik
niczego by nie egzekwował (GitHub nie pozwala zatwierdzić własnego PR-a),
a placeholder udawał ochronę, której nie było (REVIEW.md §5, P0). Wróci
razem z wymogiem „Require review from Code Owners”, gdy agenci zaczną
otwierać PR-y z osobnego konta. W **ocenianym** repo ochrona `policy/`
przez CODEOWNERS nadal jest zalecana — patrz [`python/USAGE.md`](python/USAGE.md).
