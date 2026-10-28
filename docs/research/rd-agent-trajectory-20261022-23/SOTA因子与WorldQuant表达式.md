# 10-22~23 运行最终收录的因子及 WorldQuant BRAIN 表达式

运行：WSL `log/2026-10-22_16-11-01-956423`（美股 S&P500）。最终 SOTA 由 Loop 2、7、10、15、32 采纳的 **10 个新因子**组成，每次回测时还会和 20 个 Alpha158 基线特征（RESI5、WVMA5、RSQR5、KLEN、RSQR10、CORR5、CORD5、CORR10、ROC60、RESI10、VSTD5、RSQR60、CORR60、WVMA60、STD5、RSQR20、CORD60、CORD10、CORR20、KLOW）一起输入 LightGBM。下面只翻译这 10 个新因子。

## 翻译依据与核对方式

- 表达式根据各因子 workspace 中**实际执行的 `factor.py`** 翻译，不依据 LLM 写的公式说明（两者有细节差异，例如 Loop 32 的高低价相等时取 0.5）。
- 核对脚本：[independent-checks/wq_translation_check.py](independent-checks/wq_translation_check.py)。它用 pandas 按 BRAIN 算子的语义逐个实现下列表达式，再和 RD-Agent 产出的 `result.h5` 比较。2020–2022 每日截面 Spearman 相关，**10 个因子全部为 1.0000**。
- 这只验证了**代数等价**，没有在 BRAIN 平台上实际跑过。以下几处需要你在 BRAIN 文档里确认：
  - `ts_regression` 的 `rettype` 编号：这里按 1 = 截距、2 = 斜率、4 = SSE 使用；
  - `group_median` 在你的账户中是否可用；
  - `max` / `min` 作为逐元素多参数算子是否可用。

## 建议的 BRAIN 设置

Region `USA`，Universe `TOP500`（最接近 S&P500），**Delay 1**（RD-Agent 的标签是 `close(t+2)/close(t+1)-1`，用 t 日数据、t+1 日收盘建仓，对应 Delay 1）。原系统不做中性化，可以先选 `None` 对照，再试 `Market`/`Industry`。

## 10 个因子

| # | 因子名 | 来源 | 含义 |
|---:|---|---|---|
| 1 | CrossSectionalRankAmihudDollarVolume20D | Loop 2 | 20 日 Amihud 非流动性（按美元成交额） |
| 2 | MA5ATRNormalizedCloseToHigh10D | Loop 2 | 收盘价距 10 日最高价的回撤，除以 ATR20，再取 5 日均值 |
| 3 | MA5OvernightGap | Loop 2 | 5 日平均隔夜跳空 |
| 4 | CrossSectionalRankShortTermReversal10D | Loop 7 | 10 日反转 |
| 5 | CrossSectionalRankMA5IntradayReturn | Loop 7 | 5 日平均日内收益 |
| 6 | CrossSectionalRankCloseLocation5D | Loop 10 | 5 日平均收盘位置（收盘价在当日高低区间中的位置） |
| 7 | CrossSectionalRankVolumeWeightedReturn5D | Loop 10 | 5 日成交量加权收益 |
| 8 | CrossSectionalRankDownsideDeviation20D | Loop 10 | 20 日下行波动 |
| 9 | CrossSectionalRankVolatilityScaledResidualMomentum10D | Loop 15 | 市场模型残差动量 / 残差波动 |
| 10 | CrossSectionalRankRidgeResidualVolatilityScaled3D | Loop 32 | 名为“岭回归残差”，实际近似为当日收益剔除同期量价变量后的学生化残差（见下文） |

### 1. AmihudDollarVolume20D

```
rank(ts_mean(abs(returns) / (close * volume), 20))
```

### 2. MA5ATRNormalizedCloseToHigh10D

```
tr = max(high - low, abs(high - ts_delay(close, 1)), abs(low - ts_delay(close, 1)));
ts_mean((close / ts_max(high, 10) - 1) / ts_mean(tr, 20), 5)
```

⚠ **量纲错误**：分子是无量纲比例，分母 ATR 却以美元计，因子值实际上被 1/股价 缩放。实测这个因子的绝对值与 1/close 的截面 Spearman 相关为 **0.86**，与修正量纲后的版本只有 0.50 相关（[proxy_check.py](independent-checks/proxy_check.py)）。它在很大程度上是一个“低价股”因子。如果想要原意，建议改成：

```
tr = max(high - low, abs(high - ts_delay(close, 1)), abs(low - ts_delay(close, 1)));
ts_mean((close / ts_max(high, 10) - 1) / (ts_mean(tr, 20) / close), 5)
```

### 3. MA5OvernightGap

```
ts_mean(open / ts_delay(close, 1) - 1, 5)
```

### 4. ShortTermReversal10D

```
rank(-(close / ts_delay(close, 10) - 1))
```

### 5. MA5IntradayReturn

```
rank(ts_mean(close / open - 1, 5))
```

### 6. CloseLocation5D

```
rank(ts_mean((close - low) / (high - low), 5))
```

`high == low` 时原代码会得到 NaN/inf（被排除在排名外），BRAIN 中的行为可能不同。如有需要，可以改成 `if_else(high > low, (close - low) / (high - low), 0.5)`。

### 7. VolumeWeightedReturn5D

```
rank(ts_sum(volume * returns, 5) / ts_sum(volume, 5))
```

### 8. DownsideDeviation20D

```
rank(ts_std_dev(min(returns, 0), 20))
```

### 9. VolatilityScaledResidualMomentum10D

市场收益用当日截面中位数收益；在 20 日窗口内做 OLS `r = α + β·m + ε`，用这个窗口估出的 α、β 计算最近 10 日的残差之和，再除以 20 日残差标准差。

