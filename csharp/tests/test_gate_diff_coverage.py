"""Testy G2.diff_coverage (dispatcher core-owy) na prawdziwym `dotnet test
--collect:"XPlat Code Coverage"` + `diff-cover` — integracja, nie golden file.
Odpowiednik `test_gate_diff_coverage.py` w python-packu. Testy pomijane bez
`dotnet`/`diff-cover`.
"""

from __future__ import annotations

import shutil

import pytest
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g2_diff_coverage import DiffCoverage

requires_tools = pytest.mark.skipif(
    shutil.which("dotnet") is None or shutil.which("diff-cover") is None,
    reason="dotnet/diff-cover niedostępne",
)


@requires_tools
def test_nowa_linia_pokryta_testem_daje_ratio_jeden(dotnet_repo):
    dotnet_repo.checkout("feature", create=True)
    dotnet_repo.write(
        "src/Demo/Calc.cs",
        "namespace Demo;\n\npublic class Calc\n{\n"
        "    public int Cena(int x, double rabat = 0.0) => (int)(x * (1 - rabat));\n}\n",
    )
    dotnet_repo.write(
        "tests/Demo.Tests/CalcTests.cs",
        "using Xunit;\nusing Demo;\n\nnamespace Demo.Tests;\n\n"
        "public class CalcTests\n{\n"
        "    [Fact]\n    public void Placeholder() { Assert.True(true); }\n\n"
        "    [Fact]\n    public void Cena_ZRabatem()\n    {\n"
        "        var calc = new Calc();\n"
        "        Assert.Equal(80, calc.Cena(100, 0.2));\n    }\n}\n",
    )
    dotnet_repo.commit("feat: rabat, pokryty testem")
    change = ChangeContext.from_git(dotnet_repo.path, "main", "HEAD")

    result = DiffCoverage({}).run(change)

    assert result.status == "pass"
    assert result.facts["coverage.diff_ratio"] == 1.0
    assert result.facts["coverage.tool_available"] is True
