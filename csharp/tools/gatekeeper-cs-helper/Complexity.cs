using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace GatekeeperCsHelper;

/// <summary>
/// Złożoność cyklomatyczna (McCabe) dla C# — trzecia komenda tego samego
/// helpera Roslyn co discover/lint (PLAN-G1-complexity.md §8 w core-repo:
/// "ten sam helper Roslyn co PLAN-G2.md — nie budować drugiego parsera C#").
/// Semantyka lustrzana do `gatekeeper_python/adapters/complexity.py`
/// (python-pack), przełożona z węzłów `ast` na Roslyn:
///
/// | ast (Python)              | Roslyn (C#)                               |
/// |----------------------------|-------------------------------------------|
/// | If/IfExp                   | IfStatement, ConditionalExpression (?:)   |
/// | For/AsyncFor/While         | For/ForEach/While/DoStatement             |
/// | ExceptHandler               | CatchClause                               |
/// | BoolOp (+n-1)               | BinaryExpression &&/\|\| (węzeł per operator, |
/// |                             | C# zagnieżdża łańcuch jako osobne węzły — |
/// |                             | +1 na węzeł = to samo co +(n-1) w Pythonie) |
/// | match/case                  | SwitchStatement/SwitchExpression + case   |
/// | With                        | using/lock: 0                             |
/// </summary>
public static class Complexity
{
    public static ComplexityOutput Measure(IEnumerable<string> filePaths)
    {
        var methods = new List<MethodComplexity>();
        foreach (var path in filePaths)
        {
            string source;
            try { source = File.ReadAllText(path); }
            catch (IOException) { continue; }

            var tree = CSharpSyntaxTree.ParseText(source, path: path);
            var root = tree.GetRoot();
            if (tree.GetDiagnostics().Any(d => d.Severity == DiagnosticSeverity.Error))
                continue;

            var visitor = new ComplexityVisitor(path, tree);
            visitor.Visit(root);
            methods.AddRange(visitor.Results);
        }
        return new ComplexityOutput(methods);
    }

    private sealed class Frame
    {
        public int Complexity = 1;
    }

    private sealed class ComplexityVisitor : CSharpSyntaxWalker
    {
        private readonly string _path;
        private readonly SyntaxTree _tree;
        private readonly Stack<string> _classStack = new();
        private readonly Stack<Frame> _frames = new();
        public readonly List<MethodComplexity> Results = new();

        public ComplexityVisitor(string path, SyntaxTree tree)
        {
            _path = path;
            _tree = tree;
        }

        private void Bump(int delta = 1)
        {
            if (_frames.Count > 0) _frames.Peek().Complexity += delta;
        }

        private string QualifiedName(string name) =>
            _classStack.Count > 0 ? $"{_classStack.Peek()}.{name}" : name;

        public override void VisitClassDeclaration(ClassDeclarationSyntax node)
        {
            _classStack.Push(node.Identifier.Text);
            base.VisitClassDeclaration(node);
            _classStack.Pop();
        }

        public override void VisitStructDeclaration(StructDeclarationSyntax node)
        {
            _classStack.Push(node.Identifier.Text);
            base.VisitStructDeclaration(node);
            _classStack.Pop();
        }

        public override void VisitMethodDeclaration(MethodDeclarationSyntax node) =>
            VisitFunctionLike(node, node.Identifier.Text, (SyntaxNode?)node.Body ?? node.ExpressionBody);

        public override void VisitLocalFunctionStatement(LocalFunctionStatementSyntax node) =>
            VisitFunctionLike(node, node.Identifier.Text, (SyntaxNode?)node.Body ?? node.ExpressionBody);

        public override void VisitConstructorDeclaration(ConstructorDeclarationSyntax node) =>
            VisitFunctionLike(node, node.Identifier.Text, (SyntaxNode?)node.Body ?? node.ExpressionBody);

        // Lambda: NIE osobna ramka — złożoność dolicza się do otaczającej
        // metody (PLAN-G1-complexity.md §3: "Lambda dodaje swoją M do
        // otaczającej funkcji, nie jako osobna metoda") — brak override'u
        // Visit*LambdaExpression celowo: domyślny spacer po prostu wchodzi
        // w jej ciało bez pushowania nowej ramki.

        private void VisitFunctionLike(SyntaxNode node, string name, SyntaxNode? body)
        {
            _frames.Push(new Frame());
            if (body is not null) Visit(body);
            var frame = _frames.Pop();

            var span = node.GetLocation().GetLineSpan();
            var lineno = span.StartLinePosition.Line + 1;
            var endLineno = span.EndLinePosition.Line + 1;
            Results.Add(new MethodComplexity(
                _path, QualifiedName(name), lineno, endLineno, frame.Complexity, Nloc(node)));
        }

        private int Nloc(SyntaxNode node)
        {
            var text = node.ToString();
            return text.Split('\n').Count(l => l.Trim().Length > 0);
        }

        public override void VisitIfStatement(IfStatementSyntax node)
        {
            Bump();
            base.VisitIfStatement(node);
        }

        public override void VisitConditionalExpression(ConditionalExpressionSyntax node)
        {
            Bump();
            base.VisitConditionalExpression(node);
        }

        public override void VisitForStatement(ForStatementSyntax node)
        {
            Bump();
            base.VisitForStatement(node);
        }

        public override void VisitForEachStatement(ForEachStatementSyntax node)
        {
            Bump();
            base.VisitForEachStatement(node);
        }

        public override void VisitWhileStatement(WhileStatementSyntax node)
        {
            Bump();
            base.VisitWhileStatement(node);
        }

        public override void VisitDoStatement(DoStatementSyntax node)
        {
            Bump();
            base.VisitDoStatement(node);
        }

        public override void VisitCatchClause(CatchClauseSyntax node)
        {
            Bump();
            base.VisitCatchClause(node);
        }

        public override void VisitBinaryExpression(BinaryExpressionSyntax node)
        {
            if (node.Kind() is SyntaxKind.LogicalAndExpression or SyntaxKind.LogicalOrExpression)
                Bump();
            base.VisitBinaryExpression(node);
        }

        public override void VisitSwitchStatement(SwitchStatementSyntax node)
        {
            Bump(); // +1 za sam switch (jak `Match` w Pythonie)
            foreach (var section in node.Sections)
            {
                if (!IsDefaultSection(section)) Bump();
            }
            base.VisitSwitchStatement(node);
        }

        private static bool IsDefaultSection(SwitchSectionSyntax section) =>
            section.Labels.Any(l => l is DefaultSwitchLabelSyntax);

        public override void VisitSwitchExpression(SwitchExpressionSyntax node)
        {
            Bump(); // +1 za samo wyrażenie switch
            foreach (var arm in node.Arms)
            {
                if (!IsDiscardArm(arm)) Bump();
            }
            base.VisitSwitchExpression(node);
        }

        private static bool IsDiscardArm(SwitchExpressionArmSyntax arm) =>
            arm.Pattern is DiscardPatternSyntax;

        // `using`/`lock`: bez override'u => 0, jak `With`/`AsyncWith` w Pythonie.
    }
}
