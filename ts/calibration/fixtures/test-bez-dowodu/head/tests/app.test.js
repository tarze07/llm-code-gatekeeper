import { add } from "../src/app";

// Test dopisany razem z `classify`, ale asertujacy `add` - zachowanie, ktore
// w tym PR-ze sie nie zmienilo. Przejdzie na kodzie sprzed zmiany, wiec nie
// dowodzi niczego o tej zmianie: dokladnie to ma zlapac G2.cross_verify.
describe("add", () => {
  it("dodaje dwie liczby", () => {
    expect(add(2, 2)).toBe(4);
  });
});
