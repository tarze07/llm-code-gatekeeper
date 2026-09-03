using Xunit;
using Demo;

namespace Demo.Tests;

public class CalcTests
{
    [Fact]
    public void Placeholder() { Assert.True(true); }

    [Fact]
    public void Cena_BezRabatu_NieDowodziNiczego()
    {
        var calc = new Calc();
        Assert.Equal(100, calc.Cena(100));
    }
}
