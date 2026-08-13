# 脚本索引

本索引按项目目录和职责组织。测试文件仅用于验证对应模块；运行产物 `runs/` 不属于源代码。

## 配置与示例（`configs/`、`src/.../config/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `configs/games/phase0_demo.yaml` | 四玩家 Level 0 示例对局配置。 | 作为 `demo --config` 的输入。 |
| `src/monopoly_agent_battle/config/models.py` | 定义并严格校验单局配置，包括玩家座位、随机种子、规则和数据版本、初始资金与运行目录。 | 由配置加载器和后续对局/实验入口调用。 |
| `src/monopoly_agent_battle/config/loader.py` | 加载 YAML 配置，生成规范 JSON 及 SHA-256 `config_hash`。 | 由 CLI 或实验编排调用。 |

## 领域模型（`src/monopoly_agent_battle/domain/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `models.py` | 定义 Level 0 可重放的棋盘格、产权、玩家、游戏状态、监狱、终局、回合阶段、待结算操作队列和领域事件；移动操作保存目的地、GO 收入、建房资格与恢复上下文，玩家待租金决策保存骰子和恢复阶段，持续效果记录类型、来源、目标、剩余来源回合和激活回合。 | 被游戏规则和引擎使用；不依赖 LLM 或 CLI。 |
| `commands.py` | 定义游戏引擎接受的掷骰、回合结束、破产、资产操作、监狱罚款、社区基金出狱卡、租金豁免选择、主动弃置机会卡及结构化机会卡使用命令；机会卡目标可包含玩家、颜色组、主/次地产。 | 由控制器、决策协议或测试构造后提交给引擎，并由运行器序列化供回放。 |

## 游戏规则与引擎（`src/monopoly_agent_battle/game/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `board_data/classic_us_40.py` | 固化并自校验 `classic-us-40-v1` 的 40 格棋盘、产权数值、颜色组和铁路租金。 | 由 Level 0 规则和游戏引擎读取。 |
| `rules/classic_level0.py` | 计算 Level 0 净资产、街道/铁路/公共财产租金。 | 由游戏引擎调用。 |
| `engine.py` | 持有唯一可变游戏状态的种子化确定性引擎；执行回合及顺序化付款、抽牌和移动结算。社区基金 GO 卡通过 `MOVE` 操作完成移动、收入、重新落点和阶段恢复，卡牌移动不授予常规建房资格；租金豁免保存并恢复双骰续投阶段。实现全部 16 张机会卡、C-028 half-up 取整/银行差额、抢夺随机、手牌上限、持续效果和产权卡原子校验。 | 构造 `GameEngine(config)`，以 `execute(command)` 提交命令；不依赖 LLM。 |
| `controllers.py` | 提供固定命令序列的脚本化控制器；命令联合类型覆盖机会卡使用、主动弃牌和租金豁免选择。 | 主要供自动化测试和无 LLM 模拟调用。 |
| `runner.py` | 执行无 LLM 的固定命令脚本，持久化命令与领域事件；命令耗尽且仍处于 `CARD_RESOLUTION` 时报告等待选择。结果快照覆盖卡堆、手牌、豁免、待处理租金及其恢复阶段、持续效果、含移动上下文的结算队列、玩家和产权。 | 调用 `run_scripted_game(config, controller, artifacts)`。 |
| `replay.py` | 从冻结配置和已记录命令重放运行产物；反序列化机会卡目标、主动弃牌、社区基金出狱卡和租金豁免选择，并用 `dice_rolled`/`card_die_rolled` 固定随机结果，验证事件编号、事件内容和包含移动/租金恢复上下文的最终快照。 | 调用 `verify_run(run_directory)`；不依赖运行时 RNG、LLM 或控制器。 |

## 决策协议（`src/monopoly_agent_battle/decision/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `decision/models.py` | 定义冻结的决策请求、合法候选项、响应、校验结果及其 JSON 审计表示。 | 决策生成、提示词渲染、运行器和审计记录共享。 |
| `decision/requests.py` | 从当前引擎状态投影玩家可见视图，并以克隆引擎执行预检生成仅包含真实合法命令的候选项；只为监狱和其他存在选择的事件阶段创建请求，普通未监禁 `ROLLING` 与 `TURN_COMPLETE` 不创建决策。其他玩家仅暴露手牌数量；卡堆顺序、其他玩家手牌和运行时信息不会进入视图。 | 每个实际决策点调用 `build_decision_request(engine, sequence)`。 |
| `decision/prompts.py` | 将决策请求稳定渲染成中文 AI Prompt，AI 可见上下文只包含完整轮次、行动玩家、阶段和决策类别，不含 `decision_id` 或 `game_id`；包含角色约束、可见状态、问题、候选项和 JSON-only 输出契约。 | 由决策运行器作为控制器实际入参调用；当前不连接外部模型。 |
| `decision/protocol.py` | 解析并严格校验不可信 JSON 响应；模型只能选择冻结的 option ID，不能提交或篡改命令参数；将已选项映射为既有引擎命令。 | 由决策运行器在调用引擎前使用。 |
| `decision/runner.py` | 以决策协议运行完整对局；普通未监禁 `ROLLING` 自动执行掷骰、`TURN_COMPLETE` 自动结束回合，两者仅写入可回放的命令/领域事件；监狱及其他真实选择才调用控制器并写决策审计。控制器实际收到 `render_decision_prompt()` 的完整中文 Prompt；提供确定性默认策略，连接失败最多重试两次，非法/失败响应回退至默认合法项。 | 调用 `run_decision_game(engine, controller, artifacts)`。 |
| `doc/stage3-decision-prompt-acceptance.md` | Stage 3 待重新人工验收清单：控制器实际 Prompt、可见性与审计边界、自动流程边界、全部实际决策候选变体（含 16 张机会卡）及字段组装代码交叉引用。 | 必须由负责人审核；确认前不得进入真实 LLM 接入与提示词冻结。 |
| `doc/stage3-problems.md` | 记录截至 2026-08-13 当前已实现的完整决策范围、自动流程边界、负责人原话规则反馈，以及当前实现与反馈的客观差异。 | 作为 Stage 3 规则问题交接记录；所列问题尚未修改代码。 |


