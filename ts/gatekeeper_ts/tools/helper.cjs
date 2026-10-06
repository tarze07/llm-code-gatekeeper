/*
 * gatekeeper-ts-helper — odpowiednik `gatekeeper-cs-helper` (csharp-pack) dla
 * TS/JS: wykrywanie testów i linter jakości testów na drzewie składniowym,
 * bez uruchamiania czegokolwiek z ocenianego repo.
 *
 * Kontrakt (ten sam co helper C#, PLAN-G2.md §2 w csharp-repo):
 *   node helper.cjs <discover|lint> --files <plik1> [plik2 ...]
 *   cwd = korzeń repo, ścieżki **względne**, JSON na stdout,
 *   kod wyjścia 0 niezależnie od znalezisk (błąd argumentów/parsera to != 0).
 *
 * Dlaczego CommonJS, nie ESM: `require()` honoruje `NODE_PATH`, `import` nie.
 * Strona Pythona (`testing/discovery.py`) dokłada do `NODE_PATH` wynik
 * `npm root -g`, żeby parser dało się znaleźć także wtedy, gdy oceniane repo
 * nie ma go u siebie — dokładnie ten sam mechanizm, którego używa już
 * `adapters/complexity.py` dla configu eslinta. Rozszerzenie `.cjs` (nie `.js`)
 * wymusza CJS niezależnie od `"type": "module"` w package.json repo, w którego
 * katalogu helper bywa uruchamiany.
 *
 * Dlaczego `@typescript-eslint/parser`, a nie TypeScript Compiler API: od
 * TypeScript 7 (port natywny) pakiet `typescript` nie eksponuje już
 * `createSourceFile`/`SyntaxKind`/`forEachChild` w JS — sprawdzone na
 * `typescript@7.0.2`, wszystkie trzy to `undefined`. Plan z `PLAN-G2.md`
 * (csharp) zakładał Compiler API; ESTree z `@typescript-eslint/parser` jest
 * stabilnym zamiennikiem, a ten pack i tak już go wymaga dla `G1.complexity`.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { createRequire } = require("module");

// --------------------------------------------------------------- parser

/**
 * Parser rozwiązywany najpierw z ocenianego repo (żeby użyć tej wersji, którą
 * repo samo trzyma), potem z NODE_PATH/globalnych modułów. Bez tego helper
 * działałby wyłącznie w repo, które ma `@typescript-eslint/parser` lokalnie.
 */
function loadParser(root) {
  const candidates = [];
  try {
    candidates.push(createRequire(path.join(root, "package.json")));
  } catch {
    /* repo bez package.json — zostaje rozwiązanie globalne */
  }
  candidates.push(require);
  let lastError = null;
  for (const req of candidates) {
    try {
      return req("@typescript-eslint/parser");
    } catch (err) {
      lastError = err;
    }
  }
  const detail = lastError ? `: ${lastError.message}` : "";
  throw new Error(
    "nie znaleziono `@typescript-eslint/parser` — zainstaluj go w ocenianym repo " +
      `albo globalnie (npm install -g @typescript-eslint/parser)${detail}`
  );
}

const JSX_EXTENSIONS = new Set([".tsx", ".jsx"]);

function parseFile(parser, filePath, source) {
  // `jsx` musi być wyłączone dla `.ts` — inaczej `<T>expr` (rzutowanie) jest
  // czytane jako element JSX i plik nie parsuje się wcale.
  return parser.parse(source, {
    loc: true,
    range: true,
    tokens: true,
    comment: true,
    errorOnUnknownASTType: false,
    jsx: JSX_EXTENSIONS.has(path.extname(filePath)),
  });
}

// ------------------------------------------------------------- wspólne AST

function walk(node, visit, parent = null) {
  if (node === null || typeof node !== "object") return;
  if (Array.isArray(node)) {
    for (const child of node) walk(child, visit, parent);
    return;
  }
  if (typeof node.type !== "string") return;
  visit(node, parent);
  for (const key of Object.keys(node)) {
    if (key === "parent" || key === "loc" || key === "range") continue;
    walk(node[key], visit, node);
  }
}

