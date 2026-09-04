"""Wspólne rozwiązywanie środowiska Node dla tego pack'a.

Wydzielone z `adapters/complexity.py` w momencie, gdy pojawił się drugi
konsument (`testing/discovery.py` — helper `G2.*`), tą samą zasadą, którą
core zastosował do `diffcover.py`: dopóki był jeden użytkownik, ekstrakcja
byłaby przedwczesną abstrakcją.

Sedno problemu: narzędzia, których ten pack potrzebuje
(`@typescript-eslint/parser`), bywają zainstalowane globalnie, a Node nie
przeszukuje globalnego `node_modules` sam z siebie. `NODE_PATH` to naprawia
— ale wyłącznie dla `require()` (CJS), nie dla `import` (ESM); stąd helper
jest plikiem `.cjs`, a config eslinta generowany w `adapters/complexity.py`
używa `require(...)`.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def global_node_modules() -> str | None:
    """`npm root -g` albo `None`, gdy npm nie odpowiada. Wynik jest cache'owany
    na proces — to jedno wywołanie podprocesu na cały przebieg bramki, nie
    jedno na plik."""
    try:
        result = subprocess.run(
            ["npm", "root", "-g"], capture_output=True, text=True, timeout=15, check=True
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    root = result.stdout.strip()
    return root or None


def node_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Kopia środowiska z globalnym `node_modules` dopiętym do `NODE_PATH`."""
    env = dict(base if base is not None else os.environ)
    global_modules = global_node_modules()
    if global_modules:
        previous = env.get("NODE_PATH")
        env["NODE_PATH"] = (
            f"{global_modules}{os.pathsep}{previous}" if previous else global_modules
        )
    return env


__all__ = ["global_node_modules", "node_env"]
