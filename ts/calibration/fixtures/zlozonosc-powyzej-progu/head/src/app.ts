export function hello(): string {
  return "hi";
}

export function przetworz(
  a: boolean,
  b: boolean,
  c: boolean,
  d: boolean,
  e: boolean,
  f: boolean,
  g: boolean,
  h: boolean,
  i: boolean,
  j: boolean
): string {
  if (a) {
    if (b) {
      if (c) {
        if (d) {
          if (e) {
            if (f) {
              if (g) {
                if (h) {
                  if (i) {
                    if (j) {
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
