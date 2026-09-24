# llm-code-gatekeeper-web

Panel WWW do przeglądania raportów bramy jakości. Pakiet **opcjonalny**:
`core` i CLI `gatekeeper` działają bez niego, a on zależy od `core` tylko
w jedną stronę.

Zakres tego wydania to etapy 0–6 z [`PLAN-WEB-UI.md`](../PLAN-WEB-UI.md):
przeglądarka raportów, rejestr projektów, **uruchamianie kontroli z kolejką
i osobnym nadzorcą**, postęp i anulowanie, oceny znalezisk, metryki,
zarządzanie profilami polityki i kopia bazy. Panel startuje bez logowania;
sesję z jednorazowym kodem startowym włącza `--wymagaj-logowania`.
Czego nie ma — mówi wprost [`CONTRACT.md`](CONTRACT.md) §11.

## Instalacja i start

```bash
cd web && python3 -m venv .venv && source .venv/bin/activate
pip install -e ../core          # core z dysku, nie z GitHuba
pip install -e ".[dev]"

gatekeeper-web serve \
  --host 127.0.0.1 --port 8080 \
  --state-dir ~/.local/state/gatekeeper-web \
  --repo-root ~/projekty          # gdzie wolno rejestrować repozytoria
```

`serve` uruchamia dwa procesy: serwer HTTP i **nadzorcę kolejki**, i zatrzymuje
oba przy wyjściu. Nadzorca musi być osobnym procesem, bo silnik forkuje i
zakłada proces nadzorujący bez wątków — czyli dokładne przeciwieństwo serwera
ASGI. Nadzorcę da się prowadzić samodzielnie:

```bash
gatekeeper-web supervise --state-dir ~/.local/state/gatekeeper-web
gatekeeper-web serve --no-supervisor ...
```

Podczas pracy nad panelem można dodać `--reload`. Serwer uruchamia wtedy
fabrykę aplikacji w osobnym procesie i zachowuje wybrane `--state-dir`,
`--host` oraz `--port` po przeładowaniu.

### Logowanie

Domyślnie panel **o nic nie pyta** — otwierasz adres i jesteś na pulpicie.
Dla jednego operatora na własnej maszynie kod przepisywany z terminala był
kosztem bez odbiorcy.

Cena tej wygody jest konkretna i warto ją znać: panel wpuszcza **każdego, kto
sięgnie na ten port**. Nasłuch na pętli zwrotnej odcina sieć, ale nie odcina
innych procesów na tej samej maszynie ani stron otwartych w przeglądarce
(przed zapisem broni CSRF i kontrola `Origin`, przed odczytem — nic).

Gdy to za mało — z maszyny korzysta ktoś jeszcze albo chodzą na niej
niezaufane procesy — sesja wraca:

```bash
gatekeeper-web serve --wymagaj-logowania
```

Terminal wypisuje wtedy **jednorazowy kod startowy**; wpisuje się go na
`/logowanie` i kod nie jest częścią adresu strony.

`--host 0.0.0.0` kończy się odmową startu niezależnie od logowania: kod
startowy nie zastępuje HTTPS i ról. Wersja zespołowa to osobny model
wdrożenia (plan §10), a nie zmiana adresu nasłuchu.

Kopia bazy (nic nie kasuje historii):

```bash
gatekeeper-web backup --state-dir ~/.local/state/gatekeeper-web -o panel-kopia.db
```

## Pierwsze kroki — uruchamianie kontroli

1. **Polityki** → **Utwórz profil startowy**. Panel zakłada profil wypełniony
   polityką startową i od razu ją aktywuje — bez szukania `gates.yaml` na dysku.
   Progi w niej to kalibracja narzędzia, nie Twojego projektu: przejrzyj je
   i zawęź kolejnym szkicem. Wolisz prowadzić to ręcznie? „Nowy profil" daje
   pusty profil, a jego pierwszy szkic i tak startuje z polityki startowej —
   aktywacja pozostaje osobnym, świadomym kliknięciem.
