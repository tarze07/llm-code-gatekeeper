using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace GatekeeperCsHelper;

/// <summary>
/// Odpowiednik <c>testing/quality.py</c> (python-pack) — testy-atrapy
/// wykrywane przez syntax tree, nie przez uruchomienie. Reguły w tej samej
/// kolejności i o tych samych <c>rule_id</c> co w Pythonie (PLAN-G2.md §4),
/// przełożone z asercji <c>assert</c> na wywołania statycznej klasy
/// <c>Assert</c> (xUnit) — C# nie ma słowa kluczowego odpowiadającego
/// Pythonowemu <c>assert</c> w kontekście testów.
/// </summary>
public static class QualityLint
{
    public static LintOutput Lint(IEnumerable<string> filePaths)
    {
        var issues = new List<QualityIssue>();
        foreach (var path in filePaths)
        {
            string source;
            try { source = File.ReadAllText(path); }
            catch (IOException) { continue; }

            var tree = CSharpSyntaxTree.ParseText(source, path: path);
            var root = tree.GetRoot();

            foreach (var method in root.DescendantNodes().OfType<MethodDeclarationSyntax>())
            {
                if (!method.AttributeLists.SelectMany(l => l.Attributes)
                        .Any(a => a.Name.ToString() is "Fact" or "FactAttribute" or "Theory" or "TheoryAttribute"))
                    continue;

                var className = (method.Parent as ClassDeclarationSyntax)?.Identifier.Text;
                var name = method.Identifier.Text;
                var nodeId = className is null ? $"{path}::{name}" : $"{path}::{className}.{name}";
                var body = (SyntaxNode?)method.Body ?? method.ExpressionBody;
                if (body is null) continue;

                foreach (var rule in Rules)
                {
                    var issue = rule(nodeId, name, body, tree);
                    if (issue is not null) issues.Add(issue);
                }
            }
        }
        return new LintOutput(issues);
    }

    private delegate QualityIssue? Rule(string nodeId, string testName, SyntaxNode body, SyntaxTree tree);

    private static readonly Rule[] Rules =
    {
        NoAssertion,
        ConstantAssertion,
        MockEcho,
        OnlySmoke,
        ExceptionSwallowed,
    };

    // ---------------------------------------------------------- test.no_assertion

    private static bool IsAssertCall(InvocationExpressionSyntax inv) =>
        inv.Expression is MemberAccessExpressionSyntax { Expression: IdentifierNameSyntax { Identifier.Text: "Assert" } };

    private static bool IsMockVerifyCall(InvocationExpressionSyntax inv) =>
        inv.Expression is MemberAccessExpressionSyntax { Name.Identifier.Text: "Verify" };

    private static bool HasEvidence(SyntaxNode body) =>
        body.DescendantNodes().OfType<InvocationExpressionSyntax>()
            .Any(inv => IsAssertCall(inv) || IsMockVerifyCall(inv));

    private static QualityIssue? NoAssertion(string nodeId, string name, SyntaxNode body, SyntaxTree tree)
    {
        if (HasEvidence(body)) return null;
        return new QualityIssue(
            nodeId, "test.no_assertion", "high",
            $"Test `{name}` nie zawiera żadnej asercji",
            $"Test `{name}` przejdzie niezależnie od tego, co zwróci testowany kod — nie ma "
            + "w nim ani wywołania `Assert.*`, ani `mock.Verify(...)`. Zielony wynik niczego nie potwierdza.",
            new Dictionary<string, object?>());
    }

    // ---------------------------------------------------------- test.constant_assertion

    private static bool IsLiteralTrue(ExpressionSyntax e) =>
        e is LiteralExpressionSyntax { RawKind: (int)SyntaxKind.TrueLiteralExpression };

    private static bool IsSelfCompare(ExpressionSyntax a, ExpressionSyntax b) =>
        NormalizedText(a) == NormalizedText(b);

    private static string NormalizedText(SyntaxNode n) =>
        string.Join("", n.DescendantTokens().Select(t => t.Text));

    private static bool IsTrivialAssertEqual(InvocationExpressionSyntax inv)
    {
        var args = inv.ArgumentList.Arguments;
        if (args.Count < 2) return false;
        var expected = args[0].Expression;
        var actual = args[1].Expression;
        // Oba literały (Assert.Equal(1, 1)) albo to samo wyrażenie po obu stronach.
        var bothLiteral = expected is LiteralExpressionSyntax && actual is LiteralExpressionSyntax;
        return (bothLiteral && NormalizedText(expected) == NormalizedText(actual)) || IsSelfCompare(expected, actual);
    }

    private static QualityIssue? ConstantAssertion(string nodeId, string name, SyntaxNode body, SyntaxTree tree)
    {
        foreach (var inv in body.DescendantNodes().OfType<InvocationExpressionSyntax>())
        {
            if (!IsAssertCall(inv)) continue;
            var member = (MemberAccessExpressionSyntax)inv.Expression;
            var method = member.Name.Identifier.Text;
            var args = inv.ArgumentList.Arguments;
            var trivial = method switch
            {
                "True" or "False" when args.Count >= 1 => IsLiteralTrue(args[0].Expression)
                    || (args[0].Expression is LiteralExpressionSyntax { RawKind: (int)SyntaxKind.FalseLiteralExpression }),
                "Equal" => IsTrivialAssertEqual(inv),
                _ => false,
            };
            if (!trivial) continue;
            var line = tree.GetLineSpan(inv.Span).StartLinePosition.Line + 1;
            return new QualityIssue(
                nodeId, "test.constant_assertion", "high",
                $"Test `{name}` asertuje stałą, nie zachowanie",
                $"Linia {line} w `{name}` to `Assert.{method}` na wyrażeniu, które jest zawsze "
                + "prawdziwe niezależnie od testowanego kodu (stała albo `x == x`) — test przejdzie "
                + "nawet po całkowitym usunięciu implementacji.",
                new Dictionary<string, object?> { ["snippet"] = inv.ToString(), ["line"] = line });
        }
        return null;
    }

