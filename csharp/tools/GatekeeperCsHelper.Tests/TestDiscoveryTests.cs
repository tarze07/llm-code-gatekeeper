namespace GatekeeperCsHelper.Tests;

public class TestDiscoveryTests
{
    private static readonly string CalcTestsPath = Path.Combine(AppContext.BaseDirectory, "Fixtures", "CalcTests.cs");

    [Fact]
    public void WykrywaWszystkieMetodyZAtrybutemFact()
    {
        var result = TestDiscovery.Discover(new[] { CalcTestsPath });
        Assert.Equal(6, result.Tests.Count);
        Assert.Contains(result.Tests, t => t.Name == "Add_ReturnsSum" && t.ClassName == "CalcTests");
    }

    [Fact]
    public void NodeIdZawieraPlikIKlaseIMetode()
    {
        var result = TestDiscovery.Discover(new[] { CalcTestsPath });
        var item = result.Tests.Single(t => t.Name == "Add_ReturnsSum");
        Assert.Equal($"{CalcTestsPath}::CalcTests.Add_ReturnsSum", item.NodeId);
    }

    [Fact]
    public void KomentarzGatekeeperCharacterizationUstawiaDeclaredEscape()
    {
        var result = TestDiscovery.Discover(new[] { CalcTestsPath });
        var marked = result.Tests.Single(t => t.Name == "Characterization_Marked");
        Assert.Equal("characterization", marked.DeclaredEscape);

        var unmarked = result.Tests.Single(t => t.Name == "Add_ReturnsSum");
        Assert.Null(unmarked.DeclaredEscape);
    }

    [Fact]
    public void BodyHashJestStabilnyMiedzyWywolaniami()
    {
        var first = TestDiscovery.Discover(new[] { CalcTestsPath });
        var second = TestDiscovery.Discover(new[] { CalcTestsPath });
        Assert.Equal(
            first.Tests.Single(t => t.Name == "Add_ReturnsSum").BodyHash,
            second.Tests.Single(t => t.Name == "Add_ReturnsSum").BodyHash);
    }

    [Fact]
    public void BodyHashRozniSieDlaRoznychTresci()
    {
        var result = TestDiscovery.Discover(new[] { CalcTestsPath });
        var hashes = result.Tests.Select(t => t.BodyHash).ToHashSet();
        Assert.Equal(result.Tests.Count, hashes.Count);
    }

    [Fact]
    public void BrakujacyPlikDajePustyWynikNieWyjatek()
    {
        var result = TestDiscovery.Discover(new[] { "/nie/ma/takiego/pliku.cs" });
        Assert.Empty(result.Tests);
    }
}
