"""Adaptery lintera i kontroli typów: tsc, eslint.

Typy w trybie strict wyłapują dużą część halucynacji API — wywołanie metody,
której nie ma, albo argumentu o innej nazwie. To jest tania bramka o wysokiej
trafności. `tsc` pełni tu dla TS/JS dokładnie tę samą rolę, co `mypy` dla
Pythona (`llm-code-gatekeeper`, `adapters/linters.py`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gatekeeper_core.adapters.base import (
    ToolFailed,
    ToolMissing,
    parse_compiler_diagnostics,
    relative_to_repo,
    run_tool,
)
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.core.finding import Finding, Severity
from gatekeeper_core.core.plugins import StaticCheckOutcome
from gatekeeper_core.core.runner import Sandbox, SandboxPolicy

TSC = "tsc"
ESLINT = "eslint"


# --------------------------------------------------------------------------
# tsc — kontrola typów TypeScriptu; dla TS/JS pełni tę samą rolę co mypy
# --------------------------------------------------------------------------


def resolve_bin(repo: Path, name: str) -> str:
    """Preferuj binarkę przypiętą w `node_modules/.bin` repozytorium nad
    globalną — projekt ma zwykle zablokowaną konkretną wersję tsc/eslinta
    w `package.json`, a `PATH` może wskazywać coś zupełnie innego."""
    local = repo / "node_modules" / ".bin" / name
    return str(local) if local.is_file() else name


def tsc_scenario(level: str, code: str, message: str) -> str:
    return (
        f"Kompilator TypeScriptu zgłasza `{code}`: {message}. W kodzie od agenta ta klasa "
        "błędów zwykle oznacza wywołanie API, które nie istnieje albo przyjmuje inne "
        "argumenty — czyli awarię przy pierwszym uruchomieniu tej ścieżki, nie przy "
        "budowaniu."
    )


def parse_tsc(payload: str, repo: Path, gate: str) -> list[Finding]:
    def severity_of(level: str, code: str) -> Severity:
        return Severity.HIGH if level == "error" else Severity.MEDIUM

    return parse_compiler_diagnostics(payload, repo, gate, "tsc", tsc_scenario, severity_of)


def run_tsc(
    repo: Path,
    sandbox: Sandbox,
    gate: str,
    timeout_s: float = 120.0,
    args: list[str] | None = None,
) -> list[Finding]:
    tsc_bin = resolve_bin(repo, "tsc")
    command = [tsc_bin, "--noEmit", "--pretty", "false", *(args or [])]
    # tsc: 0 = czysto, 1 = znalezione błędy, 2 = błąd konfiguracji/użycia
    result = run_tool(command, repo, sandbox, timeout_s, ok_returncodes=(0, 1))
    return parse_tsc(result.stdout, repo, gate)


# --------------------------------------------------------------------------
# eslint
# --------------------------------------------------------------------------

#: Rdzeniowe reguły „problem” (błąd, nie styl). Reszta reguł `error`-level
#: w konfiguracji zespołu (a wiele configów ma ich dziesiątki, w tym czysto
#: stylistyczne) trafia do MEDIUM — inaczej pierwszy config ze
#: `"semi": "error"` zalewa raport.
ESLINT_HIGH_RULES = frozenset(
    {
        "no-undef",
        "no-unreachable",
        "no-dupe-keys",
        "no-dupe-args",
        "no-dupe-class-members",
        "no-unsafe-negation",
        "no-unsafe-optional-chaining",
        "no-const-assign",
        "no-cond-assign",
        "no-func-assign",
        "no-import-assign",
        "no-self-compare",
        "use-isnan",
        "no-invalid-regexp",
        "no-eval",
        "no-implied-eval",
        "@typescript-eslint/no-unsafe-assignment",
        "@typescript-eslint/no-unsafe-call",
        "@typescript-eslint/no-unsafe-member-access",
        "@typescript-eslint/no-unsafe-return",
    }
)


def eslint_severity(rule_id: str | None, level: int) -> Severity:
    if level >= 2:
        return Severity.HIGH if rule_id in ESLINT_HIGH_RULES else Severity.MEDIUM
    return Severity.LOW


def eslint_scenario(rule_id: str, message: str) -> str:
    return (
        f"Linter zgłasza `{rule_id}`: {message}. Reguły „problem” (no-undef, no-unreachable, "
        "no-eval i podobne) wskazują kod, który zachowa się inaczej, niż wygląda, albo "
        "wykona coś niebezpiecznego — nie kwestię formatowania."
    )


def parse_eslint(payload: str, repo: Path, gate: str) -> list[Finding]:
    """`eslint --format json` → `Finding`.

    `payload` puste/`None` → `"[]"` (eslint na czystym repo bez plików do
    sprawdzenia legalnie nic nie wypisuje) — to jest jedyny przypadek
    „legalnie pusto", więc nie przechodzi przez poniższy `except`. Każda
    INNA wartość, której nie da się sparsować jako JSON (urwany output,
    traceback na stdout, zepsuty `--format`), to nie „brak znalezisk", tylko
    brak dowodu — fail-closed: `ToolFailed` zamiast cichego `[]` (REVIEW.md
    §5 P1). `TsJsStaticChecker.check()` łapie `ToolFailed`: z `require_eslint:
    true` bramka kończy się `error`, bez tego — `eslint_available=False` i
    znaleziska z tego przebiegu są odrzucane, nie fałszywie zielone.
    """
    try:
        data = json.loads(payload or "[]")
    except json.JSONDecodeError as exc:
        raise ToolFailed(f"eslint nie zwrócił poprawnego JSON-a: {payload[:500]!r}") from exc
    findings: list[Finding] = []
    for file_result in data:
        file = relative_to_repo(str(file_result.get("filePath") or ""), repo)
        for item in file_result.get("messages") or []:
            rule_id = item.get("ruleId")  # `None` dla błędów parsera eslinta
            message = str(item.get("message") or "").strip()
            findings.append(
                Finding(
                    gate=gate,
                    rule_id=f"eslint.{rule_id or 'parse-error'}",
                    severity=eslint_severity(rule_id, int(item.get("severity") or 1)),
                    title=message,
                    failure_scenario=eslint_scenario(rule_id or "parse-error", message),
                    file=file,
                    line=item.get("line"),
                    evidence={"snippet": f"{rule_id}:{message}", "level": item.get("severity")},
                )
            )
    return findings


def run_eslint(
    repo: Path,
    sandbox: Sandbox,
    gate: str,
    timeout_s: float = 120.0,
    args: list[str] | None = None,
) -> list[Finding]:
    eslint_bin = resolve_bin(repo, "eslint")
    command = [eslint_bin, "--format", "json", *(args or []), "."]
    # eslint: 0 = czysto, 1 = znaleziska, 2 = błąd konfiguracji/użycia
    result = run_tool(command, repo, sandbox, timeout_s, ok_returncodes=(0, 1))
    return parse_eslint(result.stdout, repo, gate)


_ESLINT_CONFIG_NAMES = (
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    "eslint.config.mts",
    "eslint.config.cts",
)


def has_eslint_config(repo: Path) -> bool:
    """Plik configu eslinta w korzeniu repo albo klucz `eslintConfig`
    w `package.json` (legacy config, nadal honorowany przez eslint 8).

    Zepsuty `package.json` to nie „jest config" — eslint i tak by na nim
    padł; tu po prostu nie liczy się jako config.
    """
    if any((repo / name).is_file() for name in _ESLINT_CONFIG_NAMES):
        return True
    package_json = repo / "package.json"
    if not package_json.is_file():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and "eslintConfig" in data


class TsJsStaticChecker:
    """`StaticChecker` (`gatekeeper_core.core.plugins`) dla TS/JS: tsc + eslint."""

    checker_id = "ts_js"
    languages = ("typescript", "javascript")

    def empty_facts(self) -> dict[str, Any]:
        return {
            "static.ts_files_checked": 0,
            "static.tsconfig_found": False,
            "static.tsc_available": True,
            "static.js_files_checked": 0,
            "static.eslint_config_found": False,
            "static.eslint_available": True,
        }

    def check(
        self, change: ChangeContext, config: dict[str, Any], gate_id: str, budget_s: float
    ) -> StaticCheckOutcome:
        facts = self.empty_facts()
        findings: list[Finding] = []
        ts_files = [
            f.path for f in change.effective_files if f.language == "typescript" and f.status != "D"
        ]
        js_files = [
            f.path for f in change.effective_files if f.language == "javascript" and f.status != "D"
        ]
        facts["static.ts_files_checked"] = len(ts_files)
        facts["static.js_files_checked"] = len(js_files)

        require_tsc = bool(config.get("require_tsc", False))
        require_eslint = bool(config.get("require_eslint", False))
        sandbox = Sandbox(
            SandboxPolicy(
                network=False, timeout_s=budget_s, keep_env=tuple(config.get("keep_env", ()))
            )
        )

        if ts_files:
            tsconfig_path = str(config.get("tsconfig_path", "tsconfig.json"))
            tsconfig = change.repo / tsconfig_path
            facts["static.tsconfig_found"] = tsconfig.is_file()
            if not facts["static.tsconfig_found"] and require_tsc:
                # Bez configu tsc się nie uruchamia — a agent, który chce ominąć
                # kontrolę typów, po prostu nie dokłada tsconfiga. Z
                # `require_tsc: true` to brak dowodu, nie `pass` (REVIEW.md §5 P1).
                return StaticCheckOutcome(
                    findings=findings,
                    facts=facts,
                    error=(
                        f"zmiana zawiera pliki TypeScript ({len(ts_files)}), a w repo nie ma "
                        f"`{tsconfig_path}` — tsc nie sprawdził "
                        "typów (require_tsc: true)"
                    ),
                )
            if facts["static.tsconfig_found"]:
                try:
                    findings.extend(
                        run_tsc(
                            change.repo,
                            sandbox,
                            gate_id,
                            timeout_s=budget_s / 4,
                            args=list(config.get("tsc_args", [])),
                        )
                    )
                except ToolMissing as exc:
                    facts["static.tsc_available"] = False
                    if require_tsc:
                        return StaticCheckOutcome(findings=findings, facts=facts, error=str(exc))
                except ToolFailed as exc:
                    if require_tsc:
                        return StaticCheckOutcome(findings=findings, facts=facts, error=str(exc))
                    facts["static.tsc_available"] = False

        if ts_files or js_files:
            has_config = has_eslint_config(change.repo)
            facts["static.eslint_config_found"] = has_config
            if not has_config and require_eslint:
                return StaticCheckOutcome(
                    findings=findings,
                    facts=facts,
                    error=(
                        f"zmiana zawiera pliki TS/JS ({len(ts_files) + len(js_files)}), a w repo "
                        "nie ma configu eslinta (eslint.config.*, .eslintrc*, "
                        "`eslintConfig` w package.json) — eslint nie sprawdził kodu "
                        "(require_eslint: true)"
                    ),
                )
            if has_config:
                try:
                    findings.extend(
                        run_eslint(
                            change.repo,
                            sandbox,
                            gate_id,
                            timeout_s=budget_s / 4,
                            args=list(config.get("eslint_args", [])),
                        )
                    )
                except ToolMissing as exc:
                    facts["static.eslint_available"] = False
                    if require_eslint:
                        return StaticCheckOutcome(findings=findings, facts=facts, error=str(exc))
                except ToolFailed as exc:
                    if require_eslint:
                        return StaticCheckOutcome(findings=findings, facts=facts, error=str(exc))
                    facts["static.eslint_available"] = False
        return StaticCheckOutcome(findings=findings, facts=facts)
