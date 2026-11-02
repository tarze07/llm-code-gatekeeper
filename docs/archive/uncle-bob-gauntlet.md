# Uncle Bob i teza „nie czytam kodu agentów"

> **Wersja markdown prezentacji.** Slajdy HTML: [`uncle-bob-gauntlet.html`](uncle-bob-gauntlet.html) · PDF: [`uncle-bob-gauntlet.pdf`](uncle-bob-gauntlet.pdf)

---

## TL;DR

- Chodzi o **wątek na X/Twitterze z 23 lipca 2026 r.** (nie o wpis blogowy ani artykuł) — Robert C. „Uncle Bob" Martin w odpowiedzi na tweeta Oriego Pomerantza napisał, że zamiast czytać kod, „otacza agentów ekstremalnymi ograniczeniami" i wielowarstwowym *gauntletem* testów oraz metryk.
- Jego argument: **człowiek jest wąskim bardłem** — jeśli AI generuje kod wielokrotnie szybciej, ręczny przegląd niweczy zysk. Osąd przenosi się z implementacji na specyfikację zachowania.
- W zamian proponuje **gauntlet**: testy jednostkowe, testy akceptacyjne w Gherkin, procedury QA, metryki jakości (złożoność cyklomatyczna, rozmiar modułów, struktura zależności), testowanie mutacyjne i pokrycie. Człowiek recenzuje **zachowanie**, nie implementację.
- Najpoważniejszy krytyk to Grady Booch: metryki dają pewność działania, ale nie wykrywają luk bezpieczeństwa ani martwego kodu.

---

## Spis slajdów

| # | Tytuł | Sekcja |
|---|---|---|
| 01 | Tytuł | Wprowadzenie |
| 02 | Teza | |
| 03 | Źródło | |
| 04 | Cytat | |
| 05 | Wąskie gardło | Argumentacja |
| 06 | Reguły vs narzędzia | |
| 07 | Zasada nadrzędna | |
| 08 | Podział pracy | Gauntlet |
| 09 | Gauntlet — pięć bramek | |
| 10 | Cyklomatyczność i CRAP | |
| 10b | Cyklomatyczność w praktyce | |
| 11 | Pipeline pięciu agentów | |
| 12 | Rola człowieka | |
| 13 | Specyfikacje | |
| 14 | Analogia | Kontekst |
| 15 | Zwrot | |
| 16 | Zastrzeżenie o cytacie | |
| 17 | Booch — sprzeciw | Reakcje |
| 18 | Społeczność | |
| 19 | Formalizacja | |
| 20 | To nie jest vibe coding | |
| 21 | Wdrożenie | Wdrożenie |
| 22 | Kalibracja | |
| 23 | Caveats | |
| 24 | Takeaway | |
| 25 | Źródła | |

---

## 01. Tytuł

**WĄTEK NA X · 23 LIPCA 2026**

> # Uncle Bob przestał czytać kod agentów.

Nie esej. Nie „vibe coding". Analiza tezy, argumentacji i tego, co stawia w zamian — **gauntlet**.

---

## 02. Teza

**TEZA**

> # Człowiek jest wąskim gardłem.
> # Osąd trzeba przenieść *wyżej*.

Agent pisze wielokrotnie szybciej, niż człowiek jest w stanie czytać. Ręczny przegląd kodu linia po linii niweczy ten zysk. Zamiast czytać implementację — Uncle Bob otacza agentów ekstremalnymi ograniczeniami i recenzuje **zachowanie**, nie ciało funkcji.

---

## 03. Źródło

**ŹRÓDŁO**

> # To tweet, nie artykuł.

- Odpowiedź na wpis Oriego Pomerantza, 23 lipca 2026 — wątek viralowy, ok. 15 tys. polubień i ponad 2 tys. udostępnień w ciągu doby.
- Blog *cleancoder.com* milczy od stycznia 2023. Długiej formy jego autorstwa na ten temat nie ma.
- Wcześniejsza, „zarządcza" wersja tej samej myśli — tweet do @wookash_podcast, wiosna 2026.

