# 系统操作手册

本手册面向部署、运行和排查本项目的技术人员，作为**总览入口**：概述系统模块与产物，说明凭据约定，串联单局运行、回放和结果检查流程，汇总常见错误诊断，并说明当前能力下的预实验准备方式。

具体的配置字段与运行命令细节，请配合以下两份专题教程阅读，本手册不重复其表格：

- 配置编写：[游戏对局配置教程](game-config-tutorial.md)
- 运行与查看：[手动运行游戏与查看结果](manual-game-run-tutorial.md)

> 当前系统仅支持经典规则 Level 0。Level 1（通货膨胀、单向外交）、Level 2、随机事件、拍卖、言官/宦官均未实现，且不预留接口。汉代、宋代朝廷暂缓，未纳入运行范围。

---

## 1. 环境准备

### 1.1 运行时要求

- Python **≥ 3.11**（见 `pyproject.toml`）。
- 依赖：`pydantic>=2.10,<3`、`PyYAML>=6.0,<7`；开发/质检额外需要 `pytest`、`pytest-cov`、`coverage`、`ruff`、`pyright`。
- 所有命令默认在**仓库根目录**执行；虚拟环境固定为 `.venv`。

### 1.2 安装（首次）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

安装后可执行入口 `monopoly-agent-battle`（定义于 `pyproject.toml` 的 `[project.scripts]`），也可用 `.\.venv\Scripts\python.exe -m monopoly_agent_battle.cli.main` 等价调用。

### 1.3 验证安装

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/random_baseline_demo.yaml
```

该命令使用四名随机玩家、无需任何凭据，可完整跑完一局并生成运行产物，是验证环境是否可用的最快方式。

---

## 2. 系统模块总览

系统按需求文档第 4 节划分为独立模块，代码组织见 [source-index.md](source-index.md)。各模块职责与主要脚本对应关系：

| 模块 | 职责 | 主要代码 |
|---|---|---|
| 领域模型 | 棋盘、玩家、产权、命令、事件等纯数据结构 | `domain/models.py`、`domain/commands.py` |
| 游戏引擎 | 唯一可变状态；执行已校验命令并产出事件 | `game/engine.py`、`game/rules/`、`game/board_data/`、`game/cards/` |
| 决策协议 | 生成合法候选、校验响应、构造引擎命令、故障回退 | `decision/requests.py`、`decision/protocol.py`、`decision/runner.py` |
| Agent 编排 | baseline、随机、商/秦/唐/明朝廷工作流 | `agents/` |
| LLM 适配 | 供应商无关协议、Mock/Fake/OpenAI 兼容/录制客户端 | `llm/` |
| 历史上下文 | 事件播报、会话组装、token 裁剪 | `context/` |
| 绩效 | 净资产窗口、一致性统计、差评判定 | `performance/` |
| 运行产物 | 冻结配置、JSONL 审计、结果快照、回放 | `logging/run_artifacts.py`、`game/replay.py` |
| 报告 | 从产物生成安全可读的单局报告 | `reporting/single_game.py` |
| CLI | `demo` / `play` / `report` / `resume` 入口 | `cli/main.py` |

### 2.1 关键设计约束（运行时须知）

- **引擎是唯一事实来源。** LLM、Agent、日志都不能直接改状态；LLM 输出只经决策协议校验后转为引擎命令。
- **确定性。** 所有游戏随机性由每局 `seed` 派生；相同配置、种子与相同 Agent 输出可复现同一非 LLM 游戏过程。随机 baseline 使用由 `seed`+座位+`player_id` 派生的独立 RNG，不消耗引擎 RNG。
- **信息隔离。** 手牌具体内容仅持有者（及其朝廷内部）可见，牌堆顺序对所有人不可见；重连/重试等运行时记录只进 `runtime.jsonl`，绝不进入 Agent 上下文。

---

## 3. 配置模型

完整字段与示例见[配置教程](game-config-tutorial.md)。此处只给出配置模型的关键要点：

- 每局由一个 YAML 定义，加载时经 `config/models.py` 严格校验，并由 `config/loader.py` 序列化为规范 JSON、计算 SHA-256 `config_hash`。冻结配置与哈希写入 `config.json`。
- 必填：`game_id`、`experiment_id`、`seed`、`players`、`rules_version`、`board_data_version`、`card_data_version`。当前受支持取值：`rules_version=classic-level0-v1`、`rules_level=0`、`board_data_version=classic-us-40-v1`、`card_data_version=classic-cards-v1`。**配置解析会拒绝 Level 0 不支持的规则开关。**
- 玩家 2–4 名，`seat` 取 1–4 且唯一。`controller_type` ∈ `random_baseline` / `llm_baseline` / `shang_court` / `qin_court` / `tang_court` / `ming_court`。
- `random_baseline` 禁止 `model_profile`；`llm_baseline` 必须引用一个 `model_profile`；朝廷玩家必须为每名官员填写 `court_role_profiles`。
- 每个 `model_profile` 可独立配置 `provider`、`base_url`、`api_key_env`、`model`、`seed`、采样参数与超时；真实密钥绝不写入 YAML。
- LLM 运行参数（`validation_retries`、`window_turns`、`prompt_profile`、`context_token_cap` 等）也会冻结进 `config.json`，参与 `config_hash`。

修改任何配置后**必须更换 `experiment_id` 或 `game_id`**，程序不会覆盖已有运行目录。

---

## 4. 凭据与环境变量约定

**API Key 一律通过环境变量注入，禁止写入 YAML、日志或任何运行产物。** YAML 中只写环境变量名称（`api_key_env`）。

### 4.1 注入方式

程序在 CLI 启动时通过 `config/local_env.py` 自动加载**仓库根目录**下的 `.env.local`（若存在）。行格式为 `NAME=VALUE`，支持 `#` 注释、`export ` 前缀和成对引号；**已存在于系统环境的变量优先**（`setdefault` 语义，不覆盖）。

