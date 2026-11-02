# Przegląd implementacji llm-code-gatekeeper — 7 września 2026

**Stan po poprawkach — 8 września 2026: wszystkie osiem opisanych usterek
naprawiono i sprawdzono testami.** Szczegóły zmian i aktualne wyniki znajdują
się w [raporcie poprawek](POPRAWKI-REVIEW-2026-09-08.md).

Poniżej zachowano pierwotny przegląd z 7 września: osiem potwierdzonych
usterek, w tym regresję C#, która zwracała `pass` dla niekompilującej się zmiany.

Przegląd obejmuje bieżące zmiany względem `08409d5`, w szczególności nowy
pakiet `web/` oraz integrację bramek z izolacją procesów i kopiami repozytorium.
Zakres panelu przyjęto z `web/CONTRACT.md`: etapy 0–1, czyli import, historia,
widoki, eksport i rejestr projektów. Kolejka, uruchamianie kontroli z panelu,
zarządzanie politykami i logowanie należą do kolejnych etapów.

P1 oznacza usterkę wymagającą pilnej poprawki; P2 — błąd działania do poprawienia;
P3 — mniejszą usterkę o ograniczonym wpływie. Opisane wyniki pochodzą
z przeglądu kodu i testów uruchomionych na lokalnej implementacji.

## 1. [P1] Nowa izolacja powoduje fałszywie pozytywny wynik kontroli C#

**Miejsce:** `core/gatekeeper_core/core/execution.py:82–86`.
Powiązany adapter: `csharp/gatekeeper_csharp/adapters/dotnet.py`, funkcja
`run_dotnet_build`.

Każda bramka dostaje teraz świeżą kopię commita. Nie ma w niej ignorowanego
przez Git pliku `obj/project.assets.json`, który powstaje podczas
`dotnet restore`. Adapter nadal wywołuje `dotnet build --no-restore`.
W rezultacie kompilacja zatrzymuje się na `NETSDK1004`, zanim sprawdzi kod.
Ta diagnostyka nie staje się znaleziskiem w zmienionych liniach, więc bramka
zwraca `pass` i zero znalezisk.

**Odtworzenie:** utworzyć projekt biblioteki `net8.0`, ignorować `bin/` i `obj/`,
wykonać restore, a następnie zatwierdzić zmianę:

```csharp
public class Calc { public int Value() => "bad type"; }
```

W teście uzyskano:

| Wywołanie | Wynik |
|---|---|
| `dotnet build --no-restore` w przygotowanym repozytorium | błąd `CS0029` |
| `StaticGuard(...).run(change)` w tym samym repozytorium | `fail`, znalezisko `dotnet.CS0029` |
| Build w świeżej kopii commita | błąd `NETSDK1004`, brak assets |
| `run_gates(..., gates=[StaticGuard(...)])` | `pass`, zero znalezisk |

Problem występuje również przy `require_dotnet_build=True`.

**Poprawka:** przygotowywać zależności .NET wewnątrz izolowanej kopii z użyciem
kontrolowanego cache albo zapewnić równoważny, bezpieczny etap przygotowania.
Nieudany build bez użytecznych diagnostyk musi być zgłoszony jako błąd
wykonania kontroli. Dodać test przez `run_gates`, ponieważ bezpośredni test
adaptera omija przyczynę regresji.

## 2. [P2] Import zapisuje raporty, których panel nie potrafi później odczytać

**Miejsce:** `web/gatekeeper_web/services/reports.py:193–224` oraz
`web/gatekeeper_web/services/reports.py:148`.

Walidacja sprawdza czas całego przebiegu, ale nie sprawdza `duration_s`
poszczególnych bramek. Ponadto adapter wywołuje `dict(...)` na wartościach
wejściowych przed sprawdzeniem ich typu.

**Odtworzenie:** w `web/samples/bez-znalezisk.json` ustawić
`gates[0].duration_s` na `"wrong"` i zaimportować plik przez API.

| Operacja | Wynik rzeczywisty |
|---|---|
| Import | `201 Created`, raport zapisany |
| Otwarcie szczegółów w HTML | `500 Internal Server Error` |
| Odczyt raportu przez API | `500 Internal Server Error` |
| Eksport JSON | `500 Internal Server Error` |

Późniejsza konwersja `float("wrong")` kończy się wyjątkiem. W osobnym
przypadku ustawienie `gates[0].facts` na `3` powoduje błąd 500 już podczas
importu, zamiast czytelnego odrzucenia danych.

