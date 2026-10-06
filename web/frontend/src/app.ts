/**
 * Interakcje panelu. Wszystko tutaj jest *dodatkiem*: strona wyrenderowana
 * przez serwer pokazuje komplet danych i działa bez tego pliku.
 *
 * Filtrowanie znalezisk dzieje się po stronie przeglądarki, bo pełna lista
 * jest już w dokumencie — panel nie stosuje limitu 10 znalezisk z komentarza
 * w PR (PLAN-WEB-UI.md §3).
 */

interface FilterState {
  severity: string;
  gate: string;
  text: string;
}

function normalize(value: string): string {
  return value.trim().toLowerCase();
}

function matches(item: HTMLElement, state: FilterState): boolean {
  if (state.severity && item.dataset.severity !== state.severity) {
    return false;
  }
  if (state.gate && item.dataset.gate !== state.gate) {
    return false;
  }
  if (state.text) {
    const haystack = normalize(item.dataset.search ?? item.textContent ?? "");
    if (!haystack.includes(state.text)) {
      return false;
    }
  }
  return true;
}

function setupFindingFilter(root: HTMLElement): void {
  const items = Array.from(root.querySelectorAll<HTMLElement>("[data-finding]"));
  const severity = document.querySelector<HTMLSelectElement>("#filtr-waga");
  const gate = document.querySelector<HTMLSelectElement>("#filtr-bramka");
  const text = document.querySelector<HTMLInputElement>("#filtr-tekst");
  const counter = document.querySelector<HTMLElement>("#licznik-znalezisk");
  if (!severity && !gate && !text) {
    return;
  }

  const apply = (): void => {
    const state: FilterState = {
      severity: severity?.value ?? "",
      gate: gate?.value ?? "",
      text: normalize(text?.value ?? ""),
    };
    let visible = 0;
    for (const item of items) {
      const show = matches(item, state);
      item.hidden = !show;
      if (show) {
        visible += 1;
      }
    }
    if (counter) {
      // Liczba zawsze podaje ile z ilu — inaczej filtr wygląda jak brak danych.
      counter.textContent = `${visible} z ${items.length}`;
    }
  };

  for (const control of [severity, gate]) {
    control?.addEventListener("change", apply);
  }
  text?.addEventListener("input", apply);
  apply();
}

function setupDetailsToggle(): void {
  const button = document.querySelector<HTMLButtonElement>("#rozwin-wszystko");
  if (!button) {
    return;
  }
  button.hidden = false;
  button.addEventListener("click", () => {
    const sections = Array.from(document.querySelectorAll<HTMLDetailsElement>("details.bramka"));
    const shouldOpen = sections.some((section) => !section.open);
    for (const section of sections) {
      section.open = shouldOpen;
    }
    button.textContent = shouldOpen ? "Zwiń wszystkie bramki" : "Rozwiń wszystkie bramki";
    button.setAttribute("aria-expanded", String(shouldOpen));
  });
}

interface JobEvent {
  seq: number;
  at: string;
  kind: string;
  gate: string | null;
  message: string;
  completed: number | null;
  total: number | null;
}

interface JobEventsResponse {
  state: string;
  state_label: string;
  terminal: boolean;
  last_seq: number;
  events: JobEvent[];
}

/**
 * Postęp zadania. Przeglądarka pyta „co nowego po numerze N", a nie pobiera
 * całej historii co dwie sekundy. Bez JavaScriptu strona nadal pokazuje
 * komplet zdarzeń — tyle że dopiero po odświeżeniu.
 */