```dotenv
# .env.local （已被 .gitignore 忽略，不会提交）
MONOPOLY_TANG_EMPEROR_API_KEY=你的真实APIKey
MONOPOLY_QIN_API_KEY=另一供应商的APIKey
```

也可在当前 PowerShell 会话临时设置（会覆盖 `.env.local`，因其先于加载）：

```powershell
$env:MONOPOLY_TANG_EMPEROR_API_KEY = "临时Key"
```

### 4.2 约定与安全边界

- 变量名建议统一前缀（如 `MONOPOLY_<朝廷>_<角色>_API_KEY`），并在 YAML 的 `api_key_env` 中逐一对应。多个角色可共用同一变量。
- `.env.local` 已被 `.gitignore` 忽略。**不要**把真实密钥提交到版本库。
- 仅 `provider: openai_compatible` 需要凭据；`mock` 和 `fake` 均为本地、无网络、无凭据。
- 若 `api_key_env` 指向的变量未设置，创建 OpenAI 兼容客户端时会失败——先确认变量已注入且命令在根目录执行。

> 已确认的运行产物策略：`llm_calls.jsonl` 的响应摘要**保留原长、不脱敏**（真实密钥本就不进入 `config.json`、`llm_calls.jsonl` 或错误信息）；`output_directory` 拒绝含 `..` 的路径穿越，允许相对与绝对路径；**日志保留周期由人工管理，系统不做程序化自动清理**；并发运行时同名目录冲突由 `mkdir(exist_ok=False)` 天然拒绝，不覆盖既有产物。

---

## 5. 单局运行

CLI 提供四个子命令（`cli/main.py`）：`demo`、`play`、`report`、`resume`。

