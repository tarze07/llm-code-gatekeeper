# Stan prac: panel WWW, etapy 2–5

Data: 8 września 2026. Nic nie jest zacommitowane — całość leży w drzewie
roboczym. Punkt wyjścia: etapy 0–1 z poprzedniej sesji (`web/`, przeglądarka
raportów). Plan źródłowy: [`PLAN-WEB-UI.md`](PLAN-WEB-UI.md).

## 1. Skrót

Zrobione: **etapy 2, 3, 4 i 5**. Panel przestał być tylko przeglądarką —
rejestruje lokalne repozytoria, zamraża wejście kontroli, uruchamia
**ten sam silnik co CLI** w osobnym procesie pod nadzorcą, pokazuje postęp,
umie zatrzymać analizę, przyjmuje oceny znalezisk i liczy metryki.

Zweryfikowane na żywo: przebieg zlecony przez API przeszedł całą drogę
kolejka → nadzorca → proces przebiegu → bramki w Bubblewrapie → zapisany
raport z decyzją BLOCK.

| Pakiet | Testy | ruff | mypy --strict |
|---|---|---|---|
| `core` | 172 (było 162, +10 nowych) | czysto | czysto |
| `web` | 211 (było 121, +90 nowych) | czysto | czysto |

## 2. Zmiany w `core` (fundament pod etap 3–4)

| Plik | Co się zmieniło |
|---|---|
| `core/progress.py` | **nowy** — `RunControl`: odbiornik zdarzeń + pytanie „czy anulowano", `RunCancelled` |
| `core/service.py` | **nowy** — wspólna usługa `prepare()` / `execute()` wydzielona z `cli.run()` |
| `core/execution.py` | `run_wave()` przyjmuje `control`: zdarzenia start/koniec bramki, ograniczone oczekiwanie, zamknięcie grup procesów przy anulowaniu |
| `core/orchestrator.py` | `run_gates(control=…)`: zdarzenia planu i fal, licznik „N z M" domykany też przez bramki pominięte |
| `cli.py` | `run` korzysta z `core.service` — walidacja polityki nie ma już dwóch kopii |

Kluczowe: `control` jest **opcjonalny**. Bez niego zachowanie silnika jest
identyczne jak wcześniej; CLI go nie podaje.

`RunRequest.scope_map_path` rozwiązuje problem z planu §5: snapshot mapy
zakresu jest *faktycznie przekazywany* bramce `G0.scope` jako ścieżka
bezwzględna, więc oceniany commit nie podstawia własnych kryteriów oceny.
Potwierdzone na żywo (`diff.scope_map_matched: true` przy polityce z panelu).

Nowe testy: `core/tests/test_service_progress.py` (10) — lint polityki przed
dotknięciem repo, snapshot mapy zakresu, licznik postępu, awaria odbiornika
nie wywraca przebiegu, anulowanie kończy przebieg bez decyzji.

## 3. Etap 2 — rejestr projektów i profile

* `services/repos.py` — walidacja ścieżki (**rozwiniętej**, więc dowiązanie
  nie jest furtką), wymóg korzenia repozytorium Git, lista referencji,
  podgląd zakresu liczony tym samym `ChangeContext.from_git` co CLI.
* Nazwa wersji Git: wąski wzorzec + `git rev-parse --verify --end-of-options`
  — `--upload-pack=…` i `main; rm -rf /` są odrzucane jako nazwa, nie
  wykonywane.
* `services/environment.py` — bramki z entry pointów, moduły językowe,
  narzędzia z wersjami, Bubblewrap, miejsce na dysku. Bez działającego
  Bubblewrapa panel mówi, czego brakuje, i **nie oferuje** trybu bez izolacji.
* `Settings.allowed_repo_roots` + `--repo-root` w `serve`.
* Ekrany: `/srodowisko`, `/projekty/{id}` (ustawienia, referencje, polityka).

## 4. Etap 3 — kolejka i uruchamianie

* `storage/jobs.py` — stany zadania, dzierżawa nadzorcy, zdarzenia z numerem
  kolejnym. Każda zmiana stanu to jeden atomowy `UPDATE` z warunkiem na stan
  poprzedni; wyścig „zakończone kontra anulowane" rozstrzyga baza.
* `jobs/spec.py` — zamrożone wejście: SHA + **faktyczny merge-base**, pełna
  treść polityki, wybrane bramki, wersje pakietów, skróty plików blokad,
  tożsamość repozytorium.
* `jobs/worker.py` — osobny program na jeden przebieg. Kolejność zapisu:
  **najpierw raport, potem stan zadania**.
* `jobs/supervisor.py` — jedna instancja (`flock`), jedno zadanie naraz,
  proces przebiegu w nowej sesji.
* `POST /api/v1/jobs` → `202`, `GET /jobs/{id}`, `/events`, `/cancel`,
  `/retry`; ekrany `/nowa-kontrola` (podgląd przed uruchomieniem) i `/zadania`.
* Klucz idempotencji: podwójne kliknięcie „Uruchom" nie zleca dwóch analiz.

## 5. Etap 4 — postęp i anulowanie

* Zdarzenia silnika lądują w `job_events`; przeglądarka pyta „co nowego po
  numerze N" co 2 s (TypeScript, `frontend/src/app.ts`). Bez JavaScriptu
  strona nadal pokazuje komplet po odświeżeniu.
