# Feature: 修复 - 应用 - 测试闭环 Agent

**作者**： YanQi
**日期**： 2026-06-18
**状态**： Quick Draft

---

## 1. 背景 (Background)

### 1.1 问题描述

当前 `bughunter` 是一个只读分析器：调用方传入报错堆栈和仓库路径，
库让 LLM 使用 `read_file`、`grep_code`、`list_dir` 在仓库内只读检索，
最终通过 `submit_analysis` 产出「根因 + 建议」的结构化结果。
现有公开用法见 [README.md](../../../../README.md)，
核心循环见 [bughunter/agent.py](../../../../bughunter/agent.py)，
工具 schema 与结构化结果见 [bughunter/schema.py](../../../../bughunter/schema.py)。

老板希望把它演进为更通用的闭环 agent，在分析之后继续支持
「给出修复代码 → 人工确认 → 修改代码 → 写单元测试 / 集成测试 → 执行测试」。
这个方向会触碰原设计中的「只读、不执行命令」约束，
因此需要保持默认只读不变，同时把写盘与命令执行设计成显式 opt-in 的独立阶段。

### 1.2 现状分析

- `analyze(stack_trace, repo_path, ...)` 是当前主要入口，返回 `AnalysisResult`；
  CLI 支持读取堆栈并输出 Markdown 或 JSON。
  参考：[bughunter/agent.py](../../../../bughunter/agent.py)、[bughunter/cli.py](../../../../bughunter/cli.py)。
- `agent.py` 内部直接实现 function-calling 工具循环，包括工具分发、`submit_analysis` 暂存、
  `ResultParseError` 回灌重入、`max_steps` 兜底和 `_assistant_message`。
  参考：[bughunter/agent.py](../../../../bughunter/agent.py)。
- `tools.py` 当前只提供 `read_file`、`grep_code`、`list_dir`，
  并通过 `CodeTools._resolve()` 限制路径必须位于 `repo_path` 内。
  参考：[bughunter/tools.py](../../../../bughunter/tools.py)。
- `llm.py` 定义 `LLMClient` 防腐层，默认 `UrllibOpenAIClient` 使用 Python 标准库 `urllib`
  调 OpenAI 兼容 `/chat/completions`。
  参考：[bughunter/llm.py](../../../../bughunter/llm.py)、[docs/design.md](../../../design.md)。
- `schema.py` 当前只定义分析结果相关 dataclass、工具 schema 和 `build_result()` 校验逻辑。
  参考：[bughunter/schema.py](../../../../bughunter/schema.py)。
- 现有测试覆盖 agent 工具循环、工具沙箱、schema 校验、LLM 解析 / 重试、CLI 行为。
  参考：[tests/test_agent.py](../../../../tests/test_agent.py)、
  [tests/test_tools.py](../../../../tests/test_tools.py)、[tests/test_llm.py](../../../../tests/test_llm.py)。

### 1.3 主要使用场景

- 调用方系统调用 `analyze()` 获取根因结构化结果，展示给程序员。
- 调用方系统调用 `propose_fix()` 获取修复方案和修改块，交给程序员人工确认。
- 程序员确认后，调用方系统再次调用库接口，让库确定性地应用代码修改。
- 调用方系统调用 `generate_tests()` 获取测试文件方案，确认后再应用。
- 调用方系统调用 `run_command()` 或 `apply_and_test()` 执行白名单中的单元测试 / 集成测试命令，并把测试输出作为下一轮修复上下文。

## 2. 目标 (Goals)

- 将 `bughunter` 从只读分析器演进为「修复 - 应用 - 测试」闭环能力库。
- 保持 `analyze()` 的现有行为、安全性和兼容性不变。
- 新增无状态、无交互、结构化返回的纯接口，由调用方系统负责编排人工确认和阶段串联。
- 模型只产出修复 / 测试方案，不直接落盘、不直接执行命令；落盘和命令执行由确定性代码在显式调用时完成。

### 2.1 非目标 (Non-Goals)

- 本库不负责向程序员发起确认、不打印交互式提示、不管理人工审批流程。
- 本库不允许模型自由生成并执行命令。
- 本库不在 `analyze()` 中引入写盘或命令执行能力。
- 本期不引入第三方依赖。
- 本期不实现长期任务状态存储；每个公开函数保持无状态。

## 3. 需求细化 (Requirements)

### 3.1 功能性需求

