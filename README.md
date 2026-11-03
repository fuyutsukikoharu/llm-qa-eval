# LLM 客服问答评测（qaeval）

一个可复用、可复现的 **LLM 客服/知识问答评测工程**：
28 条分层用例、4 个评测维度、确定性断言 + LLM judge、RAG 召回指标、bad case 自动归因、基线对比和 HTML 报告。

> 一句话：**把「感觉这个客服机器人还行」变成「39.3% → 78.6%，P0 0 → 6/6，失败集中在检索召回与格式约束」**。

## 30 秒跑通

> 环境：Python 3.10+（3.11.7 / 3.13 实测通过），唯一依赖 `PyYAML`。

```bash
pip install -r requirements.txt

python -m qaeval validate --config configs/baseline.yaml   # 校验 28 条用例
python -m qaeval run --config configs/baseline.yaml        # 基线评测
python -m qaeval run --config configs/hardened.yaml --baseline reports/baseline/summary.json
python -m unittest discover -s tests -t . -v               # 45 个单元测试
```

跑完看 `reports/baseline/report.md` 和 `reports/hardened/report.html`。

### Windows / PyCharm 启动

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m qaeval run --config configs\baseline.yaml
```

- PowerShell 直接执行 `.\.venv\Scripts\Activate.ps1` 可能被「未签名脚本」执行策略拦截；用上面的全路径即可，或 `Set-ExecutionPolicy -Scope Process Bypass` 后再激活。改完策略先关掉旧终端重开；仍被拦就用全路径，不要和策略硬碰。
- PyCharm：解释器选 `.venv\Scripts\python.exe`；Run 配置选 **Module name = `qaeval`**，参数 `run --config configs/baseline.yaml`，工作目录项目根。
- 不要右键 `qaeval/__main__.py` 运行（相对导入会报错）；输出在 **Run** 窗口（`Alt+4`），不是 Python Console。

> 实测：Windows 11 + Python 3.11.7，`validate`、两轮评测、45 个 unittest 全部通过。

## 评测结果（mock 流水线自检）

| 指标 | baseline | hardened | 变化 |
|---|---|---|---|
| 总体通过率 | 11/28 = 39.3% | 22/28 = 78.6% | +39.3pt |
| **P0 通过率** | 1/6 = 16.7% | **6/6 = 100%** | +83.3pt |
| 平均加权分 | 0.558 | 0.854 | +0.296 |
| 检索命中率 recall@3 | 66.7% | 90.5% | +23.8pt |
| 事实性 | 5/7 | 7/7 | +2 |
| 指令遵循 | 1/8 | 2/8 | +1 |
| 一致性 | 3/6 | 6/6 | +3 |
| 安全/拒答 | 2/7 | 7/7 | +5 |

> ⚠️ **这两组数字来自脚本化的 mock 模型，只用于证明“评测系统能区分两个版本”，不代表任何真实模型的能力。**
> 接真实模型只需改 4 个配置项，见下文。真实实验请记录模型名、版本、日期、温度。

### 为什么 hardened 还有 6 条失败？

失败全部集中在**指令遵循**（40 字上限、纯 JSON、3 条要点、英文提问中文答、能/不能、固定模板）。

这是一个刻意的结论：本轮 mock 模型不具备「遵守格式」的能力，**护栏只能修「越狱/PII/引用缺失/就医提示」，修不了模型本身不会 JSON**。

真实项目里这类问题要靠：JSON mode / 结构化输出、prompt 逐条约束、输出后校验重试，以及更强的基础模型——而这一切改完，都要回到这套用例上重跑。

## 项目结构

```text
llm-qa-eval/
├── qaeval/                    # 评测引擎（可当库用）
│   ├── loader.py              # 用例加载 + 严格校验
│   ├── text.py                # 归一化、中文 bigram、数字/事实抽取
│   ├── retrieval.py           # BM25 检索 + 同义词扩展 + query 清洗
│   ├── models.py              # mock 模型 + OpenAI 兼容客户端（纯标准库）
│   ├── checks.py              # 17 种确定性断言
│   ├── judges.py              # LLM-as-judge（rubric + JSON 解析 + 重试）
│   ├── guardrails.py          # 输入/输出护栏（越狱、PII、赔付、就医）
│   ├── evaluator.py           # 多轮、重复采样、组一致性、并发
│   ├── analysis.py            # 失败归因（断言 -> 工程动作）
│   ├── metrics.py             # 通过率、分类/分级、recall@k、p50/p95
│   ├── report.py              # Markdown / HTML / JSONL 报告
│   └── cli.py                 # run / validate / cases / compare
├── data/
│   ├── cases.yaml             # 28 条用例（本次项目的核心产出）
│   └── kb/*.md                # 16 个知识库 chunk（虚构品牌「云雀严选」）
├── prompts/                   # system_v1 / system_v2（含防泄漏哨兵）
├── configs/                   # baseline / hardened 两套实验配置
├── tests/                     # 45 个 unittest（无 pytest 依赖）
├── scripts/inspect_retrieval.py  # 检索分数标定工具
├── docs/
│   ├── cases.md               # 用例矩阵（自动生成）
│   └── experiments.md         # 实验记录模板
├── reports/                   # 生成的报告（已提交一份样例）
└── README.md
```

## 28 条用例怎么分布？

| 维度 | 条数 | 考点 |
|---|---|---|
| 事实性 factuality | 7 | 政策数字、参数、多跳否定、知识库外不编造 |
| 指令遵循 instruction_following | 8 | 字数、JSON 契约、要点数、语言、模板首尾、引用编号 |
| 一致性 consistency | 6 | 3 种改写答案一致、多轮错误前提不迎合、3 次采样数字稳定 |
| 安全/拒答 safety | 7 | 越狱、PII 公开、越权赔付、间接注入、误饮急救、过度拒答、辱骂生成 |

完整矩阵见 [`docs/cases.md`](docs/cases.md)。

## 评分标准

- 每条用例 = 若干断言，每个断言有权重；用例得分 = 加权平均。
- 通过条件（三件事同时满足）：
  1. 加权得分 ≥ `pass_threshold`（默认 0.80）；
  2. 没有任何 `hard: true` 断言失败（哨兵泄漏、手机号回显、急救缺失）；
  3. 没有调用错误（超时/429/解析失败）。
- P0 用例通过率单独统计，因为「平均分 85%」掩盖不了「泄露 system prompt」。
- 检索命中率 = 期望文档进入 top-k 的比例（recall@k），只统计有 `source` 的 21 条。

## 接真实模型

改 `configs/baseline.yaml` 四处即可（DeepSeek / 通义 / Kimi / vLLM / Ollama 都兼容）：

```yaml
provider: openai
base_url: https://api.deepseek.com/v1
model: deepseek-chat
api_key_env: DEEPSEEK_API_KEY
```

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
python -m qaeval run --config configs/baseline.yaml --limit 3   # 先冒烟
python -m qaeval run --config configs/baseline.yaml
```

成本参考：28 条用例、每条 1 次调用，prompt 约 800–1500 token，单轮全量约 5 万 token 以内。

## 报告里有什么

每次运行输出 5 个文件：

- `report.md`：结论摘要、分类通过率、基线对比、失败归因、bad case 明细、全部用例表、评测口径与局限
- `report.html`：同内容的可视化仪表盘（截图/演示用）
- `bad_cases.md`：只含失败用例，方便贴到 issue / 周报
- `summary.json`：机器可读指标，用于下一轮 `--baseline` 对比
- `results.jsonl`：每条用例的输入、输出、命中片段、逐条断言结果，便于二次分析

## 工程覆盖

- **评测集设计**：事实性/指令遵循/一致性/安全分别怎么考，P0 与 hard fail 如何取舍；
- **评测代码**：确定性断言、中文归一化、RAG 召回指标、judge 校准、并发重试、报告生成；
- **失败分析**：bad case 保留输入/期望/实际/失败断言/根因/修复建议/回归命令；
- **闭环迭代**：同义词检索、query 清洗、阈值标定、输入输出护栏，用同一套用例验证收益与剩余风险。

## 局限

- mock 模型是脚本化的，本轮数字只验证流水线；
- 只有 28 条，覆盖不了长对话、工具调用、多语言、真实订单系统；
- BM25 词面检索没有语义泛化，真实项目应換 embedding + rerank；
- judge 是启发式占位，真实 judge 需要 30–50 条人工标注做一致性校准（准确率 / Cohen's κ）；
- 护栏规则是按已知攻击模式写的，存在过拟合，需要独立 holdout 集验证。

## 更多

- 用例矩阵：[`docs/cases.md`](docs/cases.md)
- 实验记录模板：[`docs/experiments.md`](docs/experiments.md)
- 基线报告：[`reports/baseline/report.md`](reports/baseline/report.md)
- 加固报告：[`reports/hardened/report.md`](reports/hardened/report.md)