| 路径 | 用途 | 使用方式 |
|---|---|---|
| `logging/run_artifacts.py` | 创建单局运行目录，持久化冻结配置、JSONL 命令/领域事件、决策审计、私有运行时重试/回退事件与结果快照。 | 由单局运行器调用。 |
| `cli/main.py` | 提供阶段 0 运行产物闭环演示命令。 | `monopoly-agent-battle demo --config configs/games/phase0_demo.yaml` |

## 自动化测试（`tests/`）

| 路径 | 覆盖范围 | 使用方式 |
|---|---|---|
| `tests/unit/test_config.py` | 配置校验、YAML 加载和配置哈希。 | `python -m pytest tests/unit/test_config.py` |
| `tests/unit/test_decision_protocol.py` | 决策可见性隔离、实际决策阶段候选项、普通流程拒绝创建请求、响应 schema 拒绝、Prompt 审计字段隔离和监狱多选项。 | `python -m pytest tests/unit/test_decision_protocol.py` |
| `tests/integration/test_decision_runner.py` | 决策驱动完整对局、自动普通掷骰事件审计/回放、监狱掷骰 Prompt 选择、连接重试、回退及原始校验错误保留。 | `python -m pytest tests/integration/test_decision_runner.py` |
| `tests/unit/game/test_board.py` | 40 格棋盘数据完整性与产权数值。 | `python -m pytest tests/unit/game/test_board.py` |
| `tests/unit/game/test_engine.py` | 移动、租金、抵押、建造和双骰入狱等核心规则。 | `python -m pytest tests/unit/game/test_engine.py` |
| `tests/unit/game/test_turn_flow.py` | 双骰、阶段转换、付款处置与显式破产的回合状态机。 | `python -m pytest tests/unit/game/test_turn_flow.py` |
| `tests/unit/game/test_jail_and_endgame.py` | 监狱付费/双骰/第三次失败、酒店出售、完整轮次与终局规则。 | `python -m pytest tests/unit/game/test_jail_and_endgame.py` |
| `tests/integration/test_scripted_game.py` | 脚本化控制器提交命令的端到端行为。 | `python -m pytest tests/integration/test_scripted_game.py` |
| `tests/unit/game/test_cards.py` | 验证社区基金抽取/弃置/持有、生日多方付款恢复、GO 移动队列、不授予卡牌移动建房资格，以及双骰后抽牌移动和租金豁免选择恢复续投。 | `python -m pytest tests/unit/game/test_cards.py` |
| `tests/unit/game/test_chance_cards.py` | 覆盖 16 张机会卡的 48 个文字验收场景（每张 2 个有效场景与 1 个无效场景），并覆盖现金/产权/建筑、C-028 均富和税额、购地价格、抢夺随机结果、持续效果精确期限与查封优先组合租金、豁免选择、手牌上限；无效路径使用完整状态快照验证原子拒绝。另含卡堆重洗、持有卡破产归还、产权转移后颜色组效果和查封+同盟边界回归。 | `python -m pytest tests/unit/game/test_chance_cards.py` |
| `tests/integration/test_scripted_runner.py` | 覆盖终局审计产物、固定序列回放、机会卡抽取/使用、双地产目标及事件编号篡改拒绝；所有调用 `verify_run()` 的场景均由冻结配置、固定随机结果和正式命令重建，不依赖测试前隐藏状态注入。 | `python -m pytest tests/integration/test_scripted_runner.py` |
| `tests/integration/test_cli_demo.py` | CLI 创建可审计运行目录的端到端闭环。 | `python -m pytest tests/integration/test_cli_demo.py` |

## 常用质量检查

```bash
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/ruff.exe check .
.venv/Scripts/pyright.exe
.venv/Scripts/python.exe -m pytest
```
