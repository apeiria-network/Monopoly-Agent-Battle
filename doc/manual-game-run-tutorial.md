# 手动运行游戏与查看结果

本教程说明如何使用仓库中的 YAML 配置启动一局游戏，并查看运行结果、过程记录和回放验证信息。

> 以下命令默认在仓库根目录执行，且项目依赖已经可用。当前可运行规则为 Level 0。LLM 可使用本地 `mock` 或 `fake` provider，也可通过环境变量注入 API Key 使用 `openai_compatible` provider。配置文件编写方法见 [game-config-tutorial.md](game-config-tutorial.md)。

## 快速开始

本节通过一个完整示例，演示如何从配置文件启动一局游戏，以及如何定位和检查本局产生的运行结果。示例使用四名随机玩家，最多运行 10 个完整回合。配置会另存为新的文件，运行结果也写入新的目录，因此不会覆盖仓库中的示例或已有记录。

### 1. 准备配置

先复制仓库提供的随机玩家配置：

```powershell
Copy-Item configs/games/random_baseline_demo.yaml configs/games/quickstart_random.yaml
```

将 `configs/games/quickstart_random.yaml` 中的运行标识和回合上限改为：

```yaml
game_id: quickstart-random
experiment_id: quickstart-001
max_complete_rounds: 10
```

其余字段沿用示例配置即可。该配置包含四名 `random_baseline` 玩家，每名玩家的初始现金为 `1500`，随机种子为 `42`。各配置项的含义和可选值见“完整配置结构”。

### 2. 启动对局

在仓库根目录执行：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/quickstart_random.yaml
```

程序会根据 YAML 创建本局游戏，并自动推进玩家回合。对局在完成 10 个完整回合，或只剩一名未破产玩家时结束。命令成功完成后，本局运行目录为：

```text
runs/quickstart-001/quickstart-random/
```

运行产物的目录结构如下：

```text
runs/quickstart-001/quickstart-random/
├── config.json       # 本局实际使用的配置和配置哈希
├── events.jsonl      # 命令产生的领域事件，按顺序记录
├── decisions.jsonl   # 控制器在决策点的请求、响应和校验结果
├── result.json       # 对局结束时的状态快照
├── runtime.jsonl     # 重试、裁剪等运行时审计信息
└── llm_calls.jsonl   # LLM 调用记录；本例为纯随机局，不会生成
```

其中，`result.json` 用于查看最终状态，`events.jsonl` 用于追踪对局过程，`decisions.jsonl` 用于检查控制器在决策点执行了什么操作。只要对局包含普通 LLM 玩家或朝廷 Agent，就会生成 `llm_calls.jsonl`。

### 3. 查看终局结果

读取本局的 `result.json`：

```powershell
Get-Content runs/quickstart-001/quickstart-random/result.json -Raw
```

可以重点关注以下字段：

| 字段 | 含义 |
|---|---|
| `status` | 对局状态。正常结束时为 `completed`。 |
| `end_reason` | 结束原因。本示例通常为 `round_limit`。 |
| `complete_rounds` | 已完成的完整回合数，本示例应为 `10`。 |
| `rankings` | 终局排名。 |
| `players` | 每名玩家的现金、位置、地产和监狱状态。 |
| `properties` | 每块地产的所有者、建筑层数和抵押状态。 |
| `llm_calls` | LLM 调用次数。纯随机局应为 `0`。 |

### 4. 查看对局过程

需要核对购地、建房、卖楼或抵押等过程时，读取事件文件：

```powershell
Get-Content runs/quickstart-001/quickstart-random/events.jsonl
```

也可以只筛选关注的事件类型：

```powershell
Select-String -Path runs/quickstart-001/quickstart-random/events.jsonl -Pattern 'property_purchased|building_added|building_sold'
```

### 5. 验证运行结果

如需确认运行产物中的命令、事件和终局状态彼此一致，可以执行回放验证：

```powershell
@'
from pathlib import Path
from monopoly_agent_battle.game.replay import verify_run

