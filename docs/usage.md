# bughunter 使用文档

bughunter 是一个零第三方依赖的 Python 模块：传入报错堆栈和代码仓库路径，由大模型自主检索代码后，返回结构化的根因分析与优化建议。

## 1. 安装

无需安装任何第三方包，仅依赖 Python 3 标准库。两种方式任选：

- **可安装方式**（推荐）：在项目根目录执行 `pip install -e .`，随后可直接 `import bughunter`、并使用 `bughunter` 命令。
- **拷贝方式**：把 `bughunter/` 目录放进项目（或加入 `PYTHONPATH`）即可，内网无 pip 也能用。

要求：Python 3.10 及以上（代码使用了 `str | None` 等写法，`pyproject.toml` 已声明 `requires-python >= 3.10`）。

## 2. 配置

通过 `Settings` 配置 LLM 端点，参数优先，否则从环境变量读取。

| 字段 | 环境变量 | 说明 |
| --- | --- | --- |
| `base_url` | `BUGHUNTER_BASE_URL` | OpenAI 兼容端点，给到 `/v1` 这一层 |
| `model` | `BUGHUNTER_MODEL` | 模型名 |
| `api_key` | `BUGHUNTER_API_KEY` | API Key；内网无鉴权时留空 |
| `timeout` | `BUGHUNTER_TIMEOUT` | 请求超时秒数，默认 60 |
| `max_retries` | `BUGHUNTER_MAX_RETRIES` | LLM 调用失败重试次数（指数退避 1/2/4s），默认 3；仅对网络错误与 429/5xx 重试，4xx 不重试 |

`base_url` 只需给到 `/v1`，客户端会自行拼接 `/chat/completions`。例如 `https://api.deepseek.com/v1`，或内网 `http://10.0.0.5:8000/v1`。

环境变量配置示例（PowerShell）：

```powershell
$env:BUGHUNTER_BASE_URL = "http://10.0.0.5:8000/v1"
$env:BUGHUNTER_MODEL = "qwen2.5-coder"
$env:BUGHUNTER_API_KEY = "sk-xxx"   # 内网无鉴权可省略
```

## 3. 作为模块调用

最简用法，从环境变量读配置：

```python
from bughunter import analyze

result = analyze(stack_trace, repo_path="/path/to/repo")
print(result.summary)
print(result.root_cause)
for s in result.suggestions:
    print(s.title, "-", s.detail)
```

显式传入配置（不依赖环境变量）：

```python
from bughunter import analyze, Settings

settings = Settings(
    base_url="http://10.0.0.5:8000/v1",
    model="qwen2.5-coder",
    api_key="",          # 内网无鉴权
)
result = analyze(stack_trace, repo_path="/path/to/repo", settings=settings)
```

### 返回结构

`analyze()` 返回 `AnalysisResult`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `summary` | `str` | 一句话结论 |
| `root_cause` | `str` | 根因，含具体代码解释 |
| `code_references` | `list[CodeReference]` | 相关代码片段（`file` / `line_start` / `line_end` / `excerpt`） |
| `suggestions` | `list[Suggestion]` | 优化建议（`title` / `detail` / `code_refs`） |
| `confidence` | `"high"` / `"medium"` / `"low"` | 置信度 |

### 渲染报告

```python
from bughunter import to_markdown, to_dict
import json

print(to_markdown(result))                       # 人类可读的 Markdown 报告
print(json.dumps(to_dict(result), ensure_ascii=False, indent=2))   # 结构化 JSON
```

## 4. 命令行

```bash
# 堆栈来自文件，输出 Markdown 报告
python -m bughunter --repo /path/to/repo --stack-file trace.txt

# 堆栈从标准输入读取，输出 JSON
cat trace.txt | python -m bughunter --repo /path/to/repo --format json
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--repo` | 必填，可供检索的代码仓库根目录 |
| `--stack-file` | 报错堆栈文件；省略则从标准输入读取 |
| `--format` | `md`（默认）或 `json` |
| `--max-steps` | 工具循环最大轮数，默认 12 |
| `--max-retries` | LLM 调用失败重试次数，默认 3（指数退避 1/2/4s） |
| `--base-url` / `--model` / `--api-key` | 覆盖环境变量（`--base-url` 与 `--model` 需同时给出） |

## 5. 切换大模型

走 OpenAI 兼容协议，切换模型只改 `base_url + model`（+ `api_key`），代码无需改动。

| 场景 | base_url 示例 |
| --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` |
| 通义千问（DashScope 兼容模式） | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 内网自托管（vLLM / Ollama 等） | `http://10.0.0.5:8000/v1` |

> 各端点的具体地址与模型名以对应服务方文档为准，此处仅为形态示意。

## 6. 内网部署

- 零第三方依赖，把 `bughunter/` 目录拷进内网环境即可，无需 pip 联网拉包。
- 端点指向内网自托管的 OpenAI 兼容服务（如 vLLM、Ollama 的兼容接口）。
- 无鉴权时 `api_key` 留空字符串。
- 工具默认纯只读且限制在 `repo_path` 沙箱内，不执行任何命令，便于在敏感环境使用。

## 7. 替换 LLM 实现（防腐层）

核心循环只依赖 `LLMClient` 接口。要换底层实现（如改用 httpx、官方 SDK，或测试用 mock），写一个满足接口的类传给 `analyze(..., llm=...)` 即可，无需改 `agent.py`：

```python
from bughunter import analyze, ChatResponse

class MyClient:
    def chat(self, messages, tools, tool_choice="auto") -> ChatResponse:
        ...   # 自行实现，返回中立的 ChatResponse
        return ChatResponse(content=None, tool_calls=[...])

result = analyze(stack_trace, repo_path="/path/to/repo", llm=MyClient())
```

接口定义、`ChatResponse` / `ToolCall` 中立模型见 `bughunter/llm.py`，设计说明见 [design.md](design.md)。

## 参考来源

- OpenAI Function calling 文档：<https://platform.openai.com/docs/guides/function-calling>