**Poprawka:** zweryfikować pełną strukturę i typy przed konwersją oraz
zapisem. Wadliwy raport powinien zwracać 422 i nie trafiać do bazy. Testować
zarówno odrzucenie, jak i możliwość odczytu każdego przyjętego raportu.

## 3. [P2] Zagnieżdżone dane uwierzytelniające omijają maskowanie

**Miejsce:** `web/gatekeeper_web/services/redaction.py:46–54`.

Nazwa wskazująca na sekret jest respektowana tylko wtedy, gdy wartość jest
tekstem. Dla obiektu funkcja przechodzi do jego dzieci i gubi informację,
że cały obiekt pochodzi z pola `credentials`.

**Odtworzenie:** do faktów bramki dodać:

```json
{"credentials": {"value": "review_dummy_value_no_real_secret"}}
```

Po imporcie eksport JSON zawiera tę wartość w oryginale. Test używał
wyłącznie sztucznego znacznika. Przy rzeczywistych poświadczeniach sekret
pozostałby w bazie i pobieranym raporcie mimo deklarowanego maskowania.

**Poprawka:** zamaskować całe pole o wrażliwej nazwie niezależnie od typu
albo przekazywać informację o wrażliwym rodzicu do wszystkich potomków.
Sprawdzić wynik po zapisie i eksporcie, a nie tylko pojedynczą funkcję.

## 4. [P2] Ostrzeżenia i wyciszone powody są oznaczane jako blokady

**Miejsce:** `web/gatekeeper_web/services/view.py:220–225` oraz `:104–106`.

Widok łączy `decision.reasons`, `decision.warnings` i `decision.suppressed`.
Następnie uznaje powód za blokujący na podstawie samego `source`, np.
`threshold`. Nie zachowuje informacji, czy polityka tylko ostrzegła,
czy wyciszyła powód wyjątkiem.

**Odtworzenie:** w próbce `demo-celowe-usterki.json` przenieść powód
`coverage.diff_ratio` z `decision.reasons` do `decision.warnings`, a następnie
powtórzyć dla `decision.suppressed`. W obu przypadkach powód nadal znajduje
się w `GateView.blocking_reasons`. Szablon używa tej listy do oznaczenia
bramki jako „narusza politykę”.

Operator dostaje mylące wyjaśnienie wpływu bramki na decyzję. Ogólny werdykt
pozostaje odczytany z raportu, ale opis poszczególnych bramek jest błędny.

**Poprawka:** zachować kategorię powodu w modelu widoku i odróżniać aktywne
powody decyzji od ostrzeżeń i wyjątków. Samo źródło `threshold` nie określa
skutku reguły.

## 5. [P2] Filtrowanie historii dla wszystkich projektów kończy się błędem 422

**Miejsce:** `web/gatekeeper_web/pages.py:105`; formularz:
`web/gatekeeper_web/templates/runs.html:13–14`.

Opcja „wszystkie” ma wartość pustą. Przeglądarka wysyła więc `projekt=`,
natomiast endpoint oczekuje liczby całkowitej lub braku parametru.
Pusty tekst nie jest dla niego brakiem parametru.

**Odtworzenie:** otworzyć Historię, pozostawić projekt „wszystkie”, wybrać
decyzję BLOCK i nacisnąć „Filtruj”. Żądanie odpowiadające formularzowi:

```text
GET /przebiegi?projekt=&decyzja=BLOCK&od=&do=&q=
```

Zamiast listy wyników pojawia się strona 422 z komunikatem o niepoprawnej
liczbie w polu `projekt`. Dotyczy to również wyszukiwania i dat przy
pozostawieniu wszystkich projektów.

**Poprawka:** normalizować pustą wartość do `None` przed walidacją albo
nie wysyłać pustego parametru. Sprawdzić rzeczywistą serializację formularza.

## 6. [P2] Brak pomiaru czasu jest prezentowany jako zero sekund

**Miejsce:** `web/gatekeeper_web/services/deserialize.py:37` oraz `:48`.

Konwersja `float(value or 0.0)` zamienia brak opcjonalnego `duration_s`
na pomiar równy zero. Jest to niezgodne z `web/CONTRACT.md`, według którego
brak pomiaru powinien pozostać brakiem danych.