function setupJobProgress(container: HTMLElement): void {
  const jobId = container.dataset.jobEvents;
  const body = document.querySelector<HTMLElement>("#zdarzenia");
  if (!jobId || !body || container.dataset.terminal === "1") {
    return;
  }
  const label = document.querySelector<HTMLElement>("#postep-tekst");
  const rows = Array.from(body.querySelectorAll<HTMLElement>("tr"));
  let last = rows.length > 0 ? Number(rows[rows.length - 1]?.dataset.seq ?? 0) : 0;
  if (!Number.isFinite(last)) {
    last = 0;
  }

  const append = (event: JobEvent): void => {
    const row = document.createElement("tr");
    row.dataset.seq = String(event.seq);
    const progress = event.total ? `${event.completed} z ${event.total}` : "";
    for (const value of [
      String(event.seq),
      event.at,
      event.kind,
      event.gate ?? "",
      event.message,
      progress,
    ]) {
      const cell = document.createElement("td");
      // textContent, nigdy innerHTML: komunikat pochodzi z narzędzia
      // uruchomionego na cudzym kodzie.
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  };

  const poll = async (): Promise<void> => {
    let payload: JobEventsResponse;
    try {
      const response = await fetch(`/api/v1/jobs/${jobId}/events?after=${last}`, {
        headers: { accept: "application/json" },
      });
      if (!response.ok) {
        return;
      }
      payload = (await response.json()) as JobEventsResponse;
    } catch {
      // Zerwana sieć nie ma prawa zepsuć strony; następna próba za 2 sekundy.
      return;
    }
    for (const event of payload.events) {
      append(event);
      last = Math.max(last, event.seq);
    }
    if (label) {
      const progress = payload.events.at(-1);
      const counter =
        progress && progress.total ? ` — ukończono ${progress.completed} z ${progress.total}` : "";
      label.textContent = payload.terminal
        ? "zakończone"
        : `${payload.state_label}${counter}`;
    }
    if (payload.terminal) {
      window.clearInterval(timer);
      // Po zakończeniu przeładowujemy, żeby pokazać wynik i przyciski działań.
      window.location.reload();
    }
  };

  const timer = window.setInterval(() => void poll(), 2000);
  void poll();
}

/**
 * Przelacznik skorki. Bez tego pliku zostaje motyw systemowy
 * (`prefers-color-scheme` w `app.css`) — dlatego przycisk powstaje tutaj,
 * a nie w szablonie: bez JavaScriptu nie ma prawa pojawic sie martwa kontrolka.
 *
 * Wybor pamietamy w `localStorage`, bo to preferencja tej przegladarki, a nie
 * stan panelu — nie ma po co jechac do bazy ani do ciasteczka.
 */
const KLUCZ_SKORKI = "gk-motyw";

type Motyw = "jasny" | "ciemny" | "system";

function zapisanyMotyw(): Motyw {
  try {
    const zapisany = window.localStorage.getItem(KLUCZ_SKORKI);
    if (zapisany === "jasny" || zapisany === "ciemny") {
      return zapisany;
    }
  } catch {
    // Prywatne okno albo zablokowane dane witryny — motyw systemowy wystarczy.
  }
  return "system";
}

function zastosujMotyw(motyw: Motyw): void {
  const korzen = document.documentElement;
  if (motyw === "system") {
    korzen.removeAttribute("data-motyw");
  } else {
    korzen.setAttribute("data-motyw", motyw);
  }
}

function systemowoCiemny(): boolean {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

function setupThemeToggle(): void {
  const marka = document.querySelector<HTMLElement>("header.top .marka");
  if (!marka) {
    return;
  }
  const przycisk = document.createElement("button");
  przycisk.type = "button";
  przycisk.className = "przelacznik-skorki";

  const odswiez = (motyw: Motyw): void => {
    const ciemny = motyw === "ciemny" || (motyw === "system" && systemowoCiemny());
    przycisk.textContent = ciemny ? "skorka: ciemna" : "skorka: jasna";
    przycisk.setAttribute(
      "aria-label",
      ciemny ? "Skorka ciemna — przelacz na jasna" : "Skorka jasna — przelacz na ciemna",
    );
    przycisk.setAttribute("aria-pressed", String(ciemny));
  };

  let motyw = zapisanyMotyw();
  zastosujMotyw(motyw);
  odswiez(motyw);

  przycisk.addEventListener("click", () => {
    const ciemnyTeraz = motyw === "ciemny" || (motyw === "system" && systemowoCiemny());
    motyw = ciemnyTeraz ? "jasny" : "ciemny";
    zastosujMotyw(motyw);
    odswiez(motyw);
    try {
      window.localStorage.setItem(KLUCZ_SKORKI, motyw);
    } catch {
      // Wybor zadziala do konca tej strony; zapamietanie go nie jest krytyczne.
    }
  });

  // Zmiana ustawienia systemu ma byc widoczna, dopoki operator nie wybral sam.
  window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (motyw === "system") {
      odswiez(motyw);
    }
  });

  marka.appendChild(przycisk);
}

function start(): void {
  setupThemeToggle();
  const findings = document.querySelector<HTMLElement>("[data-findings-root]");
  if (findings) {
    setupFindingFilter(findings);
  }
  setupDetailsToggle();
  const job = document.querySelector<HTMLElement>("[data-job-events]");
  if (job) {
    setupJobProgress(job);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start);
} else {
  start();
}
