"""`SemgrepRulePackProvider` (`gatekeeper_core.core.plugins`) tego pack'a.

`rules_dir()` przez `importlib.resources`, nie przez wspinanie się po
`__file__.parent` — przeżywa instalację z wheela (patrz
`gatekeeper_core.adapters.semgrep.CoreRulePack`, ten sam wzorzec).
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


class TsRulePack:
    pack_id = "ts"

    def rules_dir(self) -> Path:
        return Path(str(files("gatekeeper_ts") / "rules" / "semgrep" / "ts.yaml"))