### 5.1 `play`：运行完整对局

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config <配置文件路径>
```

`play` 读取配置、装配控制器（随机 / 普通 LLM / 商 / 秦 / 唐 / 明 / 混合），逐回合推进，直到只剩一名未破产玩家或达到 `max_complete_rounds`，并把产物写入 `<output_directory>/<experiment_id>/<game_id>/`。

无凭据可运行的示例配置：

| 配置 | 说明 |
|---|---|
| `configs/games/random_baseline_demo.yaml` | 四名随机玩家，不产生 LLM 产物。 |
| `configs/games/fake_llm_demo.yaml` | 四名 Fake LLM 玩家，本地生成协议回复，无网络。 |
| `configs/games/four_courts_fake_demo.yaml` | 商/秦/唐/明四朝廷 Fake LLM，可验证完整朝廷流程。 |
| `configs/games/phase4_mock_demo.yaml` | Mock LLM baseline。 |
| `configs/games/{shang,tang,ming}_court_mock_demo.yaml` | 对应朝廷的 Mock 短局。 |

真实接口示例见 `configs/games/example.yaml`（13 名官员各绑定独立 URL / 环境变量 / 模型；URL 与模型名为占位值，使用前替换并注入对应环境变量）。

对局启动后全程无人值守，主循环自动推进到「只剩一名未破产玩家」或达到 `max_complete_rounds`，中途不需要任何键盘输入；LLM 断线会按配置自动重试，仍失败则自动回退到默认合法选项并记入审计。可按需选择前台或脱离终端两种运行方式。

#### 5.1.1 前台运行（终端不可关闭）

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/你的.yaml
```

对局跑在当前前台进程里，**运行期间不能关闭终端**（关闭或 Ctrl+C 会中断对局）。适合短局或人在旁边守着的场景。

#### 5.1.2 脱离终端后台运行（关终端仍继续）

用 `Start-Process` 拉起独立进程并把输出重定向到日志文件，之后可关闭终端：

```powershell
Start-Process -FilePath ".\.venv\Scripts\monopoly-agent-battle.exe" `
  -ArgumentList 'play','--config','configs/games/你的.yaml' `
  -RedirectStandardOutput 'runs\你的-stdout.log' `
  -RedirectStandardError  'runs\你的-stderr.log' `
  -WindowStyle Hidden -PassThru
```

- `-PassThru` 会返回进程对象，记下其 `Id`（PID）以便后续管理。
- 事后查看进度：`Get-Content runs\你的-stdout.log -Wait`（实时跟随）或直接读运行目录下的产物。
- 需要停止：`Stop-Process -Id <PID>`。**注意：中途停止会产生不完整的废局**，LLM/朝廷局不支持断点续跑，需改 `game_id` 或 `experiment_id` 后重跑。
- 凭据仍从仓库根目录的 `.env.local` 读取（CLI 进程启动时自行加载）；若改用会话级 `$env:` 临时变量，须在同一 PowerShell 会话内 `Start-Process`。

无论前台还是后台，真实 LLM 局都可能耗时较长并产生费用；系统不做限时或预算上限（属未实现的阶段 7）。

### 5.2 `demo`：仅初始化产物

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe demo --config configs/games/phase0_demo.yaml
```

只冻结配置、创建运行目录并写入初始 `result.json`（`status=initialized`），**不执行对局**。用于验证配置可解析、目录可创建。

### 5.3 运行产物

产物目录固定为 `<output_directory>/<experiment_id>/<game_id>/`：

| 文件 | 内容 |
|---|---|
| `config.json` | 冻结配置 + `config_hash` + 规则/提示词版本 |
| `events.jsonl` | 每条已执行命令及其领域事件，`event_id` 单调递增 |
| `decisions.jsonl` | 决策请求、候选、响应校验、回退与实际命令 |
| `llm_calls.jsonl` | 每次 LLM 调用（含失败）：角色、模型、token、耗时、错误、调用时回合号（`complete_rounds`）、原长响应摘要。**纯随机局不生成** |
| `llm_digest.md` | 精简 LLM 回复摘要，一行一条「第 N 轮 · 发起者 · 决策：选项 · 理由（400 字符截断）」，最终决策（皇帝或 baseline 玩家）行加粗。含 LLM 调用时由 `play`/`report` 生成 |
| `runtime.jsonl` | 重连、重试、上下文裁剪等运行时审计（不提供给 Agent） |
| `result.json` | 终局状态、排名、有效性与计量 |
| `performance.jsonl` | 朝廷官员绩效窗口（仅含朝廷玩家时生成） |
| `checkpoint.json` | 每条命令后自动更新的断点快照 |

`result.json` 关注字段：

| 字段 | 含义 |
|---|---|
| `status` | 正常完成为 `completed` |
| `end_reason` | 结束原因，如 `round_limit` 或破产终局 |
| `complete_rounds` | 已完成完整回合数 |
| `rankings` | 终局排名 |
| `validity_status` | 有效性：`valid` 或 `invalid` |
| `llm_calls` | LLM 调用次数；纯随机局为 `0` |
| `decision_fallbacks` | 因无效响应或重连耗尽而使用默认候选的次数 |

**有效性判定：** 当 LLM 触发的默认回退次数达到全部 LLM 调用数的 **10%** 时，`validity_status=invalid`（`decision/runner.py::_validity_status`）。重连或重试后成功得到合法回复不计入该分子。无效局仍完整保留全部日志，仅不计入正式排名与积分。

---

## 6. 回放验证

回放器从 `config.json` 重建引擎，用记录的命令与 `dice_rolled` / `card_die_rolled` 固定所有随机结果重新执行，比较事件序列与终局状态快照。**不调用 LLM、不依赖运行时 RNG。**

```powershell
@'
from pathlib import Path
from monopoly_agent_battle.game.replay import verify_run