verify_run(Path("runs/quickstart-001/quickstart-random"))
print("回放验证通过")
'@ | .\.venv\Scripts\python.exe
```

输出“回放验证通过”表示验证成功。运行目录不会被覆盖；再次运行时，请修改 `experiment_id` 或 `game_id`，例如将 `quickstart-001` 改为 `quickstart-002`。

## 1. 命令与配置文件

完整对局使用：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config <配置文件路径>
```

例如：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/random_baseline_demo.yaml
```

`play` 会读取配置并推进游戏，直到只剩一名未破产玩家，或达到 `max_complete_rounds` 的完整回合上限。对局启动后全程无人值守，中途不需要任何输入。

上面是**前台运行**，运行期间不能关闭终端（关闭或 Ctrl+C 会中断对局）。若希望关掉终端后仍继续运行，可**脱离终端后台运行**，把输出重定向到日志：

```powershell
Start-Process -FilePath ".\.venv\Scripts\monopoly-agent-battle.exe" `
  -ArgumentList 'play','--config','configs/games/random_baseline_demo.yaml' `
  -RedirectStandardOutput 'runs\bg-stdout.log' `
  -RedirectStandardError  'runs\bg-stderr.log' `
  -WindowStyle Hidden -PassThru
```

记下返回的进程 `Id`（PID）；用 `Get-Content runs\bg-stdout.log -Wait` 实时查看，用 `Stop-Process -Id <PID>` 停止。中途停止会产生不完整的废局，需改 `game_id`/`experiment_id` 后重跑。

项目还提供 `demo` 命令：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe demo --config configs/games/phase0_demo.yaml
```

它只冻结配置并创建初始化产物，不会执行完整对局。要验证玩家控制器、规则、事件和结果，应使用 `play`。

## 2. 完整配置结构

每局游戏由一个 YAML 文件定义。字段、玩家类型、朝廷官员绑定、OpenAI 兼容接口和 API Key 环境变量的完整写法，请阅读 [游戏对局配置教程](game-config-tutorial.md)。

下面是四人全随机对局的完整可运行模板：

```yaml
game_id: random-game-001
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

将其保存为例如 `configs/games/my_random_game.yaml` 后运行：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/my_random_game.yaml
```

### 2.1 基础字段

| 字段 | 必填 | 可配置值与作用 |
|---|---:|---|
| `game_id` | 是 | 单局名称，也是输出目录最后一级。例如 `game-001`。不能为空，不能含 `/` 或 `\`。 |
| `experiment_id` | 是 | 实验或批次名称，位于 `game_id` 上一级。例如 `baseline-comparison`。不能为空，不能含 `/` 或 `\`。 |
| `seed` | 是 | 任意整数。相同配置和种子可复现骰子、牌堆和随机玩家选择；修改它可得到另一局随机过程。 |
| `players` | 是 | 2 至 4 名玩家的列表；每名玩家的 `seat` 必须唯一。 |
| `initial_cash` | 否 | 每名玩家初始现金；默认 `1500`，可设为不小于 `0` 的整数。 |
| `initial_chance_cards` | 否 | 开局每位玩家从洗好的机会牌堆获得的机会卡张数；默认 `0`（不发牌），可设 `0` 至 `3`，与机会卡手牌上限一致。发牌不产生事件播报。 |
| `max_complete_rounds` | 否 | 最多完成多少完整回合；默认 `50`，必须至少为 `1`。 |
| `rules_version` | 是 | 当前使用 `classic-level0-v1`。 |
| `rules_level` | 否 | 当前必须为 `0`。Level 1 和 Level 2 配置会被拒绝。 |
| `board_data_version` | 是 | 当前使用 `classic-us-40-v1`。 |
| `card_data_version` | 是 | 当前使用 `classic-cards-v1`。 |
| `output_directory` | 否 | 运行产物根目录；默认 `runs`。可设为例如 `test-runs`。 |

### 2.2 玩家字段

每名玩家均需要：

```yaml
- player_id: player-1
  seat: 1
  controller_type: random_baseline