2. **Projekty** → dodaj projekt, podając od razu **ścieżkę lokalnego
   repozytorium** i **profil polityki**. Oba pola są opcjonalne: bez nich
   powstaje projekt na same importowane raporty, a uzupełnisz je później
   w Ustawieniach projektu. Kolumna „Gotowość" mówi, czego brakuje.
3. **Nowa kontrola** → wybierz wersję bazową i ocenianą **z list wypełnionych
   zawartością ocenianego repozytorium**: gałęzie lokalne, zdalne, tagi oraz
   **ostatnie commity** (skrócone SHA, data, temat). `HEAD` jest osobną
   pozycją. Wersję spoza listy wpisuje się w polu obok — działa tam też zapis
   względny, np. `60d1046^` (rodzic tego commita), więc żeby sprawdzić
   pojedynczy commit, nie trzeba szukać SHA rodzica. Obejrzyj dokładny
   zakres (merge-base, liczba plików i linii, lista ścieżek oraz **lista
   commitów** liczona od merge-base) i dopiero wtedy naciśnij „Uruchom
   kontrolę".
4. **Zadania** → postęp „ukończono N z M kontroli", zatrzymanie, ponowienie
   („powtórz ten sam zakres" albo „sprawdź najnowszą wersję").
5. Szczegóły przebiegu → powody decyzji, bramki z faktami, wszystkie
   znaleziska, ograniczenia bramy, eksport HTML/Markdown/JSON.
6. Strona znaleziska → ocena „potwierdzony problem" / „fałszywy alarm";
   **Metryki** liczą z nich precyzję bramki.

## Pierwsze kroki — sam przegląd raportów

Panel działa też bez uruchamiania czegokolwiek. Projekt bez ścieżki
repozytorium jest pojemnikiem na zaimportowane raporty:

```bash
gatekeeper run --base origin/main --format json --output raport.json
gatekeeper-web import raport.json --project "Taskboard" --create
```

## Co panel robi z wynikiem

Rozdziela trzy rzeczy, których mylenie jest najczęstszym błędem prezentacji:

* **zadanie** — czy sprawdzenie w ogóle się wykonało (`w kolejce`,
  `trwa`, `zakończone`, `anulowane`, `przerwane`, `awaria`);
* **bramka** — `pass` / `fail` / `error` / `skipped` pojedynczej kontroli;
* **polityka** — `PASS` / `PASS-WITH-REVIEW` / `BLOCK` dla ocenianej zmiany.

Zakończone zadanie z decyzją BLOCK jest poprawnie wykonanym sprawdzeniem.
Bramka ze statusem `pass` może jednocześnie łamać politykę — panel pokazuje
oba fakty i nazywa konflikt, zamiast wybierać wygodniejszy.

Brak pomiaru jest opisany jako **brak danych**, nigdy jako zero. Bramka
w stanie `error` dostaje zdanie: „brak znalezisk oznacza brak pomiaru,
a nie brak problemów". Lista „czego ta brama nie sprawdza" jedzie razem
z raportem, również do eksportu.

Limit dziesięciu znalezisk z komentarza w PR **nie** obowiązuje w panelu:
widok i eksport zawierają komplet.

## Bezpieczeństwo

Panel czyta raporty z cudzych repozytoriów, więc granice dostępu są częścią
funkcji, a nie dodatkiem (plan §8):

* nasłuch tylko na pętli zwrotnej + weryfikacja nagłówka `Host`;
* opcjonalna sesja operatora — `--wymagaj-logowania`, jednorazowy kod
  startowy, ciasteczko HttpOnly/SameSite, sekret nie trafia do URL ani logów;
  **domyślnie wyłączona**, więc dostęp do portu jest dostępem do panelu;
