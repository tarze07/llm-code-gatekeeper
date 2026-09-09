# llm-code-gatekeeper-web

Panel WWW do przeglądania raportów bramy jakości. Pakiet **opcjonalny**:
`core` i CLI `gatekeeper` działają bez niego, a on zależy od `core` tylko
w jedną stronę.

Zakres tego wydania to etapy 0–6 z [`PLAN-WEB-UI.md`](../PLAN-WEB-UI.md):
przeglądarka raportów, rejestr projektów, **uruchamianie kontroli z kolejką
i osobnym nadzorcą**, postęp i anulowanie, oceny znalezisk, metryki,
zarządzanie profilami polityki, logowanie kodem startowym i kopia bazy.
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

Po starcie terminal wypisuje **jednorazowy kod startowy**. Wpisuje się go na
`/logowanie`. Kod nie jest częścią adresu strony. `--host 0.0.0.0` kończy się
odmową startu: kod startowy nie zastępuje HTTPS i ról. Wersja zespołowa to
osobny model wdrożenia (plan §10), a nie zmiana adresu nasłuchu.

Kopia bazy (nic nie kasuje historii):

```bash
gatekeeper-web backup --state-dir ~/.local/state/gatekeeper-web -o panel-kopia.db
```

## Pierwsze kroki — uruchamianie kontroli

1. **Polityki** → utwórz profil, wklej `gates.yaml` (kopia startowa jest
   w [`core/policy/`](../core/policy/)), zapisz szkic, sprawdź walidację
   i **aktywuj** go świadomie.
2. **Projekty** → dodaj projekt, wskaż ścieżkę lokalnego repozytorium
   i przypisz profil polityki.
3. **Nowa kontrola** → wybierz wersję bazową i ocenianą, obejrzyj dokładny
   zakres (merge-base, liczba plików i linii, lista ścieżek) i dopiero wtedy
   naciśnij „Uruchom kontrolę".
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
* sesja operatora z jednorazowym kodem startowym (ciasto HttpOnly/SameSite;
  sekret sesji nie trafia do URL ani logów);
* repozytorium wolno zarejestrować **wyłącznie** w katalogach podanych przez
  `--repo-root`; sprawdzana jest ścieżka rozwinięta, więc dowiązanie nie jest
  furtką, a walidacja powtarza się przy każdym uruchomieniu;
* nazwa wersji Git jest weryfikowana jako obiekt commit i przekazywana gitowi
  po `--end-of-options` — pole tekstowe nie staje się opcją ani komendą;
* API nie przyjmuje ścieżek do wykonania, interpretera, pluginów ani treści
  polityki: operator wybiera zarejestrowany projekt i zatwierdzony profil;
* bez działającego Bubblewrapa panel **nie oferuje** trybu bez izolacji —
  mówi, czego brakuje;
* CSP bez CDN-a i bez `unsafe-inline`, `nosniff`, `no-referrer`, `DENY` dla ramek;
* CSRF (double-submit) i kontrola `Origin` dla każdej operacji zapisującej;
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

`gatekeeper_web/static/app.css` jest pisany ręcznie. `app.js` powstaje
z TypeScriptu w `frontend/` i **jest wersjonowany**, żeby instalacja pakietu
nie wymagała Node.js:

```bash
cd frontend && npm install && npm run build   # → ../gatekeeper_web/static/app.js
```

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