```

| 字段 | 作用与约束 |
|---|---|
| `player_id` | 玩家唯一标识；不能为空。建议使用稳定且易读的名称，如 `player-1`、`random-a`。 |
| `seat` | 座位号，只能是 `1` 至 `4`，同一局中不能重复。座位决定行动顺序。 |
| `controller_type` | 可选 `random_baseline`、`llm_baseline`、`shang_court`、`qin_court`、`tang_court` 或 `ming_court`。新配置应显式填写。 |
| `model_profile` | 仅 `llm_baseline` 使用，引用 `model_profiles` 中的 profile 名称；随机玩家禁止填写。 |

## 3. 控制器配置场景

### 3.1 全随机玩家

`random_baseline` 只会从引擎提供的合法候选中随机选择，不构造 Prompt，也不调用 LLM：

```yaml
players:
  - player_id: random-1
    seat: 1
    controller_type: random_baseline
  - player_id: random-2
    seat: 2
    controller_type: random_baseline
```

随机玩家不能绑定模型：

```yaml
# 错误：random_baseline 不允许 model_profile
- player_id: random-1
  seat: 1
  controller_type: random_baseline
  model_profile: mock-player
```

纯随机游戏不会生成 `llm_calls.jsonl`，且 `result.json` 中的 `llm_calls` 为 `0`。

### 3.2 LLM 玩家与朝廷 Agent

当前支持三种 LLM provider：

- `mock`：固定策略或脚本回复，无凭据、无网络调用，适合单元测试和回放验证；
- `fake`：接收完整 Prompt 和上下文，在本地按 `seed` 随机生成协议回复，适合完整模拟对局；
- `openai_compatible`：调用 OpenAI 兼容的 `/chat/completions` 接口，API Key 从环境变量读取。

普通 LLM 玩家使用 `llm_baseline`。商、秦、唐、明朝廷玩家分别使用 `shang_court`、`qin_court`、`tang_court`、`ming_court`，并为每名官员绑定独立的 `model_profile`。

配置细节和完整示例请阅读 [游戏对局配置教程](game-config-tutorial.md)。项目提供了四朝廷示例：

```text
configs/games/example.yaml
```

如果希望四个朝廷在不访问网络的情况下使用完整 Agent 流程，可运行：

```powershell
.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/four_courts_fake_demo.yaml
```

使用 `openai_compatible` 前，先设置 YAML 中 `api_key_env` 指定的环境变量。例如：

```powershell
$env:MONOPOLY_SHANG_EMPEROR_API_KEY = "你的API Key"
```

然后启动：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/example.yaml
```

`example.yaml` 中的 URL 和模型名是占位值，使用真实服务前必须替换。API Key 不要直接写入 YAML。

### 3.3 随机与 Mock-LLM 混合对局

同一局可以混用随机玩家、普通 LLM 玩家和朝廷 Agent。下面保留一个随机玩家与 Mock-LLM 玩家混合示例；真实接口与朝廷配置见 [游戏对局配置教程](game-config-tutorial.md)。

```yaml
game_id: mixed-game-001
experiment_id: local-mixed-test
seed: 42

players:
  - player_id: random-1
    seat: 1
    controller_type: random_baseline
  - player_id: mock-1
    seat: 2
    controller_type: llm_baseline
    model_profile: mock-a
  - player_id: random-2
    seat: 3
    controller_type: random_baseline
  - player_id: mock-2
    seat: 4
    controller_type: llm_baseline
    model_profile: mock-b

model_profiles:
  mock-a:
    provider: mock
    model: mock-baseline-v1
    temperature: 0.2
    max_tokens: 256
    timeout_seconds: 30
  mock-b:
    provider: mock
    model: mock-baseline-v1
    temperature: 0.6
    max_tokens: 512
    timeout_seconds: 45

initial_cash: 1500
max_complete_rounds: 50
rules_version: classic-level0-v1
rules_level: 0
board_data_version: classic-us-40-v1
card_data_version: classic-cards-v1
output_directory: runs
```

在混合局中，随机玩家的决策同样写入 `decisions.jsonl`，但只对 Mock-LLM 玩家记录 LLM 调用和 `llm_calls.jsonl`。

