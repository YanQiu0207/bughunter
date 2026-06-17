"""UrllibOpenAIClient._parse 与 _post 的单元测试（零网络）。"""

from __future__ import annotations

import io
import json
import unittest
import unittest.mock
import urllib.error
import urllib.request

from bughunter.config import Settings
from bughunter.llm import LLMError, UrllibOpenAIClient

_SETTINGS = Settings(base_url="http://localhost/v1", model="m")


class ParseTest(unittest.TestCase):
    def test_normal_response_with_tool_calls(self) -> None:
        raw = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "app.py"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
        resp = UrllibOpenAIClient._parse(raw)
        self.assertIsNone(resp.content)
        self.assertEqual(len(resp.tool_calls), 1)
        self.assertEqual(resp.tool_calls[0].name, "read_file")
        self.assertEqual(resp.tool_calls[0].arguments, {"path": "app.py"})
        self.assertEqual(resp.tool_calls[0].id, "tc1")

    def test_normal_response_text_only(self) -> None:
        raw = {"choices": [{"message": {"content": "hello", "tool_calls": None}}]}
        resp = UrllibOpenAIClient._parse(raw)
        self.assertEqual(resp.content, "hello")
        self.assertEqual(resp.tool_calls, [])

    def test_empty_choices_raises_llm_error(self) -> None:
        with self.assertRaises(LLMError) as ctx:
            UrllibOpenAIClient._parse({"choices": []})
        self.assertIn("choices", str(ctx.exception))

    def test_missing_choices_raises_llm_error(self) -> None:
        with self.assertRaises(LLMError):
            UrllibOpenAIClient._parse({})

    def test_invalid_arguments_json_raises_llm_error(self) -> None:
        raw = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "{not valid json}",
                                },
                            }
                        ],
                    }
                }
            ]
        }
        with self.assertRaises(LLMError) as ctx:
            UrllibOpenAIClient._parse(raw)
        self.assertIn("read_file", str(ctx.exception))


class PostTest(unittest.TestCase):
    """_post 的网络错误分支与重试逻辑，用 mock 替换 urlopen，零真实网络。"""

    def _client(self, max_retries: int = 1) -> UrllibOpenAIClient:
        # 默认 max_retries=1：错误测试立即失败，不触发退避等待
        return UrllibOpenAIClient(
            Settings(base_url="http://localhost/v1", model="m", max_retries=max_retries)
        )

    def test_http_error_raises_llm_error(self) -> None:
        err = urllib.error.HTTPError(
            url="http://localhost/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"bad key"),
        )
        with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(LLMError) as ctx:
                self._client()._post({"model": "m", "messages": []})
        self.assertIn("401", str(ctx.exception))

    def test_url_error_raises_llm_error(self) -> None:
        err = urllib.error.URLError(reason="connection refused")
        with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(LLMError):
                self._client()._post({"model": "m", "messages": []})

    def test_non_json_response_raises_llm_error(self) -> None:
        mock_resp = unittest.mock.MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        mock_resp.read.return_value = b"not json"
        with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
            with self.assertRaises(LLMError):
                self._client()._post({"model": "m", "messages": []})

    def test_chat_full_path_returns_chat_response(self) -> None:
        raw = {
            "choices": [
                {
                    "message": {
                        "content": "hello",
                        "tool_calls": None,
                    }
                }
            ]
        }
        mock_resp = unittest.mock.MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(raw).encode("utf-8")
        with unittest.mock.patch("urllib.request.urlopen", return_value=mock_resp):
            resp = self._client().chat(messages=[], tools=[])
        self.assertEqual(resp.content, "hello")
        self.assertEqual(resp.tool_calls, [])

    def _ok_response(self):
        """构造一个正常的 urlopen 返回 mock。"""
        raw = {"choices": [{"message": {"content": "ok", "tool_calls": None}}]}
        mock_resp = unittest.mock.MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = unittest.mock.MagicMock(return_value=False)
        mock_resp.read.return_value = json.dumps(raw).encode("utf-8")
        return mock_resp

    def test_retry_on_429_then_success(self) -> None:
        """429 可重试：第一次 429，第二次成功。"""
        err = urllib.error.HTTPError(
            url="http://localhost/v1/chat/completions",
            code=429,
            msg="Too Many Requests",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"slow down"),
        )
        ok = self._ok_response()
        with unittest.mock.patch("urllib.request.urlopen", side_effect=[err, ok]):
            with unittest.mock.patch("time.sleep") as mock_sleep:
                resp = self._client(max_retries=3).chat(messages=[], tools=[])
        self.assertEqual(resp.content, "ok")
        mock_sleep.assert_called_once_with(1)  # 退避 2**0 = 1s

    def test_retry_exhausted_on_urlerror_raises_llm_error(self) -> None:
        """URLError 持续失败：重试耗尽后抛 LLMError。"""
        err = urllib.error.URLError(reason="connection refused")
        with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
            with unittest.mock.patch("time.sleep"):
                with self.assertRaises(LLMError):
                    self._client(max_retries=3)._post({"model": "m", "messages": []})

    def test_no_retry_on_401(self) -> None:
        """401 不可重试：立即失败，不调用 sleep。"""
        err = urllib.error.HTTPError(
            url="http://localhost/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"bad key"),
        )
        with unittest.mock.patch("urllib.request.urlopen", side_effect=err):
            with unittest.mock.patch("time.sleep") as mock_sleep:
                with self.assertRaises(LLMError):
                    self._client(max_retries=3)._post({"model": "m", "messages": []})
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
