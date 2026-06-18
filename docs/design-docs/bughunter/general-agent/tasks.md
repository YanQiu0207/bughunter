# 实施任务清单

> 由 spec.md 生成
> 任务总数： 6
> 核心原则： 先建后迁后扩展——先抽取通用循环和数据契约，再新增确定性执行能力，最后接入 CLI、文档与测试。

## 依赖关系总览

```text
Task 1 (通用循环 + analyze 迁移)
  ↓
Task 2 (提案 schema + propose/generate)
  ↓
Task 3 (配置扩展 + runner)
  ↓
Task 4 (patch + apply_and_test)
  ↓
Task 5 (报告、CLI、导出)
  ↓
Task 6 (测试与文档同步)
```

## 变更影响概览

### 文件变更清单

| 文件 | 操作 | 涉及任务 | 说明 |
|------|------|---------|------|
| `bughunter/loop.py` | 新建 | Task 1 | 通用工具循环 |
| `bughunter/agent.py` | 修改 | Task 1, 2 | 迁移 `analyze()`，新增 `propose_fix()`、`generate_tests()` |
| `bughunter/schema.py` | 修改 | Task 2, 4 | 新增提案、应用、命令结果数据结构与 schema |
| `bughunter/config.py` | 修改 | Task 3 | 新增命令白名单、命令超时、系统上下文配置 |
| `bughunter/runner.py` | 新建 | Task 3 | 白名单命令执行 |
| `bughunter/patch.py` | 新建 | Task 4 | 确定性应用修改与组合接口 |
| `bughunter/report.py` | 修改 | Task 5 | 新增提案 / 命令结果渲染 |
| `bughunter/cli.py` | 修改 | Task 5 | 新增无交互子命令 |
| `bughunter/__init__.py` | 修改 | Task 5 | 导出新增 API |
| `tests/test_loop.py` | 新建 | Task 1, 6 | 通用循环测试 |
| `tests/test_agent.py` | 修改 | Task 1, 2, 6 | 阶段入口测试 |
| `tests/test_patch.py` | 新建 | Task 4, 6 | 应用修改测试 |
| `tests/test_runner.py` | 新建 | Task 3, 6 | 命令执行测试 |
| `tests/test_config.py` | 修改 | Task 3, 6 | 配置解析测试 |
| `tests/test_report.py` | 修改 | Task 5, 6 | 渲染测试 |
| `docs/design.md` | 修改 | Task 6 | 同步架构文档 |
| `docs/usage.md` | 修改 | Task 6 | 同步使用文档 |
| `README.md` | 修改 | Task 6 | 同步入口说明 |

### 受影响接口

| 接口 | 变更类型 | 调用方 | 涉及任务 |
|------|---------|--------|---------|
| `analyze()` | 内部实现迁移 | 现有 Python API、CLI | Task 1 |
| `propose_fix()` | 新增 | Python API、CLI | Task 2, 5 |
| `generate_tests()` | 新增 | Python API | Task 2, 5 |
| `apply_edits()` | 新增 | Python API、CLI | Task 4, 5 |
| `restore_backup()` | 新增 | Python API | Task 4, 5 |
| `run_command()` | 新增 | Python API、CLI | Task 3, 5 |
| `apply_and_test()` | 新增 | Python API | Task 4, 5 |
| `Settings` | 字段新增 | LLM 客户端、runner、agent | Task 3 |

### 构建系统变更

- `pyproject.toml` 无需变更：当前 `setuptools` 以 `packages = ["bughunter"]` 打包整个包目录，新 `.py` 模块会随包发布。

## 风险与假设

| # | 描述 | 影响任务 | 假设/处理 |
|---|------|---------|----------|
| 1 | `apply_and_test()` 放置位置有两种选择 | Task 4 | 按老板方案放在 `patch.py`，`__init__.py` 统一导出 |
| 2 | CLI 子命令会改变入口参数形态 | Task 5 | 保留无子命令时的旧 `analyze` 行为，降低破坏性 |
| 3 | Windows / Unix 超时命令写法不同 | Task 6 | 单元测试优先使用 Python 自身命令构造跨平台超时 |

## 任务列表

### 任务 1 [x] 抽取通用工具循环并保持 `analyze()` 行为不变

