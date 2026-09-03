namespace Demo;

public class Calc
{
    public string Raty(bool kwota, bool lata, bool dochod)
    {
        if (kwota)
        {
            if (lata && dochod)
            {
                return "ok";
            }
        }
        return "brak";
    }

    public string Prosta(int x) => x > 0 ? "dodatnie" : "ujemne";

    public string Wybierz(int x)
    {
        switch (x)
        {
            case 1:
                return "a";
            case 2:
                return "b";
            default:
                return "c";
        }
    }

    public string ZLambda(List<int> xs)
    {
        return xs.Select(x => x > 0 ? "+" : "-").First();
    }

    public string ZLokalnaFunkcja()
    {
        string Pomocnicza(int x)
        {
            if (x > 0) return "+";
            return "-";
        }
        return Pomocnicza(1);
    }

    public string ZPetlaForeach(List<int> xs)
    {
        foreach (var x in xs)
        {
            if (x > 0)
            {
                return "found";
            }
        }
        return "none";
    }
}
