"use strict";
/**
 * Interakcje panelu. Wszystko tutaj jest *dodatkiem*: strona wyrenderowana
 * przez serwer pokazuje komplet danych i działa bez tego pliku.
 *
 * Filtrowanie znalezisk dzieje się po stronie przeglądarki, bo pełna lista
 * jest już w dokumencie — panel nie stosuje limitu 10 znalezisk z komentarza
 * w PR (PLAN-WEB-UI.md §3).
 */
function normalize(value) {
    return value.trim().toLowerCase();
}
function matches(item, state) {
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
function setupFindingFilter(root) {
    const items = Array.from(root.querySelectorAll("[data-finding]"));
    const severity = document.querySelector("#filtr-waga");
    const gate = document.querySelector("#filtr-bramka");
    const text = document.querySelector("#filtr-tekst");
    const counter = document.querySelector("#licznik-znalezisk");
    if (!severity && !gate && !text) {
        return;
    }
    const apply = () => {
        const state = {
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
function setupDetailsToggle() {
    const button = document.querySelector("#rozwin-wszystko");
    if (!button) {
        return;
    }
    button.hidden = false;
    button.addEventListener("click", () => {
        const sections = Array.from(document.querySelectorAll("details.bramka"));
        const shouldOpen = sections.some((section) => !section.open);
        for (const section of sections) {
            section.open = shouldOpen;
        }
        button.textContent = shouldOpen ? "Zwiń wszystkie bramki" : "Rozwiń wszystkie bramki";
        button.setAttribute("aria-expanded", String(shouldOpen));
    });
}
/**
 * Postęp zadania. Przeglądarka pyta „co nowego po numerze N", a nie pobiera
 * całej historii co dwie sekundy. Bez JavaScriptu strona nadal pokazuje
 * komplet zdarzeń — tyle że dopiero po odświeżeniu.
 */
function setupJobProgress(container) {
    const jobId = container.dataset.jobEvents;
    const body = document.querySelector("#zdarzenia");
    if (!jobId || !body || container.dataset.terminal === "1") {
        return;
    }
    const label = document.querySelector("#postep-tekst");
    const rows = Array.from(body.querySelectorAll("tr"));
    let last = rows.length > 0 ? Number(rows[rows.length - 1]?.dataset.seq ?? 0) : 0;
    if (!Number.isFinite(last)) {
        last = 0;
    }
    const append = (event) => {
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
    const poll = async () => {
        let payload;
        try {
            const response = await fetch(`/api/v1/jobs/${jobId}/events?after=${last}`, {
                headers: { accept: "application/json" },
            });
            if (!response.ok) {
                return;
            }
            payload = (await response.json());
        }
        catch {
            // Zerwana sieć nie ma prawa zepsuć strony; następna próba za 2 sekundy.
            return;
        }
        for (const event of payload.events) {
            append(event);
            last = Math.max(last, event.seq);
        }
        if (label) {
            const progress = payload.events.at(-1);
            const counter = progress && progress.total ? ` — ukończono ${progress.completed} z ${progress.total}` : "";
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
function start() {
    const findings = document.querySelector("[data-findings-root]");
    if (findings) {
        setupFindingFilter(findings);
    }
    setupDetailsToggle();
    const job = document.querySelector("[data-job-events]");
    if (job) {
        setupJobProgress(job);
    }
}
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
}
else {
    start();
}