- 文件： `bughunter/loop.py`（新建）, `bughunter/agent.py`（修改）, `tests/test_loop.py`（新建）, `tests/test_agent.py`（修改）
- 依赖： 无
- spec 映射： 4.1、4.2.1、4.3、7.1
- 说明： 将 `agent.py` 中现有 function-calling 循环抽取为 `run_tool_loop()`，`analyze()` 改为调用通用循环。
- context：
  - `bughunter/agent.py:analyze()` — 现有循环实现与行为基准
  - `bughunter/schema.py` — `TOOL_SCHEMAS`、`TOOL_SUBMIT`、`build_result()`
  - `bughunter/tools.py:CodeTools.dispatch()` — 下游工具分发入口
  - `tests/test_agent.py` — 现有 agent 行为测试
- 验收标准：
  - [x] `python -m unittest tests.test_loop tests.test_agent` 通过
  - [x] `python -m unittest discover -s tests` 通过
  - [x] `analyze()` 公开签名保持不变
- 子任务：
  - [x] 1.1: 新建 `bughunter/loop.py`
  - [x] 1.2: 迁移 `_assistant_message()` 和循环逻辑
  - [x] 1.3: 修改 `analyze()` 调用 `run_tool_loop()`
  - [x] 1.4: 迁移 / 补充循环测试

### 任务 2 [x] 新增修复与测试提案阶段

- 文件： `bughunter/schema.py`（修改）, `bughunter/agent.py`（修改）, `tests/test_agent.py`（修改）, `tests/test_schema.py`（修改）
- 依赖： Task 1
- spec 映射： 3.1、4.1、4.2.2、4.2.3、7.1
- 说明： 新增 `FileEdit`、`FixProposal`、`TestProposal`、`submit_fix`、`submit_tests`、build 函数，并实现只读的 `propose_fix()` 与 `generate_tests()`。
- context：
  - `bughunter/schema.py` — 现有 dataclass 与 `_require()` 校验风格
  - `bughunter/agent.py` — 阶段入口和 prompt
  - `bughunter/loop.py:run_tool_loop()` — 上游通用循环
  - `bughunter/tools.py:CodeTools` — 下游只读工具
- 验收标准：
  - [x] `python -m unittest tests.test_agent tests.test_schema` 通过
  - [x] FakeLLMClient 可驱动 `grep_code -> read_file -> submit_fix`
  - [x] 非法 `confidence` 或非法 `edits` 会抛 `ResultParseError`
- 子任务：
  - [x] 2.1: 新增提案 dataclass 与 schema
  - [x] 2.2: 新增 `build_fix_proposal()` 与 `build_test_proposal()`
  - [x] 2.3: 新增 `propose_fix()` 与 `generate_tests()`
  - [x] 2.4: 补充阶段入口与 schema 测试

### 任务 3 [x] 新增命令配置与白名单 runner

- 文件： `bughunter/config.py`（修改）, `bughunter/runner.py`（新建）,
  `bughunter/schema.py`（修改）, `tests/test_config.py`（修改）,
  `tests/test_runner.py`（新建）
- 依赖： Task 2
- spec 映射： 3.1、3.2、4.2.1、4.2.2、4.2.5、8.2
- 说明： 扩展 `Settings`，新增 `run_command()`，只允许执行白名单 `argv`，捕获 stdout / stderr / 超时。
- context：
  - `bughunter/config.py:Settings` — 配置对象与环境变量读取
  - `bughunter/schema.py` — `CommandResult` 数据结构
  - `bughunter/runner.py` — 直接修改目标
  - `tests/test_llm.py` — 可参考当前配置 / 错误测试风格
- 验收标准：
  - [x] `python -m unittest tests.test_config tests.test_runner` 通过
  - [x] 非白名单命令 name 被拒绝
  - [x] 命令执行使用 `shell=False`
  - [x] 超时返回 `timed_out=True`
- 子任务：
  - [x] 3.1: 扩展 `Settings` 和环境变量解析
  - [x] 3.2: 新增 `CommandResult`
  - [x] 3.3: 实现 `run_command()`
  - [x] 3.4: 补充配置与 runner 测试

### 任务 4 [x] 新增确定性应用修改与组合接口

- 文件： `bughunter/patch.py`（新建）, `bughunter/schema.py`（修改）, `tests/test_patch.py`（新建）
- 依赖： Task 3
- spec 映射： 3.1、3.2、4.2.2、4.2.3、4.2.5、4.3、7.1
- 说明： 实现 `apply_edits()` 与 `apply_and_test()`，确保路径沙箱、唯一匹配、失败回滚和成功备份。
- context：
  - `bughunter/tools.py:CodeTools._resolve()` — 沙箱思路
  - `bughunter/schema.py` — `FileEdit`、`ApplyResult`、`ApplyAndTestResult`
  - `bughunter/runner.py:run_command()` — 下游测试执行入口
  - `tests/test_tools.py` — 路径越界测试参考
