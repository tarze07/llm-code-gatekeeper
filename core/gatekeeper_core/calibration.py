"""Zestaw kalibracyjny: deklaratywne PR-y (celowo zepsute i celowo czyste)
uruchamiane przeciw realnej polityce repo, żeby złapać regresję w regułach,
zanim złapie ją produkcja (PLAN.md §6 — „Faza 0"; TOOLS.md, kamień 3).

Przypadek to **dane**, nie kod Pythona: `calibration/cases.yaml` opisuje
oczekiwany werdykt, a `calibration/fixtures/<nazwa>/{base,head}/` to dwa
drzewa katalogów — stan repo przed i po zmianie. Recenzent bez znajomości
pytest potrafi dodać przypadek, wklejając realny PR jako dwa katalogi.

Przypadek, którego narzędzie (`requires_tools`) nie jest zainstalowane, jest
**pomijany**, nie failuje — ta sama zasada co w bramkach: brak narzędzia to
brak dowodu, a nie powód, żeby cały zestaw kalibracyjny czerwienił CI
repozytoriom, które świadomie nie mają np. semgrepa.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path

import yaml

from .core.change import ChangeContext
from .core.finding import RunResult, Verdict
from .core.orchestrator import run_gates
from .core.policy import Policy
from .core.runner import backend

DEFAULT_CASES_PATH = Path("calibration/cases.yaml")
DEFAULT_FIXTURES_DIR = Path("calibration/fixtures")

RULE_GATE_MAP: dict[str, str] = {
    "scope": "G0.scope",
    "diff": "G0.scope",
    "provenance": "G0.provenance",
    "deps": "G1.deps",
    "complexity": "G1.complexity",
    "static": "G1.static",
    "tests": "G2.cross_verify",
    "sanity": "G2.test_sanity",
    "coverage": "G2.diff_coverage",
    "secrets": "G3.secrets",
    "sast": "G3.sast",
    "sca": "G3.sca",
}


def gate_for_rule(rule_id: str) -> str:
    """Zwraca identyfikator bramki na podstawie prefiksu reguły polityki."""
    prefix = rule_id.split(".", 1)[0]
    return RULE_GATE_MAP.get(prefix, prefix)


def _resolve_default_cases_path(path: Path | str = DEFAULT_CASES_PATH) -> Path:
    p = Path(path)
    if not p.exists() and p == DEFAULT_CASES_PATH:
        core_p = Path("core") / DEFAULT_CASES_PATH
        if core_p.exists():
            return core_p
    return p


def _resolve_default_fixtures_dir(path: Path | str = DEFAULT_FIXTURES_DIR) -> Path:
    p = Path(path)
    if not p.exists() and p == DEFAULT_FIXTURES_DIR:
        core_p = Path("core") / DEFAULT_FIXTURES_DIR
        if core_p.exists():
            return core_p
    return p


@dataclass(frozen=True)
class CalibrationMetrics:
    """Metryki kalibracji bramy jakości (macierz pomyłek i wskaźniki skuteczności)."""

    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def precision_block(self) -> float | None:
        """Precyzja decyzji BLOCK: tp / (tp + fp)."""
        denom = self.tp + self.fp
        return self.tp / denom if denom > 0 else None

    @property
    def escape_rate(self) -> float | None:
        """Wskaźnik ucieczki defektów do produkcji: fn / (tp + fn)."""
        denom = self.tp + self.fn
        return self.fn / denom if denom > 0 else None

    @property
    def accuracy(self) -> float | None:
        """Dokładność klasyfikacji: (tp + tn) / total."""
        return (self.tp + self.tn) / self.total if self.total > 0 else None


@dataclass(frozen=True)
class GateMetric:
    """Statystyki skuteczności pojedynczej bramki w zestawie kalibracyjnym."""

    gate: str
    evaluated_count: int = 0
    blocked_tp: int = 0
    blocked_fp: int = 0
    assigned_defects: int = 0
    detected_defects: int = 0

    @property
    def blocked_total(self) -> int:
        return self.blocked_tp + self.blocked_fp

    @property
    def precision(self) -> float | None:
        """Precyzja decyzji BLOCK dla danej bramki: blocked_tp / blocked_total."""
        return self.blocked_tp / self.blocked_total if self.blocked_total > 0 else None

    @property
    def detection_rate(self) -> float | None:
        """Skuteczność wykrywania defektów dedykowanych tej bramce: detected / assigned."""
        return self.detected_defects / self.assigned_defects if self.assigned_defects > 0 else None


@dataclass(frozen=True)
class Expectation:
    verdict: Verdict | None = None
    #: Reguły, które MUSZĄ się pojawić wśród `decision.reasons` (blokują albo
    #: kierują do recenzenta — `warn_only` je stąd usuwa, patrz `warning_rules`).
    blocking_rules: tuple[str, ...] = ()
    #: Reguły, które MUSZĄ się pojawić wśród `decision.warnings` — typowe dla
    #: bramek świeżo wprowadzonych przez `warn_only` (TOOLS.md „kolejność budowy").
    warning_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class Case:
    name: str
    description: str
    fixture: str
    expect: Expectation
    #: Programy, bez których przypadek nie da się ocenić uczciwie (np. `semgrep`).
    requires_tools: tuple[str, ...] = ()
    requires_plugins: tuple[str, ...] = ()
    gate: str | None = None


@dataclass
class CaseResult:
    case: Case
    passed: bool
    skipped: bool = False
    reason: str = ""
    run: RunResult | None = None


@dataclass
class CalibrationReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CaseResult]:
        return [r for r in self.results if not r.passed and not r.skipped]

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def metrics(self) -> CalibrationMetrics:
        tp = 0
        fp = 0
        tn = 0
        fn = 0
        for r in self.results:
            if r.skipped:
                continue
            expects_block = (r.case.expect.verdict is Verdict.BLOCK) or bool(
                r.case.expect.blocking_rules
            )
            actual_block = bool(r.run and r.run.decision.verdict is Verdict.BLOCK)
            if expects_block and actual_block:
                tp += 1
            elif not expects_block and actual_block:
                fp += 1
            elif not expects_block and not actual_block:
                tn += 1
            else:
                fn += 1
        return CalibrationMetrics(tp=tp, fp=fp, tn=tn, fn=fn)

    @property
    def gate_stats(self) -> dict[str, GateMetric]:
        all_gates: set[str] = {
            "G0.provenance",
            "G0.scope",
            "G1.complexity",
            "G1.deps",
            "G1.static",
            "G2.cross_verify",
            "G2.test_sanity",
            "G2.diff_coverage",
            "G3.secrets",
            "G3.sast",
            "G3.sca",
        }
        for r in self.results:
            if r.case.gate:
                all_gates.add(r.case.gate)
            if r.run:
                for gr in r.run.gate_results:
                    all_gates.add(gr.gate)

        eval_counts: dict[str, int] = dict.fromkeys(all_gates, 0)
        blocked_tp: dict[str, int] = dict.fromkeys(all_gates, 0)
        blocked_fp: dict[str, int] = dict.fromkeys(all_gates, 0)
        assigned_defects: dict[str, int] = dict.fromkeys(all_gates, 0)
        detected_defects: dict[str, int] = dict.fromkeys(all_gates, 0)

        for r in self.results:
            if r.skipped:
                continue
            expects_block = (r.case.expect.verdict is Verdict.BLOCK) or bool(
                r.case.expect.blocking_rules
            )

            # Ocenione bramki
            evaluated_in_run: set[str] = set()
            if r.run:
                for gr in r.run.gate_results:
                    if gr.status != "skipped":
                        evaluated_in_run.add(gr.gate)
            for g in evaluated_in_run:
                eval_counts[g] = eval_counts.get(g, 0) + 1

            # Blokady per bramka
            blocking_gates_in_run: set[str] = set()
            if r.run and r.run.decision.verdict is Verdict.BLOCK:
                for reason in r.run.decision.reasons:
                    g = reason.gate or gate_for_rule(reason.rule)
                    blocking_gates_in_run.add(g)
            for g in blocking_gates_in_run:
                if expects_block:
                    blocked_tp[g] = blocked_tp.get(g, 0) + 1
                else:
                    blocked_fp[g] = blocked_fp.get(g, 0) + 1

            # Przypisane i wykryte defekty
            actual_blocking_rules = (
                {reason.rule for reason in r.run.decision.reasons} if r.run else set()
            )
            actual_warning_rules = (
                {warning.rule for warning in r.run.decision.warnings} if r.run else set()
            )

            for rule in r.case.expect.blocking_rules:
                g = r.case.gate or gate_for_rule(rule)
                assigned_defects[g] = assigned_defects.get(g, 0) + 1
                if rule in actual_blocking_rules:
                    detected_defects[g] = detected_defects.get(g, 0) + 1

            for rule in r.case.expect.warning_rules:
                g = r.case.gate or gate_for_rule(rule)
                assigned_defects[g] = assigned_defects.get(g, 0) + 1
                if rule in actual_warning_rules:
                    detected_defects[g] = detected_defects.get(g, 0) + 1

            if (
                r.case.gate
                and r.case.expect.verdict is Verdict.BLOCK
                and not r.case.expect.blocking_rules
            ):
                g = r.case.gate
                assigned_defects[g] = assigned_defects.get(g, 0) + 1
                if r.run and r.run.decision.verdict is Verdict.BLOCK:
                    detected_defects[g] = detected_defects.get(g, 0) + 1

        stats: dict[str, GateMetric] = {}
        for g in sorted(all_gates):
            stats[g] = GateMetric(
                gate=g,
                evaluated_count=eval_counts.get(g, 0),
                blocked_tp=blocked_tp.get(g, 0),
                blocked_fp=blocked_fp.get(g, 0),
                assigned_defects=assigned_defects.get(g, 0),
                detected_defects=detected_defects.get(g, 0),
            )
        return stats

    def render(self) -> str:
        lines = ["# Zestaw kalibracyjny", ""]
        m = self.metrics
        prec_str = f"{m.precision_block:.1%}" if m.precision_block is not None else "brak danych"
        esc_str = f"{m.escape_rate:.1%}" if m.escape_rate is not None else "brak danych"
        acc_str = f"{m.accuracy:.1%}" if m.accuracy is not None else "brak danych"

        lines.append("## Podsumowanie metryk")
        lines.append(f"- Precyzja decyzji BLOCK: {prec_str} (cel: > 80%) [TP={m.tp}, FP={m.fp}]")
        lines.append(
            f"- Wskaźnik ucieczki (Escape rate): {esc_str} (cel: < 15%) [FN={m.fn}, TP={m.tp}]"
        )
        lines.append(f"- Dokładność ogólna (Accuracy): {acc_str} [TN={m.tn}]")
        lines.append("")

        lines.append("## Skuteczność bramek")
        lines.append("| Bramka | Ocenione | Zablokowane (TP/FP) | Wykryte defekty | Skuteczność |")
        lines.append("|---|---:|---:|---:|---:|")
        for g, gs in self.gate_stats.items():
            if gs.evaluated_count == 0 and gs.assigned_defects == 0:
                continue
            det_str = f"{gs.detection_rate:.0%}" if gs.detection_rate is not None else "—"
            blocked_detail = f"{gs.blocked_total} ({gs.blocked_tp}/{gs.blocked_fp})"
            defect_ratio = f"{gs.detected_defects}/{gs.assigned_defects}"
            lines.append(
                f"| {g} | {gs.evaluated_count} | {blocked_detail} | {defect_ratio} | {det_str} |"
            )
        lines.append("")

        lines.append("## Status przypadków")
        for r in self.results:
            icon = "⏭️" if r.skipped else ("✅" if r.passed else "❌")
            suffix = f" — {r.reason}" if r.reason else ""
            verdict_badge = ""
            if r.run:
                verdict_badge = f" [{r.run.decision.verdict.value}]"
            elif r.case.expect.verdict:
                verdict_badge = f" [oczekiwano: {r.case.expect.verdict.value}]"
            lines.append(f"- {icon} `{r.case.name}`{verdict_badge}{suffix}")

        checked = [r for r in self.results if not r.skipped]
        passed = sum(1 for r in checked if r.passed)
        skipped = len(self.results) - len(checked)
        lines.append("")
        lines.append(
            f"{passed}/{len(checked)} przypadków zgodnych z oczekiwaniem "
            f"({skipped} pominiętych — brak narzędzia)"
        )
        return "\n".join(lines)


def load_cases(path: Path | str = DEFAULT_CASES_PATH) -> list[Case]:
    path = _resolve_default_cases_path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases: list[Case] = []
    for raw in data.get("cases") or []:
        expect_raw = raw.get("expect") or {}
        expect = Expectation(
            verdict=Verdict(expect_raw["verdict"]) if "verdict" in expect_raw else None,
            blocking_rules=tuple(expect_raw.get("blocking_rules") or ()),
            warning_rules=tuple(expect_raw.get("warning_rules") or ()),
        )
        cases.append(
            Case(
                name=raw["name"],
                description=raw.get("description", ""),
                fixture=raw.get("fixture", raw["name"]),
                expect=expect,
                requires_tools=tuple(raw.get("requires_tools") or ()),
                requires_plugins=tuple(raw.get("requires_plugins") or ()),
                gate=raw.get("gate"),
            )
        )
    return cases


def run_calibration(
    cases: list[Case],
    policy: Policy,
    fixtures_dir: Path | str = DEFAULT_FIXTURES_DIR,
) -> CalibrationReport:
    fixtures_dir = _resolve_default_fixtures_dir(fixtures_dir)
    report = CalibrationReport()
    for case in cases:
        # W kontenerze narzędzia pochodzą z obrazu — brak zgłosi samo uruchomienie.
        missing = (
            None
            if backend() == "container"
            else next((t for t in case.requires_tools if shutil.which(t) is None), None)
        )
        if missing:
            report.results.append(
                CaseResult(
                    case=case, passed=True, skipped=True, reason=f"narzędzie niedostępne: {missing}"
                )
            )
            continue
        missing_plugin = None
        for p in case.requires_plugins:
            eps = list(entry_points(group=p)) if "." in p else []
            if not eps:
                try:
                    __import__(p)
                except ImportError:
                    missing_plugin = p
                    break
        if missing_plugin:
            report.results.append(
                CaseResult(
                    case=case,
                    passed=True,
                    skipped=True,
                    reason=f"plugin niedostępny: {missing_plugin}",
                )
            )
            continue
        with _build_case_repo(fixtures_dir / case.fixture) as (repo_path, base_sha, head_sha):
            change = ChangeContext.from_git(repo_path, base_sha, head_sha)
            result = run_gates(change, policy)
        report.results.append(_check(case, result))
    return report


def _check(case: Case, result: RunResult) -> CaseResult:
    blocking_rules = {r.rule for r in result.decision.reasons}
    warning_rules = {w.rule for w in result.decision.warnings}
    problems: list[str] = []

    if case.expect.verdict is not None and result.decision.verdict != case.expect.verdict:
        problems.append(
            f"oczekiwano werdyktu {case.expect.verdict.value}, dostano "
            f"{result.decision.verdict.value}"
        )
    missing_blocking = [
        r
        for r in case.expect.blocking_rules
        if not any(
            br == r or br.startswith(f"{r}:") or br.startswith(f"{r} ") for br in blocking_rules
        )
    ]
    if missing_blocking:
        problems.append(f"brak oczekiwanych reguł blokujących: {', '.join(missing_blocking)}")
    missing_warning = [
        r
        for r in case.expect.warning_rules
        if not any(
            wr == r or wr.startswith(f"{r}:") or wr.startswith(f"{r} ") for wr in warning_rules
        )
    ]
    if missing_warning:
        problems.append(f"brak oczekiwanych ostrzeżeń: {', '.join(missing_warning)}")

    return CaseResult(case=case, passed=not problems, reason="; ".join(problems), run=result)


# --------------------------------------------------------------------------
# budowa repo z pary katalogów base/head
# --------------------------------------------------------------------------


@contextmanager
def _build_case_repo(fixture_dir: Path) -> Iterator[tuple[Path, str, str]]:
    if not fixture_dir.exists():
        raise FileNotFoundError(f"brak fixture'u kalibracyjnego: {fixture_dir}")
    with tempfile.TemporaryDirectory(prefix="gatekeeper-calib-") as tmp:
        repo_path = Path(tmp) / "repo"
        repo_path.mkdir()
        _git(repo_path, "init", "-q", "-b", "main")
        _git(repo_path, "config", "user.email", "calibration@example.com")
        _git(repo_path, "config", "user.name", "Kalibracja")
        _git(repo_path, "config", "commit.gpgsign", "false")
        # Fixture ma trafić do commita bajt w bajt, niezależnie od globalnego configu.
        _git(repo_path, "config", "core.autocrlf", "false")

        _sync_tree(repo_path, fixture_dir / "base")
        base_sha = _commit(repo_path, "kalibracja: stan bazowy")
        _sync_tree(repo_path, fixture_dir / "head")

        commit_msg = "kalibracja: zmiana pod testem"
        msg_file = repo_path / "commit_message.txt"
        if msg_file.exists():
            commit_msg = msg_file.read_text(encoding="utf-8").strip()
            msg_file.unlink()

        head_sha = _commit(repo_path, commit_msg)
        yield repo_path, base_sha, head_sha


def _sync_tree(repo_path: Path, src: Path) -> None:
    """Sprowadza drzewo robocze do dokładnej zawartości `src` (poza `.git`).

    Bez tego usunięcie pliku między `base/` a `head/` nie zostałoby wykryte
    jako usunięcie w diffie — worktree zachowałby stary plik.
    """
    for item in repo_path.iterdir():
        if item.name == ".git":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    if src.exists():
        for item in src.iterdir():
            dest = repo_path / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)


def _commit(repo_path: Path, message: str) -> str:
    _git(repo_path, "add", "-A")
    # `--allow-empty`: `base/` bywa identyczny z poprzednim stanem (np. gdy
    # fixture zaczyna od pustego repo) i to nie jest błąd fixture'u.
    _git(repo_path, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo_path, "rev-parse", "HEAD").strip()


def _git(repo_path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return proc.stdout
