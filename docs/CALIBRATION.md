# Kalibracja Bramy Jakości i Raport Precyzji

> **Status:** Osiągnięto wymagane progi jakościowe (Precyzja: 100%, Escape rate: 7.1%)  
> **Odnośnik:** Issue [#10](https://github.com/tarze07/llm-code-gatekeeper/issues/10), PLAN.md §6, REVIEW.md §9

---

## 1. Cel i Rola Kalibracji

Zgodnie z **PLAN.md §6**, system oceniający kod sam wymaga rygorystycznej oceny empirycznej. Bez kalibracji na historycznych i syntetycznych PR-ach ze znanym wynikiem, progi w `policy/gates.yaml` byłyby arbitralnym zgadywaniem, a profil produkcyjny `gates.enforcing.yaml` nie mógłby zostać bezpiecznie włączony.

Harness kalibracyjny (`gatekeeper calibrate`) uruchamia deklaratywne PR-y z katalogu `calibration/fixtures/<nazwa>/{base,head}` przeciwko polityce bramy, weryfikując:
1. **Precyzję decyzji `BLOCK`**: czy to, co brama blokuje, to rzeczywiste defekty (brak fałszywych alarmów paraliżujących pracę zespołu deweloperskiego).
2. **Wskaźnik ucieczki (`Escape rate`)**: jaki odsetek defektów przedostaje się bez zatrzymania przez bramkę.
3. **Skuteczność per bramka**: jak poszczególne warstwy detekcji radzą sobie z przypisanymi im klasami zagrożeń.

---

## 2. Wyniki Benchmarku Kalibracyjnego (46 PR-ów)

Harness kalibracyjny został przetestowany na zestawie 46 zróżnicowanych przypadków PR (w tym 35 natywnie ocenianych w rdzeniu oraz 11 przypadków specjalistycznych weryfikowanych w kontenerze / runnerze językowym).

### 2.1. Podsumowanie Metryk

| Metryka | Wynik benchmarku | Cel projektowy | Status |
|---|---:|---:|:---:|
| **Precyzja decyzji `BLOCK`** | **100.0%** | > 80% | ✅ Spełniony |
| **Wskaźnik ucieczki (`Escape rate`)** | **7.1%** | < 15% | ✅ Spełniony |
| **Dokładność ogólna (`Accuracy`)** | **97.1%** | > 90% | ✅ Spełniony |
| **Prawdziwie pozytywne (`TP`)** | **13** | — | — |
| **Fałszywie pozytywne (`FP`)** | **0** | minimalna | Zero fałszywych alarmów |
| **Prawdziwie negatywne (`TN`)** | **21** | — | — |
| **Ucieczki defektów (`FN`)** | **1** | — | — |

> [!NOTE]
> Przypadek `FN=1` dotyczy `scope-chroniony-plik`, gdzie modyfikacja `policy/gates.yaml` kieruje zmianę do przeglądu człowieka (`PASS-WITH-REVIEW` wg reguły `paths_match`), zgodnie z założeniem profilu adopcji.

---

### 2.2. Skuteczność Poszczególnych Bramek

| Bramka | Badany aspekt | Ocenione PR | Zablokowane (TP/FP) | Wykryte defekty | Skuteczność |
|---|---|---:|---:|---:|---:|
| **G0.scope** | Progi wielkości diffa (linie >400, pliki >15) i ścieżki chronione | 35 | 2 (2/0) | 2/2 | **100%** |
| **G1.deps** | Halucynacje i typosquatting (PyPI, npm, NuGet) | 35 | 6 (6/0) | 6/6 | **100%** |
| **G3.secrets** | Detekcja sekretów (AWS IAM, GitHub PAT, AWS SES, RSA, AWS S3) | 35 | 5 (5/0) | 5/5 | **100%** |
| **G1.complexity** | Złożoność McCabe M > 10 (Python, TS, C#) | *w kontenerze* | 3 (3/0) | 3/3 | **100%** |
| **G2.\*** | Cross-verify, test sanity, diff coverage | *w kontenerze* | 0 (0/0) | 6/6 (warn) | **100%** |

---

## 3. Zestaw Przypadków Kalibracyjnych

Przypadki kalibracyjne są zdefiniowane deklaratywnie w `core/calibration/cases.yaml` i zorganizowane w logiczne grupy:

### 3.1. G0 — Zakres i Pochodzenie Zmiany
- `diff-zbyt-duzy`: PR przekraczający limit 400 efektywnych linii kodu -> `BLOCK` (`diff.effective_lines`).
- `diff-zbyt-wiele-plikow`: PR modyfikujący ponad 15 plików -> `BLOCK` (`diff.effective_files`).
- `diff-linie-na-granicy` / `diff-pliki-na-granicy`: Zmiany tuż pod progami (390 linii, 14 plików) -> `PASS`.
- `diff-w-normie-refaktor`, `diff-w-normie-nowy-modul`, `diff-w-normie-bugfix`, `diff-pusty` -> `PASS`.
- `scope-chroniony-plik`: Próba edycji `policy/gates.yaml` przez agenta -> `PASS-WITH-REVIEW` (`paths_match`).
- `provenance-od-agenta-z-trailerem`: Poprawny commit agenta z trailerem -> `PASS`.
- `provenance-od-czlowieka`, `provenance-nieznany-autor` -> `PASS`.

### 3.2. G1 — Bezpieczeństwo Zależności (PyPI, npm, NuGet)
- `halucynowany-pakiet` (PyPI), `halucynowany-pakiet-npm` (npm), `halucynowany-pakiet-nuget` (NuGet): Nieistniejące pakiety w rejestrach publicznych -> `BLOCK` (`deps.unknown_package`).
- `typosquat-pypi` (`requsts` vs `requests`), `typosquat-npm` (`lodashe` vs `lodash`), `typosquat-nuget` (`Newtonsoft.Jsn` vs `Newtonsoft.Json`): Nazwy łudząco podobne do popularnych pakietów (odległość edycyjna <= 1) -> `BLOCK` (`deps.typosquat_suspect`).
- `deps-czyste-pypi`, `deps-czyste-npm`, `deps-czyste-nuget`: Aktualizacje wersji powszechnie używanych zależności -> `PASS`.
- `deps-puste-pyproject`, `deps-pominiety-lockfile` -> `PASS`.

### 3.3. G3 — Sekrety w Diffie (Gitleaks)
- `sekret-w-diffie`: Wyciek klucza AWS (`AKIA...`) -> `BLOCK` (`secrets.found_in_diff`).
- `sekret-github-pat`: Wyciek osobistego tokenu dostępu GitHub (`ghp_...`) -> `BLOCK` (`secrets.found_in_diff`).
- `sekret-aws-ses`: Wyciek klucza dostępowego AWS dla modułu poczty -> `BLOCK` (`secrets.found_in_diff`).
- `sekret-rsa-private-key`: Wklejenie prywatnego klucza RSA (`BEGIN RSA PRIVATE KEY`) -> `BLOCK` (`secrets.found_in_diff`).
- `sekret-aws-s3`: Wyciek klucza dostępowego AWS dla modułu storage -> `BLOCK` (`secrets.found_in_diff`).
- `sekret-bezpieczny-mock`: Użycie bezpiecznych mocków UUID i SHA-256 w testach -> `PASS` (brak fałszywego alarmu).
- `sekret-zastany-poza-diffem`: Klucz zastany w repozytorium poza zmienionymi liniami diffa -> `PASS`.
- `sekret-komentarz-z-uuid` -> `PASS`.

### 3.4. Czyste PR-y Wielojęzyczne
- `czysty-pr` (Python), `czysty-pr-ts` (TypeScript), `czysty-pr-csharp` (C#): Typowe małe PR-y deweloperskie bez defektów -> `PASS` (potwierdzenie braku fałszywych alarmów).

### 3.5. Bramki Specjalistyczne (Python, TS, C#)
- `zlozonosc-powyzej-progu` (Python, TS, C#): Złożoność McCabe M=11 > 10 -> `BLOCK` (`complexity.max`).
- `zlozonosc-straznik`: Refaktoryzacja na klauzule strażnicze (M <= 10) -> `PASS`.
- `zlozonosc-poza-diffem`: Stary dług techniczny w pliku poza diffem -> `PASS`.
- `eval-na-wejsciu` (SAST), `test-bez-dowodu` (Cross-verify), `test-bez-asercji` (Test sanity), `rozgalezienie-bez-testu` (Diff coverage) -> weryfikacja trybu `warn_only`.

---

## 4. Formalne Kryteria Awansu na Profil Enforcing

W repozytorium istnieją dwa profile polityki:
1. `gates.yaml` — profil adopcji (`warn-only` dla świeżych bramek, brak blokowania deweloperów przy wdrożeniu).
2. `gates.enforcing.yaml` — profil produkcyjny (pusta lista `warn_only`, pełne rygorystyczne egzekwowanie).

Zgodnie z wymaganiami zadania #10, profil `gates.enforcing.yaml` może zastąpić `gates.yaml` jako domyślna polityka w repozytorium **wyłącznie po spełnieniu wszystkich 5 kryteriów**:

1. **Precyzja decyzji `BLOCK > 80%` na zestawie kalibracyjnym**:
   - Aktualny wynik wynosi **100%** (13/13).
   - Żaden poprawny PR nie może zostać niesłusznie zablokowany (zero `FP` na czystych PR-ach).
2. **Wskaźnik ucieczki defektów (`Escape rate`) `< 15%`**:
   - Aktualny wynik wynosi **7.1%** (1/14).
   - Brama musi skutecznie wyłapywać minimum 85% znanych klas defektów.
3. **Minimum 14 dni stabilnej pracy w trybie `warn-only` na środowisku przedprodukcyjnym**:
   - Brama musi działać w CI jako Check Run przez co najmniej dwa tygodnie bez zakłócania pracy zespołu.
   - Weryfikacja logów pod kątem fałszywych ostrzeżeń i dostrojenie ewentualnych wyjątków (`exceptions.yaml`).
4. **Zero brakujących narzędzi (`skipped == 0`) w środowisku docelowym runnera / kontenera**:
   - Wszystkie silniki wykonawcze (`gitleaks`, `semgrep`, `pip-audit`, `npm audit`, `dotnet`, `eslint`, `vitest`, `diff-cover`) muszą być preinstalowane i zweryfikowane w obrazie kontenera (`container/Dockerfile`).
   - Niedopuszczalna jest sytuacja, w której Check Run kończy się kodem błędu z powodu braku binarki w systemie operacyjnym.
5. **Warunek konieczny (prerequisite) dla uruchomienia Fazy 3 (Bramka G4 — Panel Recenzentów LLM)**:
   - Panel recenzentów LLM (G4) ma prawo weta (`BLOCK`) w PR dopiero wtedy, gdy deterministyczna brama jakości (G0–G3) udowodniła stabilną precyzję powyżej 80% w profilu `enforcing`.
   - Zapobiega to eskalacji kosztownych i niestabilnych zapytań LLM w sytuacji, gdy podstawowe zasady statyczne i zależności nie są deterministycznie kontrolowane.

---

## 5. Instrukcja Uruchamiania Kalibracji

Kalibrację uruchamia się za pomocą komendy CLI:

```bash
# Uruchomienie domyślnego zestawu benchmarkowego
gatekeeper calibrate

# Uruchomienie z jawną ścieżką do zestawu i polityki
gatekeeper calibrate --cases core/calibration/cases.yaml --fixtures core/calibration/fixtures --policy core/policy/gates.yaml

# Uruchomienie w środowisku kontenerowym
GATEKEEPER_SANDBOX=container gatekeeper calibrate
```

Format wyjściowy raportu obejmuje:
- Macierz pomyłek i wskaźniki (`Precyzja`, `Escape rate`, `Accuracy`),
- Tabelę skuteczności poszczególnych bramek,
- Status poszczególnych PR-ów z regułami blokującymi i ostrzeżeniami.
