# Kontrakt panelu (etap 0)

Ten plik zamyka etap 0 z [`PLAN-WEB-UI.md`](../PLAN-WEB-UI.md) §9: ustala commit
bazowy, format raportu, rozdzielenie zadania od decyzji i układ katalogu stanu.
Etapy 2–6 mają się o niego opierać zamiast ustalać te rzeczy po raz drugi.

## 1. Commit bazowy

Prace nad panelem startują z `08409d5` (`docs+ci: G2.* istnieje dla wszystkich
trzech jezykow`). Panel zależy od `llm-code-gatekeeper-core>=0.1.0` i **nie**
zależy od żadnego pack'a językowego: raport jest danymi, a nie kodem, więc do
jego wyświetlenia nie trzeba mieć zainstalowanego TypeScriptu ani .NET-a.

Zależność idzie w jedną stronę. `core`, `python`, `ts` i `csharp` nie wiedzą
o istnieniu panelu, a CLI `gatekeeper` działa bez niego.

## 2. Format raportu

| Wersja | Rozpoznanie | Status |
|---|---|---|
| `core/v0` | **brak** pola `report_version` | to, co dziś wypisuje `gatekeeper run --format json` |
| `core/v1` | `report_version: 1` | zarezerwowane; adapter istnieje, żeby dodanie pola nie było zmianą łamiącą |

Każdy inny numer jest odrzucany z komunikatem, a nie wczytywany „na próbę".

### Pola wymagane

`run_id`, `base_sha`, `head_sha`, `started_at`, `decision.verdict`
(`PASS` / `PASS-WITH-REVIEW` / `BLOCK`), `gates[]` z `gate` i `status`
(`pass` / `fail` / `error` / `skipped`). Znalezisko musi mieć `rule_id`,
`title`, `failure_scenario`, `severity` i `confidence` — ten sam wymóg co
`Finding.__post_init__` w core, bo znalezisko bez scenariusza awarii jest
opinią.

### Pola opcjonalne i ich brak

`not_checked`, `decision.warnings`, `decision.suppressed`, `policy_version`,
`duration_s`, `facts`. Brak któregokolwiek oznacza **brak danych** i tak jest
pokazywany. Panel nigdy nie zamienia braku pomiaru na zero ani pustej listy
ograniczeń na „brama sprawdza wszystko".

Brak `duration_s` lub wartość `null` pozostaje brakiem pomiaru zarówno dla
przebiegu, jak i bramki — w historii, szczegółach oraz eksporcie. Jawne zero
jest pomiarem. Podany czas musi być skończoną, nieujemną liczbą.
`started_at` wymaga daty ISO 8601: podana strefa jest przeliczana na UTC
przy wyświetlaniu, a data bez strefy nie otrzymuje etykiety UTC.
Niepoprawne typy i struktury są odrzucane przed zapisem z kodem HTTP 422.

Fingerprint pochodzi z raportu. Jeśli go nie ma, panel liczy go
`gatekeeper_core.core.finding.compute_fingerprint` — tą samą funkcją co CLI,
żeby ocena znaleziska z panelu wskazywała ten sam obiekt co
`gatekeeper verdict`.

### Czego panel nie przechowuje

`artifacts[]` są usuwane przy imporcie. To ścieżki w katalogu roboczym bramki,
który już nie istnieje; pokazanie ich sugerowałoby możliwość pobrania.

## 3. Redakcja przy imporcie

Raport jest wejściem niezaufanym. Przed zapisem:

* ścieżki katalogów tymczasowych (`/tmp/…`, `/var/tmp/…`, `/run/…`) skracane do
  `…/<nazwa pliku>`;
* teksty, obiekty i listy w polach, których nazwa pasuje do
  `secret|token|password|api_key|…`, zamieniane w całości na `[zredagowano]`;
  liczbowe i logiczne pomiary, np. `secrets.count` i `secrets.found`, pozostają;
* wartości tekstowe obcinane do 2000 znaków, cały plik do 5 MB, liczba
  znalezisk do 5000.

