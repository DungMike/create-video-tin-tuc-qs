"""Centralized config loader — reads config.yaml + resolves ${ENV_VAR} from .env."""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

# Resolve paths relative to the service root (where config.yaml lives)
SERVICE_ROOT = Path(__file__).resolve().parent.parent


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ${ENV_VAR} references in config values."""
    if isinstance(value, str):
        def _replacer(match: re.Match) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, "")
        return _ENV_VAR_PATTERN.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


class AppConfig:
    """Singleton config backed by config.yaml + .env."""

    _instance: "AppConfig | None" = None
    _data: dict

    def __new__(cls) -> "AppConfig":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        env_path = SERVICE_ROOT / ".env"
        load_dotenv(env_path, override=True)

        config_path = SERVICE_ROOT / "config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"config.yaml not found at {config_path}")

        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        self._data = _resolve_env_vars(raw or {})

    def reload(self) -> None:
        """Force-reload config (useful in tests)."""
        self._load()

    # --- Accessors ---

    @property
    def mining(self) -> dict:
        return self._data.get("mining", {})

    @property
    def search(self) -> dict:
        return self._data.get("search", {})

    @property
    def telegram(self) -> dict:
        return self._data.get("telegram", {})

    @property
    def reddit(self) -> dict:
        return self._data.get("reddit", {})

    @property
    def bluesky(self) -> dict:
        return self._data.get("bluesky", {})

    @property
    def crawl_settings(self) -> dict:
        return self._data.get("crawl_settings", {})

    @property
    def quality_scoring(self) -> dict:
        return self._data.get("quality_scoring", {})

    @property
    def asset_library(self) -> dict:
        return self._data.get("asset_library", {})

    @property
    def storage_cfg(self) -> dict:
        return self._data.get("storage", {})

    # --- Derived paths (absolute, relative to SERVICE_ROOT) ---

    @property
    def db_path(self) -> Path:
        rel = self.asset_library.get("db_path", "storage/asset_index.db")
        return SERVICE_ROOT / rel

    @property
    def assets_dir(self) -> Path:
        rel = self.storage_cfg.get("assets_dir", "storage/assets")
        return SERVICE_ROOT / rel

    @property
    def failed_urls_path(self) -> Path:
        rel = self.storage_cfg.get("failed_urls_path", "storage/failed_urls.json")
        return SERVICE_ROOT / rel

    @property
    def crawl_logs_dir(self) -> Path:
        rel = self.storage_cfg.get("crawl_logs_dir", "storage/crawl_logs")
        return SERVICE_ROOT / rel

    def get_source_settings(self, source_name: str) -> dict:
        """Return rate-limit/retry settings for a specific source."""
        overrides = self.crawl_settings.get("per_source_overrides", {})
        defaults = {
            "delay": self.crawl_settings.get("default_delay_seconds", 2),
            "max_retries": self.crawl_settings.get("max_retries", 3),
            "backoff_multiplier": 1.5,
        }
        if source_name in overrides:
            defaults.update(overrides[source_name])
        return defaults

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Access nested config via dot notation: cfg.get('mining.llm.model')."""
        keys = dotted_key.split(".")
        node = self._data
        for k in keys:
            if isinstance(node, dict):
                node = node.get(k)
            else:
                return default
            if node is None:
                return default
        return node


def get_config() -> AppConfig:
    """Module-level accessor."""
    return AppConfig()