/** Tekst wyrażenia bez białych znaków — do porównań „to samo po obu stronach". */
function normalizedText(source, node) {
  if (!node || !node.range) return "";
  return source.slice(node.range[0], node.range[1]).replace(/\s+/g, "");
}

function staticName(node) {
  if (!node) return null;
  if (node.type === "Literal" && typeof node.value === "string") return node.value;
  if (node.type === "TemplateLiteral" && node.expressions.length === 0) {
    return node.quasis.map((q) => q.value.cooked).join("");
  }
  return null;
}

/**
 * Nazwa wywoływanej funkcji sprowadzona do korzenia łańcucha:
 * `it` → `it`, `test.skip` → `test`, `it.each([...])` → `it`,
 * `describe.only` → `describe`. Modyfikator zwracany osobno.
 */
function calleeRoot(callee) {
  let node = callee;
  const chain = [];
  for (;;) {
    if (node.type === "MemberExpression" && !node.computed) {
      chain.unshift(node.property.name ?? "");
      node = node.object;
      continue;
    }
    if (node.type === "CallExpression") {
      // `it.each(table)(name, fn)` — wołane jest to, co zwróciło `.each(...)`.
      node = node.callee;
      continue;
    }
    if (node.type === "TaggedTemplateExpression") {
      node = node.tag;
      continue;
    }
    break;
  }
  if (node.type !== "Identifier") return { root: null, chain };
  return { root: node.name, chain };
}

const TEST_CALLEES = new Set(["test", "it"]);
const SUITE_CALLEES = new Set(["describe", "suite"]);
//: Te same nazwy co `testing.discovery.ESCAPE_MARKERS` (python-pack) i
//: `EscapeMarkers` (helper C#) — komunikaty bramki core'owej mają być spójne
//: słownictwem niezależnie od języka.
const ESCAPE_MARKERS = ["characterization", "test_backfill", "refactor_only"];

function callbackOf(callNode) {
  for (const arg of callNode.arguments) {
    if (arg.type === "ArrowFunctionExpression" || arg.type === "FunctionExpression") return arg;
  }
  return null;
}

/**
 * Testy i suity w kolejności wystąpienia, z pełną ścieżką `describe`.
 * Zwraca `{ names: [...describe, test], node, call }`.
 */
function collectTests(ast) {
  const found = [];

  function visit(node, ancestors) {
    if (node.type === "CallExpression") {
      const { root, chain } = calleeRoot(node.callee);
      const name = staticName(node.arguments[0]);
      const body = callbackOf(node);
      if (root && name !== null) {
        if (SUITE_CALLEES.has(root) && body) {
          walkBody(body, ancestors.concat(name));
          return true;
        }
        if (TEST_CALLEES.has(root)) {
          // `it.todo("...")` nie ma ciała — nie jest testem do sprawdzenia.
          if (body) found.push({ names: ancestors.concat(name), node: body, call: node, chain });
          return true;
        }
      }
    }
    return false;
  }

  function walkBody(node, ancestors) {
    if (node === null || typeof node !== "object") return;
    if (Array.isArray(node)) {
      for (const child of node) walkBody(child, ancestors);
      return;
    }
    if (typeof node.type !== "string") return;
    if (visit(node, ancestors)) return; // suita/test obsłużone rekurencyjnie
    for (const key of Object.keys(node)) {
      if (key === "parent" || key === "loc" || key === "range") continue;
      walkBody(node[key], ancestors);
    }
  }

  walkBody(ast, []);
  return found;
}

/**
 * `plik::describe > describe > nazwa` — ten sam separator, którym
 * `testing/runner.py` skleja `ancestorTitles` + `title` z JSON-a vitesta/jesta.
 * Dzięki temu nodeid liczony tutaj i nodeid odtworzony z wyniku przebiegu
 * są tym samym stringiem, bez polegania na `--testNamePattern`.
 */
function nodeIdOf(filePath, names) {
  return `${filePath}::${names.join(" > ")}`;
}

// ------------------------------------------------------------- body_hash