- 验收标准：
  - [x] `python -m unittest tests.test_patch` 通过
  - [x] `old_string` 零匹配 / 多匹配均拒绝
  - [x] `create` 覆盖已有文件会被拒绝
  - [x] 写入失败会回滚已写文件
  - [x] 成功后生成备份目录
- 子任务：
  - [x] 4.1: 新增 `ApplyResult` 与 `ApplyAndTestResult`
  - [x] 4.2: 实现 `apply_edits()`
  - [x] 4.3: 实现 `apply_and_test()`
  - [x] 4.4: 补充 patch 测试

### 任务 5 [x] 接入报告渲染、CLI 与公共导出

- 文件： `bughunter/report.py`（修改）, `bughunter/cli.py`（修改）, `bughunter/__init__.py`（修改）, `tests/test_cli.py`（修改）, `tests/test_report.py`（修改）
- 依赖： Task 4
- spec 映射： 3.1、4.1、4.2.1、8.3
- 说明： 导出新增 API；新增 `fix_proposal_to_markdown()`、`command_result_to_markdown()`；CLI 作为薄包装输出 JSON，不做人工确认。
- context：
  - `bughunter/report.py` — 现有 `to_markdown()` 和 `to_dict()` 风格
  - `bughunter/cli.py:main()` — 现有 CLI 入口
  - `bughunter/__init__.py` — 公共导出列表
  - `tests/test_cli.py`、`tests/test_report.py` — 现有测试
- 验收标准：
  - [x] `python -m unittest tests.test_cli tests.test_report` 通过
  - [x] 旧 CLI analyze 用法仍可工作
  - [x] 新 CLI 不包含交互式确认提示
- 子任务：
  - [x] 5.1: 新增报告渲染函数
  - [x] 5.2: 新增 CLI 子命令
  - [x] 5.3: 更新 `__init__.py`
  - [x] 5.4: 补充 CLI 和 report 测试

### 任务 6 [x] 全量测试与文档同步

- 文件： `README.md`（修改）, `docs/design.md`（修改）, `docs/usage.md`（修改）, `docs/design-docs/bughunter/general-agent/tasks.md`（修改）
- 依赖： Task 5
- spec 映射： 7.1、7.2、8.1、8.4、10
- 说明： 跑全量测试，更新 README 和 docs，标记任务完成状态。
- context：
  - `README.md` — 项目入口说明
  - `docs/design.md` — 架构和安全模型说明
  - `docs/usage.md` — 使用说明
  - `python -m unittest discover -s tests` — 全量验证命令
- 验收标准：
  - [x] `python -m unittest discover -s tests` 通过
  - [x] README 包含新增闭环 API 的简要说明
  - [x] `docs/design.md` 描述新阶段与安全模型演进
  - [x] `docs/usage.md` 包含新增 API / CLI 的使用方式
- 子任务：
  - [x] 6.1: 全量运行单元测试
  - [x] 6.2: 更新 README
  - [x] 6.3: 更新设计与使用文档
  - [x] 6.4: 更新 tasks.md 完成状态

## Spec 覆盖映射

| Spec 章节 | 任务 | 说明 |
|-----------|------|------|
| 1.1-1.3 | Task 1-6 | 背景贯穿所有任务 |
| 2、2.1 | Task 1-6 | 目标与非目标由所有任务共同保证 |
| 3.1 | Task 1-5 | 功能性需求逐项实现 |
| 3.2 | Task 3-6 | 安全、兼容、确定性、测试要求 |
| 4.1 | Task 1, 2, 4, 5 | 阶段接口与调用流 |
| 4.2 | Task 1-5 | 组件、接口、数据模型和错误处理 |
| 4.3 | Task 1, 4 | 通用循环和补丁应用核心逻辑 |
| 4.4 | Task 3, 4, 6 | 安全优势与局限通过配置、回滚和文档呈现 |
| 5 | Task 4 | 放弃方案落实为安全边界 |
| 6 | Task 6 | 当前为 Quick Draft，后续文档任务保留补充空间 |
| 7 | Task 1-6 | 测试计划按任务分布执行 |
| 8 | Task 3, 5, 6 | 配置、CLI、运维注意事项 |
| 9 | Task 6 | Changelog 后续更新 |
| 10 | Task 6 | 参考资料在文档同步中维护 |

