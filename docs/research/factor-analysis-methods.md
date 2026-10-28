# RD-Agent / Qlib 因子内生分析、统计验证与失败归因

> 研究快照：2026-10-18。本文只讨论研究与观测方案，不建议直接修改当前生产回测链路。

## 结论先行

1. **Alphalens Reloaded 值得接入，但只能作为第一层“单因子体检”。**它原生覆盖多周期远期收益、分位数组合、Spearman Rank IC、分组/组中性分析、分位数换手、因子秩自相关和事件窗口；完整 tear sheet 本身就是 returns、information、turnover 三部分的组合。[0.5.0 `tears.py`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/tears.py) 和 [`performance.py`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/performance.py) 给出了实际计算定义。
2. **它不能替代统计显著性、反数据挖掘和真实交易评估。**Alphalens 的 IC、分位数收益和普通标准误没有自动解决重叠标签造成的序列相关，也没有管理多重检验、Deflated Sharpe Ratio（DSR）、Probability of Backtest Overfitting（PBO/CSCV）、时间序列交叉验证、成交量约束或市场冲击。应把这些作为独立的统计层和组合层。
3. **当前项目的反馈闭环证据过窄。**因子反馈目前只读取 `IC`、含成本年化超额收益和最大回撤三个汇总数，[`feedback.py`](../../rdagent/scenarios/qlib/developer/feedback.py)；`read_exp_res.py` 只导出全部标量指标和 `report_normal_1day.pkl` 对应的 `ret.parquet`，[`read_exp_res.py`](../../rdagent/scenarios/qlib/experiment/factor_template/read_exp_res.py)。这不足以回答“因子为什么失败”。
4. **推荐架构是“Qlib 执行真相 + 隔离分析 sidecar + 持久化试验账本”。**Qlib 继续负责标签、训练、持仓和成交约束；分析 sidecar 读取不可变 Parquet/JSON，先复算 Alphalens 口径，再加 HAC/Fama–MacBeth/FDR/DSR/PBO、分层稳定性和容量压力测试。Langfuse 只保存索引、摘要和决策链，不应取代可复算的数值工件。
5. **不要把最新分析包装进现有 Python 3.10 进程。**截至本快照，Alphalens Reloaded 最新版为 0.5.0（2026-10-17），官方说明其维护范围以兼容性、错误和安全修复为主，新开发转向 ML4T Diagnostic；0.5.0 要求 Python `>=3.11,<3.16`。[0.5.0 发布说明](https://github.com/stefan-jansen/alphalens-reloaded/releases/tag/0.5.0)、[项目 README](https://github.com/stefan-jansen/alphalens-reloaded)、[0.5.0 `pyproject.toml`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/pyproject.toml)。本机和 `local_qlib:latest` 实测均为 Python 3.10，因此应使用固定版本的 Python 3.12 sidecar，而不是升级线上 Qlib 镜像。

## 1. 当前项目已经测了什么，遗漏了什么

当前模板的主链路是：Alpha158 与新因子合并 → Robust Z-score / 填空 → LightGBM → `SigAnaRecord` → `TopkDropoutStrategy` 回测，[`conf_combined_factors.yaml`](../../rdagent/scenarios/qlib/experiment/factor_template/conf_combined_factors.yaml)。标签只有 `Ref($close, -2)/Ref($close, -1) - 1`，即按 Qlib 官方解释，用 T 日收盘可见信息、T+1 买入、T+2 卖出的单一日频标签。[Qlib 数据文档](https://github.com/microsoft/qlib/blob/main/docs/component/data.rst)

Qlib 0.9.8 的 `calc_ic` 明确定义：`IC` 是逐日横截面 **Pearson** 相关，`Rank IC` 是逐日横截面 **Spearman** 相关；`SigAnaRecord` 保存 `ic.pkl`、`ric.pkl`，并记录 IC、ICIR、Rank IC、Rank ICIR。[Qlib 0.9.8 `alpha.py`](https://raw.githubusercontent.com/microsoft/qlib/v0.9.8/qlib/contrib/eva/alpha.py)、[`record_temp.py`](https://raw.githubusercontent.com/microsoft/qlib/v0.9.8/qlib/workflow/record_temp.py)。Alphalens 所称 IC 则是 Spearman Rank IC。[Alphalens 0.5.0 `factor_information_coefficient`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/performance.py)

因此当前最先要消除的语义混淆是：**Qlib `IC` 不能直接与 Alphalens `IC` 比较；应同时展示 `Pearson IC` 与 `Rank IC`，所有面板写清口径。**

当前工作树还有四个直接影响失败归因的缺口：

- 反馈只接收三个标量，没有看到 IC/Rank IC 时间序列、分位数收益形状、覆盖率、换手或子样本表现，[`feedback.py`](../../rdagent/scenarios/qlib/developer/feedback.py)。
- 导出脚本没有把 `pred.pkl`、`label.pkl`、`sig_analysis/ic.pkl`、`ric.pkl` 和 `positions_normal_1day.pkl` 带回工作区，虽然 Qlib 的 `RecordTemp` 已经生成这些工件。[Qlib recorder 官方文档](https://github.com/microsoft/qlib/blob/main/docs/component/recorder.rst)
- 因子去重计算逐日 Pearson 相关的时间均值，然后仅以 `max(corr) < 0.99` 过滤；没有取绝对值，所以接近 -1 的反向重复因子会保留，也没有控制行业、规模或非线性冗余，[`factor_runner.py`](../../rdagent/scenarios/qlib/developer/factor_runner.py)。
- 回测配置有固定开平仓费、最低费用、涨跌停与收盘成交价，但没有 `volume_threshold` 或 `impact_cost`；Qlib `Exchange` 本身支持成交量上限和市场冲击参数，因此当前净收益仍不是容量结论。[当前模板](../../rdagent/scenarios/qlib/experiment/factor_template/conf_combined_factors.yaml)、[Qlib 0.9.8 `Exchange`](https://raw.githubusercontent.com/microsoft/qlib/v0.9.8/qlib/backtest/exchange.py)

## 2. Alphalens 原生能力与边界

| 问题 | Alphalens Reloaded 0.5.0 原生能力 | 仍需补充 |
|---|---|---|
| 数据对齐 | `get_clean_factor_and_forward_returns` 对齐 `(date, asset)` 因子与买入价格，生成多周期远期收益；支持丢失率上限、分位数/固定 bins、分组内分桶和正负值分别分桶。官方特别要求传入与信号可交易时点一致的价格，避免前视或延迟错配。[`utils.py`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/utils.py) | 点时成分股、退市样本、公司行动、停牌/涨跌停、财务数据公告时点、数据版本指纹仍由 Qlib/数据层保证。`filter_zscore` 使用整段远期收益统计时还可能产生前视，生产研究宜关闭后用仅基于当期横截面的规则处理。[同一源码](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/utils.py) |
| 预测力 | 多周期 Rank IC、月度 IC、按组 IC、组中性 IC；因子加权组合收益、alpha/beta；各分位数收益及头尾差。[`performance.py`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/performance.py) | Pearson IC、HAC 显著性、横截面控制变量、增量贡献、跨试验多重检验。 |
| 形状与稳定性 | 分位数收益柱图/小提琴图、月度 IC 热图、按组分析。[API 文档](https://alphalens.ml4trading.io/api-reference.html) | 年度/市场状态/规模/流动性切片、结构突变、滚动置信区间、最差子样本。 |
| 换手与衰减 | 分位数换手、因子秩自相关；多周期远期收益可观察衰减。[`performance.py`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/performance.py) | 实际策略换手、成交失败、交易成本、市场冲击、AUM 容量。 |
| 事件研究 | 事件前后平均累计收益、可分组/组调整。[`create_event_returns_tear_sheet`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/tears.py) | 事件重叠、公告可见时间和选择偏差审计。 |
| 投资组合 | 因子权重和等权分位数组合，可构造美元中性诊断组合。0.5.0 已把头尾分位差统一为 50% 多头/50% 空头、100% gross；旧结果曾放大一倍。[0.5.0 发布说明](https://github.com/stefan-jansen/alphalens-reloaded/releases/tag/0.5.0) | 它的默认多空诊断不是当前 Qlib 长仓 `TopkDropoutStrategy` 的收益对象；不得直接把 tear sheet 的多空收益当成可交易回测。 |

### 版本与替代方案判断

- **Alphalens Reloaded 0.5.0**：刚发布、维护明确、功能稳定，适合做经典 tear sheet 与口径对照；但 Python 3.10 不兼容。0.4.5 虽支持 Python 3.10，[0.4.5 `pyproject.toml`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.4.5/pyproject.toml)，却没有 0.5.0 的分位差杠杆修正，因此不建议为了进程内安装而退回旧版。
- **ML4T Diagnostic 0.1.9**：截至本快照最新发布于 2026-10-15，公开能力包括 HAC-adjusted IC、WalkForward/CPCV、DSR、PBO、FDR、特征漂移与交易诊断，[发布页](https://github.com/ml4t/diagnostic/releases)、[官方能力表](https://github.com/ml4t/diagnostic)。但它仍是 0.1.x，要求 Python `>=3.12,<3.15`，[`pyproject.toml`](https://raw.githubusercontent.com/ml4t/diagnostic/main/pyproject.toml)，且官方明确说它不是 Alphalens 的 drop-in replacement：分桶、按日加权、换手、组中性和 HTML 面板均有定义差异或缺项。[官方迁移指南](https://www.ml4trading.io/docs/diagnostic/user-guide/migration/)

**选择建议：**短期把 Alphalens 0.5.0 作为可解释的经典基准，把少量统计检验自行实现并与 Qlib 交叉核对；中期在同一个 Python 3.12 sidecar 中评估 ML4T Diagnostic 0.1.9，但必须固定版本并做黄金数据回归，不把它直接嵌入线上执行容器。

## 3. 建议的六层因子分析框架

### 第一层：数据与标签审计——先证明“测的是对的”

每个因子、每个日期至少输出：有效股票数、覆盖率、缺失率、有限值率、重复值比例、均值/标准差/分位点、截尾比例，以及按行业/规模/流动性分组的覆盖率。检查：

- 因子时间戳、可见时间、买入价、卖出价与标签窗口是否严格一致；Alphalens 官方输入契约也强调价格应反映信号生成后真正可买到的价格。[`get_clean_factor_and_forward_returns`](https://raw.githubusercontent.com/stefan-jansen/alphalens-reloaded/0.5.0/src/alphalens/utils.py)
- 股票池是否是逐日点时成分，退市和暂停交易证券是否保留，复权与公司行动是否一致。
- 预处理器只在训练段拟合；训练、验证、测试之间不得用全样本分位点或均值。
- 同时构造 1/5/10/20 日标签，并记录是否重叠；单一 1 日标签无法区分“没有预测力”和“预测周期不匹配”。

这一层不过关时，后续任何 IC 和回测都只是在精确测量错误数据。

### 第二层：单因子内生画像——回答“信号在哪里、以什么形状存在”

对每个周期同时计算逐日 Pearson IC 与 Spearman Rank IC，并保存完整时间序列，不只保存均值。报告：均值、中位数、标准差、ICIR、正 IC 比例、滚动均值、月/年热图、分布和极端日贡献。

分位数分析至少包含：

- 每日横截面 5 或 10 分位的等权远期收益、样本数和置信区间；
- 分位数收益的单调性，而不只看 Q5-Q1；
- 头尾两腿分开，避免“只有空头腿有效”被总 spread 隐藏；
- 行业中性、规模中性后的结果，以及行业/市值/流动性分层结果；
- 因子秩自相关、分位数换手与 IC/收益随持有期的衰减曲线。

Alphalens 可直接给出大部分基础面板；Qlib 的报告层也已有 Pearson IC、Rank IC、组合收益、成本、回撤和换手图。[Qlib report 官方文档](https://github.com/microsoft/qlib/blob/main/docs/component/report.rst)

### 第三层：条件与增量价值——回答“它是否只是旧风险暴露的别名”

仅以两因子平均相关系数去重不够。建议每个日期运行横截面回归：

\[
r_{i,t\rightarrow t+h}=a_t+b_t z(f_{i,t})+\gamma_t^{\top}X_{i,t}+\varepsilon_{i,t},
\]

其中 `X` 至少包括行业哑变量、对数市值、波动率、流动性、动量/反转和已有 SOTA 因子。汇总 \(b_t\) 的均值、胜率与稳健标准误。这是 Fama–MacBeth 两阶段思想在横截面因子溢价检验中的直接应用；原始论文以逐期横截面回归检验风险与平均收益关系。[Fama & MacBeth, 1973](https://www.journals.uchicago.edu/doi/10.1086/260061)

还应做三组增量实验：

1. **残差化/partial IC**：先用 `X` 解释因子与标签，再计算残差之间的相关。
2. **模型消融**：在完全相同的训练样本、随机种子和超参数下比较 baseline、baseline+factor、factor-only；报告逐滚动窗差值，而不是仅比较两次总回测。
3. **冗余簇**：用平均绝对 Pearson、平均绝对 Spearman、尾部持仓重合率和条件增益共同判定；正负高度相关都算冗余。

### 第四层：稳健推断与多重检验——回答“这是不是运气”

多日远期收益会重叠，逐日 IC 和组合收益因此存在序列相关。普通 `mean/std` 或独立样本 t 检验会低估不确定性。对每个周期的 IC 均值和 Fama–MacBeth 系数序列使用 Newey–West HAC 标准误，并报告 kernel、lag、有限样本修正和样本数；Newey–West 原论文给出异方差和自相关一致、正半定的协方差估计。[Newey & West, 1987](https://www.nber.org/papers/t0055)。一个可复核的现成实现是 `compute_ic_hac_stats`，其默认 lag 至少覆盖 `label_horizon - 1`。[ML4T Diagnostic HAC 文档](https://www.ml4trading.io/docs/diagnostic/methods/hac-ic/)

RD-Agent 会连续产生并淘汰候选因子，所以“看过多少次测试集”本身就是研究数据：

- 为同一研究目标保存所有得到有效收益/IC 序列的候选，不只保存胜者；稳定 `trial_id` 绑定因子代码 hash、参数、数据指纹、标签、成本和样本窗。
- 对一批因子的 p 值控制 FDR。Benjamini–Hochberg 控制的是被拒绝假设中的预期假发现比例，原始保证针对独立检验；因子高度相关时应明确依赖假设，必要时用更保守的家族错误控制或重采样方法。[Benjamini & Hochberg, 1995](https://rss.onlinelibrary.wiley.com/doi/10.1111/j.2517-6161.1995.tb02031.x)
- 不再使用固定的 “t > 2” 作为新因子门槛。Harvey、Liu 与 Zhu 针对大量因子挖掘指出传统阈值不合理，并提出考虑检验相关性与缺失的多重检验框架。[Harvey, Liu & Zhu, 2016 / NBER working paper](https://www.nber.org/papers/w20592)
- 对被选择策略的 Sharpe 使用 DSR，显式输入尝试次数、非正态性与选择偏差；DSR 的目标正是修正多重试验和非正态导致的绩效膨胀。[Bailey & López de Prado, 2014](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- 用 CSCV 生成多条样本外路径并计算 PBO，观察样本内排名在样本外是否反转；PBO 原论文将其定义为模型无关、非参数的回测过拟合估计框架。[Bailey et al., 2017](https://escholarship.org/uc/item/4w1110bb)

显著性不是收益保证；HAC 也不能修复标签泄漏。统计层只回答“在给定数据与试验历史下，这个结果有多可能是抽样和选择造成的”。

### 第五层：样本外与状态稳定性——回答“它是否只活在某一段历史”

把现有固定 train/valid/test 改造成研究用的外层 walk-forward：训练只使用测试窗之前的数据，标签跨边界的训练样本要 purge，并保留从未用于选择的最终 holdout。Qlib 官方 `RollingGen` 支持 expanding/sliding 任务，并提供 `trunc_days` 以避免测试起点附近的未来信息泄漏。[Qlib `RollingGen` API](https://qlib.readthedocs.io/en/latest/reference/api.html)

若用现成 sidecar，ML4T Diagnostic 的 `WalkForwardCV` 和 `CombinatorialCV` 支持 `label_horizon`、purging 与 embargo；官方同样要求时序排列、不得 shuffle，并强调 splitter 不能证明特征构造本身无泄漏。[交叉验证文档](https://www.ml4trading.io/docs/diagnostic/user-guide/cross-validation/)

每个滚动窗再按以下状态切片，但不应在看完结果后任意发明切片：牛/熊或高/低波动、流动性分位、规模分位、行业、年份、指数成分层级。报告效应方向是否一致、最差窗、窗口间方差和结构突变，不只汇总全期均值。

### 第六层：组合实现与容量——回答“预测力为何没有变成可交易收益”

至少并列三种组合口径：

1. Alphalens 的 50/50 多空诊断组合，用于比较纯信号；
2. 与线上一致的 Qlib `TopkDropoutStrategy`，用于判断真实映射；
3. 因子中性或风险约束组合，用于隔离行业、规模、beta 与集中度。

对 Qlib 回测做参数网格：`topk`、`n_drop`、持有期、成交价、开/平仓费、最低费用、成本倍数、成交量参与率、AUM 和冲击成本。Qlib `Exchange` 已原生读取涨跌停、停牌、成交单位和成交量，并支持 `volume_threshold` 与 `impact_cost`。[Qlib 0.9.8 `Exchange`](https://raw.githubusercontent.com/microsoft/qlib/v0.9.8/qlib/backtest/exchange.py)。因子投资成本研究也强调成本与容量是同一问题，换手、交易规模、证券流动性与再平衡规则都影响可实现的超额收益。[Li, Chow, Pickard & Garg, 2019](https://doi.org/10.1080/0015198X.2019.1567190)

核心输出应包括：gross/net 收益差、break-even 单边成本、换手分布、无法成交比例、平均/峰值参与率、持仓集中度、按 AUM 的净 IR/回撤曲线。固定费率回测只能回答某一费用假设下的结果，不能回答容量。

## 4. 失败归因矩阵

| 观察到的症状 | 优先检查 | 更可能的原因 | 下一步实验 |
|---|---|---|---|
| Pearson IC 与 Rank IC 都接近 0 | 时间戳、标签方向、覆盖率、多周期 IC | 没有信号、预测周期错位、标签错位 | 对齐可交易价；扩展 1/5/10/20D；随机置换作负对照 |
| Pearson 有效、Rank IC 无效 | 极端值贡献、散点与分位数收益 | 少数离群点驱动的线性相关 | 截尾敏感性、Spearman、去掉极端日/股票重算 |
| Rank IC 有效、Pearson 弱 | 分位数形状、非线性 | 单调但非线性，或尺度不稳定 | rank/z-score 变换；分位数与单调模型 |
| 平均 IC 正，但 HAC t 值弱 | IC ACF、重叠周期、有效样本数 | 序列相关导致有效样本远少于天数 | HAC、非重叠采样、块 bootstrap |
| 全样本好，滚动窗/最近期差 | 年度与状态切片、漂移 | regime 依赖、拥挤、数据定义变化 | walk-forward；漂移检测；预注册状态交互 |
| 单因子好，加入 baseline 后无增益 | partial IC、Fama–MacBeth 控制、消融 | 与 Alpha158/SOTA 因子冗余 | 残差化；正负绝对相关聚类；固定模型消融 |
| IC 好，分位数不单调 | 分位数样本数、两腿收益、尾部 | 只在局部区间有效、离群或分桶不稳 | 更多/更少 bins；样条或阈值分析；逐日等权口径 |
| Alphalens 多空好，Qlib TopK 弱 | 组合定义、空头腿、换手、集中度 | alpha 只来自空头腿或组合映射错误 | 多/空腿拆分；复刻 TopK 权重；风险约束组合 |
| 毛收益好，净收益差 | 换手、成本倍数、参与率 | 衰减快、换手高、流动性差 | 持有期/`n_drop` 网格；break-even cost；容量曲线 |
| 样本内胜者明显，滚动样本外排名反转 | 全部候选试验账本 | 选择偏差/回测过拟合 | FDR + DSR + CSCV/PBO；锁定最终 holdout |
| 行业中性后失效 | 行业暴露与分组 IC | 行业择时而非个股选择能力 | 明确将其归为行业因子，或在行业内重新构造 |
| 只有某次代码/数据版本有效 | 数据指纹、代码 hash、随机种子 | 不可复现、缓存污染或数据修订 | 清缓存复跑；冻结环境；黄金数据回归 |

这个矩阵应直接成为 Agent feedback 的结构化输入。LLM 应解释已经计算出的诊断证据，而不是根据三个汇总数猜原因。

## 5. 适合本项目的分阶段最小方案

### 阶段 0：冻结线上链路，建立可复算出口

不改 Qlib 执行逻辑和现有模型依赖，只扩展离线导出。每次 run 生成：

- `run_manifest.json`：`run_id/trial_id`、父基线、因子名与代码 hash、数据指纹、Qlib commit/镜像 digest、Python/依赖、市场/基准、train/valid/test、标签表达式、可交易 lag、策略与成本参数、随机种子；
- `factor_panel.parquet`：`datetime, instrument, factor...`，保留进入预处理前与处理后的值；
- `pred_label.parquet`：预测、原始标签与各远期周期标签；
- Qlib 原生 `ic.pkl/ric.pkl/report_normal/positions_normal/indicators_normal` 的安全格式副本；
- `trial_returns.parquet`：所有被实际评估候选的对齐日收益列，不删除失败者。

验收条件：同一 manifest 可以在干净环境复算相同指标；不同 run 不再靠“最新 recorder”猜测归属。

### 阶段 1：经典单因子报告，先获得可解释性

在固定 Python 3.12 sidecar 中 pin `alphalens-reloaded==0.5.0`，只读阶段 0 的工件。首版输出：

- 1/5/10/20D Pearson IC、Rank IC、月/年热图；
- 5 分位收益、单调性、Q5/Q1 两腿与 100% gross spread；
- 行业中性和按行业、规模、流动性分层；
- 因子秩自相关、分位数换手、持有期衰减；
- 与 Qlib 1D `ic.pkl/ric.pkl` 的逐日误差对账。

验收条件：相同定义下 Alphalens 与 Qlib 的 1D Rank IC 一致；所有图表能追溯到输入 Parquet 与口径版本。

### 阶段 2：统计与增量价值

加入：HAC IC/系数显著性、Fama–MacBeth 控制回归、partial IC、固定模型消融、滚动窗和预注册子样本。建立跨运行 trial ledger，批量输出 raw p、HAC p、FDR q、DSR、有效试验数。

验收条件：任何“接受/替换 SOTA”决策都同时有点估计、稳健不确定性、增量证据和试验历史，不再仅由一次总回测决定。

### 阶段 3：样本外、组合与容量

使用 purged walk-forward；候选成熟后才运行 CSCV/PBO 和最终 holdout。Qlib 回测增加成本倍数、成交量参与率、冲击成本和 AUM 网格，比较 Alphalens 多空诊断与实际 TopK 长仓。

验收条件：给出净收益失效点、容量曲线、PBO/DSR 以及各滚动窗分布；最终 holdout 在锁定因子与参数前从未用于 Agent 反馈。

### 阶段 4：回接 RD-Agent 与 Langfuse

将每层作为独立 observation：`data-audit → signal-diagnostics → conditional-tests → backtest → statistical-validation → decision`。trace 记录 manifest URI、artifact hash、数据指纹、代码 hash 和关键摘要；大表和时间序列留在 Parquet/MLflow。决策 span 至少引用：

- coverage 与异常标志；
- Pearson/Rank IC、HAC t/p、分位数单调性；
- baseline 增量、滚动最差窗；
- gross/net、turnover、break-even cost、容量；
- raw trial count、FDR q、DSR、PBO；
- 明确的失败类别与下一项可证伪实验。

这样 Langfuse 可以回答“Agent 看到了什么、为什么做这个决策”，而数值工件可以回答“这个判断能否独立复算”。

## 6. 实施时必须固定的定义

为了避免得到很多图却无法比较，以下字段应写入 manifest，而不是藏在代码默认值里：

- 因子可见时点、交易时点、买卖价格和 forward-return 起止边界；
- 股票池的点时定义、成分历史、停牌/退市/涨跌停处理；
- 复权方式、缺失处理、截尾/标准化及其拟合窗口；
- Pearson 还是 Spearman、每日等权还是按观察数 pooled；
- 分桶数、分组内分桶、zero-aware 与不足样本的处理；
- 多空 gross、是否行业/市场中性、空头可行性；
- ICIR 与 Sharpe 的年化约定、HAC kernel/lag/修正；
- 试验家族的边界、全部 trial 数、FDR/DSR/PBO 参数；
- 策略、调仓、成本、成交量上限、冲击成本和 AUM。

ML4T Diagnostic 的迁移文档展示了一个关键警告：即使两个库都叫“quantile return”或“turnover”，余数分配、按日加权和 lag 定义也可能不同。[官方迁移指南](https://www.ml4trading.io/docs/diagnostic/user-guide/migration/)。因此每项指标都应有 `metric_definition_version`，升级库后先跑固定黄金面板，再决定是否迁移历史基线。

## 7. 最小决策门而非单一分数

不建议把所有指标压成一个“因子总分”。最小晋级规则应是串联门：

1. 数据契约通过；
2. 预注册主周期的 Rank IC/分位收益方向与经济假设一致；
3. HAC 后仍有统计证据，且 FDR/试验历史已纳入；
4. 控制旧因子和风险暴露后仍有增量；
5. walk-forward 多数窗口方向一致，最差窗口可解释；
6. 与真实 TopK 映射一致，成本与容量压力后仍可接受；
7. 最终 holdout、DSR/PBO 只用于成熟候选的最终资格判断。

阈值应在查看候选结果前按策略目标、样本频率和风险预算预注册。论文给出的经验阈值可作为校准材料，不能代替本项目的经济可行性和样本外验证。

## 主要一手来源

- Alphalens Reloaded：[仓库与维护说明](https://github.com/stefan-jansen/alphalens-reloaded)、[0.5.0 发布说明](https://github.com/stefan-jansen/alphalens-reloaded/releases/tag/0.5.0)、[0.5.0 源码](https://github.com/stefan-jansen/alphalens-reloaded/tree/0.5.0/src/alphalens)。
- Microsoft Qlib：[v0.9.8 发布](https://github.com/microsoft/qlib/releases/tag/v0.9.8)、[Recorder](https://github.com/microsoft/qlib/blob/main/docs/component/recorder.rst)、[Report](https://github.com/microsoft/qlib/blob/main/docs/component/report.rst)、[Exchange](https://raw.githubusercontent.com/microsoft/qlib/v0.9.8/qlib/backtest/exchange.py)。
- ML4T Diagnostic：[仓库](https://github.com/ml4t/diagnostic)、[统计检验](https://www.ml4trading.io/docs/diagnostic/user-guide/statistical-tests/)、[泄漏感知交叉验证](https://www.ml4trading.io/docs/diagnostic/user-guide/cross-validation/)、[迁移边界](https://www.ml4trading.io/docs/diagnostic/user-guide/migration/)。
- 学术原文：[Fama–MacBeth](https://www.journals.uchicago.edu/doi/10.1086/260061)、[Newey–West](https://www.nber.org/papers/t0055)、[Benjamini–Hochberg](https://rss.onlinelibrary.wiley.com/doi/10.1111/j.2517-6161.1995.tb02031.x)、[Harvey–Liu–Zhu](https://www.nber.org/papers/w20592)、[Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)、[Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb)、[Transaction Costs of Factor-Investing Strategies](https://doi.org/10.1080/0015198X.2019.1567190)。
