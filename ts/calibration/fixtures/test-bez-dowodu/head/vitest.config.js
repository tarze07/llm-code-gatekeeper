// `globals: true` — test korzysta z `describe/it/expect` bez importu z
// "vitest", dzięki czemu fixture kalibracyjny nie musi wozić `node_modules`
// (uruchamia go vitest z instalacji globalnej, `requires_tools: [vitest]`).
export default { test: { globals: true } };