verify_run(Path("runs/<experiment_id>/<game_id>"))
print("回放验证通过")
'@ | .\.venv\Scripts\python.exe
```

输出“回放验证通过”表示配置、命令、事件与 `result.json` 相互一致。失败通常意味着产物被手动编辑、缺少 JSONL 记录、事件编号不连续或运行未完整结束。

---

## 7. 结果检查

### 7.1 可读单局报告

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe report --run-dir runs/<experiment_id>/<game_id> --output report.md
```

`report`（`reporting/single_game.py`）从产物生成**不含私有 payload** 的安全聚合报告（状态、终局、排名、玩家资产、事件计数、决策/回退/非 LLM 计数、LLM 统计、绩效窗口数），并渲染为 Markdown。省略 `--output` 时打印到标准输出路径。

### 7.2 直接检查 JSONL / result.json

判断“终局为何几乎没有房子”这类问题时，应同时看 `building_added` 与 `building_sold`，而非只看 `result.json` 的最终 `building_level`：

```powershell
Get-Content runs/<experiment_id>/<game_id>/result.json -Raw
Select-String -Path runs/<experiment_id>/<game_id>/events.jsonl -Pattern 'property_purchased|building_added|building_sold|rent_paid'
```

`decisions.jsonl` 每条记录含 `controller_type`（`llm` / `non_llm`）、`executed_command`、`fallback`、`validation_errors`，用于定位某玩家某回合的选择原因。

更详细的字段清单见[手动运行教程](manual-game-run-tutorial.md) 第 6 节。

---

## 8. 常见错误诊断

### 8.1 配置与启动

| 现象 | 诊断与处理 |
|---|---|
| 运行目录已存在 | 换 `experiment_id` 或 `game_id`；程序从不覆盖。 |
| `players must contain between 2 and 4 entries` | 玩家须 2–4 名。 |
| `player seats must be unique` | `seat` 唯一且在 1–4。 |
| `random baseline player ... must not set model_profile` | 删除随机玩家的 `model_profile`。 |
| `LLM baseline player ... requires model_profile` | 为 `llm_baseline` 填写已定义的 profile。 |
| `... requires court_role_profiles` | 为朝廷玩家填全部官员 profile。 |
| `player model_profile not defined` | 在 `model_profiles` 中补充或修正引用名。 |
| `no client factory registered for provider: ...` | `provider` 只能是 `mock` / `fake` / `openai_compatible`。 |
| 拒绝 Level 1/2 或未支持版本 | 当前只支持 Level 0 与受支持的规则/数据版本。 |

### 8.2 凭据与网络

| 现象 | 诊断与处理 |
|---|---|
| API Key 环境变量未设置 / 创建客户端失败 | 在根目录 `.env.local` 填写 `api_key_env` 对应变量，或在当前 PowerShell 设置；确认命令在根目录执行。 |
| `.env.local` 未被读取 | 必须位于**仓库根目录**且命令从根目录启动；系统已有的同名变量会优先。 |
| OpenAI 兼容请求超时/失败 | 检查 `base_url`、模型名与网络；运行器会按 `validation_retries` 及连接级重试处理，超限则回退到候选集首项并记录到 `runtime.jsonl` 与 `decisions.jsonl`。 |

