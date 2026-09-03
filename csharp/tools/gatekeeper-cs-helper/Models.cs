using System.Text.Json.Serialization;

namespace GatekeeperCsHelper;

/// <summary>
/// Odpowiednik <c>DiscoveryResult</c> (core/plugins.py) — kształt musi zostać
/// zgodny z tym, co Python czyta w <c>testing/discovery.py</c> (csharp-pack).
/// </summary>
public sealed record TestItem(
    [property: JsonPropertyName("file")] string File,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("class_name")] string? ClassName,
    [property: JsonPropertyName("nodeid")] string NodeId,
    [property: JsonPropertyName("lineno")] int Lineno,
    [property: JsonPropertyName("body_hash")] string BodyHash,
    [property: JsonPropertyName("declared_escape")] string? DeclaredEscape
);

public sealed record DiscoverOutput(
    [property: JsonPropertyName("tests")] IReadOnlyList<TestItem> Tests
);

/// <summary>
/// Odpowiednik <c>QualityIssue</c> (core/plugins.py). <c>Evidence</c> jest
/// <c>object?</c>, nie <c>string?</c> — `g2_test_sanity.py::_to_finding` czyta
/// `evidence.get("line", item.lineno)` i podstawia wprost do `Finding.line`
/// (`int | None`); stringowanie numeru linii złamałoby ten kontrakt.
/// </summary>
public sealed record QualityIssue(
    [property: JsonPropertyName("nodeid")] string NodeId,
    [property: JsonPropertyName("rule_id")] string RuleId,
    [property: JsonPropertyName("severity")] string Severity,
    [property: JsonPropertyName("title")] string Title,
    [property: JsonPropertyName("failure_scenario")] string FailureScenario,
    [property: JsonPropertyName("evidence")] Dictionary<string, object?> Evidence
);

public sealed record LintOutput(
    [property: JsonPropertyName("issues")] IReadOnlyList<QualityIssue> Issues
);

/// <summary>Odpowiednik <c>MethodComplexity</c> (core/plugins.py).</summary>
public sealed record MethodComplexity(
    [property: JsonPropertyName("file")] string File,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("lineno")] int Lineno,
    [property: JsonPropertyName("end_lineno")] int EndLineno,
    [property: JsonPropertyName("complexity")] int ComplexityValue,
    [property: JsonPropertyName("nloc")] int Nloc
);

public sealed record ComplexityOutput(
    [property: JsonPropertyName("methods")] IReadOnlyList<MethodComplexity> Methods
);
