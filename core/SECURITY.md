# Uruchamianie ocenianego kodu

Brama wymaga Linuksa, Bubblewrap (`bwrap`) i działających przestrzeni nazw
użytkownika, procesów oraz sieci. Na Ubuntu/Debian backend instaluje się przez
`sudo apt-get install bubblewrap`. Brak backendu lub błąd jego konfiguracji
przerywa wykonanie narzędzia; `require_isolation=False` nie wyłącza zabezpieczeń.
Szablon workflow i CI monorepo instalują Bubblewrap.

Każda bramka uruchomiona przez `run_gates` otrzymuje własną kopię `head_sha`
z niezależną bazą Git. Lokalne zmiany i aktualnie wybrana gałąź nie wpływają
na analizę, a raport zachowuje ścieżkę oryginalnego repo oraz oceniane SHA.
Kopie są usuwane po przebiegu, także po timeoutcie. Kosztem jest dodatkowe
miejsce i czas kopiowania historii dla każdej bramki.

Nadzorca uruchamia bramki w osobnych procesach i mierzy czas ich wykonania
niezależnie od wartości zwracanej przez plugin. Po przekroczeniu budżetu
zabija proces i zwraca `error`. Przygotowanie kopii Git poprzedza ten budżet.
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