### 8.3 运行结果异常

| 现象 | 诊断与处理 |
|---|---|
| 没有 `llm_calls.jsonl` | 纯随机局的**预期结果**，检查 `decisions.jsonl` / `events.jsonl` / `result.json`。 |
| `validity_status=invalid` | LLM 触发的默认回退达 10%。查 `runtime.jsonl` 与 `decisions.jsonl` 的 `fallback` / `validation_errors` 定位反复非法响应或断线的角色。 |
| 回放验证失败 | 不要删改产物；核对是否手动编辑、JSONL 是否缺行、事件编号是否连续、运行是否完整结束。 |
| 大量 `fallback` | 检查对应角色的 `validation_errors`，多为 LLM 输出不符合决策协议 JSON。 |

---

## 9. 预实验准备（当前能力）

> 阶段 7 的预实验任务生成器 / 任务状态机 / 批量成本报告尚未开发。以下仅描述**现有 `play` + `report` + 回放 + 配置能力**下的手动准备方式，不涉及尚未实现的 CLI。

### 9.1 冻结与可复现

- 每局配置在启动时冻结进 `config.json` 并计算 `config_hash`；相同 `config_hash` + 相同 Agent 输出可复现非 LLM 游戏过程。开展预实验前，请为每局固定 `seed`、座位、规则与数据版本、各角色 `ModelProfile`。
- 任何提示词、模型参数或规则数据变更都会改变 `config_hash`，须在结果中可追溯；变更后用新的 `experiment_id` / `game_id` 另存运行。

### 9.2 无凭据的干跑（dry run）

在获得真实凭据前，用 `fake` provider 验证完整链路（含朝廷流程）：

```powershell
.\.venv\Scripts\monopoly-agent-battle.exe play --config configs/games/four_courts_fake_demo.yaml
```

再对产物执行回放验证与 `report`，确认游戏流程、Agent 协议、审计产物与绩效窗口均正常，作为“可执行任务清单”的自检。

### 9.3 接入真实模型

1. 以 `configs/games/example.yaml` 为模板，替换各官员的 `base_url`、`model`，并在 `.env.local` 中注入 `api_key_env` 对应密钥。
2. 为每局分配固定 `seed` 与座位安排（座位均衡策略见需求文档第 7 节），逐局 `play`。
3. 每局结束后运行回放验证 + `report`，并保留 `runs/<experiment_id>/<game_id>/` 全部产物作为审计证据。
4. 供应商、模型版本、采样参数、局数、种子等由项目负责人冻结；成本估算与批量任务编排属阶段 7，待相应 CLI 实现后补充。

### 9.4 结论登记（受门控）

需求文档 P-011-002 至 P-011-005 的结论、异质模型是否启用、是否执行正式实验等，均需项目负责人依据预实验报告人工登记。程序只记录与校验已批准结论，不得自动决定是否采用方案。

---

## 10. 质量门（开发/交付前）

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pyright.exe
```

日常仅测改动模块及其直接消费者；修改共享领域模型、引擎、决策协议、配置哈希、审计/回放格式，或申请阶段验收、交付前，须运行完整质量门。测试文件职责见 [source-index.md](source-index.md)。

---

## 11. 参考文档

| 文档 | 用途 |
|---|---|
| [game-config-tutorial.md](game-config-tutorial.md) | YAML 配置字段、玩家/官员绑定、API Key 环境变量。 |
| [manual-game-run-tutorial.md](manual-game-run-tutorial.md) | 快速开始、产物文件说明、事件/决策/LLM 查看、回放。 |
| [source-index.md](source-index.md) | 全部脚本的路径、职责与测试映射。 |
| `MonopolyAgentBattle_developer_docs/requirements_specification.md` | 需求与验收依据。 |
| `MonopolyAgentBattle_developer_docs/classic_rules_supplement.md` | 规则唯一规范来源。 |
| `MonopolyAgentBattle_developer_docs/develop_plan.md` / `develop_board.md` | 开发计划与进度看板。 |