- 新增通用工具循环 `run_tool_loop()`，供 `analyze()`、`propose_fix()`、`generate_tests()` 复用。
- `analyze()` 改为薄封装，行为与现有实现保持一致。
- 新增 `propose_fix(stack_trace, repo_path, ...) -> FixProposal`：只读检索仓库，产出修复方案，不落盘。
- 新增 `generate_tests(repo_path, ...) -> TestProposal`：只读检索仓库，产出测试文件方案，不落盘；可注入 `Settings.system_context` 作为系统启动 / 集成测试说明。
- 新增 `FileEdit`、`FixProposal`、`TestProposal`，其中修改块使用 `old_string` 唯一匹配 + `new_string` 替换；`create` 动作用于新建文件。
- 新增 `apply_edits(proposal, repo_path, ...) -> ApplyResult`：确定性应用修改，校验路径沙箱、唯一匹配、`create` 不覆盖，失败回滚，成功备份。
- 新增 `run_command(name, repo_path, settings) -> CommandResult`：
  只执行 `Settings.allowed_commands` 中声明的白名单命令，禁止 `shell=True`。
- 新增 `apply_and_test(proposal, repo_path, settings, ...) -> ApplyAndTestResult`：先应用修改，成功后运行白名单测试命令；应用失败则不运行测试。
- `Settings` 新增 `allowed_commands`、`command_timeout`、`system_context`，并支持环境变量解析。
- `report.py` 新增修复方案与命令结果的 Markdown 渲染能力。
- CLI 新增薄包装子命令，输出结构化 JSON，不包含交互式确认。
- `__init__.py` 导出新增公共 API 与数据结构。

### 3.2 非功能性需求

- **兼容性**：现有 `analyze()` API 与 CLI 现有行为不破坏。
- **安全性**：默认只读；写盘与命令执行必须由调用方显式调用；模型不能直接写盘或执行命令。
- **确定性**：`apply_edits()`、`run_command()`、`apply_and_test()` 不调用 LLM。
- **可回滚**：应用修改前先完成全量校验；任一步写入失败要回滚已写文件；成功后保留备份。
- **零第三方依赖**：文件写入、diff、命令执行、JSON 配置解析均使用 Python 标准库。
- **可测试性**：所有新增能力必须有纯本地单元测试；LLM 阶段继续使用 FakeLLMClient 范式。

## 4. 设计方案 (Design)

### 4.1 方案概览

新增一组与 `analyze()` 并列的无状态接口：

```text
analyze(stack, repo)            -> AnalysisResult
propose_fix(stack, repo, ...)   -> FixProposal
generate_tests(repo, ...)       -> TestProposal
apply_edits(proposal, repo)     -> ApplyResult
restore_backup(repo, backup)    -> None
run_command(name, repo, cfg)    -> CommandResult
apply_and_test(proposal, repo)  -> ApplyAndTestResult
```

调用方系统负责编排：

```text
analyze -> propose_fix -> 人工确认 -> apply_and_test
```

测试失败时，调用方系统可把测试输出回灌给 `propose_fix()` 再生成下一轮方案。

### 4.2 组件设计 (Component Design)

#### 4.2.1 核心类/模块设计

- `bughunter/loop.py`：通用 function-calling 工具循环。
- `bughunter/agent.py`：保留阶段入口与 prompt，调用 `run_tool_loop()`。
- `bughunter/schema.py`：新增提案类、应用 / 命令结果类、收口工具 schema 和 build 函数。
- `bughunter/patch.py`：确定性应用修改与 `apply_and_test()`。
- `bughunter/runner.py`：确定性白名单命令执行。
- `bughunter/config.py`：新增命令白名单、命令超时和系统上下文配置。
- `bughunter/report.py`：新增人工确认用 Markdown 渲染。
- `bughunter/cli.py`：新增无交互子命令。

#### 4.2.2 接口设计

```python
def propose_fix(...) -> FixProposal: ...
def generate_tests(...) -> TestProposal: ...
def apply_edits(...) -> ApplyResult: ...
def restore_backup(...) -> None: ...
def run_command(...) -> CommandResult: ...
def apply_and_test(...) -> ApplyAndTestResult: ...
```

#### 4.2.3 数据模型

- `FileEdit(path, action, old_string, new_string, rationale)`
- `FixProposal(summary, edits, confidence)`
- `TestProposal(summary, edits, confidence)`
- `ApplyResult(applied, skipped, backup_path)`
- `CommandResult(name, exit_code, stdout, stderr, timed_out)`
- `ApplyAndTestResult(apply_result, command_result)`

#### 4.2.4 并发模型

本期不引入并发执行。`apply_edits()` 顺序写入文件并在异常时回滚已经写入的文件。

#### 4.2.5 错误处理

