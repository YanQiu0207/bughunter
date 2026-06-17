# bughunter 设计文档

## 1. 目标与约束

bughunter 解决一个具体问题：把一段报错堆栈交给大模型，让它**自主检索本地代码**后，产出「问题根因（含代码解释）+ 优化建议」的结构化结论。

这是一个典型的「单 agent + 工具循环 + 结构化输出」任务，并非多 agent 协作，因此不需要重型框架。在与老板对齐后，确定如下硬约束：

- **不用任何 agent 框架**，自写极简工具循环（核心约 120 行），源码全在项目内、可随意修改。
- **零第三方依赖**：LLM 调用用 Python 标准库 `urllib` 直接打 OpenAI 兼容的 `/chat/completions`；结构化、校验、测试全部用标准库（`dataclasses`、`json`、`re`、`unittest`）。
- **支持多种大模型**：走 OpenAI 兼容协议，切换 DeepSeek、通义千问、内网自托管模型只改 `base_url + model`（+ `api_key`）。
- **可替换 LLM 实现（防腐层）**：用标准库 `Protocol` 定义厂商无关的 `LLMClient` 接口，核心循环只依赖接口。
- **面向内网部署**：零依赖 + 标准库 HTTP，断网内网最干净，无需 pip 拉任何包。

## 2. 整体架构

```
                analyze(stack_trace, repo_path)
                            │
              ┌─────────────┴─────────────┐
              │        agent.py           │   工具循环（只依赖 LLMClient 接口）
              └──────┬─────────────┬──────┘
                     │             │
          ┌──────────▼───┐   ┌─────▼──────────┐
          │   llm.py     │   │   tools.py     │
          │  防腐层      │   │  只读代码工具  │
          │ LLMClient    │   │  + 沙箱        │
          └──────┬───────┘   └────────────────┘
                 │
      ┌──────────▼──────────┐
      │ UrllibOpenAIClient  │  默认实现，封装 OpenAI 兼容协议细节
      └──────────┬──────────┘
                 │  urllib（标准库 HTTP）
                 ▼
        OpenAI 兼容端点 /chat/completions
```

各模块职责：

| 模块 | 职责 |
| --- | --- |
| `config.py` | `Settings`：端点、模型、超时、重试次数等配置；参数优先，否则读环境变量 |
| `schema.py` | 结构化结果 `dataclasses` + 所有工具的 JSON Schema 常量 + `build_result` 校验 |
| `llm.py` | 防腐层：`LLMClient` 接口、中立响应模型、默认实现 `UrllibOpenAIClient` |
| `tools.py` | `read_file` / `grep_code` / `list_dir`，纯标准库实现 + 仓库沙箱 |
| `agent.py` | 工具循环 + `analyze()` 入口 |
| `report.py` | 把 `AnalysisResult` 渲染成 Markdown 报告，或转 dict |
| `cli.py` | 命令行入口 |

## 3. 核心机制：function-calling 工具循环

OpenAI 兼容的 Chat Completions 原生支持 function calling：请求体带 `tools`（每个工具是一段 JSON Schema），模型回 `tool_calls`，本地执行后把结果以 `role: "tool"` 塞回 `messages` 再次请求，如此往复直到收口。bughunter 据此自写循环：

```
messages = [system, user(报错堆栈)]
循环（最多 max_steps 轮）：
    resp = llm.chat(messages, tools=TOOL_SCHEMAS, tool_choice="auto")
    若 resp 没有 tool_calls：
        追加 assistant 消息 + 一条 user 消息，明确要求调用 submit_analysis 收口
        continue
    把 assistant（带 tool_calls）追加进 messages
    先执行所有普通工具，结果以 role=tool 回灌：
        对每个 tool_call：
            若是 submit_analysis：暂存，不立即执行（保证消息历史完整）
            否则执行 dispatch(read_file / grep_code / list_dir)
            把工具结果以 role=tool 追加进 messages
    若有暂存的 submit_analysis：
        补齐它的 role=tool 消息（防重入时历史非法）
        尝试 build_result(arguments)
        成功 → return 结构化结果
        失败（参数不符合 schema）→ 把错误信息以 role=tool 回灌，continue 让模型修正后重新提交
超过 max_steps 仍未收口 → 抛 MaxStepsExceeded
```

