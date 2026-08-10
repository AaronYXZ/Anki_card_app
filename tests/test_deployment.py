import json
from pathlib import Path


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