function tokensInRange(tokens, range) {
  return tokens.filter((t) => t.range[0] >= range[0] && t.range[1] <= range[1]);
}

/**
 * Odpowiednik `ast.dump(..., include_attributes=False)` (python) i
 * `BodyHash` na `DescendantTokens()` (helper C#): strumień tokenów ciała testu
 * bez komentarzy i białych znaków, więc przeformatowanie nie czyni z testu
 * nowego. Markery eskapowe wchodzą do hasha — zdjęcie markera **jest**
 * zmianą testu i ma go ponownie skierować do weryfikacji krzyżowej.
 */
function bodyHash(tokens, fnNode, markers) {
  const text = tokensInRange(tokens, fnNode.range)
    .map((t) => t.value)
    .join("");
  const marker = markers.slice().sort().join(",");
  return crypto.createHash("sha256").update(`${text} ${marker}`, "utf8").digest("hex").slice(0, 16);
}

// --------------------------------------------------------- markery eskapowe

const MARKER_RE = /gatekeeper:\s*([a-z_]+)/;

/**
 * Komentarze przylegające do testu od góry — bezpośrednio nad wywołaniem
 * `it(...)`/`test(...)`, dopuszczając ciąg kolejnych linii komentarza.
 * Odpowiednik `GetLeadingTrivia()` z Roslyna, którego ESTree nie ma.
 */
function declaredMarkers(comments, callNode) {
  const out = [];
  let expectedLine = callNode.loc.start.line - 1;
  for (let i = comments.length - 1; i >= 0; i -= 1) {
    const comment = comments[i];
    if (comment.loc.end.line > expectedLine) continue;
    if (comment.loc.end.line !== expectedLine) break;
    const match = MARKER_RE.exec(comment.value);
    if (match && ESCAPE_MARKERS.includes(match[1])) out.push(match[1]);
    expectedLine = comment.loc.start.line - 1;
  }
  return out.sort();
}

// ------------------------------------------------------------- discover

function discover(parser, files) {
  const tests = [];
  for (const filePath of files) {
    let source;
    try {
      source = fs.readFileSync(filePath, "utf8");
    } catch {
      continue; // plik usunięty w diffie — nic do wykrycia
    }
    let ast;
    try {
      ast = parseFile(parser, filePath, source);
    } catch {
      continue; // plik w trakcie edycji / niepoprawny — jak `except SyntaxError` w Pythonie
    }
    for (const item of collectTests(ast)) {
      const markers = declaredMarkers(ast.comments || [], item.call);
      tests.push({
        file: filePath,
        name: item.names[item.names.length - 1],
        suite: item.names.slice(0, -1).join(" > ") || null,
        nodeid: nodeIdOf(filePath, item.names),
        lineno: item.call.loc.start.line,
        body_hash: bodyHash(ast.tokens || [], item.node, markers),
        declared_escape: markers.length > 0 ? markers[0] : null,
      });
    }
  }
  return { tests };
}

// ----------------------------------------------------------------- lint

function isExpectCall(node) {
  if (node.type !== "CallExpression") return false;
  const { root } = calleeRoot(node.callee);
  return root === "expect" || root === "assert";
}

/** `expect(x).toBe(y)` → `{ subject: x, matcher: "toBe", args: [y], negated: false }`. */
function matcherOf(node) {
  if (node.type !== "CallExpression" || node.callee.type !== "MemberExpression") return null;
  const matcher = node.callee.property.name;
  let target = node.callee.object;
  let negated = false;
  while (target.type === "MemberExpression" && !target.computed) {
    if (target.property.name === "not") negated = !negated;
    target = target.object;
  }
  if (target.type !== "CallExpression") return null;
  const { root } = calleeRoot(target.callee);
  if (root !== "expect") return null;
  return { subject: target.arguments[0] ?? null, matcher, args: node.arguments, negated };
}

function allMatchers(fnNode) {
  const out = [];
  walk(fnNode, (n) => {
    const m = matcherOf(n);
    if (m) out.push({ ...m, node: n });
  });
  return out;
}

