"""Settings 与 Settings.from_env 的单元测试。"""

from __future__ import annotations

import os
import unittest

from bughunter.config import (
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_MAX_RETRIES,
    ENV_MODEL,
    ENV_TIMEOUT,
    Settings,
)


class SettingsInitTest(unittest.TestCase):
    def test_valid_settings(self) -> None:
        s = Settings(base_url="http://localhost:8000/v1", model="gpt-4o")
        self.assertEqual(s.base_url, "http://localhost:8000/v1")
        self.assertEqual(s.model, "gpt-4o")

    def test_trailing_slash_stripped(self) -> None:
        s = Settings(base_url="http://localhost:8000/v1/", model="m")
        self.assertEqual(s.base_url, "http://localhost:8000/v1")

    def test_empty_base_url_raises(self) -> None:
        with self.assertRaises(ValueError):
            Settings(base_url="", model="m")

    def test_empty_model_raises(self) -> None:
        with self.assertRaises(ValueError):
            Settings(base_url="http://localhost/v1", model="")

    def test_invalid_scheme_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Settings(base_url="file:///etc/passwd", model="m")
        self.assertIn("http/https", str(ctx.exception))

    def test_ftp_scheme_raises(self) -> None:
        with self.assertRaises(ValueError):
            Settings(base_url="ftp://example.com/v1", model="m")

    def test_https_accepted(self) -> None:
        s = Settings(base_url="https://api.example.com/v1", model="m")
        self.assertEqual(s.base_url, "https://api.example.com/v1")

    def test_default_max_retries(self) -> None:
        s = Settings(base_url="http://localhost/v1", model="m")
        self.assertEqual(s.max_retries, 3)

    def test_negative_max_retries_raises(self) -> None:
        with self.assertRaises(ValueError):
            Settings(base_url="http://localhost/v1", model="m", max_retries=-1)


class SettingsFromEnvTest(unittest.TestCase):
    def _set_env(self, **kwargs: str) -> None:
        for k, v in kwargs.items():
            os.environ[k] = v

    def _del_env(self, *keys: str) -> None:
        for k in keys:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        self._del_env(ENV_BASE_URL, ENV_MODEL, ENV_API_KEY, ENV_TIMEOUT, ENV_MAX_RETRIES)

    def test_from_env_valid(self) -> None:
        self._set_env(
            **{
                ENV_BASE_URL: "http://10.0.0.1/v1",
                ENV_MODEL: "qwen2",
                ENV_API_KEY: "sk-xxx",
            }
        )
        s = Settings.from_env()
        self.assertEqual(s.base_url, "http://10.0.0.1/v1")
        self.assertEqual(s.model, "qwen2")
        self.assertEqual(s.api_key, "sk-xxx")

    def test_from_env_missing_base_url_raises(self) -> None:
        self._del_env(ENV_BASE_URL)
        self._set_env(**{ENV_MODEL: "m"})
        with self.assertRaises(RuntimeError) as ctx:
            Settings.from_env()
        self.assertIn(ENV_BASE_URL, str(ctx.exception))

    def test_from_env_missing_model_raises(self) -> None:
        self._set_env(**{ENV_BASE_URL: "http://localhost/v1"})
        self._del_env(ENV_MODEL)
        with self.assertRaises(RuntimeError) as ctx:
            Settings.from_env()
        self.assertIn(ENV_MODEL, str(ctx.exception))

    def test_from_env_timeout_parsed(self) -> None:
        self._set_env(
            **{ENV_BASE_URL: "http://localhost/v1", ENV_MODEL: "m", ENV_TIMEOUT: "30"}
        )
        s = Settings.from_env()
        self.assertAlmostEqual(s.timeout, 30.0)

    def test_from_env_default_timeout(self) -> None:
        self._set_env(**{ENV_BASE_URL: "http://localhost/v1", ENV_MODEL: "m"})
        self._del_env(ENV_TIMEOUT)
        s = Settings.from_env()
        self.assertAlmostEqual(s.timeout, 60.0)

    def test_from_env_trailing_slash_stripped(self) -> None:
        self._set_env(**{ENV_BASE_URL: "http://localhost/v1/", ENV_MODEL: "m"})
        s = Settings.from_env()
        self.assertEqual(s.base_url, "http://localhost/v1")

    def test_from_env_invalid_timeout_raises(self) -> None:
        self._set_env(
            **{ENV_BASE_URL: "http://localhost/v1", ENV_MODEL: "m", ENV_TIMEOUT: "abc"}
        )
        with self.assertRaises(RuntimeError) as ctx:
            Settings.from_env()
        self.assertIn(ENV_TIMEOUT, str(ctx.exception))

    def test_from_env_max_retries_parsed(self) -> None:
        self._set_env(
            **{
                ENV_BASE_URL: "http://localhost/v1",
                ENV_MODEL: "m",
                ENV_MAX_RETRIES: "5",
            }
        )
        s = Settings.from_env()
        self.assertEqual(s.max_retries, 5)

    def test_from_env_default_max_retries(self) -> None:
        self._set_env(**{ENV_BASE_URL: "http://localhost/v1", ENV_MODEL: "m"})
        self._del_env(ENV_MAX_RETRIES)
        s = Settings.from_env()
        self.assertEqual(s.max_retries, 3)

    def test_from_env_invalid_max_retries_raises(self) -> None:
        self._set_env(
            **{
                ENV_BASE_URL: "http://localhost/v1",
                ENV_MODEL: "m",
                ENV_MAX_RETRIES: "abc",
            }
        )
        with self.assertRaises(RuntimeError) as ctx:
            Settings.from_env()
        self.assertIn(ENV_MAX_RETRIES, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
