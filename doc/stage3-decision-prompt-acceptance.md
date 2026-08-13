# Stage 3 决策语句人工验收清单

> 状态：**待人工验收**。本清单冻结当前 Stage 3 的提示词结构与决策类别；在负责人确认措辞、信息范围和候选表达前，不进入 Stage 4 的真实 LLM 接入或提示词冻结。

## 1. 固定提示词模板

每一次决策均按如下结构发送，所有 JSON 使用 UTF-8、缩进和稳定键排序：

```text
你正在代表玩家「{player_id}」参与一局大富翁。

你必须仅根据下方提供的信息，在“合法候选操作”中选择一个操作。不得自行发明操作、修改参数、假设未提供的信息或请求隐藏信息。请选择更有利于本玩家长期净资产和胜利概率的操作。

## 决策上下文
{decision_context_json}

## 可见游戏状态
{visible_state_json}

## 当前决策问题
{question}

## 合法候选操作
{options_json}

## 输出要求
只输出一个 JSON 对象，不要使用 Markdown 代码块，也不要附加额外文本。
{
  "selected_option": "<合法候选项的 option_id>",
  "reasoning": "<简短、可审计的决策理由>"
}
```

## 2. 字段来源与可见性边界

| 区块 | 字段/来源 | AI 可见性 | 人工验收项 |
|---|---|---|---|
| 决策上下文 | `decision_id`、`game_id`、完整轮次、当前行动玩家、回合阶段、决策类别 | 可见 | 标识和阶段是否足够解释当前节点。 |
| 可见游戏状态 | 棋盘版本、当前玩家/阶段/连双数、所有玩家的现金、位置、监狱状态、破产、存活回合、产权位置和手牌数量 | 可见 | 是否符合公开信息规则。 |
| 产权 | 每处可购买格的位置、名称、所有者、建筑等级、抵押状态 | 可见 | 是否需要增加公开估值或租金信息。 |
| 持续效果 | 类型、来源、目标、颜色组、剩余回合 | 可见 | 是否足以判断涨价、查封、同盟影响。 |
| 自己私有状态 | 本人机会卡 ID/名称、本人社区基金出狱卡 ID、本人免租次数 | 仅行动玩家可见 | 命名和数量是否合适。 |
| 其他玩家私有状态 | 其他玩家机会卡具体 ID/名称、社区基金出狱卡具体 ID、免租次数 | **不可见** | 确认抢夺卡只能按目标及公开手牌数量决策。 |
| 引擎内部 | 机会/社区基金抽牌堆顺序、弃牌堆、RNG 状态、克隆预检、重试、回退、运行时错误 | **不可见** | 确认不会泄漏给 AI。 |
| 合法候选操作 | `option_id`、自然语言摘要、效果预览、默认标记；服务器端命令类型和参数不在 Prompt 中展示 | 可见 | 摘要是否足以做出选择。 |

## 3. 固定输出契约

AI 只能输出一个 JSON 对象，且字段必须恰好为：

```json
{
  "selected_option": "合法候选项的 option_id",
  "reasoning": "1 至 400 个字符的理由"
}
```

- `selected_option` 必须匹配当次候选项；服务器将其映射为已冻结的命令和参数。
- 不接受 `parameters`、额外字段、Markdown、多个 JSON 对象或非 JSON 文本。
- 非法响应或连续三次连接失败时，运行器记录私有审计信息，并执行当次默认合法项。

## 4. 决策类别及实际问题句

| 决策类别 | 触发条件 | 实际问题句 | 候选来源 | 默认项 |
|---|---|---|---|---|
| 掷骰 | `ROLLING` 且玩家未被监禁 | `请选择掷骰以继续本回合。` | 掷骰 | 第一项合法候选，通常为掷骰。 |
| 监狱 | `ROLLING` 且玩家监狱状态非 free | `你正在监狱中；请选择支付罚款或掷骰尝试出狱。` | 掷骰、现金足够时缴纳 50 元罚款、持有时使用社区基金出狱卡 | 第一项合法候选，通常为掷骰。 |
| 资产管理 | `ASSET_MANAGEMENT` | `请选择一项资产管理操作，或结束本回合。` | 结束回合、可建造/出售/抵押/赎回的地产、持有机会卡的弃置与使用 | `end_turn`。 |
| 付款处置 | `PAYMENT_RESOLUTION` | `你需要支付 {amount}；请处置资产或宣告破产。` | 宣告破产、可出售建筑、可抵押地产 | 第一项合法候选，通常为宣告破产。 |
| 免租选择 | `CARD_RESOLUTION` | `请选择使用免租机会，或放弃免租并正常支付租金。` | 使用或放弃免租 | 第一项合法候选。 |
| 回合收束 | `TURN_COMPLETE` | `请选择掷骰以继续本回合。`（当前实现复用该句） | 仅结束回合 | `end_turn`。**需人工确认是否改为“本回合已结束，请确认结束回合。”** |

## 5. 候选操作表达清单

当前摘要由命令类型生成。带目标的机会卡候选会使用稳定 option ID，但当前中文摘要为通用“执行 {命令} 操作。”。这是人工验收的重点：确认是否接受，或要求 Stage 3 补充目标、金额和效果的自然语言表达。

