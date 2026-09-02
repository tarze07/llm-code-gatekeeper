namespace GatekeeperCsHelper.Tests;

public class ComplexityTests
{
    private static readonly string CalcPath =
        Path.Combine(AppContext.BaseDirectory, "Fixtures", "ComplexityCalc.cs");

    private static MethodComplexity Get(string name) =>
        Complexity.Measure(new[] { CalcPath }).Methods.Single(m => m.Name == name);

    [Fact]
    public void ZagniezdzoneIfIAndDajaOczekiwanaZlozonosc()
    {
        // 1 (start) + 1 (if kwota) + 1 (if lata && dochod) + 1 (&&) = 4
        Assert.Equal(4, Get("Calc.Raty").ComplexityValue);
    }

    [Fact]
    public void WyrazenieWarunkoweDajeM2()
    {
        // 1 (start) + 1 (?:) = 2
        Assert.Equal(2, Get("Calc.Prosta").ComplexityValue);
    }

    [Fact]
    public void SwitchZDwomaCaseIDefaultDajeM4()
    {
        // 1 (start) + 1 (switch) + 2 (dwa nie-default case, default nie liczy się)
        Assert.Equal(4, Get("Calc.Wybierz").ComplexityValue);
    }

    [Fact]
    public void LambdaDodajeZlozonoscDoOtaczajacejMetodyNieJestOsobnaMetoda()
    {
        var methods = Complexity.Measure(new[] { CalcPath }).Methods;
        // 7 metod zdefiniowanych w fixture (Raty, Prosta, Wybierz, ZLambda,
        // ZLokalnaFunkcja, Pomocnicza, ZPetlaForeach) — lambda w ZLambda
        // NIE dokłada 8. wpisu.
        Assert.Equal(7, methods.Count);
        // 1 (start) + 1 (?: wewnątrz lambdy)
        Assert.Equal(2, Get("Calc.ZLambda").ComplexityValue);
    }

    [Fact]
    public void LokalnaFunkcjaLiczySieOsobno()
    {
        var methods = Complexity.Measure(new[] { CalcPath }).Methods;
        var outer = methods.Single(m => m.Name == "Calc.ZLokalnaFunkcja");
        var inner = methods.Single(m => m.Name == "Calc.Pomocnicza");

        Assert.Equal(1, outer.ComplexityValue); // sam wywołuje Pomocnicza, brak własnych decyzji
        Assert.Equal(2, inner.ComplexityValue); // 1 (start) + 1 (if)
    }

    [Fact]
    public void ForeachZIfemDajeM3()
    {
        // 1 (start) + 1 (foreach) + 1 (if)
        Assert.Equal(3, Get("Calc.ZPetlaForeach").ComplexityValue);
    }

    [Fact]
    public void LineNoIEndLineNoOdpowiadajaZasiegowiMetody()
    {
        var raty = Get("Calc.Raty");
        Assert.Equal(5, raty.Lineno);
        Assert.Equal(15, raty.EndLineno);
    }

    [Fact]
    public void BrakujacyPlikDajePustaListeNieWyjatek()
    {
        Assert.Empty(Complexity.Measure(new[] { "/nie/ma/takiego/pliku.cs" }).Methods);
    }
}
