"""The model catalogue: id resolution, provider wiring, env handling."""
import pytest

from backend.shared.config import models as cat


class TestEnvHandling:
    def test_blank_env_falls_back_to_the_default(self, monkeypatch):
        """docker-compose renders an unset ``${FOO-}`` passthrough as "".

        `os.getenv(name, default)` returns that empty string rather than the
        default, which would silently blank out a model id.
        """
        monkeypatch.setenv("SOME_MODEL", "")
        assert cat._env("SOME_MODEL", "fallback") == "fallback"

    def test_set_env_wins(self, monkeypatch):
        monkeypatch.setenv("SOME_MODEL", "custom-model")
        assert cat._env("SOME_MODEL", "fallback") == "custom-model"

    def test_missing_env_uses_the_default(self, monkeypatch):
        monkeypatch.delenv("SOME_MODEL", raising=False)
        assert cat._env("SOME_MODEL", "fallback") == "fallback"


class TestIdResolution:
    def test_known_ids_resolve_to_themselves(self):
        for option in cat.MODEL_OPTIONS:
            assert cat.normalize_model_id(option.id) == option.id

    def test_legacy_ids_still_resolve(self):
        assert cat.normalize_model_id("gemini-2.5-flash") == "gemini-flash"
        assert cat.normalize_model_id("sonnet-3.5") == "claude-sonnet"

    @pytest.mark.parametrize("given", [None, "", "no-such-model"])
    def test_unknown_input_degrades_to_the_default(self, given):
        assert cat.normalize_model_id(given) == cat.DEFAULT_MODEL_ID

    def test_ids_are_unique(self):
        ids = [m.id for m in cat.MODEL_OPTIONS]
        assert len(ids) == len(set(ids))


class TestFreeTier:
    """Free OpenRouter options exist so a Gemini quota cap doesn't block work."""

    def test_free_options_are_present_and_marked(self):
        free = [m for m in cat.MODEL_OPTIONS if m.tier == "free"]
        assert free, "expected at least one free development model"
        assert all(m.provider == "openrouter" for m in free)

    def test_free_models_point_at_a_free_openrouter_slug(self):
        for option in (m for m in cat.MODEL_OPTIONS if m.tier == "free"):
            assert option.backend_model.endswith(":free"), (
                f"{option.id} -> {option.backend_model} is not a free OpenRouter model"
            )

    def test_openrouter_availability_follows_its_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert not cat.provider_configured("openrouter")

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert cat.provider_configured("openrouter")

    def test_building_without_a_key_says_what_to_do(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            cat.build_llm("free-small")


class TestCatalogueSerialisation:
    def test_every_option_serialises_with_the_fields_the_ui_reads(self):
        for entry in cat.available_models():
            for key in ("id", "label", "provider", "tier", "description",
                        "recommended", "available"):
                assert key in entry

    def test_only_configured_filters_by_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        ids = {m["id"] for m in cat.available_models(only_configured=True)}
        assert "free-small" not in ids