对应实现见 `bughunter/agent.py` 的 `analyze()`。

> function calling 协议（`tools` / `tool_calls` 的请求与响应格式）来源：OpenAI 官方文档 [Function calling](https://platform.openai.com/docs/guides/function-calling)。

### 3.1 结构化输出的关键技巧

不依赖 `response_format`（并非所有 OpenAI 兼容端点都支持该字段），而是定义一个名为 `submit_analysis` 的工具，**它的参数 schema 就是最终结果结构**。模型「调用」这个工具即视为提交，其参数就是结构化结果。这样用 function-calling 的 schema 强约束字段，通用性最好，对各家兼容端点都成立。

结果结构（见 `schema.py`）：

```python
@dataclass
class CodeReference:
    file: str
    line_start: int
    line_end: int
    excerpt: str

@dataclass
class Suggestion:
    title: str
    detail: str
    code_refs: list[CodeReference]

@dataclass
class AnalysisResult:
    summary: str
    root_cause: str          # 根因，含具体代码解释
    code_references: list[CodeReference]
    suggestions: list[Suggestion]
    confidence: Literal["high", "medium", "low"]
```

`build_result(arguments)` 负责把模型提交的 JSON 转成 `AnalysisResult`，缺字段、类型不符或 `confidence` 越界时抛 `ResultParseError`，避免脏数据流出。

## 4. 防腐层（anti-corruption layer）

核心循环不应该和「OpenAI 协议」「urllib」这些外部细节耦合。为此在 `llm.py` 引入防腐层：

- **中立响应模型**：`ChatResponse`、`ToolCall` 两个 `dataclass`，厂商无关，是核心循环唯一认识的返回类型。
- **接口**：用标准库 `typing.Protocol` 定义 `LLMClient`，结构化子类型，零依赖、无需继承。

```python
@runtime_checkable
class LLMClient(Protocol):
    def chat(self, messages, tools, tool_choice="auto") -> ChatResponse: ...
```

- **默认实现** `UrllibOpenAIClient` 是防腐层的「腐败侧」，集中处理所有协议细节：拼接 `/chat/completions`、加 `Authorization: Bearer` 头、`urllib` 发请求、解析 `choices[0].message` 与 `tool_calls`（把 `function.arguments` 的 JSON 字符串 `json.loads` 成 dict），最后转成中立的 `ChatResponse` 返回。

收益：替换实现只需写一个满足 `LLMClient` 的新类（比如 `HttpxClient`、`OpenAISDKClient`、测试用的 `FakeLLMClient`），传给 `analyze(..., llm=...)` 即可，`agent.py` 一行都不用改。单元测试正是用 `FakeLLMClient` 脚本化整条工具链，全程零网络。

## 5. 安全：只读沙箱

模型能驱动工具读本地文件，必须防越权与路径穿越。`tools.py` 的约束：

- **路径沙箱**：所有路径先 `Path(repo_path, p).resolve()`，再校验是否仍在仓库根目录内，越界直接抛 `SandboxError`，`dispatch` 把它转成 `[拒绝]` 文本返回给模型，绝不读取仓库外内容。
- **纯只读**：只提供 `read_file` / `grep_code` / `list_dir`，**不提供任何命令执行**，内网使用更安全。
- **上下文保护**：单次结果有上限（`MAX_READ_BYTES`、`MAX_GREP_MATCHES`、`MAX_LIST_ENTRIES`）并截断，防止把上下文撑爆。
- **遍历裁剪**：`grep_code` 用 `os.walk` + `re` 纯标准库实现（不依赖 ripgrep，内网可能没有），跳过 `.git`、`node_modules` 等目录与常见二进制后缀。

## 6. 健壮性设计

内网生产场景下，LLM 端点偶发抖动（网络中断、429 限流、临时 5xx）与模型偶尔提交脏数据是两类最常见的「非致命失败」。bughunter 对两者均做了自愈处理，而非直接中断：

### 6.1 LLM 调用重试（`llm.py` `_post`）

`UrllibOpenAIClient._post` 对**可重试错误**做指数退避重试，最多 `max_retries` 次（默认 3，可通过 `Settings.max_retries` / `BUGHUNTER_MAX_RETRIES` 配置）：

| 错误类型 | 是否重试 | 说明 |
| --- | --- | --- |
| `URLError`（连接失败/超时） | 是 | 网络抖动最常见来源 |
| `HTTPError` 429 | 是 | 限流，退避后通常恢复 |
| `HTTPError` 5xx（500/502/503/504） | 是 | 服务端临时故障 |
| `HTTPError` 其他 4xx（401/403/404…） | 否 | 鉴权/配置错误，重试无意义 |
| `JSONDecodeError`（响应非合法 JSON） | 否 | 响应已到，是协议异常而非瞬时故障 |

退避策略：`1s → 2s → 4s`（`2 ** attempt`）。重试耗尽后抛 `LLMError`，错误信息包含端点与最终失败原因。

重试仅作用于 `_post`（HTTP 层），不影响 `agent.py` 的工具循环逻辑；防腐层接口 `LLMClient.chat` 的契约不变，替换实现可自行决定是否重试。

### 6.2 提交失败重入（`agent.py`）

模型调用 `submit_analysis` 时，其 `arguments` 偶尔会缺字段或类型不符。`build_result` 在此情况下抛 `ResultParseError`。此时不直接中断整个分析，而是：

1. 为 `submit_tc` 补齐 `role=tool` 消息（内容为错误提示，而非 "submitted"），保证消息历史合法；
2. `continue` 回到循环顶部，让模型看到错误反馈后修正参数、重新提交。

这一机制受 `max_steps` 兜底：模型若反复提交脏数据，最终仍会触发 `MaxStepsExceeded`，不会无限循环。把一次「本可自愈」的脏提交从硬失败变成软重试，是这一设计的核心收益。

## 7. 为什么不用现成框架

| 方案 | 问题 |
| --- | --- |
| CrewAI / AutoGen | 面向多 agent 协作，对单 agent + 工具循环属重型，且引入大量传递依赖 |
| LangChain | 抽象层厚、依赖多，内网安装与排错成本高 |
| openai / pydantic-ai SDK | 需要 pip 安装第三方包；老板明确不想当 pip 黑盒、要能直接改源码 |
| **自写极简循环（本项目）** | 约 120 行、零依赖、源码全在项目内、内网直接跑 |

任务本质是单 agent 的 function-calling 循环，标准库已经够用，框架带来的抽象在这里是负担而非收益。

## 8. 验证方式

- **单元测试**（`python -m unittest`，零网络零依赖）：
  - `tests/test_tools.py`：临时仓库验证 `read_file` / `grep_code` / `list_dir` 行为，并验证沙箱拒绝越界路径、不泄露仓库外内容。
  - `tests/test_agent.py`：注入 `FakeLLMClient` 脚本化 `grep_code → read_file → submit_analysis`，断言循环正确分发工具、工具结果回灌、无工具调用时催促收口、`max_steps` 超限抛错。
- **端到端**：配好真实（含内网）端点，对一个小仓库 + 真实堆栈跑 `analyze()`，人工核对根因是否定位到正确 `file:line`、建议是否合理。

## 参考来源

- OpenAI Function calling 文档（`tools` / `tool_calls` 协议）：<https://platform.openai.com/docs/guides/function-calling>
- Python `typing.Protocol`（结构化子类型）：<https://docs.python.org/3/library/typing.html#typing.Protocol>
- Python `urllib.request`：<https://docs.python.org/3/library/urllib.request.html>
