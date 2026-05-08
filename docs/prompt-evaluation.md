# Prompt Evaluation Guide

本文档说明如何评估股票分析提示词优化前后的能力变化。当前评测链路是离线规则评测，不会调用真实 LLM，适合在本地和 CI 中做 prompt regression gate。

## 评测目标

提示词优化不只看报告是否“更好看”，而是优先衡量：

- JSON 输出是否稳定、可解析、字段完整
- 是否减少编造价格、财报、新闻、机构观点等 unsupported facts
- 数据缺失时是否明确说明无法判断
- 新闻时间是否符合近 N 日窗口约束
- 结论是否能回指输入依据
- 风险和信号冲突是否被明确处理
- 在不编造事实的前提下，是否保留观察区间、触发条件、失效条件和仓位节奏等可执行计划
- 当输入已有价格、均线、高低点时，是否避免“有价无计划”的空泛输出

## 样例集

默认样例位于：

```bash
tests/fixtures/prompt_eval/cases.jsonl
```

每条样例描述一个评测场景，例如：

- 技术数据缺失但有新闻冲突
- 新闻需要带日期且不能超出窗口
- 缺少价格依据时不得输出具体买入价、止损价、目标价
- 有明确价格、均线、高低点、平均成本等输入依据时，应给出条件型交易计划，而不是泛泛写“无法判断”
- `sniper_points` 应避免全部写“数据缺失，无法判断”；至少保留一个观察区间、一个触发条件和一个失效条件

保存模型响应时，将不同 prompt 版本的输出放到目录中：

```bash
tests/fixtures/prompt_eval/responses/baseline/<case_id>.json
tests/fixtures/prompt_eval/responses/candidate/<case_id>.json
```

## 运行评测

只评估一个版本：

```bash
python scripts/evaluate_prompt_outputs.py \
  --responses tests/fixtures/prompt_eval/responses/candidate \
  --min-average 80
```

对比旧版和新版：

```bash
python scripts/evaluate_prompt_outputs.py \
  --baseline tests/fixtures/prompt_eval/responses/baseline \
  --responses tests/fixtures/prompt_eval/responses/candidate \
  --min-average 80
```

输出会包含每个 case 的总分、分项分和问题列表，并给出候选版本相对 baseline 的平均分差值。

## 评分维度

满分 100：

- Format 25：JSON 可解析、无 Markdown fence、必填字段齐全
- Factuality 35：不编造事实、正确处理数据缺失、新闻日期合规
- Analysis 30：风险字段完整、能处理信号冲突、结论有依据表达
- Compliance 10：`decision_type` 保持 `buy|hold|sell`，不出现确定性承诺

## 上线建议

上线前建议同时满足：

- 新 prompt 平均分不低于旧 prompt
- JSON 解析和字段完整性不退化
- 幻觉、超窗新闻、无依据价格点位明显减少
- 至少人工抽查 5-10 条真实 Qwen 输出，确认报告没有变成空泛模板

如果后续接入 LLM-as-judge，建议保留这套规则评测作为底线 gate，再用人工或 judge 评估“分析质量”和“表达可读性”。
