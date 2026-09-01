# 游戏对局配置教程

本教程面向普通使用者，说明如何编写 YAML 游戏配置并启动一局对局。

> 以下命令默认在项目根目录执行。当前可运行规则为 Level 0。

## 1. 配置文件的作用

每个 YAML 文件定义一局游戏，包括：

- 玩家和座位顺序；
- 玩家控制器类型；
- LLM 模型、接口地址和 API Key 环境变量；
- 游戏规则版本和随机种子；
- 对局回合上限；
- 运行结果保存目录。

建议复制已有示例后修改，不要直接覆盖原示例：

```powershell
Copy-Item configs/games/example.yaml configs/games/my-game.yaml
```

## 2. 最小配置结构

下面是一局四名随机玩家的完整配置：

```yaml
game_id: my-game-001
experiment_id: local-test-001
seed: 42

players:
  - player_id: player-1
    seat: 1
    controller_type: random_baseline
  - player_id: player-2
    seat: 2
    controller_type: random_baseline
  - player_id: player-3
    seat: 3
    controller_type: random_baseline
  - player_id: player-4
    seat: 4
    controller_type: random_baseline

initial_cash: 1500
max_complete_rounds: 50
rules_version: classic-level0-v1
rules_level: 0
board_data_version: classic-us-40-v1
card_data_version: classic-cards-v1
output_directory: runs
```

启动对局：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/my-game.yaml
```

## 3. 基础字段

| 字段 | 是否必填 | 说明 |
|---|---:|---|
| `game_id` | 是 | 单局名称，也用于生成输出目录。建议每局使用唯一值。 |
| `experiment_id` | 是 | 实验或批次名称，位于输出目录的上一级。 |
| `seed` | 是 | 游戏随机种子，影响骰子、卡牌洗牌和随机玩家行为。 |
| `players` | 是 | 玩家列表，必须包含 2 至 4 名玩家。 |
| `initial_cash` | 否 | 初始现金，默认 `1500`。 |
| `max_complete_rounds` | 否 | 最大完整回合数，默认 `50`。 |
| `rules_version` | 是 | 当前填写 `classic-level0-v1`。 |
| `rules_level` | 是 | 当前必须填写 `0`。 |
| `board_data_version` | 是 | 当前填写 `classic-us-40-v1`。 |
| `card_data_version` | 是 | 当前填写 `classic-cards-v1`。 |
| `output_directory` | 否 | 运行结果根目录，默认 `runs`。 |

## 4. 玩家配置

每名玩家必须有唯一的 `player_id` 和 `seat`：

```yaml
- player_id: player-1
  seat: 1
  controller_type: random_baseline
```

`seat` 只能是 `1` 至 `4`，决定玩家行动顺序。

当前支持的控制器类型：

| `controller_type` | 说明 | 是否需要 LLM |
|---|---|---:|
| `random_baseline` | 从合法候选中随机选择。 | 否 |
| `llm_baseline` | 一个 LLM 直接作出玩家决策。 | 是 |
| `shang_court` | 商代朝廷 Agent。 | 是 |
| `qin_court` | 秦代朝廷 Agent。 | 是 |
| `tang_court` | 唐代朝廷 Agent。 | 是 |
| `ming_court` | 明代朝廷 Agent。 | 是 |

随机玩家不能填写 `model_profile`。

## 5. LLM 玩家配置

### 5.1 普通 LLM 玩家

普通 LLM 玩家使用 `llm_baseline`，并通过 `model_profile` 引用一个模型配置：

```yaml
players:
  - player_id: llm-player
    seat: 1
    controller_type: llm_baseline
    model_profile: player-model

model_profiles:
  player-model:
    provider: openai_compatible
    base_url: https://api.example.com/v1
    api_key_env: MONOPOLY_PLAYER_API_KEY
    model: my-model
    seed: 42
    temperature: 0.2
    max_tokens: 320
    timeout_seconds: 60
```

### 5.2 朝廷 Agent 配置

朝廷玩家不能填写单个 `model_profile`，必须为每个官员指定 profile：

```yaml
players:
  - player_id: tang-court
    seat: 1
    controller_type: tang_court
    court_role_profiles:
      zhongshu: tang-zhongshu
      menxia: tang-menxia
      emperor: tang-emperor
```

然后在 `model_profiles` 中分别定义每个官员：

```yaml
model_profiles:
  tang-zhongshu:
    provider: openai_compatible
    base_url: https://provider-a.example.com/v1
    api_key_env: TANG_ZHONGSHU_API_KEY
    model: model-a
    seed: 42
    temperature: 0.2
    max_tokens: 320

  tang-menxia:
    provider: openai_compatible
    base_url: https://provider-b.example.com/v1
    api_key_env: TANG_MENXIA_API_KEY
    model: model-b
    seed: 42
    temperature: 0.2
    max_tokens: 180

  tang-emperor:
    provider: openai_compatible
    base_url: https://provider-c.example.com/v1
    api_key_env: TANG_EMPEROR_API_KEY
    model: model-c
    seed: 42
    temperature: 0.2
    max_tokens: 320
