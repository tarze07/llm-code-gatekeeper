# Próbki raportów

Materiał akceptacyjny do scenariuszy z `PLAN-WEB-UI.md` §9. Pliki są
wersjonowane w repozytorium, żeby testy nie zależały od lokalnej ścieżki
w katalogu domowym autora.

| Plik | Pochodzenie | Do czego służy |
|---|---|---|
| `demo-celowe-usterki.json` | przebieg na gałęzi z celowo wprowadzonymi usterkami | 22 znaleziska, 11 bramek, BLOCK, `G2.diff_coverage` ze statusem `pass` mimo naruszenia progu (1/37 pokrytych linii) |
| `pierwszy-przebieg.json` | pierwszy przebieg bramy na aplikacji demonstracyjnej | bramka w stanie `error` przy `warn_only`, 4 nierozwiązane pakiety, 15 testów bez rozstrzygnięcia |
| `bez-znalezisk.json` | plik napisany na potrzeby testów | zero znalezisk i **brak** pomiaru pokrycia — „brak danych" musi się odróżniać od zera |

Redakcja: ścieżki `/home/<user>/poligon` zamieniono na `/repozytoria`,
a losowe nazwy katalogów roboczych bramek na `gatekeeper-wt-XXXXXXXX`.
Poza tym pliki są dokładnie tym, co wypisał `gatekeeper run --format json`,
łącznie z brakiem pola `report_version` — to właśnie ten historyczny format,
dla którego panel ma jawny adapter.
