# Stan prac: panel WWW, etap 6

Data: 9 września 2026. Commit `1887617` na `feat/g2-ts` (142 pliki).
Punkt wyjścia: etapy 0–5 z [`STATUS-PANEL-ETAPY-2-5.md`](STATUS-PANEL-ETAPY-2-5.md).
Plan źródłowy: [`PLAN-WEB-UI.md`](PLAN-WEB-UI.md).

## 1. Skrót

Dokończony **etap 6** MVP. Panel ma opcjonalną sesję operatora, kopię
zapasową bazy i domkniętą instrukcję. Testy przeglądarkowe
ze sterownikiem nadal nie wchodzą — w środowisku nie ma Chromium; zestaw
sprawdza HTML i API. Historia nie jest usuwana automatycznie.

W tym samym commicie poszły też zmiany core (`prepare`/`execute`,
`RunControl`) i poprawki packów z przeglądu z 8 września.

Pominięte przy zapisie: `csharp/build/`. Commit nie został wypchnięty.

## 2. Sesja operatora (plan §8)

Domyślnie **wyłączona**: `gatekeeper-web serve` otwiera pulpit od razu.
Dostęp do portu na pętli zwrotnej jest dostępem do panelu — zapisy chroni
CSRF i kontrola `Origin`, odczytów nie chroni nic.

`serve --wymagaj-logowania` przywraca pełny model: jednorazowy kod startowy
w terminalu, strona `/logowanie`, ciasteczko `gk_session` (HttpOnly/SameSite),
sekret poza URL-em i logami, API bez sesji → 401, HTML → przekierowanie.

`--host 0.0.0.0` to odmowa startu w obu trybach (to nie jest wersja
zespołowa).

## 3. Kopia bazy

`gatekeeper-web backup -o …` — spójny zrzut SQLite, nic nie kasuje historii.

## 4. Reszta etapu 6

* `Settings.from_env` czyta `--repo-root` przy `--reload`
* test pakietu (CSS/JS w wheel)
* [`web/e2e/README.md`](web/e2e/README.md) (bez Chromium)
* zaktualizowane README, CONTRACT, PLAN

## 5. Czego nadal nie ma

* logowanie zespołowe, HTTPS i nasłuch poza `127.0.0.1` (rozdz. 10 planu)
* testy ze sterownikiem przeglądarki
* automatyczne usuwanie historii
* publikowanie do GitHuba/GitLaba, harmonogramy, powiadomienia