> Dokładne linki na ostatnim slajdzie.

---

## 04. Cytat

**23 LIPCA 2026 · ODPOWIEDŹ NA ORIEGO POMERANTZA**

> Moja obecna strategia to nie czytać żadnego kodu, który piszą moi agenci. To jedyny sposób, żeby wykorzystać ich produktywność. Zamiast tego otaczam agentów ekstremalnymi ograniczeniami — testami jednostkowymi, testami gherkin, procedurami QA, metrykami jakości, testowaniem mutacyjnym, pokryciem i wieloma innymi. Ostatecznie mam bardzo wysoką pewność co do kodu, który produkują, bo musiał przejść przez cały ten tor przeszkód.

*Robert C. „Uncle Bob" Martin, 23 lipca 2026*

---

## 05. Wąskie gardło

**ARGUMENT**

> # Człowiek jest wolny w kodzie.

> **× n** — Agent generuje wielokrotnie więcej kodu, niż człowiek jest w stanie przeczytać. Jeśli recenzja pozostaje ręczna i linia po linii, to **górny limit tempa rozwoju** wyznacza prędkość czytania człowieka — nie prędkość pisania modelu. Recenzent staje się bramką poboru na autostradzie generowania.

---

## 06. Reguły vs narzędzia

**DLACZEGO NIE PROMPT**

> # Agenty też toną w bałaganie.

**Prompt na 5–10 stron.** Reguły wklejone do kontekstu modele traktowały „jak Kodeks Piratów — bardziej jako wytyczne niż reguły". Efekt **lost in the middle** wymazywał je w środku dokumentu.

**Narzędzia po generacji.** Egzekwowanie przeniesione poza model: ograniczenia rozmiaru funkcji, złożoności, mutacja, pokrycie. **Ograniczam rozmiar i złożoność funkcji do bólu** — tak to ujął Uncle Bob.

> Relacja z pierwszej ręki: wczesny agent Grok zmieniał jedną rzecz i psuł drugą, krążył — aż w końcu „poddał się" wobec splątanego kodu.

---

## 07. Zasada nadrzędna

> # Nie ufamy AI.
> # Ufamy *ścieżce weryfikacji*, przez którą kod musi przejść.

---

## 08. Podział pracy

**KTO PISZE, KTO RECENZUJE**

> # Podział według *artefaktu*.

| Artefakt | Pisze | Recenzuje |
|---|---|---|
| Kod implementacyjny | agent | **nikt — z założenia** |
| Testy jednostkowe | agent | **nikt — z założenia** |
| Testy akceptacyjne w Gherkinie | agent | Uncle Bob — rygor zależny od krytyczności |
| Procedury QA | agent | Uncle Bob — rygor zależny od krytyczności |
| Końcowy test manualny | Uncle Bob | okresowo, jako zabezpieczenie |

---

## 09. Gauntlet

> # Wielowarstwowy potok *bramek*.

**01. Testy jednostkowe.** Pisze agent. Ręcznej recenzji nie ma.

**02. Gherkin — Given / When / Then.** Język domeny, nie nazwy klas. Recenzuje człowiek.

**03. Procedury QA jako skrypt.** Dokument QA zamieniany w wykonywalny test.

**04. Metryki jakości.** Złożoność cyklomatyczna, rozmiar modułów i funkcji, struktura zależności.

**05. Mutacja, pokrycie, CRAP.** Kontrola jakości samych testów — odpowiedź na pytanie „skąd wiem, że testy agenta są dobre".

---

## 10. Cyklomatyczność i CRAP

**METRYKI RYZYKA**

> # Złożoność cyklomatyczna i *CRAP* — co naprawdę mierzą

### Złożoność cyklomatyczna McCabe'a

Liczba **liniowo niezależnych ścieżek** przez funkcję. Im więcej rozgałęzień (if, case, &&, ||, pętla, catch), tym więcej ścieżek do przetestowania.

> **`M(m) = E − N + 2P`**