```
m = group_median(returns, market);
a = ts_regression(returns, m, 20, lag = 0, rettype = 1);
b = ts_regression(returns, m, 20, lag = 0, rettype = 2);
sse = ts_regression(returns, m, 20, lag = 0, rettype = 4);
rank((ts_sum(returns, 10) - 10 * a - b * ts_sum(m, 10)) / sqrt(sse / 19))
```

注意：不能写成 `ts_sum(ts_regression(..., rettype = 0), 10)`。那样每天的残差会用各自不同窗口的 α、β，和原因子不等价。如果没有 `group_median`，可以用 `group_mean(returns, 1, market)` 近似（均值代替中位数，结果会有差异）。

### 10. RidgeResidualVolatilityScaled3D（Loop 32）

**先说结论：这个因子和它的名字/说明不符。** 代码用 20 日窗口，把当日收益 `r` 回归到 4 个标准化预测变量 `[r, 成交量变化, 5 日波动, 20 日收盘位置]` 上。**`r` 自己就是第一个预测变量**，所以这不是“岭回归预测后的残差”。推导可得：

- 残差 = σ_r · λ · z_t · w，其中 `w = (19·R + 0.1·I)⁻¹` 的第一列，R 是 4 个变量的 20 日相关矩阵；
- 除以残差波动后，因子 = `z_t·w / sqrt(wᵀRw)`；
- λ = 0.1 相对于 n-1 = 19 很小，`R⁻¹` 的第一列正比于 `(1, -γ)`，其中 γ 是窗口内用另外 3 个变量回归 `r` 的 OLS 系数。所以这个因子**近似等于：把当日收益对当日成交量变化、5 日波动、20 日收盘位置做窗口内 OLS，得到的当日学生化残差**，再取 3 日排名均值。它没有用到任何“预测”，并且 3 个解释变量与 r 同期。

用简单近似 `ts_mean(rank(ts_zscore(returns, 20)), 3)` 去对，相关只有 0.57，说明另外 3 个变量的剔除作用不小。

`(19R + 0.1I)⁻¹` 的第一列与 `(R + kI)` 的伴随矩阵第一列（k = 0.1/19）只差一个正的标量倍数，而这个倍数会在比值中约掉。因此可以用余子式写成精确的 Fast Expression（已在 pandas 中对拍，相关 1.0000）：

```
x1 = returns;
x2 = volume / ts_delay(volume, 1) - 1;
x3 = ts_std_dev(returns, 5);
x4 = ts_mean(if_else(high > low, (close - low) / (high - low), 0.5), 20);
r12 = ts_corr(x1, x2, 20); r13 = ts_corr(x1, x3, 20); r14 = ts_corr(x1, x4, 20);
r23 = ts_corr(x2, x3, 20); r24 = ts_corr(x2, x4, 20); r34 = ts_corr(x3, x4, 20);
d = 1 + 0.1 / 19;
w1 = d * (d * d - r34 * r34) - r23 * (r23 * d - r34 * r24) + r24 * (r23 * r34 - d * r24);
w2 = -(r12 * (d * d - r34 * r34) - r13 * (r23 * d - r34 * r24) + r14 * (r23 * r34 - d * r24));
w3 = r12 * (r23 * d - r24 * r34) - r13 * (d * d - r24 * r24) + r14 * (d * r34 - r23 * r24);
w4 = -(r12 * (r23 * r34 - r24 * d) - r13 * (d * r34 - r24 * r23) + r14 * (d * d - r23 * r23));
num = w1 * ts_zscore(x1, 20) + w2 * ts_zscore(x2, 20) + w3 * ts_zscore(x3, 20) + w4 * ts_zscore(x4, 20);
q = w1 * w1 + w2 * w2 + w3 * w3 + w4 * w4
    + 2 * (r12 * w1 * w2 + r13 * w1 * w3 + r14 * w1 * w4 + r23 * w2 * w3 + r24 * w2 * w4 + r34 * w3 * w4);
ts_mean(rank(num / sqrt(q)), 3)
```

不建议把它当作有效 alpha 提交。它被采纳时的 +0.99pp 收益落在噪声幅度以内（见 [轨迹缺陷分析与修改建议.md](轨迹缺陷分析与修改建议.md) §4.3）。

## 方向（符号）提示

BRAIN 默认做多因子值高的股票。RD-Agent 把这些因子作为 LightGBM 的特征使用，不需要决定方向，所以原系统里**没有方向信息**。下表给出它们在 S&P500、2-日标签上的单因子 Rank IC（[single_factor_ic.json](independent-checks/single_factor_ic.json)）。全部 |t| < 2，验证期和选择期的方向也经常相反，所以这只能作参考：

| 因子 | 2018–19 Rank IC | 2020–22 Rank IC |
|---|---:|---:|
| AmihudDollarVolume20D | -0.0039 | -0.0024 |
| MA5ATRNormalizedCloseToHigh10D | +0.0011 | +0.0059 |
| MA5OvernightGap | -0.0016 | -0.0089 |
| ShortTermReversal10D | +0.0081 | +0.0011 |
| MA5IntradayReturn | -0.0033 | -0.0080 |
| CloseLocation5D | -0.0030 | -0.0026 |
| VolumeWeightedReturn5D | -0.0015 | -0.0115 |
| DownsideDeviation20D | +0.0050 | -0.0128 |
| VolatilityScaledResidualMomentum10D | -0.0009 | +0.0013 |
| RidgeResidualVolatilityScaled3D | +0.0053 | -0.0020 |

如果在 BRAIN 中模拟出来的 Sharpe 为负，在表达式外加一个负号即可。根据上面的单因子检验，预期这些因子单独模拟时都偏弱。