function anyEvidence(fnNode) {
  let found = false;
  walk(fnNode, (n) => {
    if (found) return;
    if (isExpectCall(n)) found = true;
  });
  return found;
}

const LITERAL_TYPES = new Set(["Literal", "TemplateLiteral"]);

function isLiteral(node) {
  return node !== null && node !== undefined && LITERAL_TYPES.has(node.type);
}

const RULES = [
  // ------------------------------------------------ test.no_assertion
  function noAssertion(ctx) {
    if (anyEvidence(ctx.fn)) return null;
    return {
      rule_id: "test.no_assertion",
      severity: "high",
      title: `Test \`${ctx.name}\` nie zawiera żadnej asercji`,
      failure_scenario:
        `Test \`${ctx.name}\` przejdzie niezależnie od tego, co zwróci testowany kod — nie ma ` +
        "w nim ani `expect(...)`, ani `assert(...)`. Zielony wynik niczego nie potwierdza.",
      evidence: {},
    };
  },

  // ------------------------------------------ test.constant_assertion
  function constantAssertion(ctx) {
    const EQUALITY = new Set(["toBe", "toEqual", "toStrictEqual"]);
    const TRUTHY = new Set(["toBeTruthy", "toBeFalsy"]);
    for (const m of allMatchers(ctx.fn)) {
      const expected = m.args[0] ?? null;
      const trivial =
        (EQUALITY.has(m.matcher) &&
          ((isLiteral(m.subject) && isLiteral(expected) &&
            normalizedText(ctx.source, m.subject) === normalizedText(ctx.source, expected)) ||
            (m.subject && expected &&
              normalizedText(ctx.source, m.subject) === normalizedText(ctx.source, expected)))) ||
        (TRUTHY.has(m.matcher) && isLiteral(m.subject));
      if (!trivial) continue;
      const line = m.node.loc.start.line;
      return {
        rule_id: "test.constant_assertion",
        severity: "high",
        title: `Test \`${ctx.name}\` asertuje stałą, nie zachowanie`,
        failure_scenario:
          `Linia ${line} w \`${ctx.name}\` to \`expect(...).${m.matcher}\` na wyrażeniu zawsze ` +
          "prawdziwym niezależnie od testowanego kodu (stała albo `x` porównane z `x`) — test " +
          "przejdzie nawet po całkowitym usunięciu implementacji.",
        evidence: { snippet: ctx.source.slice(m.node.range[0], m.node.range[1]), line },
      };
    }
    return null;
  },

  // ------------------------------------------------- test.mock_echo
  function mockEcho(ctx) {
    // Zmienne zainicjowane `vi.fn()` / `jest.fn()` i wartości wstawione przez
    // `.mockReturnValue(X)` / `.mockResolvedValue(X)`. Uproszczenie względem
    // Pythona (tekst wyrażenia zamiast grafu przepływu), jak w helperze C#.
    const mocks = new Set();
    const returned = new Set();
    walk(ctx.fn, (n) => {
      if (n.type === "VariableDeclarator" && n.init && n.init.type === "CallExpression") {
        const { root, chain } = calleeRoot(n.init.callee);
        if ((root === "vi" || root === "jest") && chain.includes("fn") && n.id.type === "Identifier") {
          mocks.add(n.id.name);
        }
      }
      if (n.type === "CallExpression" && n.callee.type === "MemberExpression") {
        const prop = n.callee.property.name || "";
        if (prop.startsWith("mockReturnValue") || prop.startsWith("mockResolvedValue")) {
          if (n.arguments[0]) returned.add(normalizedText(ctx.source, n.arguments[0]));
        }
      }
    });
    if (mocks.size === 0 || returned.size === 0) return null;

    for (const m of allMatchers(ctx.fn)) {
      if (m.matcher !== "toBe" && m.matcher !== "toEqual") continue;
      const expected = m.args[0] ?? null;
      if (!expected || !returned.has(normalizedText(ctx.source, expected))) continue;
      // `expect(mock())`/`expect(await mock())` — wywołanie na samym mocku.
      let subject = m.subject;
      if (subject && subject.type === "AwaitExpression") subject = subject.argument;
      if (!subject || subject.type !== "CallExpression") continue;
      const { root } = calleeRoot(subject.callee);
      if (!root || !mocks.has(root)) continue;
      const line = m.node.loc.start.line;
      return {
        rule_id: "test.mock_echo",
        severity: "medium",
        title: `Test \`${ctx.name}\` porównuje mocka z jego własnym \`mockReturnValue\``,
        failure_scenario:
          `W \`${ctx.name}\` asercja w linii ${line} sprawdza, że wywołanie mocka zwraca ` +
          "dokładnie to, co wpisano jako `.mockReturnValue(...)` tego samego mocka — to " +
          "potwierdza konfigurację mocka, nie zachowanie testowanego kodu.",
        evidence: { snippet: ctx.source.slice(m.node.range[0], m.node.range[1]), line },
      };
    }
    return null;
  },

  // ------------------------------------------------ test.only_smoke
  function onlySmoke(ctx) {
    const SMOKE = new Set(["toBeDefined", "toBeNull", "toBeUndefined"]);
    const matchers = allMatchers(ctx.fn);
    if (matchers.length === 0) return null;
    if (!matchers.every((m) => SMOKE.has(m.matcher))) return null;
    return {
      rule_id: "test.only_smoke",
      severity: "low",
      title: `Test \`${ctx.name}\` sprawdza tylko istnienie wyniku`,
      failure_scenario:
        `\`${ctx.name}\` wywołuje testowany kod, ale każda asercja ogranicza się do ` +
        "`toBeDefined`/`not.toBeNull` — funkcja zwracająca zupełnie błędną wartość (byle " +
        "zdefiniowaną) nadal przejdzie ten test.",
      evidence: {},
    };
  },

  // ---------------------------------------- test.exception_swallowed
  function exceptionSwallowed(ctx) {
    let hit = null;
    walk(ctx.fn, (n) => {
      if (hit || n.type !== "CatchClause") return;
      if (n.body.body.length === 0) hit = n;
    });
    if (!hit) return null;
    const line = hit.loc.start.line;
    return {
      rule_id: "test.exception_swallowed",
      severity: "medium",
      title: `Test \`${ctx.name}\` połyka wyjątek bez asercji`,
      failure_scenario:
        `W \`${ctx.name}\` blok \`catch\` w linii ${line} jest pusty — jeżeli testowany kod ` +
        "rzuci nieoczekiwany wyjątek zamiast tego, którego test się spodziewa, test i tak przejdzie.",
      evidence: { line },
    };
  },
];