- LLM 收口参数不合法时，继续沿用 `ResultParseError` 回灌重入。
- `apply_edits()` 在路径越界、唯一匹配失败、覆盖已有文件、写入失败时拒绝或回滚。
- `run_command()` 在命令未配置时抛错；命令超时时返回 `timed_out=True`。

### 4.3 核心逻辑实现

- 抽取 `agent.py` 现有循环到 `loop.py`，参数化收口工具名、build 函数、工具 schema 和 dispatcher。
- `propose_fix()` 与 `generate_tests()` 仍然只使用 `CodeTools` 只读工具。
- `apply_edits()` 使用 `old_string` 唯一匹配，先在内存中完成所有校验，再逐个写入。
- `run_command()` 从 `Settings.allowed_commands[name]` 取 `argv`，
  使用 `subprocess.Popen(..., shell=False, cwd=repo)` 执行，
  并分块读取 stdout / stderr，避免大输出完整进入内存或临时文件。

### 4.4 方案优劣分析

优点：

- 保持默认只读安全模型。
- 调用方系统可以自由编排人工确认和阶段串联。
- LLM 阶段和确定性执行阶段边界清晰。
- 不引入第三方依赖。

局限：

- `old_string` 唯一匹配对大范围重构不友好。
- 命令白名单需要调用方配置。
- 集成测试质量依赖 `system_context` 的描述质量。

## 5. 备选方案 (Alternatives Considered)

- 让模型直接写盘：被放弃。原因是安全边界不清晰，难以人工确认和回滚。
- 让模型自由执行命令：被放弃。原因是命令执行风险过高，必须通过白名单约束。
- 整文件替换：被放弃。原因是 token 成本更高、人工确认难度更大、误覆盖风险更高。

## 6. 业界调研 (Industry Research)

本 Quick Draft 暂不展开业界调研。后续若进入正式设计评审，应补充工具调用、补丁应用和命令沙箱相关资料。

### 6.1 业界方案

- 待补充。

### 6.2 对比分析

- 待补充。

## 7. 测试计划 (Test Plan)

### 7.1 单元测试

- `tests/test_loop.py`：通用循环分发、收口、重入、`max_steps`。
- `tests/test_agent.py`：`propose_fix()`、`generate_tests()` 的 FakeLLMClient 流程。
- `tests/test_patch.py`：唯一匹配、零匹配、多匹配、`create` 不覆盖、失败回滚、备份、路径越界。
- `tests/test_runner.py`：白名单执行、非白名单拒绝、超时。
- `tests/test_config.py`：新增配置字段和环境变量解析。
- `tests/test_report.py`：新增 Markdown 渲染。

### 7.2 集成测试

- 在临时小仓库中模拟 `propose_fix -> apply_edits -> run_command("test")`。

### 7.3 性能测试（如适用）

- 本期不设专门性能测试；通过输出截断限制命令输出大小。

## 8. 可观测性 & 运维 (Observability & Operations)

### 8.1 可观测性

- 本库返回结构化结果，不新增日志系统。
- `CommandResult` 返回 `exit_code`、`stdout`、`stderr`、`timed_out`，供调用方系统记录和告警。

### 8.2 配置参数 (Configuration)

| 参数名 | 类型 | 默认值 | 说明 | 是否支持动态修改 |
|--------|------|--------|------|------------------|
| `allowed_commands` | `dict[str, list[str]]` | `{}` | 命令白名单 | 否 |
| `command_timeout` | `float` | `300.0` | 命令执行超时秒数 | 否 |
| `system_context` | `str` | `""` | 系统启动 / 集成测试说明 | 否 |

### 8.3 运维接口 (Operations Interfaces)

- CLI 子命令：`analyze`、`propose-fix`、`apply`、`run`。

### 8.4 运维注意事项 (Operations Considerations)

- 升级后默认仍不执行命令，必须配置白名单并显式调用。
- `apply_edits()` 成功后生成备份目录，调用方应决定备份保留策略。

## 9. Changelog

| 日期 | 变更内容 | 作者 |
|------|----------|------|
| 2026-06-18 | 创建 Quick Draft | YanQi |

## 10. 参考资料 (References)

- [README.md](../../../../README.md)
- [docs/design.md](../../../design.md)
- [docs/usage.md](../../../usage.md)
- [bughunter/agent.py](../../../../bughunter/agent.py)
- [bughunter/schema.py](../../../../bughunter/schema.py)
- [bughunter/tools.py](../../../../bughunter/tools.py)
- [bughunter/llm.py](../../../../bughunter/llm.py)