Klucze zostają zachowane. Wrażliwy obiekt lub lista zostają zastąpione
znacznikiem razem z całą zawartością. Adapter widzi oryginalne pola. `content_hash`
(używany do idempotencji) liczy się z pliku **przed** redakcją.

## 4. Trzy poziomy wyniku

To rozróżnienie jest kontraktem, nie kwestią prezentacji:

| Poziom | Wartości | Znaczenie |
|---|---|---|
| Zadanie | `imported`, `completed`, później `queued`/`running`/`cancelled`/`interrupted`/`failed` | czy udało się wykonać pracę i zapisać wynik |
| Bramka | `pass`, `fail`, `error`, `skipped` | wynik pojedynczej kontroli |
| Polityka | `PASS`, `PASS-WITH-REVIEW`, `BLOCK` | decyzja o ocenianej zmianie |

Zakończone zadanie z decyzją BLOCK jest poprawnie wykonanym sprawdzeniem.
Bramka ze statusem `pass` może jednocześnie łamać politykę — panel pokazuje
oba fakty i nazywa konflikt wprost, zamiast wybierać jedną z dwóch prawd.
Referencyjny przypadek: `G2.diff_coverage` → `pass`, pokrycie 2,7% (1 z 37
linii), naruszony próg `coverage.diff_ratio`.

Model widoku zachowuje pochodzenie powodów z `reasons`, `warnings` i
`suppressed`. Ostrzeżenia oraz powody wyciszone wyjątkiem nie trafiają
do listy powodów blokujących i nie nadają bramce oznaczenia naruszenia.

## 5. Katalog stanu

```
~/.local/state/gatekeeper-web/      # albo --state-dir / GATEKEEPER_WEB_STATE_DIR
  panel.db                          # projekty, raporty, ślad działań (SQLite, WAL)
```

Katalog powstaje z prawami `0700`. Stan panelu jest **poza** ocenianymi
repozytoriami: `.gatekeeper/runs.db` w ocenianym repo pozostaje bazą CLI i
panel go nie dotyka ani nie tworzy.

Kompletny raport leży w kolumnie `run_reports.payload`, a nie w pliku obok
bazy — dzięki temu „zapisany indeks bez raportu" jest stanem niemożliwym.
Migracje są numerowane (`schema_migrations`); baza z wyższą wersją niż kod
powoduje odmowę startu, bo migracja w dół nie istnieje.

## 6. Idempotencja i konflikty

Klucz raportu to `(project_id, run_id)`, nigdy sam `run_id`: fingerprinty i
identyfikatory przebiegów z core nie zawierają repozytorium, więc ten sam
raport w dwóch projektach to dwa różne wpisy.

* ten sam plik zaimportowany drugi raz → `created: false`, bez duplikatu;
* ten sam `run_id` z inną treścią → `409 Conflict`, historia nietknięta.

## 7. Wejście zadania

Zadanie wykonuje **zamrożone wejście**, a nie „to, co jest teraz w repo".
Kształt opisuje `gatekeeper_web/jobs/spec.py`; wersjonuje go pole
`input_version` (dziś `1`), a worker odmawia wykonania wejścia z numerem,
którego nie zna.

| Sekcja | Zawartość |
|---|---|
| `project` | ID, nazwa, kanoniczna ścieżka, tożsamość repozytorium (SHA pierwszego commita) |
| `scope` | nazwy Git, ich SHA **oraz faktyczny merge-base** użyty w diffie |
| `policy` | ID profilu i wersji, hash, pełna treść `gates.yaml`, `exceptions.yaml`, `scope_map.yaml` |
| `gates`, `fast_path`, `ticket` | wybór operatora |
| `versions` | wersje pakietów panelu i modułów językowych |
| `lockfiles` | skróty plików blokad zależności obecnych w repozytorium |

Zamrażamy SHA, nie nazwy gałęzi: przesunięcie gałęzi po zatwierdzeniu
formularza nie zmienia tego, co zadanie sprawdzi. Snapshot polityki jest
zapisywany na dysk i **przekazywany bramkom** — mapa zakresu trafia do
`G0.scope` jako bezwzględna ścieżka, więc oceniany commit nie może podstawić
własnych kryteriów oceny.

