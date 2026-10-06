# Testy przeglądarkowe

Etap 6 planu zakłada zestaw ze sterownikiem przeglądarki. W tym środowisku
nie ma Chromium (`chromium-cli` / Playwright), więc CI **nie** uruchamia
e2e graficznego.

Scenariusze odbioru 1–12 z `PLAN-WEB-UI.md` §9 pokrywa `web/tests/`:
HTML renderowany przez serwer, API i prawdziwa baza SQLite. Strona działa
bez JavaScriptu — odświeżanie postępu jest dodatkiem.

Gdy pojawi się sterownik:

```bash
cd web && playwright test
```

Do tego czasu nie dokładamy zależności, której nie da się tu uruchomić.