| 命令 | 当前摘要 | 参数在服务器端冻结 | 备注 |
|---|---|---|---|
| `RollDice` | `掷骰继续本回合。` | 无 | 可能产生随机移动。 |
| `PayJailFine` | `支付 50 元罚款并出狱。` | 无 | 仅监狱中且现金足够。 |
| `UseCommunityGetOutOfJailCard` | `执行 UseCommunityGetOutOfJailCard 操作。` | `card_id` | 建议人工确认改为“使用出狱卡出狱”。 |
| `EndTurn` | `结束本回合。` | 无 | 资产管理或回合收束节点。 |
| `DeclareBankruptcy` | `宣告破产。` | 无 | 仅付款处置。 |
| `Build` | `执行 Build 操作。` | `position` | 建议呈现地产名称、费用和建筑等级。 |
| `SellBuilding` | `执行 SellBuilding 操作。` | `position` | 建议呈现地产名称、出售收入和建筑等级。 |
| `Mortgage` | `执行 Mortgage 操作。` | `position` | 建议呈现地产名称和获得资金。 |
| `RedeemMortgage` | `执行 RedeemMortgage 操作。` | `position` | 建议呈现地产名称和赎回金额。 |
| `DiscardChanceCard` | `执行 DiscardChanceCard 操作。` | `card_id` | 建议呈现本人卡牌名称。 |
| `UseChanceCard` | `执行 UseChanceCard 操作。` | 卡牌与目标字段 | 建议按卡牌效果展开自然语言。 |
| `ResolveRent(True)` | `使用免租机会。` | `use_waiver=true` | 仅有可用免租机会时合法。 |
| `ResolveRent(False)` | `放弃免租并正常支付租金。` | `use_waiver=false` | 付款金额目前在问题句中不展示。 |

## 6. 代表性渲染样例

### A. 普通掷骰

```text
## 当前决策问题
请选择掷骰以继续本回合。

## 合法候选操作
[
  {
    "effect_preview": {},
    "is_default": true,
    "option_id": "roll_dice",
    "summary": "掷骰继续本回合。"
  }
]
```

### B. 监狱（持有出狱卡）

```text
## 当前决策问题
你正在监狱中；请选择支付罚款或掷骰尝试出狱。

## 合法候选操作
[
  {"option_id": "roll_dice", "summary": "掷骰继续本回合。", "is_default": true, "effect_preview": {}},
  {"option_id": "pay_jail_fine", "summary": "支付 50 元罚款并出狱。", "is_default": false, "effect_preview": {}},
  {"option_id": "use_community_get_out_of_jail_card-community-jail-free", "summary": "执行 UseCommunityGetOutOfJailCard 操作。", "is_default": false, "effect_preview": {}}
]
```

### C. 资产管理

```text
## 当前决策问题
请选择一项资产管理操作，或结束本回合。

## 合法候选操作
[
  {"option_id": "end_turn", "summary": "结束本回合。", "is_default": true, "effect_preview": {}},
  {"option_id": "build-1", "summary": "执行 Build 操作。", "is_default": false, "effect_preview": {}},
  {"option_id": "mortgage-1", "summary": "执行 Mortgage 操作。", "is_default": false, "effect_preview": {}}
]
```

### D. 付款处置

```text
## 当前决策问题
你需要支付 200；请处置资产或宣告破产。

## 合法候选操作
[
  {"option_id": "declare_bankruptcy", "summary": "宣告破产。", "is_default": true, "effect_preview": {}},
  {"option_id": "sell_building-3", "summary": "执行 SellBuilding 操作。", "is_default": false, "effect_preview": {}},
  {"option_id": "mortgage-5", "summary": "执行 Mortgage 操作。", "is_default": false, "effect_preview": {}}
]
```

### E. 免租

```text
## 当前决策问题
请选择使用免租机会，或放弃免租并正常支付租金。

## 合法候选操作
[
  {"option_id": "resolve_rent-True", "summary": "使用免租机会。", "is_default": true, "effect_preview": {}},
  {"option_id": "resolve_rent-False", "summary": "放弃免租并正常支付租金。", "is_default": false, "effect_preview": {}}
]
```

### F. 机会卡目标

```text
## 合法候选操作
[
  {
    "option_id": "use_chance_card-chance-steal-b",
    "summary": "执行 UseChanceCard 操作。",
    "is_default": false,
    "effect_preview": {}
  }
]
```

抢夺卡候选只暴露目标玩家，不暴露其持有的具体机会卡；成功时由引擎按目标手牌顺序确定转移的卡牌。候选还可涵盖目标玩家、颜色组、单一地产或双地产组合，取决于卡牌效果；所有目标均先通过引擎克隆预检。

## 7. 人工验收结论

请逐项确认：

- [ ] 固定角色指令和“长期净资产/胜率”优化目标合适。
- [ ] 公开/私有信息范围合适，尤其是抢夺卡不泄露他人手牌内容。
- [ ] 每种问题句的措辞合适；确认是否修改 `TURN_COMPLETE` 的复用问题句。
- [ ] 当前通用命令摘要是否可接受。
- [ ] 是否要求在 Stage 3 内补全机会卡、地产、现金和效果的中文候选摘要与 `effect_preview`。
- [ ] 输出 JSON 契约、400 字理由限制、非法输出/连接失败回退策略合适。
- [ ] 在本清单签字或明确修改项前，不进入 Stage 4 真实 LLM 接入。