function lint(parser, files) {
  const issues = [];
  for (const filePath of files) {
    let source;
    try {
      source = fs.readFileSync(filePath, "utf8");
    } catch {
      continue;
    }
    let ast;
    try {
      ast = parseFile(parser, filePath, source);
    } catch {
      continue;
    }
    for (const item of collectTests(ast)) {
      const ctx = {
        source,
        fn: item.node,
        name: item.names[item.names.length - 1],
        nodeid: nodeIdOf(filePath, item.names),
      };
      for (const rule of RULES) {
        const issue = rule(ctx);
        if (issue) issues.push({ nodeid: ctx.nodeid, ...issue });
      }
    }
  }
  return { issues };
}

// ------------------------------------------------------------------ CLI

function main(argv) {
  if (argv.length < 2 || argv[1] !== "--files") {
    process.stderr.write("użycie: helper.cjs <discover|lint> --files <plik1> [plik2 ...]\n");
    return 2;
  }
  const command = argv[0];
  const files = argv.slice(2);
  if (command !== "discover" && command !== "lint") {
    process.stderr.write(`nieznana komenda: ${command} (oczekiwano discover|lint)\n`);
    return 2;
  }
  const parser = loadParser(process.cwd());
  const payload = command === "discover" ? discover(parser, files) : lint(parser, files);
  process.stdout.write(JSON.stringify(payload));
  return 0;
}

try {
  process.exitCode = main(process.argv.slice(2));
} catch (err) {
  process.stderr.write(`${err && err.message ? err.message : err}\n`);
  process.exitCode = 1;
}