* Postęp to „ukończono N z M kontroli", nigdy procent pozostałego czasu.
* Anulowanie: SIGTERM do procesu przebiegu, karencja, potem SIGKILL **grupy
  procesów** — zabicie samego rodzica nie wystarcza, bo workery bramek robią
  `setsid()`. Sprawdzone na żywo: po anulowaniu zero osieroconych procesów
  i zero pozostawionych kopii roboczych w `/tmp`.
* Restart: zadania po zmarłym nadzorcy są `interrupted`, nigdy wznawiane po
  cichu. Jeśli raport zdążył się zapisać — zadanie domykane jako `completed`.
* Anulowane i przerwane zadanie **nie ma** decyzji polityki i tego nie udaje.

## 6. Etap 5 — zarządzanie

* `storage/reviews.py` — ocena przypisana do pary **(projekt, fingerprint)**;
  zmiana zdania dopisuje wiersz, „aktualna" to ostatni.
* `services/metrics.py` — liczy **wystąpienia** i **problemy** osobno,
  a precyzja bierze pod uwagę wyłącznie ostatnią ocenę. To naprawia usterkę
  wskazaną w planie §6 (złączenie z całą historią ocen powielało wiersze).
* Incydent: osobne działanie dotyczące przebiegu, nie zmienia raportu.
* `storage/policies.py` + `services/policies.py` — profile, wersje, szkic →
  walidacja (`Policy.load()` + `lint()`) → **porównanie z wersją aktywną
  z oznaczeniem rozluźnień** → jawna aktywacja z autorem i uzasadnieniem.
  Aktywacja dotyczy przyszłych zadań; zapisane raporty zostają nietknięte.
* Ekrany: `/metryki`, `/polityki`, `/polityki/{id}`, `/polityki/wersje/{id}`,
  formularze oceny i incydentu na stronach znaleziska i przebiegu.

## 7. Baza i migracje

Schemat podniesiony z 1 do **3**, migracjami — baza z etapu 1 migruje się,
a nie jest odtwarzana:

* **2**: `repo_path`/`policy_profile_id` w `projects`, `policy_profiles`,
  `policy_revisions`, `jobs`, `job_events`, `reviews`, kolumny incydentu
  i `job_id` w `run_reports`;
* **3**: `report_findings` (płaski indeks znalezisk) **z backfillem** ze
  wcześniej zapisanych raportów — bez tego stare znaleziska zniknęłyby
  z metryk.

Migracja może mieć krok w Pythonie, nie tylko DDL — to była konieczna zmiana
mechanizmu migracji, nie ozdobnik.

## 8. Weryfikacja na żywo

Panel uruchomiony na `127.0.0.1:8080` z `--repo-root /home/tarze07/poligon`,
projekt wskazujący `taskboard-demo`, polityka skopiowana z `core/policy/`:

```
  1 queued          zadanie przyjęte do kolejki
  4 prepared        3 kontroli, zakres c8b12de0a29a → b64b59efcd44
  6 plan            3 kontroli w 2 falach [0/3]
 10 gate_finished   G0.provenance   1 commitów bez oznaczenia pochodzenia [1/3]
 11 gate_finished   G0.scope        179 linii w 9 plikach [2/3]
 14 gate_finished   G3.secrets      1 sekretów w zmienionych plikach [3/3]
 15 report_stored   decyzja BLOCK, znalezisk: 2
 16 completed       zakończone: BLOCK
```

Sprawdzone też na żywo: podgląd zakresu z merge-base, odrzucenie
`--upload-pack=…` jako nazwy gałęzi, anulowanie przebiegu w toku (bez sierot),
ocena znaleziska, incydent, metryki, CSRF i obcy `Host`.

## 9. Czego NIE zrobiłem

* **Uwierzytelnienie operatora** (sesja, jednorazowy kod startowy) — panel
  jest jednoosobowy i lokalny, `--host 0.0.0.0` kończy się odmową startu.
* **Etap 6 w części „testy przeglądarkowe"** — zestaw sprawdza HTML i API
  bez sterownika przeglądarki (`chromium-cli` niedostępny w tym środowisku).
* **Retencja raportów i kopie zapasowe** — plan wymaga ich przed jakimkolwiek
  automatycznym usuwaniem historii; nic nie usuwa historii, więc nie jest to
  blokada, ale pozycja zostaje otwarta.
* **Publikowanie do GitHuba/GitLaba, harmonogramy, powiadomienia** — rozdz. 10
  planu, poza MVP.
* **CI**: job `web` w `.github/workflows/ci.yml` nie wymagał zmian (uruchamia
  ruff/mypy/pytest, więc obejmuje nowy kod). Świadomie **nie** dokładałem tam
  Bubblewrapa — testy kolejki uruchamiają wyłącznie bramki `G0.*`, które nie
  wołają zewnętrznych narzędzi, więc zestaw działa też bez izolacji.

## 10. Otwarte kwestie do decyzji

1. Domyślny `--repo-root` to katalog domowy operatora. Wąsko rozumiane
   bezpieczeństwo mówiłoby „żadnego domyślnego", ale wtedy nic nie działa
   po instalacji. Do świadomego potwierdzenia.
2. Panel trzyma treść raportu w SQLite, nie w plikach obok bazy. Dzięki temu
   „indeks bez raportu" jest stanem niemożliwym; przy dużej historii warto
   będzie to zrewidować.
3. Uwierzytelnienie operatora jest warunkiem czegokolwiek poza `127.0.0.1`
   i powinno być następnym etapem, jeśli panel ma opuścić jedną maszynę.
