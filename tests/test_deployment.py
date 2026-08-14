import json
from pathlib import Path

from anki_card_app.database import normalize_database_url


def test_railway_configuration_runs_migrations_and_database_healthcheck() -> None:
    configuration = json.loads(Path("railway.json").read_text())

    assert configuration["build"]["builder"] == "RAILPACK"
    deploy = configuration["deploy"]
    assert deploy["preDeployCommand"] == "alembic upgrade head"
    assert "anki_card_app.main:app" in deploy["startCommand"]
    assert "--host 0.0.0.0" in deploy["startCommand"]
    assert "--port $PORT" in deploy["startCommand"]
    assert deploy["healthcheckPath"] == "/ready"
    assert deploy["restartPolicyType"] == "ON_FAILURE"


def test_railway_database_url_is_compatible_with_alembic() -> None:
    railway_url = "postgresql://user:password@postgres.railway.internal:5432/railway"
    migration_environment = Path("migrations/env.py").read_text()

    assert normalize_database_url(railway_url).startswith("postgresql+psycopg://")
    assert "psycopg2" not in normalize_database_url(railway_url)
    assert "normalize_database_url(get_settings().database_url)" in migration_environment
