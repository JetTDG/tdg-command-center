"""Keep the hosted PostgreSQL driver compatible with production requirements."""

from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version


def test_unqualified_postgresql_url_uses_installed_psycopg2_driver():
    requirements = [
        Requirement(line.strip()) for line in
        (Path(__file__).resolve().parents[1] / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert any(req.name.lower() == "psycopg2-binary" for req in requirements)
    sqlalchemy = next(req for req in requirements if req.name.lower() == "sqlalchemy")
    assert Version("2.1.4") not in sqlalchemy.specifier
    assert Version("2.0.51") in sqlalchemy.specifier
