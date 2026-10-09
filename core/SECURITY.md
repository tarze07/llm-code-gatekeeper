# Uruchamianie ocenianego kodu

Każde narzędzie uruchamiane na kodzie PR-a działa w izolacji. Są dwa backendy,
wybierane przez `GATEKEEPER_SANDBOX`:

* `bwrap` (domyślny na Linuksie) — Bubblewrap i przestrzenie nazw użytkownika,
  procesów oraz sieci (`sudo apt-get install bubblewrap`). Szablon workflow
  i CI monorepo instalują Bubblewrap.
* `container` (domyślny na Windows) — kontener Linuksa przez Docker albo
  Podman, obraz `gatekeeper-tools` (`container/Dockerfile`). Opis niżej.

Brak backendu lub błąd jego konfiguracji przerywa wykonanie narzędzia;
`require_isolation=False` nie wyłącza zabezpieczeń. Wykonania bez izolacji nie ma.

Każda bramka uruchomiona przez `run_gates` otrzymuje własną kopię `head_sha`
z niezależną bazą Git. Lokalne zmiany i aktualnie wybrana gałąź nie wpływają
na analizę, a raport zachowuje ścieżkę oryginalnego repo oraz oceniane SHA.
Kopie są usuwane po przebiegu, także po timeoutcie. Kosztem jest dodatkowe
miejsce i czas kopiowania historii dla każdej bramki.

Nadzorca uruchamia bramki w osobnych procesach i mierzy czas ich wykonania
niezależnie od wartości zwracanej przez plugin. Po przekroczeniu budżetu
zabija proces i zwraca `error`. Przygotowanie kopii Git poprzedza ten budżet.
Zabijany jest cały spis potomków procesu bramki (wg `/proc`), także narzędzia
w osobnej sesji — Sandbox i każdy `start_new_session` pluginu. Kopia kodu
jest usuwana dopiero, gdy żaden z nich już nie działa (najwyżej po
`REAP_GRACE_S`; proces po SIGKILL nie wykonuje już kodu). Ta sama ścieżka
obsługuje anulowanie z panelu. Worker bramki ginie też razem z procesem
nadzorującym (`PR_SET_PDEATHSIG`), a Sandbox razem z workerem
(`--die-with-parent`).

Ograniczenia: proces, który zdążył się odłączyć od drzewa (podwójny `fork`
i przepięcie do init), nie jest już widoczny jako potomek i nie zostanie
zabity. Gdy zginie sam proces nadzorujący (SIGKILL), zabijane są worker
i Sandbox, ale nie bezpośrednie narzędzia zaufanego pluginu spoza Sandboxa,
a kopia kodu może zostać w katalogu tymczasowym. Kod ocenianego PR-a uruchamia
się wyłącznie w Sandboxie, więc tych luk nie może wykorzystać.

Pluginy są zaufanym kodem bramy; nie należy ładować pluginów dostarczonych
przez oceniany PR.

Narzędzia i testy działają w Bubblewrap z osobnym systemem plików i PID.
Mogą zapisywać w kopii repo oraz prywatnym `/tmp` i `HOME`. Baza Git,
runtime Pythona/Node/.NET i współdzielone pakiety są dostępne tylko do
odczytu. Pozostałe pliki hosta, jego procesy i gniazdo agenta SSH nie są
udostępniane. Sieć testów jest odcięta; adaptery rejestrów/SCA mogą żądać jej
jawnie. Zmienne wyglądające na poświadczenia są usuwane, z wyjątkiem
świadomie skonfigurowanego `keep_env`.

Zainstaluj zależności przed uruchomieniem bramy. Zwykły katalog
`node_modules` jest udostępniany kopiom tylko do odczytu; brama nie akceptuje
dowiązania pod tą nazwą z ocenianego repo jako uprawnienia do odczytu
innego katalogu hosta. Dowiązania do pakietów poza udostępnionymi drzewami
nie rozszerzają dostępu. Cache NuGet (`~/.nuget/packages`) jest czytelny,
ale pliki z poświadczeniami i konfiguracją użytkownika nie są kopiowane.

Adapter wymagający dodatkowego pliku wejściowego lub katalogu raportów
może wskazać `SandboxPolicy.read_only_paths` lub `writable_paths`.
To uprawnienia nadawane przez zaufany adapter: nie należy wyprowadzać ich
z niezweryfikowanych dowiązań albo ścieżek zapisanych w ocenianym kodzie.
Raporty tymczasowe standardowych adapterów powstają wewnątrz kopii repo.

Status `error` oznacza brak dowodu. Decyzja końcowa nadal zależy od
`on_gate_error` i `warn_only`; polityka wdrażana jako obowiązkowa blokada
powinna ustawić `on_gate_error: block` dla wymaganych kontroli i usunąć je
z `warn_only`.

Semantyka montowań i przestrzeni nazw:
[dokumentacja Bubblewrap](https://github.com/containers/bubblewrap/blob/main/bwrap.xml).

## Backend `container`

Każde wywołanie narzędzia to jeden `docker run --rm` (`core/container.py`):

* sieć `none`, chyba że adapter jawnie żąda sieci (rejestry, SCA);
* `--read-only`, zapisywalne tylko kopia commita (`/work`) i tmpfs `/tmp`
  oraz `HOME`; baza Git, `node_modules` i cache NuGet hosta tylko do odczytu;
* `--cap-drop ALL`, `no-new-privileges`, uid operatora (Linux) albo 1000
  (Windows), nigdy root; `--memory` i `--pids-limit`;
* do kontenera trafiają tylko zmienne wniesione przez wywołującego
  (np. `PYTHONPATH`) i `keep_env` — środowisko hosta jako całość nie;
* narzędzia pochodzą z obrazu, nie z hosta. Zależności ocenianego repo
  instaluje operator w obrazie projektu (`gatekeeper container init`),
  jak dziś przygotowuje venv dla Bubblewrap.

Sprzątanie: kontener ma nazwę i etykietę bramki. Po przekroczeniu limitu
czasu albo budżetu bramki nadzorca usuwa go (`docker rm -f`); zabicie samego
klienta `docker run` kontenera nie zatrzymuje. Niezależnie od tego narzędzie
w kontenerze działa pod `timeout -s KILL` (limit wywołania + 30 s), więc
kontener kończy się sam, także gdy proces bramy zginął.

Granica zaufania jest inna niż przy Bubblewrap: demon Dockera działa
z uprawnieniami roota, a członkostwo w grupie `docker` daje faktycznie
uprawnienia roota na hoście. Brama nie montuje gniazda Dockera do kontenera
ani nie przyznaje mu uprawnień; ucieczka z kontenera wymagałaby podatności
w jądrze albo w środowisku uruchomieniowym kontenerów. Na Windows (Docker
Desktop) kontenery działają w maszynie wirtualnej WSL2, co dodaje warstwę
izolacji od systemu hosta.

Ścieżki: kopia jest widoczna jako `/work`. Ścieżki hosta w argumentach są
przepisywane na ścieżki kontenera, a w stdout/stderr narzędzia z powrotem.
Pliki zapisane przez narzędzie (raporty JSON, TRX, cobertura) zawierają
ścieżki `/work/…`; parsery sprowadzają je do ścieżek repo przez
`core/paths.py`. Skutek uboczny: na hoście z prawdziwym katalogiem `/work`
ścieżka spoza ocenianego repo pod `/work` byłaby przypisana do repo —
może to dać fałszywy alarm, nigdy przepuszczenie.

