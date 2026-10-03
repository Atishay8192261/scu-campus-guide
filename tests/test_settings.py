from guide.settings import Settings


def test_project_key_overrides_inherited_key_without_overriding_database_env(tmp_path, monkeypatch):
    local = tmp_path / ".env"
    local.write_text(
        "OPENAI_API_KEY=fixture-project-key\nGUIDE_DATABASE_URL=postgresql+psycopg://localhost/project\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-inherited-key")
    monkeypatch.setenv("GUIDE_DATABASE_URL", "postgresql+psycopg://localhost/from_env")
    settings = Settings(_env_file=local)
    assert settings.key("openai") == "fixture-project-key"
    assert settings.database_url.endswith("/from_env")


def test_blank_local_key_does_not_shadow_environment(tmp_path, monkeypatch):
    local = tmp_path / ".env"
    local.write_text("OPENAI_API_KEY=\n")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-inherited-key")
    assert Settings(_env_file=local).key("openai") == "fixture-inherited-key"