- *m* — analizowana **metoda**, czyli pojedyncza funkcja lub podprogram.
- *E* — krawędzie jej grafu sterowania.
- *N* — węzły.
- *P* — spójne składowe (zwykle 1).

W praktyce liczy się prościej: **1 + liczba gałęzi** w ciele funkcji.

**Progi:**

| Zakres | Ocena |
|---|---|
| 1–10 | prosta, czytelna |
| 11–20 | złożona, ryzykowna |
| 21+ | wymaga refaktoryzacji |

### CRAP — wskaźnik ryzyka zmiany

Alberto Savoia i Bob Evans (Google Testing Blog, 2007). *m* — ta sama **metoda** co w McCabe'u. Metryka łączy **comp** z pokryciem **cov** i wskazuje funkcje, które wyglądają niewinnie, ale są słabo pokryte.

> **`CRAP(m) = comp(m)² × (1 − cov(m))³ + comp(m)`**

Wynik **powyżej 30** = „CRAPpy", do sprzątania. Jeśli *cov* = 100%, wynik zrównuje się z *comp* — czyli sama złożoność wystarczy, by wyrokować.

> *Uncle Bob wymienia obie metryki obok siebie — cyklomatyczność mówi o **kształcie** kodu, CRAP o **kształcie i pokryciu razem**.*

---

## 10b. Cyklomatyczność w praktyce

**W PRAKTYCE**

> # Ta sama logika, *dwa kształty*.

### Zagnieżdżone `if` — `comp = 4`

```js
function raty(kwota, lata, dochod) {
  if (kwota > 0) {
    if (lata >= 5 && lata <= 30) {
      if (dochod > kwota * 3) {
        return kwota / (lata * 12);
      }
    }
  }
  return null;
}
```

Trzy poziomy zagłębień. Każdy *if* to nowa ścieżka, każdy kolejny poziom mnoży kombinacje. Refaktoryzacja: wyciągnij warunki do nazwanych predykatów albo użyj **strażnika**.

### Strażnik — `comp = 1`

```js
function raty(kwota, lata, dochod) {
  if (!poprawneWejscie(kwota, lata, dochod)) return null;
  return kwota / (lata * 12);
}
```

Logika biznesowa ta sama, ale **jedna gałąź** i wczesny powrót. Łatwiej testować, łatwiej czytać, łatwiej rozszerzać. *Cyklomatyczność to nie wyrok — to wskaźnik, gdzie kod się plącze.*

---

## 11. Pipeline pięciu agentów

**JEDNA HISTORYJKA**

> # Pipeline *pięciu* agentów.

1. **Specifier** — dokument ludzki zamienia w kryteria Gherkin i procedurę QA.
2. **Coder** — pisze testy jednostkowe i implementację.
3. **Cleaner** — uruchamia analizę CRAP i sprząta bałagan.
4. **Hardener** — testowanie mutacyjne „bez litości".
5. **QA** — dokument QA zamienia w wykonywalny skrypt.

> Wąskie zadania, małe okna kontekstu. Agenty startują na czysto i „umierają" po zakończeniu. Pięć minut jednego agenta vs około godziny pełnego gauntletu vs pół dnia człowieka.

---

## 12. Rola człowieka

**PRZESUNIĘCIE ROLI**

> # Człowiek nie czyta ciała funkcji.
> # Projektuje *poprawność*.

- Definiuje, co jest stanem poprawnym i które awarie trzeba wykryć.
- Ustala standardy jakości i recenzuje Gherkin oraz procedury QA.
- Nadal ręcznie projektuje strukturę modułów i „przepytuje" agentów o zależności.
- Okresowo wykonuje końcowy test manualny — jako zabezpieczenie.

---

## 13. Specyfikacje

> # Specyfikacje są *efemeryczne*.

**Nie archiwizowane.** Uncle Bob **nie traktuje** promptów ani specyfikacji jako trwałego „nowego kodu źródłowego". Ciężkie podejście *spec-driven development* odrzuca jako powrót do waterfallu z lat siedemdziesiątych.