    // ---------------------------------------------------------- test.mock_echo

    private static QualityIssue? MockEcho(string nodeId, string name, SyntaxNode body, SyntaxTree tree)
    {
        // Zbierz wyrażenia z `.Returns(<expr>)` wołane na zmiennej zainicjowanej
        // przez `new Mock<T>()` — uproszczenie względem Pythona (śledzimy tekst
        // wyrażenia, nie graf przepływu danych), wystarczające dla najczęstszego
        // przypadku: `mock.Setup(...).Returns(X); ...; Assert.Equal(X, mock.Object.Foo());`
        var mockVars = body.DescendantNodes().OfType<LocalDeclarationStatementSyntax>()
            .SelectMany(d => d.Declaration.Variables)
            .Where(v => v.Initializer?.Value is ObjectCreationExpressionSyntax
                { Type: GenericNameSyntax { Identifier.Text: "Mock" } })
            .Select(v => v.Identifier.Text)
            .ToHashSet();
        if (mockVars.Count == 0) return null;

        var returnsValues = body.DescendantNodes().OfType<InvocationExpressionSyntax>()
            .Where(inv => inv.Expression is MemberAccessExpressionSyntax { Name.Identifier.Text: "Returns" })
            .SelectMany(inv => inv.ArgumentList.Arguments)
            .Select(a => NormalizedText(a.Expression))
            .ToHashSet();
        if (returnsValues.Count == 0) return null;

        foreach (var inv in body.DescendantNodes().OfType<InvocationExpressionSyntax>())
        {
            if (!IsAssertCall(inv)) continue;
            var member = (MemberAccessExpressionSyntax)inv.Expression;
            if (member.Name.Identifier.Text != "Equal") continue;
            var args = inv.ArgumentList.Arguments;
            if (args.Count < 2) continue;

            bool actualIsMockObjectCall = args[1].Expression is InvocationExpressionSyntax
            {
                Expression: MemberAccessExpressionSyntax
                {
                    Expression: MemberAccessExpressionSyntax { Name.Identifier.Text: "Object" } objMember,
                },
            } && objMember.Expression is IdentifierNameSyntax id && mockVars.Contains(id.Identifier.Text);

            if (!actualIsMockObjectCall) continue;
            if (!returnsValues.Contains(NormalizedText(args[0].Expression))) continue;

            var line = tree.GetLineSpan(inv.Span).StartLinePosition.Line + 1;
            return new QualityIssue(
                nodeId, "test.mock_echo", "medium",
                $"Test `{name}` porównuje mocka z jego własnym `Returns`",
                $"W `{name}` asercja w linii {line} sprawdza, że wywołanie na mocku zwraca "
                + "dokładnie to, co wpisano jako `.Returns(...)` tego samego mocka — to potwierdza "
                + "konfigurację mocka, nie zachowanie testowanego kodu.",
                new Dictionary<string, object?> { ["snippet"] = inv.ToString(), ["line"] = line });
        }
        return null;
    }

    // ---------------------------------------------------------- test.only_smoke

    private static bool IsNotNullCheck(InvocationExpressionSyntax inv) =>
        inv.Expression is MemberAccessExpressionSyntax { Expression: IdentifierNameSyntax { Identifier.Text: "Assert" }, Name.Identifier.Text: "NotNull" };

    private static QualityIssue? OnlySmoke(string nodeId, string name, SyntaxNode body, SyntaxTree tree)
    {
        var asserts = body.DescendantNodes().OfType<InvocationExpressionSyntax>().Where(IsAssertCall).ToList();
        if (asserts.Count == 0 || !asserts.All(IsNotNullCheck)) return null;
        return new QualityIssue(
            nodeId, "test.only_smoke", "low",
            $"Test `{name}` sprawdza tylko `Assert.NotNull`",
            $"`{name}` wywołuje testowany kod, ale każda asercja ogranicza się do `Assert.NotNull` "
            + "— funkcja zwracająca zupełnie błędną wartość (byle nie `null`) nadal przejdzie ten test.",
            new Dictionary<string, object?>());
    }

    // ---------------------------------------------------------- test.exception_swallowed

    private static bool IsNoopCatch(CatchClauseSyntax clause)
    {
        var statements = clause.Block.Statements;
        return statements.Count == 0
            || (statements.Count == 1 && statements[0] is EmptyStatementSyntax);
    }

    private static QualityIssue? ExceptionSwallowed(string nodeId, string name, SyntaxNode body, SyntaxTree tree)
    {
        foreach (var clause in body.DescendantNodes().OfType<CatchClauseSyntax>())
        {
            if (!IsNoopCatch(clause)) continue;
            var line = tree.GetLineSpan(clause.Span).StartLinePosition.Line + 1;
            return new QualityIssue(
                nodeId, "test.exception_swallowed", "medium",
                $"Test `{name}` połyka wyjątek bez asercji",
                $"W `{name}` blok `catch` w linii {line} jest pusty — jeżeli testowany kod rzuci "
                + "nieoczekiwany wyjątek zamiast tego, którego test się spodziewa, test i tak przejdzie.",
                new Dictionary<string, object?> { ["line"] = line });
        }
        return null;
    }
}
