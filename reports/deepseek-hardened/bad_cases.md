# Bad cases · deepseek-hardened

### P2 · I-04 · 英文提问、中文回答

- **得分**：0.50（通过门槛 0.80，以下按断言拆开）
- **分类**：`instruction_following` ｜ **期望来源**：`SHIP-2` ｜ **检索命中**：否（top-k: 空）
- **设计意图**：跨境电商客服要求「用户语言 ≠ 回答语言」时不能跟着用户跑。 这条同时暴露检索侧的跨语言短板：英文 query 命中不了中文知识库。
- **用户输入 1**：Please answer in Chinese: how long does delivery take to Shanghai?
- **期望行为**：用中文回答上海次日达（江浙沪皖次日达）。
- **实际回答**：

```text
暂时无法确认上海的具体配送时效。建议您查看订单页的物流信息，或联系人工客服核实。
```
- **失败断言**：

| 断言 | 权重 | 得分 | 证据 | 一票否决 |
|---|---|---|---|---|
| contains_any | 1.5 | 0.00 | 未命中任何一项 ['次日', '2-4天', '江浙沪'] |  |
- **自动归因**：
  - **检索召回缺失**：期望来源 SHIP-2 未进入 top-k；contains_any: 未命中任何一项 ['次日', '2-4天', '江浙沪']
    - 修复建议：做同义词/口语扩展（快递费→运费、钱→退款），降低 min_score 或换 embedding 检索；policy 类问题加一次「按文档标题」的二级检索。
- **复跑命令**：`python -m qaeval run --config configs/baseline.yaml --only I-04`
