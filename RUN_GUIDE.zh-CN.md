# RD-Agent 本机运行指南（Windows + WSL2）

## 已完成的环境

- Windows：Docker Desktop 29.6.2，Linux 容器引擎已验证。
- WSL：Ubuntu 22.04、Python 3.10.12。
- Python 虚拟环境：`/home/yifei/.venvs/rdagent`。
- WSL 原生源码：`/home/yifei/projects/RD-Agent-codex-repro`。
- 固定提交：`484776c211e4fbbeef03e0ec00d6bbee7362a4f4`（项目 1.0.0）。
- Python 运行时依赖已按 `constraints/3.10.txt` 完整安装，项目为 editable 安装。
- LiteLLM 固定为 `1.96.2`，避开 `1.97.0` 在 Python 3.10/3.11 上的响应类型错误。

项目官方只支持 Linux。请把 WSL 原生源码作为实际运行目录；不要直接在 Windows PowerShell 中运行 RD-Agent。原始 Windows 目录保留作参考，WSL 原生目录能避免 `/mnt/c` 上大量小文件造成的严重性能损失。

Windows 资源管理器可用以下地址访问实际运行目录：

```text
\\wsl$\Ubuntu-22.04\home\yifei\projects\RD-Agent-codex-repro
```

## 每次运行前

1. 启动 Docker Desktop，等左下角显示 Engine running。
2. 打开 Ubuntu 22.04（WSL）终端。
3. 执行：

```bash
cd ~/projects/RD-Agent-codex-repro
source scripts/activate_wsl.sh
```

看到项目路径、Python 3.10 和 Docker 版本后即可继续。

## 填写模型配置

打开 WSL 项目中的 `.env`：

```bash
nano .env
```

模型和接口已经配置为：

- Chat：OpenRouter 的 `~deepseek/deepseek-pro-latest`（LiteLLM 完整名称为 `openrouter/~deepseek/deepseek-pro-latest`）；
- Embedding：硅基流动的 `BAAI/bge-m3`（`EMBEDDING_MODEL=litellm_proxy/BAAI/bge-m3`）。不要用仓库示例里的 `BAAI/bge-large-en-v1.5`：它最多只接受 512 token，`fin_factor` 写入编码知识库时会把因子代码和反馈整体做 embedding，硅基流动对超长输入只返回 `code 20015 The parameter is invalid`，RD-Agent 识别不出这是超长错误，重试 10 次后中断。`bge-m3` 支持 8192 token，向量维度同为 1024。
- 因子执行器：WSL 用 venv 而非 Conda 时，`fin_factor` 会直接用当前 venv 的 Python 运行生成的因子代码。
- 生成模型的执行器：`MODEL_COSTEER_ENV_TYPE=docker`，使用已经验证的 `local_qlib:latest`，不依赖 WSL 中另建 Conda 环境。
- `QLIB_DOCKER_BUILD_FROM_DOCKERFILE=false`：重复评估时复用上述镜像，避免每轮都重新构建；仅修改 Qlib Dockerfile 后才需要临时改回 `true`。

当前本机 `.env` 已填写并通过接口检查。以后若重建环境，只需把下面两个占位值替换成真实密钥：

```dotenv
OPENROUTER_API_KEY=replace-me-openrouter
LITELLM_PROXY_API_KEY=replace-me-siliconflow
```

保留 `.env` 中其余模型名称、API Base 和兼容变量不变。项目必须同时具备：

- Chat Completion；
- JSON mode；
- Embedding。

`.env` 已被 Git 忽略。不要把真实密钥提交到 Git，也不要发到聊天或截图中。

## 打开 Langfuse Agent Chain

