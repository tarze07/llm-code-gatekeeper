"""Wspólne wyposażenie testów panelu.

Testy chodzą po prawdziwej aplikacji (`create_app`) i prawdziwej bazie
w katalogu tymczasowym — atrapa repozytorium sprawdzałaby atrapę, a największe
ryzyko tego pakietu siedzi właśnie w SQLite i w warstwie HTTP.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gatekeeper_web.app import create_app
from gatekeeper_web.config import Settings

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

#: Panel odpowiada wyłącznie na adresy lokalne, więc testy muszą się nimi
#: przedstawiać. Test z `Host: testserver` jest testem innej aplikacji.
BASE_URL = "http://127.0.0.1"
ORIGIN = {"Origin": BASE_URL}


class Panel:
    """Cienka nakładka na `TestClient`: dokłada token CSRF i `Origin`."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        client.get("/")

    @property
    def token(self) -> str:
        return self.client.cookies.get("gk_csrf") or ""

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.client.get(url, **kwargs)

    def post(self, url: str, data: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        payload = dict(data or {})
        payload.setdefault("csrf_token", self.token)
        headers = {**ORIGIN, **kwargs.pop("headers", {})}
        return self.client.post(url, data=payload, headers=headers, **kwargs)

    def post_json(self, url: str, body: Any, **kwargs: Any) -> Any:
        headers = {**ORIGIN, "x-csrf-token": self.token, **kwargs.pop("headers", {})}
        return self.client.post(url, json=body, headers=headers, **kwargs)

    def patch_json(self, url: str, body: Any, **kwargs: Any) -> Any:
        headers = {**ORIGIN, "x-csrf-token": self.token, **kwargs.pop("headers", {})}
        return self.client.patch(url, json=body, headers=headers, **kwargs)

    # ------------------------------------------------------------ skróty

    def create_project(self, name: str = "Taskboard") -> int:
        response = self.post_json("/api/v1/projects", {"name": name})
        assert response.status_code == 201, response.text
        project_id: int = response.json()["project"]["id"]
        return project_id

    def import_sample(self, name: str, project_id: int) -> Any:
        return self.import_bytes(load_sample_bytes(name), project_id, filename=name)

    def import_bytes(
        self, raw: bytes, project_id: int, filename: str = "raport.json"
    ) -> Any:
        return self.post(
            "/api/v1/reports/import",
            data={"project_id": project_id},
            files={"file": (filename, raw, "application/json")},
        )


def load_sample(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SAMPLES / name).read_text(encoding="utf-8"))
    return data


def load_sample_bytes(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


class GitRepo:
    """Prawdziwe repozytorium git — testy kolejki uruchamiają prawdziwy silnik."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "panel@example.com")
        self.git("config", "user.name", "Panel")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args], capture_output=True, text=True, check=True
        )
        return proc.stdout

    def write(self, rel: str, content: str) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def checkout(self, branch: str, create: bool = False) -> None:
        self.git("checkout", "-q", *(["-b"] if create else []), branch)


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    repo = GitRepo(tmp_path / "repo")
    repo.write("README.md", "# projekt\n")
    repo.write("src/app.py", "def hello():\n    return 'hi'\n")
    repo.commit("start")
    repo.checkout("praca", create=True)
    repo.write("src/app.py", "def hello():\n    return 'zmienione'\n")
    repo.commit("zmiana")
    return repo


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path / "stan",
        # Testy rejestrują repozytorium w `tmp_path`, więc tylko on jest dozwolony.
        allowed_repo_roots=(tmp_path,),
        start_supervisor=False,
        # Logowanie kodem startowym ma osobny zestaw; tu sprawdzamy resztę panelu.
        require_login=False,
    )


@pytest.fixture
def client(settings: Settings) -> Any:
    with TestClient(create_app(settings), base_url=BASE_URL) as client:
        yield client


@pytest.fixture
def panel(client: TestClient) -> Panel:
    return Panel(client)


MINIMALNA_POLITYKA = "version: 1\nthresholds:\n  diff.effective_files:\n    max: 50\n"


@pytest.fixture
def gotowy_projekt(panel: Panel, git_repo: GitRepo) -> int:
    """Projekt z repozytorium i aktywną polityką — gotowy do uruchamiania."""
    project_id = panel.create_project("Uruchamialny")
    profile = panel.post_json("/api/v1/policies", {"name": "Domyślny"}).json()["profile"]
    draft = panel.post_json(
        f"/api/v1/policies/{profile['id']}/drafts", {"policy_yaml": MINIMALNA_POLITYKA}
    ).json()["revision"]
    assert panel.post_json(
        f"/api/v1/policy-revisions/{draft['id']}/activate", {"author": "test"}
    ).status_code == 200
    response = panel.patch_json(
        f"/api/v1/projects/{project_id}",
        {"repo_path": str(git_repo.path), "policy_profile_id": profile["id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["project"]["runnable"] is True
    return project_id


@pytest.fixture
def demo(panel: Panel) -> tuple[int, str]:
    """Projekt z zaimportowanym raportem z celowymi usterkami (22 znaleziska)."""
    project_id = panel.create_project()
    response = panel.import_sample("demo-celowe-usterki.json", project_id)
    assert response.status_code == 201, response.text
    run_id: str = response.json()["run"]["run_id"]
    return project_id, run_id