* repozytorium wolno zarejestrować **wyłącznie** w katalogach podanych przez
  `--repo-root`; sprawdzana jest ścieżka rozwinięta, więc dowiązanie nie jest
  furtką, a walidacja powtarza się przy każdym uruchomieniu;
* nazwa wersji Git jest weryfikowana jako obiekt commit i przekazywana gitowi
  po `--end-of-options` — pole tekstowe nie staje się opcją ani komendą;
* API nie przyjmuje ścieżek do wykonania, interpretera, pluginów ani treści
  polityki: operator wybiera zarejestrowany projekt i zatwierdzony profil;
* bez działającego Bubblewrapa panel **nie oferuje** trybu bez izolacji —
  mówi, czego brakuje;
* CSP bez CDN-a i bez `unsafe-inline`, `nosniff`, `DENY` dla ramek;
* `Referrer-Policy: same-origin` — ścieżka raportu nie wycieka do obcej
  strony. Świadomie **nie** `no-referrer`: przy tamtej wartości przeglądarka
  wysyła `Origin: null` przy każdym zapisie i panel odrzuca własne formularze;
* CSRF (double-submit) i kontrola `Origin` dla każdej operacji zapisującej —
  `localhost` i `127.0.0.1` to ten sam panel, ale **port musi się zgadzać**,
  więc inna usługa na tej maszynie i proxy z przepisanym `Host` są obce;
* każdy tekst z raportu renderowany z escapowaniem, eksport HTML bez JavaScriptu;
* redakcja ścieżek katalogów roboczych i pól o nazwach sugerujących sekret;
* limity rozmiaru pliku, liczby znalezisk i długości pól przy imporcie;
* raporty pobiera się po identyfikatorze, nigdy po ścieżce z żądania;
* archiwizacja projektu ukrywa go w listach, nie kasuje niczego.

Czego **nie** ma: HTTPS, ról i logowania zespołowego. Dlatego `--host 0.0.0.0`
kończy się odmową startu — wersja zespołowa to osobny model wdrożenia
(plan §10), a nie zmiana adresu nasłuchu. Historia nie jest usuwana
automatycznie; kopia: `gatekeeper-web backup`.

## Zasoby (CSS/JS)

`gatekeeper_web/polityka_startowa/` to zamrożona kopia
[`core/policy/`](../core/policy/) — treść pierwszego szkicu każdego profilu.
Nie czytamy jej z katalogu core'a celowo: profil raz aktywowany ma oceniać
tym samym, czym oceniał wczoraj, a nie zmieniać kryteria przy aktualizacji
zależności.

`gatekeeper_web/static/app.css` jest pisany ręcznie. `app.js` powstaje
z TypeScriptu w `frontend/` i **jest wersjonowany**, żeby instalacja pakietu
nie wymagała Node.js:

```bash
cd frontend && npm install && npm run build   # → ../gatekeeper_web/static/app.js
```

Skórka jasna i ciemna to sama podmiana zmiennych CSS — żadna reguła układu
o niej nie wie. Bez JavaScriptu obowiązuje ustawienie systemu
(`prefers-color-scheme`); przycisk w nagłówku pozwala wybrać świadomie i wygrywa
w obie strony (jasna skórka na ciemnym systemie też). Wybór pamięta
`localStorage` tej przeglądarki, więc nie jedzie do bazy ani do ciasteczka.
Eksport HTML raportu ma własny arkusz i zostaje jasny — to dokument do
udostępnienia i druku, nie ekran panelu.

JavaScript jest dodatkiem: bez niego strona pokazuje komplet danych i
wszystkie formularze działają.

## Testy

```bash
pytest -q
ruff check gatekeeper_web tests
MYPYPATH=../core mypy gatekeeper_web --strict
```

Testy chodzą po prawdziwej aplikacji i prawdziwej bazie w katalogu
tymczasowym. Materiał akceptacyjny to zredagowane raporty w `samples/` —
CI nie sięga do żadnej ścieżki spoza repozytorium.