**Odtworzenie:** usunąć `duration_s` z raportu i jednej bramki, a następnie
zaimportować. Formatter szczegółów otrzymuje `0.0` i pokazuje `0,0 s`.
Indeks historii zachowuje `None`, więc dwa widoki tego samego przebiegu
prezentują różne informacje.

**Poprawka:** zachować informację o brakującym pomiarze w adapterze lub
modelu widoku; wyświetlać „brak danych” również w szczegółach i eksporcie.

## 7. [P2] Godzina z dowolnej strefy jest podpisywana jako UTC

**Miejsce:** `web/gatekeeper_web/templating.py:55`.

Formatter dopisuje `UTC`, jeżeli data ma jakąkolwiek strefę, ale nie
przelicza godziny na UTC.

**Odtworzenie:** przekazać `2026-09-05T12:00:00+02:00`. Wynik to
`2026-09-05 12:00 UTC`, podczas gdy ta chwila to `2026-09-05 10:00 UTC`.
Dotyczy importowanych raportów z przesunięciem innym niż zero oraz widoków
i eksportu HTML korzystających z tego formattera.

**Poprawka:** przeliczyć datę przez `astimezone(UTC)` albo pokazać rzeczywistą
strefę. Nie nadawać etykiety UTC bez konwersji.

## 8. [P3] Opcja `serve --reload` zatrzymuje uruchamianie panelu

**Miejsce:** `web/gatekeeper_web/cli.py:51`.

CLI przekazuje do Uvicorna gotowy obiekt aplikacji. Tryb `reload` wymaga
ścieżki importu, pod którą proces może ponownie załadować aplikację.

**Odtworzenie:** w katalogu `web/` wykonać:

```bash
.venv/bin/gatekeeper-web serve --reload --state-dir /tmp/gatekeeper-review-reload-state
```

Proces kończy się kodem 3. Uvicorn wypisuje komunikat:

```text
You must pass the application as an import string to enable 'reload' or 'workers'.
```

**Poprawka:** użyć ścieżki importu lub fabryki aplikacji i zapewnić
przekazanie ustawień także do procesu uruchamianego po przeładowaniu.

## Weryfikacja i ograniczenia przeglądu

| Sprawdzenie | Wynik |
|---|---|
| `core/`: `.venv/bin/python -m pytest tests -ra --tb=short` | 162 zaliczone |
| `web/`: `.venv/bin/python -m pytest tests -ra` | 76 zaliczonych, 2 ostrzeżenia deprecacyjne |
| `web/frontend/`: `npm run check` | zaliczone |
| `git diff --check` | bez błędów |
| Dodatkowe przypadki panelu | 8 niezaliczonych asercji, potwierdzających punkty 2–7 |
| Dodatkowy przypadek C# przez orkiestrator | 1 niezaliczona asercja, potwierdzająca punkt 1 |
| Rzeczywiste wywołanie CLI z `--reload` | kod 3, potwierdzający punkt 8 |

Dodatkowe testy opisują oczekiwane poprawne zachowanie. Ich niepowodzenie
jest dowodem odtworzenia usterki w istniejącej implementacji. Nie wprowadzano
celowych błędów do kodu aplikacji ani poprawek implementacji.

Pliki pomocnicze utworzone na potrzeby tego przeglądu znajdują się lokalnie:

| Plik | Zawartość |
|---|---|
| `/tmp/test_gatekeeper_web_review.py` | przypadki panelu |
| `/tmp/test_gatekeeper_csharp_review.py` | integracyjny przypadek C# |
| `/tmp/gatekeeper-review-web.xml` | wynik przypadków panelu w formacie JUnit |
| `/tmp/gatekeeper-review-csharp.xml` | wynik przypadku C# w formacie JUnit |

Przypadki uruchamia się interpreterem odpowiedniego pakietu, np.
`.venv/bin/python -m pytest /tmp/test_gatekeeper_web_review.py -q -s`
z katalogu `web/`. Testy HTTP i izolacji wymagały uruchomienia poza
zewnętrznym sandboxem środowiska, który zakłócał działanie klienta testowego.
Sam test C# nadal korzystał z izolacji Bubblewrap implementowanej przez Gatekeepera.

Nie uruchamiano pełnych zestawów testów pakietów Python, TS i C# ani testów
w rzeczywistej przeglądarce. Formularz odtworzono żądaniem HTTP o tych samych
parametrach. Pełne zestawy rdzenia i panelu oraz kontrola typów frontendu
nie obejmowały wykrytych przypadków.