**Iteracja zamiast planu.** Skoro koszt zmiany kodu spadł, lepiej iterować niż pisać idealną specyfikację. Trwałym artefaktem pozostaje **zestaw testów akceptacyjnych**, nie plik specyfikacji.

---

## 14. Analogia

> # „Nie czytamy wyjścia kompilatora"
> # to nie *jego* sformułowanie.

Analogia krąży w komentarzach i publicystyce relacjonującej tezę. Uncle Bob mówi raczej o koszcie zmiany: gdyby każda przebudowa domu kosztowała dolara, nie płaciłbyś architektowi za idealny plan — przesunąłbyś kuchnię i sprawdził, jak działa.

---

## 15. Zwrot

**KONTEKST**

> # Tak, *zmienił zdanie*.
> # Sam to przyznaje.

| Data | Cytat |
|---|---|
| **XI 2022** | „Sztuczna inteligencja po raz kolejny rozczaruje futurologów." |
| **II 2024** | „Sztuczna inteligencja to porządne narzędzie… Nie położy kresu programowaniu ani programistom." |
| **X 2024** | „Zgadza się." — pod filmem *Nie wierzyłem, że sztuczna inteligencja jest przyszłością programowania. Miałem rację.* |
| **2026** | „Gdybym to napisał, zmieniłem zdanie. Dwa lata temu nie sądziłem, że modele okażą się aż tak zdolne." |

---

## 16. Zastrzeżenie o cytacie

> # „Odpowiedzialny za każdą linię"
> # to *nie jest* jego cytat o AI.

Popularne przypisywanie Uncle Bobowi tej frazy nie zostało potwierdzone jako dosłowna wypowiedź z lat 2023–2025. To **parafraza** jego etosu z *Przysięgi Programisty* z 18 listopada 2015 oraz książki *The Clean Coder*. Myśl „muszę rozumieć kod, bo jestem za niego odpowiedzialny" pochodzi z tweeta Pomerantza, nie Uncle Boba.

---

## 17. Booch — sprzeciw

**SPRZECIW**

> W odróżnieniu od Boba, recenzuję cały kod pisany przez agentów. Pokrycie testami i podobne metryki dają mi pewność działania, ale nie dają mi żadnej pewności, że agenci nie wprowadzili luk bezpieczeństwa, martwego kodu… *Ufaj, ale weryfikuj.*

*Grady Booch, współtwórca UML · sedno sporu: metryki nie zastępują „smell" ani nie wyłapują luk, których strukturalne wskaźniki nie widzą.*

---

## 18. Społeczność

**REAKCJE**

> # Ostry podział.

**Krytyka.** „To czysty argument z autorytetu." Pytanie „ale czy on czyta te testy?" pada wciąż. SQLite ma **590 razy więcej kodu testów niż produkcyjnego** — przy takim fundamencie przeczytanie kodu to niewielki koszt. Ktoś opisał agenta, który zaimplementował funkcję „całkowicie na odwrót", a mimo to wszystkie testy przechodziły.

**Głos środka.** Josh Manders: „Ja robię to samo, ale najpierw gauntlet, potem przegląd." Japońska analiza: *nie czytać ≠ nie weryfikować*. Skopiowanie metody bez uprzedniego zbudowania fundamentu testowego to porzucenie kontroli jakości, nie jej automatyzacja.

> Hacker News (wątek 49074693) · memy: „Uncle Bob vibe coding before GTA 6" · ironia autora *Clean Code*.

---

## 19. Formalizacja

> # Metoda żyje *poza* tweetem.

- Szkolenie O'Reilly: *„Agenci AI dla czystego kodu z Uncle Bobem Martinem"* — program zapowiada dyscypliny testowania akceptacyjnego, jednostkowego, mutacyjnego i analizy jakości.
- github.com/swingerman/disciplined-agentic-engineering — ATDD dla Claude Code, inspirowane *empire-2025*.
- AmazingAng/old-coder — „nie czytaj kodu, każ mu przejść gauntlet".
- Adrian Bailador opisał na Medium *Acceptance Pipeline Specification*: Gherkin → JSON IR → generowane testy → runner, mutacja jako warstwa poboczna.

