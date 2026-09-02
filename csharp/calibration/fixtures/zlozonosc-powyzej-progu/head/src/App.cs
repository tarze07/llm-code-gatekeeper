namespace App;

public class Program
{
    public int Dodaj(int a, int b) => a + b;

    public string Przetworz(bool a, bool b, bool c, bool d, bool e,
        bool f, bool g, bool h, bool i, bool j)
    {
        if (a)
        {
            if (b)
            {
                if (c)
                {
                    if (d)
                    {
                        if (e)
                        {
                            if (f)
                            {
                                if (g)
                                {
                                    if (h)
                                    {
                                        if (i)
                                        {
                                            if (j)
                                            {
                                                return "ok";
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        return "brak";
    }
}
