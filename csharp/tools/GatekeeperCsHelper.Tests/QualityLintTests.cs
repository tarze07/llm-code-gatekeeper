namespace GatekeeperCsHelper.Tests;

public class QualityLintTests
{
    private static readonly string CalcTestsPath = Path.Combine(AppContext.BaseDirectory, "Fixtures", "CalcTests.cs");
    private static readonly string MockEchoPath = Path.Combine(AppContext.BaseDirectory, "Fixtures", "MockEchoTests.cs");

    [Fact]
    public void TestZRealnaAsercjaNieDajeZnaleziska()
    {
        var result = QualityLint.Lint(new[] { CalcTestsPath });
        Assert.DoesNotContain(result.Issues, i => i.NodeId.EndsWith("Add_ReturnsSum"));
    }

    [Fact]
    public void BrakAsercjiDajeNoAssertion()
    {
        var result = QualityLint.Lint(new[] { CalcTestsPath });
        var issue = Assert.Single(result.Issues, i => i.NodeId.EndsWith("NoAssertion_DoesNothing"));
        Assert.Equal("test.no_assertion", issue.RuleId);
        Assert.Equal("high", issue.Severity);
    }

    [Fact]
    public void AssertTrueTrueDajeConstantAssertion()
    {
        var result = QualityLint.Lint(new[] { CalcTestsPath });
        var issue = Assert.Single(result.Issues, i => i.NodeId.EndsWith("Trivial_AssertsConstant"));
        Assert.Equal("test.constant_assertion", issue.RuleId);
    }

    [Fact]
    public void WylacznieAssertNotNullDajeOnlySmoke()
    {
        var result = QualityLint.Lint(new[] { CalcTestsPath });
        var issue = Assert.Single(result.Issues, i => i.NodeId.EndsWith("OnlySmoke_ChecksNotNull"));
        Assert.Equal("test.only_smoke", issue.RuleId);
        Assert.Equal("low", issue.Severity);
    }

    [Fact]
    public void PustyCatchDajeExceptionSwallowedIBrakInnejAsercji()
    {
        var result = QualityLint.Lint(new[] { CalcTestsPath });
        var issues = result.Issues.Where(i => i.NodeId.EndsWith("Swallows_Exception")).Select(i => i.RuleId).ToList();
        Assert.Contains("test.exception_swallowed", issues);
        Assert.Contains("test.no_assertion", issues); // pusty catch nie liczy się jako dowód
    }

    [Fact]
    public void PorownanieMockaZWlasnymReturnsDajeMockEcho()
    {
        var result = QualityLint.Lint(new[] { MockEchoPath });
        var issue = Assert.Single(result.Issues);
        Assert.Equal("test.mock_echo", issue.RuleId);
        Assert.Equal("medium", issue.Severity);
    }

    [Fact]
    public void BrakujacyPlikDajePustaListeNieWyjatek()
    {
        var result = QualityLint.Lint(new[] { "/nie/ma/takiego/pliku.cs" });
        Assert.Empty(result.Issues);
    }
}
