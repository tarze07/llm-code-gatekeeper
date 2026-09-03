using Xunit;

namespace Demo.Tests;

public class CalcTests
{
    [Fact]
    public void Add_ReturnsSum()
    {
        var result = Calc.Add(2, 3);
        Assert.Equal(5, result);
    }

    [Fact]
    public void NoAssertion_DoesNothing()
    {
        Calc.Add(2, 3);
    }

    [Fact]
    public void Trivial_AssertsConstant()
    {
        Assert.True(true);
    }

    [Fact]
    public void OnlySmoke_ChecksNotNull()
    {
        var result = Calc.Add(2, 3);
        Assert.NotNull(result.ToString());
    }

    [Fact]
    public void Swallows_Exception()
    {
        try
        {
            Calc.Add(2, 3);
        }
        catch
        {
        }
    }

    // gatekeeper: characterization
    [Fact]
    public void Characterization_Marked()
    {
        Assert.Equal(5, Calc.Add(2, 3));
    }
}