Czego nie obiecujemy: identycznego wyniku SCA w czasie. Rejestry podatności
się zmieniają.

## 8. Stany zadania

```
queued → preparing → running → completed
                  ↘ cancelling → cancelled
   każdy stan  → interrupted (zmarły nadzorca) | failed (awaria)
```

Zasady, na których stoi kolejka:

* każda zmiana stanu to **jeden atomowy UPDATE z warunkiem na stan
  poprzedni** — wyścig „zakończone kontra anulowane" rozstrzyga baza;
* zadanie jest `completed` dopiero po utrwaleniu raportu; kolejność jest
  odwrotna do intuicyjnej: **najpierw raport, potem stan**;
* dzierżawa nadzorcy wygasa. Po restarcie zadania po zmarłym nadzorcy są
  `interrupted`, nigdy wznawiane po cichu. Jeśli raport zdążył się zapisać,
  zadanie jest domykane jako `completed` — bo wynik istnieje;
* anulowanie: SIGTERM do procesu przebiegu, karencja, potem SIGKILL **grupy
  procesów**. Zabicie samego rodzica nie wystarcza, bo workery bramek robią
  `setsid()`;
* jedno zadanie naraz. Równoległość *wewnątrz* przebiegu zostaje bez zmian.

Anulowane i przerwane zadanie **nie ma** decyzji polityki i tego nie udaje.

## 9. Polityka

Profil ma wersje. Wersja po aktywacji jest niezmienna, poprzednia przechodzi
w stan `retired`. Aktywacja wymaga przejścia tej samej walidacji co
`gatekeeper policy lint` (`Policy.load()` + `lint()` z zainstalowanymi
modułami) i jest osobnym, jawnym działaniem — szkic sam z siebie nic nie
zmienia.

Aktywacja dotyczy **przyszłych** zadań. Zapisany raport zachowuje politykę,
według której zapadła jego decyzja, bo jej treść jest zamrożona w wejściu
zadania.

## 10. Oceny znalezisk

Ocena jest przypisana do pary **(projekt, fingerprint)**, nie do samego
fingerprintu: ten sam ciąg znaków w dwóch repozytoriach to dwa różne
problemy. Zmiana zdania dopisuje wiersz; „aktualna ocena" to ostatni wiersz.

Ocena nie zmienia decyzji przebiegu i nie tworzy wyjątku od reguły.
Oznaczenie incydentu jest osobnym działaniem dotyczącym przebiegu.

Metryki liczą osobno **wystąpienia** (znalezisko w każdym przebiegu) i
**problemy** (unikalne fingerprinty), a precyzja bierze pod uwagę wyłącznie
ostatnią ocenę — inaczej trzy zmiany zdania liczyłyby się jak trzy oceny.

## 11. Granica tego wydania

Zaimplementowane: etapy 0–6. Import i przeglądanie raportów, rejestr
projektów ze ścieżką repozytorium, wybór zakresu Git z podglądem,
diagnostyka środowiska, kolejka z osobnym nadzorcą, realne uruchamianie
kontroli, postęp i anulowanie, odtwarzanie po restarcie, oceny znalezisk,
incydenty, metryki, zarządzanie profilami polityki, **sesja operatora
zakładana jednorazowym kodem startowym** oraz **kopia zapasowa bazy**.

Świadomie **nieobecne**:

* logowanie zespołowe, HTTPS i nasłuch poza `127.0.0.1` — to rozdz. 10,
  nie zmiana `--host`;
* testy ze sterownikiem przeglądarki — zestaw sprawdza HTML i API;
  uzasadnienie: [`e2e/README.md`](e2e/README.md);
* automatyczne usuwanie historii — kopia zapasowa jest, retencja kasująca
  nie, bo plan wymaga kopii *przed* usuwaniem;
* publikowanie wyników do GitHuba/GitLaba i harmonogramy (rozdz. 10 planu).

## 8. Materiał akceptacyjny

`samples/` (opis w `samples/README.md`) — zredagowane, wersjonowane raporty
pokrywające scenariusze 1–3 z planu §9. CI nie sięga do żadnej lokalnej
ścieżki poza repozytorium.
