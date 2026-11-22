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

- **数据（2026-11-21 起，所有实验统一快照）：** `~/.qlib/qlib_data/cn_data` 是指向 `cn_data_20261025` 的软链接，内容为 [chenditc/investment_data](https://github.com/chenditc/investment_data) release 2026-10-25 的 `qlib_bin.tar.gz`（569,210,863 字节，SHA-256 `0a2974da97b4737e503ecddeea8bb3b2179e0e16ea0fb19fa373ff14155bf315`，已校验；交易日 2000-01-04 至 2026-10-23，`day_future.txt` 到 2026-12-31；instruments 含 all/csi300/csi500/csi800/csi1000/csiall 历史成分，point-in-time）。manifest 存于数据目录 `qlib_bin.manifest.json`。字段：open/high/low/close/vwap 为复权价，`$factor` 为复权因子，`$volume`=成交手数/factor，`$amount` 单位千元；没有市值、换手率、ST、涨跌停标记，停牌日为缺失行。换数据只需改这个软链接（如以后的 AlphaData cn_2026.11，届时单位不同），并把 `git_ignore_folder/factor_implementation_source_data*` 移走让 `daily_pv.h5` 重新生成。此前的 10-18 版保留在 `cn_data_20261018`（对应 `daily_pv.h5` 在 `*.20261018_backup`），更早的在 `cn_data_2020_backup`。
- **市场和基准可配置：** `.env` 中 `QLIB_FACTOR_MARKET` / `QLIB_FACTOR_BENCHMARK`（模型场景用 `QLIB_MODEL_*`）。当前为 `csi500` / `SH000905`；改回沪深 300 用 `csi300` / `SH000300`。
- **因子源数据：** `FACTOR_CoSTEER_SOURCE_DATA_INSTRUMENTS=csi800`，即 CSI300 + CSI500 成分股。换数据或换股票池后，把 `git_ignore_folder/factor_implementation_source_data*` 两个目录移走，下次运行会自动重新生成。

### 实验严谨性设置（已修正）

- **选因子期 / 保留期：** 训练 2010–2017，验证 2018–2019，选因子期（框架回测的 test 段）2020–2022，**保留期 2023-01 至 2026-10**。框架只在选因子期回测并据此采纳因子，保留期只用于最终一次性检验；不要根据保留期结果回头改因子。
- **同一套预处理：** `conf_combined_factors.yaml` 已补上与基线相同的 `RobustZScoreNorm` + `Fillna`，基线和"基线 + 新因子"的差异只来自因子本身。
- **回测缓存：** 缓存键包含回测日期、市场、基准、基础特征和 qlib 数据指纹（日历文件的大小和修改时间），改设置或换数据后不会再复用旧结果。因子代码的执行缓存也包含源数据文件指纹。日历修复前的旧缓存已改名为 `*.stale-before-calendar-fix`。
- **已知限制：** 回测用统一的 9.5% 涨跌停阈值。创业板（2020-08 起）和科创板是 20%，这些股票单日涨跌超过 9.5% 时会被误判为涨跌停、无法成交；CSI500 中这类股票较多，结果会有一定偏差。
- **未来函数检查：** 框架本身不检查 LLM 写的因子代码是否用了未来数据。评估时应把因子代码在截断到某日的数据上重跑，截断日之前的值必须与全量数据结果完全一致，否则判定为使用了未来数据。

### 美股（S&P 500）

- **数据：** `~/.qlib/qlib_data/us_data`，由 `tools/us_sp500/build_us_sp500.py` 构建（构建步骤和数据来源见 `tools/us_sp500/README.md`）。交易日 2005-01-03 至 2026-10-20，S&P 500 **历史成分股**（每个日期当时的成员），价格来自 Yahoo，Yahoo 已没有的退市公司用 qlib 官方美股包（到 2020-11）补齐；改名（如 FB→META）按收益率自动识别并合并。
- **基准：** `^SP500TR`（含分红的全收益指数）。股票收益含分红，用价格指数 `^GSPC` 会把超额收益高估约一个股息率（2020–2022 约 1.4 个百分点）。`^GSPC` 也在数据里。
- **运行：** 美股参数放在 `rdagent/scenarios/qlib/market_profiles/us_sp500.profile`，在 `.env` 之上加载即可，不用改 `.env`（shell 中的变量优先于 `.env`）：

  ```bash
  set -a; source rdagent/scenarios/qlib/market_profiles/us_sp500.profile; set +a
  rdagent fin_factor --loop-n 1
  ```

  关掉终端或新开终端后就回到 A 股设置。
- **美股设置：** 区域 `us`、股票池 `sp500`、无涨跌停、买卖各 0.05%（无印花税，近似点差和冲击成本）、无最低佣金；划分与 A 股相同：训练 2010–2017、验证 2018–2019、选因子期 2020–2022、保留期 2023-01 起。因子编写用的源数据在 `git_ignore_folder/factor_implementation_source_data_us`，与 A 股分开。
- **已知偏差：** 免费数据拿不到 2020-11 之后退市公司的价格，存在幸存者偏差。成分股价格覆盖率：训练期约 76–93%，选因子期约 91–95%，保留期约 96–99%（每半年的明细在 `us_data/coverage_report.json`）。缺的多是后来被收购或破产的公司，回测收益会偏高，越早的年份越明显。
- **基线参考（20 个基础特征，2020–2022，扣费）：** IC 0.0029，Rank IC 0.0061，相对 `^SP500TR` 年化超额 8.8%，最大回撤 −26.9%。IC 接近 0 而超额不低，主要来自 50 只股票等权与市值加权指数的差异和幸存者偏差，不代表预测能力；判断新因子应看相对基线的变化。

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

## 统一对比实验设定（2026-11-21 起，RD-Agent R&D-Factor 作为基线）

- **数据**：`~/.qlib/qlib_data/cn_data` → `cn_data_20261025`（chenditc/investment_data 2026-10-25，SHA-256 前 12 位 `0a2974da97b4`）。因子源数据 `FACTOR_CoSTEER_SOURCE_DATA_INSTRUMENTS=all`（官方默认全市场，三个股票池共用），`git_ignore_folder/factor_implementation_source_data/daily_pv.h5` 截为 2014-07-01–2021-12-31（2026-11-21，6,267,279 行、4,754 只；官方 generate.py 是 2008-12-29 至数据末尾，完整版备份在 `factor_implementation_source_data.all_20081229_20261023_backup`）。原因：统一规则要求挖掘期间不读测试期与 2026 年数据，2014-07–2015-12 为长窗口缓冲；全量文件在 10 GB WSL 内存下第 2 轮 OOM 卡死。核对：重叠段数值逐一相同；2016–2021 的 20 日动量逐值相同，250 日波动率只差浮点舍入（相对 1e-14），三个股票池 2016–2021 成分股 100% 覆盖。窗口超过约 370 个交易日或从数据开头累积的因子在 2016 年初会与全量版不同。提示词中的"Full Execution Dataset"概况随之显示截后的范围。调试样本（2018–2019，100 只）不变。只用量价数据；论文附录 C.2 提到的 Wind 基本面字段不在开源实现和论文附录 E.1 的提示词中，未使用。
- **股票池**：`market_profiles/cn_csi300.profile`、`cn_csi500.profile`、`cn_csi1000.profile`（基准 SH000300 / SH000905 / SH000852），历史成分股。
- **划分**：train 2016-01-01–2020-12-31，valid 2021-01-01–2021-12-31，RD-Agent 选因子段（test）2021-01-01–2021-12-31；挖掘不读 2022 年以后。2022-01-01–2025-12-31 只在最后统一评测时使用。
- **标签**：`Ref($close, -2)/Ref($close, -1) - 1`（1 日，官方原样）。
- **模型**：DeepSeek 开放平台 `deepseek/deepseek-flash`（2026-11 时为 DeepSeek-V4.1-Flash-1017），`REASONING_EFFORT=none`（关闭思考），`CHAT_TEMPERATURE=1.0`，`CHAT_MAX_TOKENS=8192`。API 没有 seed，三次重复的差异来自 LLM 采样。Embedding：硅基流动 `BAAI/bge-m3`。
- **预算**：每次运行 `--loop-n 36`（论文表 9 R&D-Factor GPT-4o 6 小时 36 轮），可加 `--all-duration 24h` 作为防卡死上限；记录实际用时、轮数和候选因子数。
- **运行**：`source ~/.venvs/rdagent/bin/activate; set -a; source .env; source rdagent/scenarios/qlib/market_profiles/cn_csi300.profile; set +a; rdagent fin_factor --loop-n 36`，每次重复用新的日志目录，`USE_CHAT_CACHE` 保持关闭。只有进程因运行环境原因中断（内存不足被杀等）时，才用 `rdagent fin_factor --path log/<目录>/__session__/<轮>/<步>` 从最后一个断点续跑，中断的那一步重新执行；若中断前的轮次触发过后来修复的缺陷，则该次重复从头重跑。

与官方 1.0.0（`484776c2`）的差异：
- 保留（缺陷修复与运行配套，2026-11-21 逐项核对）：
  - 训练日志正则（`experiment/workspace.py`）：官方历史模板本来就有 `Training Log: {{ experiment.stdout }}` 并要求分析训练问题，但官方正则只匹配 PyTorch，LightGBM 日志被滤空；修复后历史记录中能看到 LightGBM 训练行。论文结果是在日志为空时跑的，此项让 LLM 比论文多看到这部分信息。
  - 2026-11-22 修复两处会让运行崩溃或丢轮次的缺陷：
    - 组合因子 parquet 列名转为普通 `str`（`developer/factor_runner.py`）。LLM 写的因子有时以 `np.str_` 命名列，宿主机 numpy 2 把它写成 `np.str_('x')`，Docker 里 qlib 读取时 `ast.literal_eval` 报 `malformed node`，该轮被跳过（2026-11-21 CSI300 r1 第 0、1、5、7 轮）。只改列名类型，因子值和列名文字不变。
    - 向量化输入超长：官方在接口报超长时截断一次再重试，但只识别 OpenAI 的报错文字。硅基流动返回 `code 20015`，未被识别，重试 10 次后崩溃（CSI300 r1 第 17 轮）。现在把 `20015` 也认作超长（`oai/backend/base.py`）。另外官方用通用 tokenizer 截到 8191×0.9，bge-m3 自己的计数更多，截断后仍被拒；官方报错提示调小 `embedding_max_length`，但已知模型并不读取这个设置。现在已知模型的上限取两者较小值（`oai/utils/embedding.py`），`.env` 设 `EMBEDDING_MAX_LENGTH=5500`。截断只在接口拒绝之后发生，之前能正常向量化的文本不受影响。
  - 轮次编号两阶段一致、因子名全部重复时跳过本轮、提示词里的股票池名随设置变化、缓存键包含数据与配置指纹、mlflow 文件损坏重跑一次、venv 运行环境、Langfuse 追踪与续跑。
  - 提示词信息（核对后不确定是否算方法改动，按修改后版本保留）：提想法时另列全部已接受实验；设计实验时另列历史因子（轮次、是否接受、公式）；所有环节附"实际执行协议"（股票池、基准、各时间段、标签、预处理、模型、组合规则）；完整执行数据概况与调试数据覆盖统计。
  - 评估器调参：LightGBM 参数（learning_rate 0.01、colsample_bytree 1.0、num_leaves 64，官方 0.2/0.8879/210 会 1–5 棵树早停）；新因子与基线一样做 RobustZScoreNorm（截断极端值）+ Fillna（官方组合因子配置不做）。
  - `reasoning_effort` 允许 `none`（关闭思考，`llm_conf.py`、`backend/litellm.py`）。
- 恢复官方：因子数据说明 README（含 `$factor` 原文与无附加限制）、提示词示例标签 `[Machine Learning based Factor]`、由 LLM 判断是否接受因子（`QLIB_FACTOR_ACCEPTANCE_POLICY` 未设置即为 llm）；2026-11-21 又恢复三项：反馈提示词去掉 Current/SOTA Training Log 两行、反馈指标去掉 Rank IC（回到 IC/年化超额/最大回撤三项）、数据字段说明恢复为每次运行随机打乱一次顺序（`experiment/utils.py`）。
- 运行记录（2026-11-22）：第一批 `log/formal_20261121/` 中 CSI500 r1 跑满 36 轮，保留（上述两个缺陷未触发）；CSI300 r2 第 4 轮因上一次运行残留进程占内存被杀，从断点续跑（前 4 轮未触发缺陷）；CSI300 r1 触发了两个缺陷，从头重跑；CSI1000 r1 跑到第 22 轮内存不足，断点保留，CSI1000 三次重复延后再跑。
- 官方原有、保持不变：跑满 15 轮后 RAG 提示改为尝试机器学习类因子（`factor_proposal.py`）。
- 因子执行超时 `FACTOR_CoSTEER_FILE_BASED_EXECUTION_TIMEOUT=600`（论文附录 C.1 实现单元 600 秒；官方代码默认 3600）。超时后该次执行算失败，CoSTEER 最多重写 10 次，仍失败则丢弃该因子；整轮因子都失败时本轮记为失败，进入下一轮。
