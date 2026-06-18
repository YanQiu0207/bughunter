# bughunter

把一段报错堆栈交给大模型，让它**自主检索本地代码**后，给出「问题根因（含代码解释）+ 优化建议」的结构化结论。

- **零第三方依赖**：仅用 Python 3 标准库，内网可直接拷贝运行。
- **自写极简工具循环**：约 120 行 function-calling 循环，源码全在项目内、可随意修改，不当 pip 黑盒。
- **支持多种大模型**：走 OpenAI 兼容协议，切换 DeepSeek、通义千问、内网自托管模型只改 `base_url + model`。
- **可替换 LLM 实现**：用 `Protocol` 做防腐层，底层可整体替换而不动核心逻辑。
- **默认只读沙箱**：检索限制在仓库目录内；仅显式调用 `run_command()` 时执行白名单命令。

## 快速开始

安装（零第三方依赖，仅用标准库）：

```bash
pip install -e .          # 可安装方式（推荐）
# 或者直接把 bughunter/ 目录拷进项目 / 加入 PYTHONPATH
```

```python
from bughunter import analyze, Settings

result = analyze(
    stack_trace,
    repo_path="/path/to/repo",
    settings=Settings(base_url="http://10.0.0.5:8000/v1", model="qwen2.5-coder"),
)
print(result.summary)
print(result.root_cause)
```

也可通过环境变量配置后用命令行：

```bash
export BUGHUNTER_BASE_URL=http://10.0.0.5:8000/v1
export BUGHUNTER_MODEL=qwen2.5-coder

python -m bughunter --repo /path/to/repo --stack-file trace.txt
```

## 运行测试

```bash
python -m unittest discover -s tests
```

全部测试纯本地、零网络。

## 文档

- [设计文档](docs/design.md)：架构、工具循环机制、防腐层、安全沙箱、选型说明。
- [使用文档](docs/usage.md)：配置、模块调用、命令行、切换模型、内网部署。

## 闭环能力

除现有 `analyze()` 外，`bughunter` 还提供「修复 - 应用 - 测试」闭环接口：

- `propose_fix()`：只读检索代码，返回 `FixProposal`，不落盘。
- `generate_tests()`：只读检索代码，返回 `TestProposal`，不落盘。
- `apply_edits()`：在人工确认后确定性应用修改，带沙箱校验、备份和失败回滚。
- `restore_backup()`：按 `apply_edits()` 返回的备份路径恢复文件，并清理本次新建文件。
- `run_command()`：只执行 `Settings.allowed_commands` 中声明的白名单命令。
- `apply_and_test()`：组合执行 `apply_edits()` 与白名单测试命令。

流程编排由调用方系统负责：库只返回结构化结果，不做人工确认、不直接交互。

## 要求

Python 3.10 及以上（`pyproject.toml` 已声明 `requires-python >= 3.10`）。