### 3.4 LLM 运行参数

下面字段只会影响包含普通 LLM 玩家或朝廷 Agent 的对局；全随机局无需配置：

```yaml
validation_retries: 2
window_turns: 1
prompt_profile: cache-first-v1
sentence_template_version: v1
context_token_cap: 4000
```

| 字段 | 默认值 | 作用 |
|---|---:|---|
| `validation_retries` | `2` | LLM 返回不符合决策协议时，最多额外重试的次数；必须不小于 `0`。 |
| `window_turns` | `1` | 每名 LLM 玩家保留的会话回合窗口；必须至少为 `1`。 |
| `prompt_profile` | `full-v1` | Prompt 布局。`full-v1` 保持原格式；`cache-first-v1` 将固定规则置于前缀并移除规则 Markdown 的布局冗余、紧凑化候选 JSON，以提高兼容 Provider 的前缀缓存命中机会。 |
| `sentence_template_version` | 无 | 固定事件播报句式版本；可省略。 |
| `context_token_cap` | 无 | 上下文 token 上限；设置时必须至少为 `1`。 |

这些字段也会冻结在 `config.json` 中。修改后应使用新的 `experiment_id` 或 `game_id`，避免与既有运行目录冲突。

## 4. 输出位置与文件说明

运行产物目录固定为：

```text
<output_directory>/<experiment_id>/<game_id>/
```

例如：

```yaml
output_directory: runs
experiment_id: local-mixed-test
game_id: mixed-game-001
```

对应目录为：

```text
runs/local-mixed-test/mixed-game-001/
```

主要文件：

| 文件 | 内容 | 什么时候看 |
|---|---|---|
| `config.json` | 冻结后的完整配置与配置哈希。 | 确认本局实际使用的参数。 |
| `result.json` | 终局状态、排名、现金、位置、产权、建筑/抵押状态、完整回合数和基础计量。 | 首先查看最终输赢和资产。 |
| `events.jsonl` | 每个已执行命令及其产生的引擎事件。 | 排查购地、租金、自动建房、卖楼、抵押、卡牌等过程。 |
| `decisions.jsonl` | 每个控制器决策点的候选、响应校验、回退和实际命令。 | 排查随机或 LLM 玩家在某个回合为何作出某项选择。 |
| `runtime.jsonl` | 重试、上下文裁剪等运行时审计记录。 | 排查 LLM 链路和运行时告警。 |
| `llm_calls.jsonl` | 每次 LLM 客户端调用，包括失败调用。 | 普通 LLM 玩家或朝廷 Agent 对局生成；纯随机局没有该文件。 |

## 5. 批量运行多局对局

单局运行仍使用 `play --config <配置文件路径>`。需要按顺序运行多个独立 YAML 时，另使用一份批次清单。

### 5.1 单独运行一个 YAML

每局可以直接使用自己的 YAML 文件运行，不需要创建批次清单：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play `
  --config configs/games/your-game.yaml
```

该局仍按照 YAML 中的 `output_directory`、`experiment_id` 和 `game_id` 生成运行产物：

```text
<output_directory>/<experiment_id>/<game_id>/
```

单局结果查看、过程检查和回放验证沿用本教程前面的快速开始流程。

### 5.2 批量运行多个 YAML

批次清单示例：

```yaml
games:
  - game_a.yaml
  - game_b.yaml
```

清单中的相对路径以清单文件所在目录为基准。每个对局 YAML 仍使用自身的 `output_directory`、`experiment_id` 和 `game_id`，各局产物继续写入各自的运行目录。

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe experiment run `
  --batch configs/experiments/preexperiment_demo/batch.yaml
```

程序先检查全部清单文件、配置有效性和 `game_id` 是否重复；预检查失败时不启动任何对局。检查通过后按清单顺序执行，单局异常记录为 `failed` 并继续后续对局。批次状态写入清单同目录的 `tasks.jsonl`，不替代各局原有运行产物。

## 6. 查看结果和过程

以下示例假设运行目录为 `runs/local-mixed-test/mixed-game-001`。

