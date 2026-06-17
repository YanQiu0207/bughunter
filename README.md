# bughunter

把一段报错堆栈交给大模型，让它**自主检索本地代码**后，给出「问题根因（含代码解释）+ 优化建议」的结构化结论。

- **零第三方依赖**：仅用 Python 3 标准库，内网可直接拷贝运行。
- **自写极简工具循环**：约 120 行 function-calling 循环，源码全在项目内、可随意修改，不当 pip 黑盒。
- **支持多种大模型**：走 OpenAI 兼容协议，切换 DeepSeek、通义千问、内网自托管模型只改 `base_url + model`。
- **可替换 LLM 实现**：用 `Protocol` 做防腐层，底层可整体替换而不动核心逻辑。
- **只读沙箱**：检索限制在仓库目录内、不执行任何命令。

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

## 要求

Python 3.10 及以上（`pyproject.toml` 已声明 `requires-python >= 3.10`）。