项目已接入 Langfuse，但默认关闭。先在 [Langfuse Cloud](https://cloud.langfuse.com) 创建项目，然后进入项目的 `Settings -> API Keys` 创建一对项目密钥。

在 WSL 项目的 `.env` 中填写：

```dotenv
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-你的项目公钥
LANGFUSE_SECRET_KEY=sk-lf-你的项目私钥
LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
LANGFUSE_OTEL_HOST=https://us.cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=development
LANGFUSE_CAPTURE_CONTENT=false
LANGFUSE_SAMPLE_RATE=1.0
LANGFUSE_LINK_PROMPTS=true
```

`LANGFUSE_BASE_URL` 和 `LANGFUSE_OTEL_HOST` 必须与项目所在区域一致：

- 美国区：`https://us.cloud.langfuse.com`
- 欧洲区：`https://cloud.langfuse.com`
- 日本区：`https://jp.cloud.langfuse.com`
- 私有部署：填写自己的 Langfuse 根地址

运行 `fin_factor`、`fin_model`、`fin_quant` 或 `general_model` 后，在 Langfuse 的 `Traces` 中可以查看完整层级：任务、实验阶段、代码演化、评估步骤和每次模型调用；`Sessions` 会按同一个 RD-Agent 日志目录归组运行及续跑记录。

`LANGFUSE_CAPTURE_CONTENT=false` 是生产安全默认值。此时仍记录 Agent Chain、模型、Token、费用、耗时、错误、因子/代码 hash、Qlib 指标、工件 URI 和采纳决策，但不上传 Prompt、Response、假设及因子公式正文。

只有在使用公开或已经脱敏的数据进行开发调试时，才临时改成：

```dotenv
LANGFUSE_CAPTURE_CONTENT=true
```

密钥只填写在 `.env`，不要发到聊天中。

### 在 Langfuse 面板查看 Prompt

`LANGFUSE_LINK_PROMPTS=true`（默认开启）时，RD-Agent 用到的每个 YAML 提示词模板都会自动登记到 Langfuse 左侧的 **Prompts** 页面：

- 名称按模板位置分文件夹，例如 `scenarios.qlib.prompts:hypothesis_gen.system_prompt` 对应 `rdagent/scenarios/qlib/prompts/hypothesis_gen.system_prompt`，并带 `rdagent` 标签。
- 只有模板文本改动后才会新建版本，最新版本带 `latest` 标签；不会改动 `production` 标签。
- 每次模型调用（`GENERATION`）会关联到其 system prompt 对应的模板版本：在 Trace 里点开调用可跳到该 Prompt；在 Prompt 的 `Metrics` 页可看到各版本的调用次数、耗时、Token 和费用。调用实际用到的全部模板记录在调用的 `metadata.prompt_templates` 中。
- 模板是仓库代码，不含实验数据；渲染后的完整提示词仍在每次调用的 Input 中（受 `LANGFUSE_CAPTURE_CONTENT` 控制）。

不想写入 Prompts 页面时设为 `LANGFUSE_LINK_PROMPTS=false`。

Python SDK 固定为 `langfuse 4.18.0`：SDK v5 需要 Python 3.11+，而本环境是 Python 3.10。读取轨迹统一使用 Observations API v2：`api.observations.get_many(trace_id=..., fields="core,basic,io,usage")`。不要再使用 `api.trace.get/list` 或 `api.sessions`；Langfuse 面板中的 **Migrate APIs** 是近期调用记录，修正代码后不会立刻消失，但不应再出现新的调用次数。

RD-Agent 使用子进程执行实验步骤。当前实现通过 W3C Trace Context 显式传递父级上下文，并使用 `spawn` 创建干净的遥测进程，防止模型调用成为孤立 trace。每个 `execute-experiment` observation 还会记录可用的 IC、Rank IC、ICIR、回测收益/回撤、成本拖累、缓存命中、因子代码 hash 和本地工件 URI；`evaluate-experiment` 记录采纳结果。大表和时间序列仍保留在 Parquet/MLflow，不上传到 Langfuse。

下面的 smoke test 不调用模型，只创建一个安全的父子轨迹，并通过 Observations API v2 回读。输出只含 ID、名称、类型和层级，不打印正文：

```bash
dotenv run -- python -m rdagent.app.utils.langfuse_verify --smoke
```

## 验证环境

不调用模型、只验证 Docker 和端口：

```bash
rdagent health_check --no-check-env
```

填好 `.env` 后做完整检查（会各发起一次很小的 chat 和 embedding 请求）：

```bash
rdagent health_check
```

正常标准：Docker status normal、19899 端口可用、Chat test passed、Embedding test passed。

额外依赖检查：

```bash
python -m pip check
```

## 建议先跑的最小场景

用一篇公开论文测试“读取论文并实现模型”的完整链路：

```bash
rdagent general_model "https://arxiv.org/pdf/2210.09789"
```

这不是轻量命令：会调用模型、产生 API 费用，并可能构建或拉取 Docker 镜像。第一次运行明显更慢。

量化场景按需求选择：

```bash
rdagent fin_factor   # 因子演化
rdagent fin_model    # 模型演化
rdagent fin_quant    # 因子 + 模型联合演化
```

不加限制时会一直循环。只跑一轮完整流程（提出假设 → 生成因子代码 → Qlib 回测 → 反馈）用 `--loop-n`（注意是连字符，`--loop_n` 会报错）；按时长限制用 `--all-duration 2h`：

```bash
rdagent fin_factor --loop-n 1
```

已验证一轮 `fin_factor` 约 17 分钟、模型费用约 0.13 美元。

### 数据与市场

- **数据：** `~/.qlib/qlib_data/cn_data` 现在是 [chenditc/investment_data](https://github.com/chenditc/investment_data) 2026-10-18 版（交易日 2000-01-04 至 2026-10-16，含 CSI300/500/800/1000 成分股历史和 SH000300、SH000905 等指数）。下载包已用 GitHub 记录的 SHA-256 校验。旧数据保留在 `cn_data_2020_backup`。
- **市场和基准可配置：** `.env` 中 `QLIB_FACTOR_MARKET` / `QLIB_FACTOR_BENCHMARK`（模型场景用 `QLIB_MODEL_*`）。当前为 `csi500` / `SH000905`；改回沪深 300 用 `csi300` / `SH000300`。
- **因子源数据：** `FACTOR_CoSTEER_SOURCE_DATA_INSTRUMENTS=csi800`，即 CSI300 + CSI500 成分股。换数据或换股票池后，把 `git_ignore_folder/factor_implementation_source_data*` 两个目录移走，下次运行会自动重新生成。

### 实验严谨性设置（已修正）

- **选因子期 / 保留期：** 训练 2010–2017，验证 2018–2019，选因子期（框架回测的 test 段）2020–2022，**保留期 2023-01 至 2026-10**。框架只在选因子期回测并据此采纳因子，保留期只用于最终一次性检验；不要根据保留期结果回头改因子。
- **同一套预处理：** `conf_combined_factors.yaml` 已补上与基线相同的 `RobustZScoreNorm` + `Fillna`，基线和"基线 + 新因子"的差异只来自因子本身。
- **回测缓存：** 缓存键包含回测日期、市场、基准、基础特征和 qlib 数据指纹（日历文件的大小和修改时间），改设置或换数据后不会再复用旧结果。因子代码的执行缓存也包含源数据文件指纹。日历修复前的旧缓存已改名为 `*.stale-before-calendar-fix`。
- **已知限制：** 回测用统一的 9.5% 涨跌停阈值。创业板（2020-08 起）和科创板是 20%，这些股票单日涨跌超过 9.5% 时会被误判为涨跌停、无法成交；CSI500 中这类股票较多，结果会有一定偏差。
- **未来函数检查：** 框架本身不检查 LLM 写的因子代码是否用了未来数据。评估时应把因子代码在截断到某日的数据上重跑，截断日之前的值必须与全量数据结果完全一致，否则判定为使用了未来数据。

## 查看运行日志

运行任务后，在另一个已经激活环境的 WSL 终端中启动 Streamlit：

```bash
rdagent ui --port 19899 --log-dir <任务输出的日志目录>
```

数据科学场景增加 `--data-science`：

```bash
rdagent ui --port 19899 --log-dir <日志目录> --data-science
```

然后在 Windows 浏览器打开 `http://127.0.0.1:19899`。

## 停止与退出

- 前台任务：按 `Ctrl+C`。
- 退出虚拟环境：`deactivate`。
- 不使用时可退出 Docker Desktop，释放内存。

## 更新项目

环境绑定的是 WSL 原生副本，因此请在该目录更新：

```bash
cd ~/projects/RD-Agent-codex-repro
git status
git pull --ff-only origin main
source ~/.venvs/rdagent/bin/activate
python -m pip install -e . -c constraints/3.10.txt
```

更新前先确认 `git status` 没有需要保留但尚未提交的修改。

## 常见问题

### 回测只覆盖 2020 年，或改了 `QLIB_FACTOR_TEST_END` 后回测报 `IndexError: index 486 is out of bounds`

qlib 回测使用 `~/.qlib/qlib_data/cn_data/calendars/day_future.txt` 作为交易日历。本机数据里该文件原本只有 2020-01-02 至 2021-12-31，导致回测被截断：默认的"2017–2020"回测实际只跑了 2020-01 至 2020-07 的 140 个交易日，测试期结束早于 2020 时则直接报错。已把它重建为 `day.txt` 全部交易日加上原有未来日期（原文件备份为 `day_future.txt.bak-partial-2020`）。重新下载 qlib 数据后需检查：

```bash
wc -l ~/.qlib/qlib_data/cn_data/calendars/day.txt ~/.qlib/qlib_data/cn_data/calendars/day_future.txt
```

`day_future.txt` 的行数应不少于 `day.txt`。

### 多轮运行中途报 `This request requires more credits, or fewer max_tokens`

OpenRouter 余额不足。未设置 `CHAT_MAX_TOKENS` 时，每次请求按模型上限（131072 个输出 token）预留额度，余额较低时即使实际用量很小也会被拒绝。请到 OpenRouter 充值；也可以在 `.env` 设置 `CHAT_MAX_TOKENS=32768` 降低每次预留额度。

### Docker 拉取时报 `docker-credential-desktop.exe: exec format error`

本机 WSL 的 Windows 可执行文件互操作存在异常。`source scripts/activate_wsl.sh` 会自动把 `DOCKER_CONFIG` 指向项目内的独立配置，公共镜像不再调用该助手。不要跳过激活脚本。

### 命令显示 `rdagent: command not found`

重新执行：

```bash
cd ~/projects/RD-Agent-codex-repro
source scripts/activate_wsl.sh
```

### 完整健康检查提示没有有效配置

说明 `.env` 尚未填写或变量名不正确。先运行 `rdagent health_check --no-check-env` 区分系统环境问题与模型凭证问题。

### 报错 `Message is not fully defined`

这是 LiteLLM `1.97.0` 在 Python 3.10/3.11 上的已知问题，不是模型名或 API Key 错误。恢复项目约束中的兼容版本：

```bash
source ~/.venvs/rdagent/bin/activate
python -m pip install -c constraints/3.10.txt litellm==1.96.2
```

### 19899 端口被占用

换一个端口，例如：

```bash
rdagent ui --port 19900 --log-dir <日志目录>
```

### 需要 Kaggle / 数据科学场景

除了模型密钥，还需要 Kaggle API 凭证、已接受比赛规则的数据集，以及场景专用环境变量。先确保上面的通用健康检查通过，再按仓库 `README.md` 的 Data Science / Kaggle 小节配置，避免把通用环境问题和数据权限问题混在一起排查。
