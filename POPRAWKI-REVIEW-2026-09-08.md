# Poprawki po przeglądzie — 8 września 2026

Naprawiono wszystkie osiem usterek opisanych w
[przeglądzie implementacji](REVIEW-IMPLEMENTACJI-2026-09-07.md).

| Punkt przeglądu | Zmiana | Potwierdzenie |
|---|---|---|
| 1. Fałszywy `pass` w C# | Restore w prywatnej kopii, z cache NuGet, bez sieci; nieudane przygotowanie lub awaria MSBuild zgłaszane jako `error` | Przez `run_gates`: poprawny kod → `pass`, `CS0029` → `fail`, brak pakietu i awaria MSBuild → `error`; repozytorium operatora pozostaje czyste |
| 2. Uszkodzony raport przyjmowany przez import | Sprawdzenie typów przed konwersją i zapisem; walidacja czasów bramek, struktur, pól indeksu, znalezisk i powodów | 28 wariantów błędnych danych daje 422, baza pozostaje pusta, poprawny raport z tym samym ID można potem zaimportować i odczytać |
| 3. Sekret w zagnieżdżonym obiekcie | Wrażliwy obiekt lub lista maskowane w całości, z zachowaniem liczników i flag pomiarowych | Znacznik testowego sekretu nie występuje w zapisanym raporcie ani eksporcie JSON/HTML/Markdown |
| 4. Ostrzeżenia i wyjątki oznaczane jako blokady | Powody zachowują kategorię `reasons` / `warnings` / `suppressed` | Ostrzeżenie i wyciszony powód nie trafiają do `blocking_reasons`; strona nadal pokazuje regułę i jej pomiar |
| 5. Filtr „wszystkie projekty” | Puste `projekt=` oznacza brak ograniczenia projektu; niepuste wartości są walidowane | Żądanie odpowiadające formularzowi zwraca 200 i właściwe wyniki; wybór konkretnego projektu pozostaje zaznaczony |
| 6. Brak czasu zamieniany na zero | Wspólny model wyniku dopuszcza brak pomiaru; JSON i Markdown go obsługują | Brak pola i `null` dają „brak danych” w widokach i eksporcie, jawne zero pozostaje zerem |
| 7. Nieprawidłowa etykieta UTC | Konwersja czasu do UTC przed formatowaniem; brak strefy pozostaje jawny | Sprawdzone dodatnie i ujemne przesunięcia, przejście na kolejny dzień, UTC i data bez strefy |
| 8. Niedziałający `--reload` | Uvicorn dostaje ścieżkę fabryki, a proces aplikacji dziedziczy ustawienia CLI | Rzeczywisty proces `serve --reload` obsługuje HTTP i zapis projektu w wybranym katalogu; test sprząta procesy serwera |

Testy regresji są częścią projektu:

- [web/tests/test_review_regressions.py](web/tests/test_review_regressions.py)
- [web/tests/test_cli.py](web/tests/test_cli.py)
- [csharp/tests/test_gate_static.py](csharp/tests/test_gate_static.py)

## Wyniki weryfikacji

| Sprawdzenie | Wynik |
|---|---|
| Pełny zestaw `core/tests` | 162 zaliczone |
| Pełny zestaw `web/tests` | 121 zaliczonych, 2 ostrzeżenia deprecacyjne zależności |
| Pakiet C# | 9 zaliczonych przypadków, 7 pominiętych |
| Ruff: core, web, C# — kod i testy | bez błędów |
| Mypy strict: core, web, C# | bez błędów |
| `web/frontend`: `npm run check` | bez błędów |
| `git diff --check` | bez błędów |

Łącznie zaliczono 292 różne przypadki testowe, w tym 49 dodanych przy tej
poprawce. W pierwszym pełnym przebiegu C# jedna asercja oczekiwała kodu
NuGet `NU1101`, podczas gdy restore bez źródeł prawidłowo zwraca `NU1100`.
Po skorygowaniu tej asercji ponownie wykonano cały `test_gate_static.py`:
6 przypadków zaliczonych. Trzy testy adaptera były zaliczone w pełnym przebiegu.

Siedem testów C# pominięto, ponieważ środowisko nie udostępnia
`gatekeeper-cs-helper` lub `diff-cover`. Dotyczą złożoności, weryfikacji
krzyżowej, jakości testów i pokrycia. Wszystkie testy naprawianej kontroli
kompilacji C# wykonano z rzeczywistym .NET SDK i izolacją Bubblewrap.

Testy HTTP i izolacji uruchomiono poza zewnętrznym sandboxem środowiska.
Przypadek `--reload` używał tymczasowej bazy i lokalnego portu. Nie wykonywano
testów w graficznej przeglądarce ani pełnych zestawów pakietów Python i TS.

Pakiety NuGet muszą znajdować się w lokalnym cache przed kontrolą C#.
Brak pakietu daje czytelny błąd wykonania; bramka nie pobiera zależności z sieci.
