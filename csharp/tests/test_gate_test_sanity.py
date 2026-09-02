"""Testy G2.test_sanity (dispatcher core-owy) — integracja przez `gatekeeper-cs-helper
lint`, nie golden file. Odpowiednik `test_gate_test_sanity.py` w python-packu.
Testy pomijane bez `dotnet`/`gatekeeper-cs-helper`.
"""

from __future__ import annotations

import shutil

import pytest
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g2_test_sanity import TestSanity

requires_dotnet = pytest.mark.skipif(
    shutil.which("dotnet") is None or shutil.which("gatekeeper-cs-helper") is None,
    reason="dotnet/gatekeeper-cs-helper niedostępne",
)


@requires_dotnet
def test_brak_asercji_blokuje(dotnet_repo):
    dotnet_repo.checkout("feature", create=True)
    dotnet_repo.write(
        "tests/Demo.Tests/CalcTests.cs",
        "using Xunit;\nusing Demo;\n\nnamespace Demo.Tests;\n\n"
        "public class CalcTests\n{\n"
        "    [Fact]\n    public void Placeholder() { Assert.True(true); }\n\n"
        "    [Fact]\n    public void ZlyTest_BezAsercji()\n    {\n"
        "        var calc = new Calc();\n        calc.Cena(100, 0.2);\n    }\n}\n",
    )
    dotnet_repo.commit("feat: zly test")
    change = ChangeContext.from_git(dotnet_repo.path, "main", "HEAD")

    result = TestSanity({}).run(change)

    assert result.status == "fail"
    assert result.facts["sanity.blocking_count"] == 1
    assert any(f.rule_id == "test.no_assertion" for f in result.findings)


@requires_dotnet
def test_dobry_test_przechodzi_bez_znalezisk(dotnet_repo):
    dotnet_repo.checkout("feature", create=True)
    dotnet_repo.write(
        "tests/Demo.Tests/CalcTests.cs",
        "using Xunit;\nusing Demo;\n\nnamespace Demo.Tests;\n\n"
        "public class CalcTests\n{\n"
        "    [Fact]\n    public void Placeholder() { Assert.True(true); }\n\n"
        "    [Fact]\n    public void DobryTest()\n    {\n"
        "        var calc = new Calc();\n"
        "        Assert.Equal(100, calc.Cena(100));\n    }\n}\n",
    )
    dotnet_repo.commit("feat: dobry test")
    change = ChangeContext.from_git(dotnet_repo.path, "main", "HEAD")

    result = TestSanity({}).run(change)

    assert result.status == "pass"
    assert result.facts["sanity.finding_count"] == 0
