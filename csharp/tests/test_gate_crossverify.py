"""Testy G2.cross_verify (dispatcher core-owy) na prawdziwym `dotnet test` —
integracja, nie golden file. Odpowiednik `test_gate_crossverify.py` w
python-packu. Testy pomijane bez `dotnet`/`gatekeeper-cs-helper`.
"""

from __future__ import annotations

import shutil

import pytest
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g2_crossverify import CrossVerify

requires_dotnet = pytest.mark.skipif(
    shutil.which("dotnet") is None or shutil.which("gatekeeper-cs-helper") is None,
    reason="dotnet/gatekeeper-cs-helper niedostępne",
)


@requires_dotnet
def test_dobry_test_pada_na_starym_kodzie_i_bramka_przechodzi(dotnet_repo):
    """Test nowej funkcjonalności *musi* polec na kodzie sprzed zmiany."""
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
    dotnet_repo.commit("feat: rabat")
    change = ChangeContext.from_git(dotnet_repo.path, "main", "HEAD")

    result = CrossVerify({}).run(change)

    assert result.status == "pass"
    assert result.facts["tests.pass_on_pre_change_code"] is False
    assert result.facts["tests.proved"] == 1
    assert result.findings == []


@requires_dotnet
def test_bezwartosciowy_test_jest_wykrywany(dotnet_repo):
    """Test, który przechodzi identycznie na starym i nowym kodzie, nie
    dowodzi niczego o zmianie — bramka musi to zablokować."""
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
        "    [Fact]\n    public void Cena_BezRabatu_NieDowodziNiczego()\n    {\n"
        "        var calc = new Calc();\n"
        "        Assert.Equal(100, calc.Cena(100));\n    }\n}\n",
    )
    dotnet_repo.commit("feat: dokladamy bezwartosciowy test")
    change = ChangeContext.from_git(dotnet_repo.path, "main", "HEAD")

    result = CrossVerify({}).run(change)

    assert result.status == "fail"
    assert result.facts["tests.pass_on_pre_change_code"] is True
    assert any(f.rule_id == "tests.pass_on_pre_change_code" for f in result.findings)