### 6.1 查看终局结果

```powershell
Get-Content runs/local-mixed-test/mixed-game-001/result.json -Raw
```

重点字段：

| 字段 | 含义 |
|---|---|
| `status` | 正常完成时为 `completed`。 |
| `end_reason` | 结束原因，如 `round_limit` 或破产相关终局。 |
| `complete_rounds` | 已完成的完整回合数。 |
| `rankings` | 终局排名，从高到低。 |
| `players` | 每名玩家的现金、位置、持有地产、监狱状态和存活回合数。 |
| `properties` | 每块地产的所有者、`building_level` 和 `mortgaged` 状态。 |
| `llm_calls` | 本局 LLM 调用次数；纯随机局为 `0`。 |
| `decision_fallbacks` | 控制器响应无效后最终使用默认决策的次数。 |

### 6.2 查看事件流

```powershell
Get-Content runs/local-mixed-test/mixed-game-001/events.jsonl
```

JSONL 每行是一条 JSON 记录。可筛选常见事件：

```powershell
Select-String -Path runs/local-mixed-test/mixed-game-001/events.jsonl -Pattern 'building_added|building_sold'
Select-String -Path runs/local-mixed-test/mixed-game-001/events.jsonl -Pattern 'property_purchased|rent_paid'
Select-String -Path runs/local-mixed-test/mixed-game-001/events.jsonl -Pattern 'property_mortgaged|mortgage_redeemed'
```

例如，要判断终局中为什么几乎没有房子，应同时检查 `building_added` 和 `building_sold`，不能只看 `result.json` 中最终的 `building_level`。

### 6.3 查看控制器决策

```powershell
Get-Content runs/local-mixed-test/mixed-game-001/decisions.jsonl
```

每条记录包含请求候选、控制器类别、尝试响应、校验结果、是否发生回退和最终执行命令。重点查看：

- `controller_type`：`llm` 或 `non_llm`
- `executed_command`：实际写入引擎的命令
- `fallback`：是否因无效响应执行默认选项
- `validation_errors`：LLM 响应不符合协议时的校验错误

### 6.4 查看 LLM 调用记录

只要对局包含普通 LLM 玩家或朝廷 Agent，就可以查看：

```powershell
Get-Content runs/local-mixed-test/mixed-game-001/llm_calls.jsonl
```

全随机局没有这个文件是预期行为，不代表运行失败。

## 7. 回放验证

回放器会从 `config.json` 重建游戏，根据记录的命令和随机结果重新执行，并比较事件序列与终局状态。它不会再次调用 LLM。

```powershell
@'
from pathlib import Path
from monopoly_agent_battle.game.replay import verify_run

verify_run(Path("runs/local-mixed-test/mixed-game-001"))
print("回放验证通过")
'@ | .\.venv\Scripts\python.exe
```

正常输出“回放验证通过”，说明配置、命令、事件和 `result.json` 相互一致。若失败，检查运行产物是否被手动编辑、是否缺少 JSONL 记录，或运行是否未完整结束。

## 8. 常见配置问题

| 现象 | 检查项 |
|---|---|
| 运行目录已存在 | 修改 `experiment_id` 或 `game_id`；运行产物不会自动覆盖。 |
| `players must contain between 2 and 4 entries` | 玩家必须为 2 至 4 名。 |
| `player seats must be unique` | 每个 `seat` 必须唯一，且在 1 至 4 之间。 |
| `random baseline player ... must not set model_profile` | 删除随机玩家的 `model_profile`。 |
| `LLM baseline player ... requires model_profile` | 为 `llm_baseline` 玩家配置一个已定义的 profile。 |
| `player model_profile not defined` | 在 `model_profiles` 中增加对应名称，或修正玩家引用。 |
| `no client factory registered for provider: ...` | 检查 `provider` 是否为已支持的 `mock`、`fake` 或 `openai_compatible`。 |
| 没有 `llm_calls.jsonl` | 纯随机局的预期结果；检查 `decisions.jsonl`、`events.jsonl` 和 `result.json`。 |
