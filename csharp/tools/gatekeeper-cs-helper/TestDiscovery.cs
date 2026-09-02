using System.Security.Cryptography;
using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace GatekeeperCsHelper;

/// <summary>
/// Odpowiednik <c>testing/discovery.py</c> (python-pack) — wykrywanie testów
/// przez syntax tree Roslyna, nie przez uruchamianie <c>dotnet test</c>.
/// Tylko syntax, bez kompilacji: szybkie, nie wymaga, żeby oceniany projekt
/// w ogóle dał się zbudować (PLAN-G2.md §2).
///
/// Zakres pierwszej wersji: wyłącznie xUnit (<c>[Fact]</c>/<c>[Theory]</c>) —
/// PLAN-G2.md §2 uzasadnia wybór; NUnit/MSTest to rozszerzenie listy
/// rozpoznawanych atrybutów, nie zmiana architektury.
/// </summary>
public static class TestDiscovery
{
    private static readonly string[] TestAttributeNames = { "Fact", "Theory" };

    //: Markery zwalniające z dowodu G2.cross_verify — te same nazwy co
    //: `testing.discovery.ESCAPE_MARKERS` w python-packu, żeby komunikaty
    //: bramki (core-owe, `gates/g2_crossverify.py`) miały spójne słownictwo
    //: niezależnie od języka. Deklarowane komentarzem tuż nad metodą testową
    //: (`// gatekeeper: characterization`), nie atrybutem — C# nie ma
    //: odpowiednika `@pytest.mark.*` używanego jako czysta adnotacja bez
    //: efektu w runtime, a wymyślanie nowego atrybutu tylko na ten cel
    //: dokładałoby zależność do projektu ocenianego.
    private static readonly string[] EscapeMarkers = { "characterization", "test_backfill", "refactor_only" };

    public static DiscoverOutput Discover(IEnumerable<string> filePaths)
    {
        var items = new List<TestItem>();
        foreach (var path in filePaths)
        {
            string source;
            try
            {
                source = File.ReadAllText(path);
            }
            catch (IOException)
            {
                continue; // plik usunięty w diffie — nic do wykrycia
            }

            var tree = CSharpSyntaxTree.ParseText(source, path: path);
            var root = tree.GetRoot();
            if (root.ContainsDiagnostics && tree.GetDiagnostics().Any(d => d.Severity == DiagnosticSeverity.Error))
            {
                // Niepełny/uszkodzony plik (np. PR w trakcie edycji) — pusty
                // wynik dla tego pliku, tak jak Python `except SyntaxError`.
                continue;
            }

            foreach (var method in root.DescendantNodes().OfType<MethodDeclarationSyntax>())
            {
                if (!HasTestAttribute(method)) continue;

                var className = (method.Parent as ClassDeclarationSyntax)?.Identifier.Text;
                var name = method.Identifier.Text;
                var nodeId = className is null ? $"{path}::{name}" : $"{path}::{className}.{name}";

                items.Add(new TestItem(
                    File: path,
                    Name: name,
                    ClassName: className,
                    NodeId: nodeId,
                    Lineno: tree.GetLineSpan(method.Span).StartLinePosition.Line + 1,
                    BodyHash: BodyHash(method),
                    DeclaredEscape: DeclaredEscape(method)
                ));
            }
        }
        return new DiscoverOutput(items);
    }

    private static bool HasTestAttribute(MethodDeclarationSyntax method) =>
        method.AttributeLists
            .SelectMany(list => list.Attributes)
            .Any(attr => TestAttributeNames.Contains(AttributeShortName(attr)));

    private static string AttributeShortName(AttributeSyntax attr)
    {
        // `Fact` i `FactAttribute` obie ważne — Roslyn na poziomie syntax nie
        // wie, czy dopisek "Attribute" jest częścią nazwy czy konwencją C#.
        var name = attr.Name.ToString();
        var lastSegment = name.Split('.').Last();
        return lastSegment.EndsWith("Attribute") ? lastSegment[..^"Attribute".Length] : lastSegment;
    }

    /// <summary>
    /// Hash treści testu niezależny od formatowania i numerów linii —
    /// odpowiednik `ast.dump(..., include_attributes=False)` w Pythonie.
    /// Token stream (bez trivia: komentarzy, białych znaków) parametrów +
    /// ciała metody + posortowanych markerów eskapowych.
    /// </summary>
    private static string BodyHash(MethodDeclarationSyntax method)
    {
        var tokens = string.Join(
            "",
            method.ParameterList.DescendantTokens().Concat(
                method.Body?.DescendantTokens() ?? method.ExpressionBody?.DescendantTokens()
                    ?? Enumerable.Empty<SyntaxToken>()
            ).Select(t => t.Text)
        );
        var markers = string.Join(",", DeclaredMarkers(method).OrderBy(m => m));
        var bytes = Encoding.UTF8.GetBytes(tokens + "" + markers);
        return Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant()[..16];
    }

    private static IEnumerable<string> DeclaredMarkers(MethodDeclarationSyntax method)
    {
        var leading = method.AttributeLists.Count > 0
            ? method.AttributeLists[0].GetLeadingTrivia()
            : method.GetLeadingTrivia();
        foreach (var trivia in leading)
        {
            if (trivia.Kind() is not (SyntaxKind.SingleLineCommentTrivia or SyntaxKind.MultiLineCommentTrivia))
                continue;
            var text = trivia.ToString();
            var marker = ParseMarkerComment(text);
            if (marker is not null) yield return marker;
        }
    }

    private static string? ParseMarkerComment(string commentText)
    {
        const string prefix = "gatekeeper:";
        var idx = commentText.IndexOf(prefix, StringComparison.Ordinal);
        if (idx < 0) return null;
        var rest = commentText[(idx + prefix.Length)..].Trim().TrimEnd('*', '/', ' ');
        return EscapeMarkers.Contains(rest) ? rest : null;
    }

    private static string? DeclaredEscape(MethodDeclarationSyntax method)
    {
        var hit = DeclaredMarkers(method).Where(m => EscapeMarkers.Contains(m)).OrderBy(m => m).ToList();
        return hit.Count > 0 ? hit[0] : null;
    }
}