> *empire-2025* nie jest publicznie otwartym, samodzielnym repozytorium. Znane pośrednio; prace Uncle Boba prowadzone w Clojure / Speclj.

---

## 20. To nie jest vibe coding

**NAJCZĘSTSZY BŁĄD**

> # To *nie jest* vibe coding.

**Vibe.** Generujesz, liczysz na szczęście, nie weryfikujesz. Porzucenie jakości pod etykietą produktywności.

**Gauntlet.** Nie czytasz implementacji, ale recenzujesz zachowanie, metryki i testy — a testy same są kontrolowane mutacją.

> „Nie czytanie kodu" to **rezultat** dojrzałego systemu weryfikacji, nie punkt startowy.

---

## 21. Wdrożenie

**JEŚLI CHCESZ TO WDROŻYĆ**

> # Fundament najpierw.
> # Potem *mniej* czytania.

1. Uporządkuj wymagania i zidentyfikuj ryzyka.
2. Kryteria akceptacji wyraź jako testy w Gherkinie.
3. Sprawdź jakość samych testów mutacją.
4. Przygotuj procedury QA i obserwowalność produkcyjną.
5. Wyznacz zakres, który można oceniać automatycznie.
6. Dopiero wtedy ograniczaj ludzkie przeglądy w tym zakresie.

---

## 22. Kalibracja

**PROGI**

> # Nie wszędzie pełny gauntlet.

Sam Uncle Bob przyznaje: *„Samo to, że możemy coś zrobić, nie znaczy, że powinniśmy. Często używam po prostu testów jednostkowych i metryki CRAP."*

- Ścieżki krytyczne — Gherkin i QA rygorystycznie.
- Reszta — kontrola wyrywkowa.
- Kod wrażliwy bezpieczeństwem — przegląd ludzki albo recenzja adwersarialna drugim modelem (zastrzeżenie Boocha).

---

## 23. Caveats

**ZASTRZEŻENIA**

> # To *wątek*, nie recenzowana publikacja.

- Tweety można edytować i usuwać. Fragmenty o pipeline'ie pięciu agentów pochodzą też z transkrypcji wtórnych.
- Stanowisko krystalizowało się miesiącami; spór z Boochem relacjonowano już w kwietniu 2026.
- Blogi wtórne (*explainx*, *startupfortune*) służą do rekonstrukcji cytatów — ich prognozy nie są faktami.

> Sygnał do ostrożności: udokumentowane luki produkcyjne w kodzie, który „przebiegł gauntlet". Kanoniczna wersja metody powstanie dopiero wraz z esejem lub rozdziałem.

---

## 24. Takeaway

> # Nie czytaj kodu,
> # dopóki nie masz czym
> # go *łapać*.

Cytuj tweet z 23 lipca 2026 — nie „wpis blogowy".
Odpowiedzialność zostaje przy człowieku: on definiuje poprawność.
Metryki nie widzą exploitów.

---

## 25. Źródła

> # Gdzie to sprawdzić.

- **Główny tweet, 23.07.2026** → <https://x.com/unclebobmartin/status/2080257779395154409>
- **Wersja „zarządcza", wiosna 2026** → <https://x.com/unclebobmartin/status/2044114698451476492>
- **Zwrot poglądów** → <https://x.com/unclebobmartin/status/2048746803667828790>
- **Wątek Hacker News** → <https://news.ycombinator.com/item?id=49074693>
- **Spór z Boochem** → <http://mvark.blogspot.com/2026/04/uncle-bob-vs-grady-booch-rethinking.html>
- **Pipeline pięciu agentów** → <https://blog.alexrusin.com/uncle-bob-ai-coding-agents/>
- **Szkolenie O'Reilly** → <https://www.oreilly.com/live-events/ai-agents-for-clean-code-with-uncle-bob-martin/0642572376765/>

---

*Uncle Bob · gauntlet · 2026*