```

秦、商、明的角色名称如下：

```text
商代：great_priest、emperor
秦代：chancellor、grand_marshal、imperial_counsellor、emperor
唐代：zhongshu、menxia、emperor
明代：chief_grand_secretary、grand_secretary_1、grand_secretary_2、emperor
```

每个玩家或官员可以使用不同的 profile。如果两个角色引用同一个 profile，它们将共享 URL、API Key 环境变量、模型和其他参数。

## 6. API Key 配置

API Key 不能直接写入 YAML。最简单的本地配置方式是在**项目根目录**创建 `.env.local` 文件；程序通过 CLI 启动时会自动读取该文件。

可以先复制项目提供的模板：

```powershell
Copy-Item .env.example .env.local
```

然后编辑 `.env.local`：

```dotenv
MONOPOLY_API_KEY=你的真实APIKey
```

YAML 中只填写对应的环境变量名称：

```yaml
model_profiles:
  player-model:
    provider: openai_compatible
    base_url: https://api.example.com/v1
    api_key_env: MONOPOLY_API_KEY
    model: your-model
```

多个玩家或官员可以共用同一个环境变量，也可以分别设置：

```dotenv
MONOPOLY_SHANG_API_KEY=商代使用的真实APIKey
MONOPOLY_QIN_API_KEY=秦代使用的真实APIKey
MONOPOLY_TANG_API_KEY=唐代使用的真实APIKey
MONOPOLY_MING_API_KEY=明代使用的真实APIKey
```

`.env.local` 已被 `.gitignore` 忽略，不会被正常提交到 Git。`.env.example` 只作为格式模板，不应填写真实密钥。

系统中已经存在的环境变量优先于 `.env.local`，因此也可以临时在当前 PowerShell 中覆盖：

```powershell
$env:MONOPOLY_API_KEY = "临时使用的API Key"
```

不要这样填写：

```yaml
# 错误：禁止在 YAML 中保存明文 API Key
api_key: sk-真实密钥
```

程序不会把 `.env.local` 中的真实 API Key 写入配置快照、调用日志或错误信息。启动命令必须在项目根目录执行，程序才能自动找到项目根目录下的 `.env.local`。

## 7. LLM 参数

每个 `model_profile` 可以独立设置：

| 字段 | 说明 |
|---|---|
| `provider` | 真实接口使用 `openai_compatible`；无网络模拟测试使用 `fake`；固定策略和脚本测试使用 `mock`。 |
| `base_url` | OpenAI 兼容 API 的基地址，程序会请求其 `/chat/completions` 路径。 |
| `api_key_env` | API Key 环境变量名称。 |
| `model` | 供应商使用的模型名称。 |
| `seed` | LLM 随机种子；当前示例统一为 `42`。 |
| `temperature` | 可选采样参数；省略时不发送，由供应商决定默认值。 |
| `max_tokens` | 可选最大输出 token 数；省略时不发送。 |
| `timeout_seconds` | 可选请求超时时间；省略时使用客户端默认值。 |

`seed` 只能帮助部分模型提高可重复性，不能保证所有供应商完全返回相同文本。

## 8. 完整四朝廷示例

项目提供了四朝廷配置示例：

```text
configs/games/example.yaml
```

该文件包含商、秦、唐、明四名玩家，并为 13 名官员分别配置了独立的 URL、API Key 环境变量、模型和 `seed: 42`。

如果只想进行无网络模拟测试，可使用：

```text
configs/games/four_courts_fake_demo.yaml
```

该文件让四个朝廷的所有角色使用 `provider: fake`，不需要 API Key，也不会发送网络请求。

使用真实接口前，需要将 `example.yaml` 中的示例 URL、模型名替换为实际值，并设置对应的环境变量。

## 9. 配置检查与输出

配置加载时会检查：

- 玩家数量和座位号；
- 控制器类型与所需字段；
- profile 是否存在；
- OpenAI 兼容 profile 是否包含 `base_url` 和 `api_key_env`；
- 是否误写明文 `api_key`；
- 规则和数据版本是否受支持。

对局结果默认写入：

```text
runs/<experiment_id>/<game_id>/
```

常见文件：

| 文件 | 内容 |
|---|---|
| `config.json` | 本局实际配置和配置哈希。 |
| `events.jsonl` | 游戏命令及领域事件。 |
| `decisions.jsonl` | 决策请求、候选、响应和校验结果。 |
| `llm_calls.jsonl` | LLM 调用、模型、seed、token 用量和错误。 |
| `result.json` | 最终状态和排名。 |

再次运行时，应修改 `game_id` 或 `experiment_id`，因为程序不会覆盖已有运行目录。

## 10. 常见错误

| 错误 | 处理方式 |
|---|---|
| `players must contain between 2 and 4 entries` | 将玩家数量改为 2 至 4 名。 |
| `player seats must be unique` | 为每名玩家设置不同的座位号。 |
| `model_profile not defined` | 在 `model_profiles` 中增加对应 profile，或修正引用名称。 |
| `requires model_profile` | 为 `llm_baseline` 玩家填写 `model_profile`。 |
| `requires court_role_profiles` | 为朝廷玩家填写全部官员 profile。 |
| API Key 环境变量未设置 | 在项目根目录的 `.env.local` 中填写 YAML 的 `api_key_env` 对应变量，或在当前 PowerShell 中设置该变量。 |
| `no client factory registered for provider` | 检查 `provider` 是否为 `openai_compatible`、`fake` 或 `mock`。 |
| 输出目录已存在 | 修改 `game_id` 或 `experiment_id`。 |
