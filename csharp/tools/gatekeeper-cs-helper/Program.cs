using System.Text.Json;
using GatekeeperCsHelper;

// Kontrakt: JSON na stdout, kod wyjścia 0 niezależnie od znalezisk (błąd
// parsera/argumentów to != 0) — ta sama konwencja co `dotnet build`/`tsc`
// w tym projekcie (PLAN-G2.md §2).

var jsonOptions = new JsonSerializerOptions { WriteIndented = false };

if (args.Length < 2 || args[1] != "--files")
{
    Console.Error.WriteLine(
        "użycie: gatekeeper-cs-helper <discover|lint|complexity> --files <plik1.cs> [plik2.cs ...]");
    return 2;
}

var command = args[0];
var files = args[2..];

switch (command)
{
    case "discover":
        Console.WriteLine(JsonSerializer.Serialize(TestDiscovery.Discover(files), jsonOptions));
        return 0;
    case "lint":
        Console.WriteLine(JsonSerializer.Serialize(QualityLint.Lint(files), jsonOptions));
        return 0;
    case "complexity":
        Console.WriteLine(JsonSerializer.Serialize(Complexity.Measure(files), jsonOptions));
        return 0;
    default:
        Console.Error.WriteLine($"nieznana komenda: {command} (oczekiwano discover|lint|complexity)");
        return 2;
}
