"""Settings 与 Settings.from_env 的单元测试。"""

from __future__ import annotations

import os
import unittest

from bughunter.config import (
    ENV_ALLOWED_COMMANDS,
    ENV_API_KEY,
    ENV_BASE_URL,
    ENV_COMMAND_TIMEOUT,
    ENV_MAX_RETRIES,
    ENV_MODEL,
    ENV_SYSTEM_CONTEXT,
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

    def test_allowed_commands_default_empty(self) -> None:
        s = Settings(base_url="http://localhost/v1", model="m")
        self.assertEqual(s.allowed_commands, {})
        self.assertEqual(s.command_timeout, 300.0)

    def test_invalid_allowed_commands_raises(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                base_url="http://localhost/v1",
                model="m",
                allowed_commands={"test": []},
            )


class SettingsFromEnvTest(unittest.TestCase):
    def _set_env(self, **kwargs: str) -> None:
        for key, value in kwargs.items():
            os.environ[key] = value

    def _del_env(self, *keys: str) -> None:
        for key in keys:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._del_env(
            ENV_BASE_URL,
            ENV_MODEL,
            ENV_API_KEY,
            ENV_TIMEOUT,
            ENV_MAX_RETRIES,
            ENV_ALLOWED_COMMANDS,
            ENV_COMMAND_TIMEOUT,
            ENV_SYSTEM_CONTEXT,
        )

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
            **{
                ENV_BASE_URL: "http://localhost/v1",
                ENV_MODEL: "m",
                ENV_TIMEOUT: "abc",
            }
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

    def test_from_env_allowed_commands_json(self) -> None:
        self._set_env(
            **{
                ENV_BASE_URL: "http://localhost/v1",
                ENV_MODEL: "m",
                ENV_ALLOWED_COMMANDS: '{"test": ["python", "-m", "unittest"]}',
                ENV_COMMAND_TIMEOUT: "12.5",
                ENV_SYSTEM_CONTEXT: "run tests",
            }
        )
        s = Settings.from_env()
        self.assertEqual(s.allowed_commands["test"], ["python", "-m", "unittest"])
        self.assertEqual(s.command_timeout, 12.5)
        self.assertEqual(s.system_context, "run tests")

    def test_command_from_env_does_not_require_llm_env(self) -> None:
        self._set_env(
            **{
                ENV_ALLOWED_COMMANDS: '{"test": ["python", "-V"]}',
                ENV_BASE_URL: "not-a-url",
            }
        )
        s = Settings.command_from_env()
        self.assertEqual(s.model, "command-runner")
        self.assertEqual(s.base_url, "http://localhost/v1")
        self.assertEqual(s.allowed_commands["test"], ["python", "-V"])


if __name__ == "__main__":
    unittest.main()
