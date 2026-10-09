"""G3.secrets: ścieżki bezwzględne w postaci windowsowej też liczą się jako
nierozwiązane (wcześniej tylko te zaczynające się od `/`)."""

from __future__ import annotations

import pytest

from gatekeeper_core.adapters import gitleaks
from gatekeeper_core.core.change import ChangeContext
from gatekeeper_core.gates.g3_secrets import SecretsGate


@pytest.mark.parametrize("path", ["/abs/a.py", "C:/abs/a.py", "C:\\abs\\a.py"])
def test_sciezka_bezwzgledna_liczona_jako_nierozwiazana(repo, monkeypatch, path):
    leak = gitleaks.Leak(
        rule_id="aws-access-token",
        description="x",
        file=path,
        line=1,
        redacted="AKIA…",
        entropy=None,
        tool_fingerprint="fp",
    )
    monkeypatch.setattr(gitleaks, "is_available", lambda: True)
    monkeypatch.setattr(gitleaks, "scan", lambda **k: [leak])
    change = ChangeContext.from_git(repo.path, "HEAD", "HEAD")

    result = SecretsGate({}).run(change)

    assert result.facts["secrets.unresolved_paths"] == 1
