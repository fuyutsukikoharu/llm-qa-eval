# 实验记录

> 规则：每次「值得对比」的实验都记一行。没有模型版本/日期/配置的实验结果，两周后无法解释。

## 记录表

| 日期 | 配置 | 被测模型 | 温度 | 检索 | 护栏 | 通过率 | P0 | recall@3 | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| 2026-11-02 | `baseline.yaml` | mock-baseline（脚本化） | 0.2 | BM25, min_score=3.0, 无同义词 | 关 | 39.3% (11/28) | 1/6 | 66.7% | 流水线自检基线 |
| 2026-11-02 | `hardened.yaml` | mock-baseline（脚本化） | 0.2 | BM25 + 同义词 + query 清洗 | 开 | 78.6% (22/28) | 6/6 | 90.5% | 剩余 6 条均为指令遵循 |
| TODO | `baseline.yaml` | 例：deepseek-chat | 0.2 | 同上 | 关 | — | — | — | 接真实模型后填写 |
| TODO | `hardened.yaml` | 例：deepseek-chat | 0.2 | 同上 | 开 | — | — | — | 同一模型做 A/B |

## 一次实验必须记录的信息

- [ ] 模型名 + 版本（API 返回字段）
- [ ] prompt 版本（`prompts/*.md`，建议记 git hash）
- [ ] 温度、max_tokens
- [ ] 检索参数：top_k、min_score、是否同义词
- [ ] 护栏开关
- [ ] judge 配置（provider/model，是否校准）
- [ ] 是否人工复核过 bad case
- [ ] 报告路径（`reports/<run>/report.md`）

## 关于 mock 数字

上表前两行来自脚本化 mock 模型，仅用于验证评测流水线能区分两个版本，**不代表任何真实模型能力**。
真实实验请把 mock 行替换掉，并在 README / 实验记录中同步更新数字，避免把演示数据当成模型结论。
