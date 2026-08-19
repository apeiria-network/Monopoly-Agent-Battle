# 脚本索引

本索引按项目目录和职责组织。测试文件仅用于验证对应模块；运行产物 `runs/` 不属于源代码。

## 配置与示例（`configs/`、`src/.../config/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `configs/games/phase0_demo.yaml` | 四玩家 Level 0 示例对局配置。 | 作为 `demo --config` 的输入。 |
| `configs/games/phase4_mock_demo.yaml` | 四玩家 Mock LLM baseline 对局配置（`provider: mock`，无凭据）。 | 作为 `play --config` 的输入。 |
| `src/monopoly_agent_battle/config/models.py` | 定义并严格校验单局配置，包括玩家座位、模型绑定（`ModelProfile` 与上下文参数）、随机种子、规则和数据版本、初始资金与运行目录。 | 由配置加载器和后续对局/实验入口调用。 |
| `src/monopoly_agent_battle/config/loader.py` | 加载 YAML 配置，生成规范 JSON 及 SHA-256 `config_hash`。 | 由 CLI 或实验编排调用。 |

## 领域模型（`src/monopoly_agent_battle/domain/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `models.py` | 定义 Level 0 可重放的棋盘格、产权、玩家、游戏状态、监狱、终局、回合阶段、待结算操作队列、临时抢夺状态和领域事件；包含 `FORCED_DISCARD` 与 `THEFT_CARD_SELECTION` 阶段，移动操作保存目的地、GO 收入、建房资格与恢复上下文，持续效果记录类型、来源、目标、剩余来源回合和激活回合。 | 被游戏规则和引擎使用；不依赖 LLM 或 CLI。 |
| `commands.py` | 定义游戏引擎接受的掷骰、回合结束、资产操作、监狱罚款、社区基金出狱卡、强制弃置机会卡、抢夺成功后的具体选卡及结构化机会卡使用命令，并提供唯一完整的 `GameCommand` 联合类型。普通建造、破产和租金结算旧命令仅保留兼容性定义，不进入决策候选。 | 由控制器、决策协议或测试构造后提交给引擎，并由运行器序列化供回放。 |

## 游戏规则与引擎（`src/monopoly_agent_battle/game/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `board_data/classic_us_40.py` | 固化并自校验 `classic-us-40-v1` 的 40 格棋盘、产权数值、颜色组和铁路租金。 | 由 Level 0 规则和游戏引擎读取。 |
| `rules/classic_level0.py` | 计算 Level 0 净资产、街道/铁路/公共财产租金。 | 由游戏引擎调用。 |
| `engine.py` | 持有唯一可变游戏状态的种子化确定性引擎；执行回合及顺序化付款、抽牌和移动结算。骰子落到已拥有的未抵押、非酒店普通街道时自动建造一层，卡牌移动/刚购入/资金不足不触发处置。正值且未被查封的应付租金自动消耗免租次数；同盟收租对付款方单笔 `rent` 扣款，产权人与盟友在收入层自动各得一半（奇数半分由银行补差）；付款方耗尽合法出售/抵押选项时自动破产，仅取消该付款方的队列操作，后续付款人仍继续结算；队列清空后按恢复上下文还原行动玩家和回合阶段。实现全部 16 张机会卡、两步骤抢夺选卡、强制超限弃牌、C-028 half-up 取整/银行差额、持续效果和产权卡原子校验。 | 构造 `GameEngine(config)`，以 `execute(command)` 提交命令；不依赖 LLM。 |
| `controllers.py` | 提供固定命令序列的脚本化控制器；复用领域层唯一的完整 `GameCommand` 联合类型，覆盖强制弃牌、抢夺选卡、社区基金出狱卡与结构化机会卡使用。 | 主要供自动化测试和无 LLM 模拟调用。 |
| `runner.py` | 执行无 LLM 的固定命令脚本，持久化命令与领域事件；命令耗尽且仍处于 `FORCED_DISCARD` 或 `THEFT_CARD_SELECTION` 时报告 `awaiting_decision`。结果快照覆盖卡堆、手牌、免租、持续效果、含移动上下文的结算队列、临时抢夺状态、玩家和产权。 | 调用 `run_scripted_game(config, controller, artifacts)`。 |
| `replay.py` | 从冻结配置和已记录命令重放运行产物，验证事件编号、事件内容和最终状态快照；状态比较忽略非状态统计键。 | 调用 `verify_run(run_directory)`；不依赖运行时 RNG、LLM 或控制器。 |

## 决策协议（`src/monopoly_agent_battle/decision/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `decision/models.py` | 定义冻结的决策请求、合法候选项（含候选 `title` / `preview` / `response_format` 及 `OptionTarget` 目标规格）、响应、校验结果及其 JSON 审计表示。 | 决策生成、提示词渲染、运行器和审计记录共享。 |
| `decision/wording.py` | 集中定义每个普通命令与机会卡候选的 `OptionWording(title, preview, response_format)`；候选文本与单/双目标输出格式已通过项目负责人逐项人工验收，不与候选生成逻辑混放。 | `requests.py` 以候选代表命令调用 `option_wording(command)` 取得完整候选文案。 |
| `decision/requests.py` | 从当前引擎状态投影完整允许的玩家可见视图：公开棋盘格名称/类型/价格/建造成本/租金/税、所有公开资产与状态、当前落点、持续效果及自己的卡牌。以克隆引擎预执行过滤候选，并按「命令形状」折叠（`command_type + 固定参数`），`DecisionOption.target` 记录目标字段与合法取值；只为付款处置、资产管理、监狱滚骰、强制弃牌和抢夺选卡创建请求。仅抢夺成功后的单次请求临时显示目标机会卡；牌堆、RNG、审计 ID、其他玩家实时手牌和运行时信息不会进入普通视图。候选文案由 `wording.py` 透传。 | 每个实际决策点调用 `build_decision_request(engine, sequence)`。 |
| `decision/prompts.py` | 将决策请求稳定渲染成中文 AI Prompt，按六段式样板（角色与目标、游戏规则、当前局面、当前决策、合法候选、输出要求）逐段实现；① 角色与目标（暂定）、③ 当前局面（回合概述、你的状态、其他玩家状态、棋盘状态表）、④ 当前决策（监狱 / 付款处置 / 强制弃牌 / 抢夺选卡 / 资产管理五类自然语言因果句）、⑤ 候选 `option_id` / `title` / `preview` / `response_format` 与 ⑥ `reason` 输出要求已实现。候选 `response_format` 归 `wording.py` 所有；本模块仅将 `{option_id}` 替换为实际候选 ID 后渲染。AI 可见上下文不含 `decision_id`、`game_id`、冻结命令参数、付款操作 ID、牌堆顺序、RNG 或重试/回退信息。另提供 `options_from_prompt(prompt)` 供确定性 Mock 客户端解析候选段落。 | 由实际 LLM 控制器未来调用；`tests/manual/render_decision_prompt.py` 可生成固定完整 Prompt 供负责人检查。 |
| `decision/protocol.py` | 解析并严格校验不可信 JSON 响应；`selected_option` 为 `{"option","target"}` 对象，校验 `option` 与 `target` 合法取值后合并参数并重建引擎命令；复用领域层唯一的完整 `GameCommand` 联合类型。 | 由决策运行器在调用引擎前使用。 |
| `decision/runner.py` | 以决策协议运行完整对局；只对真实选择节点调用控制器并写决策审计。提供确定性默认策略、断线重连与校验失败反馈重试、按玩家分发控制器；结果含调用/重连/回退统计与 10% 无效判定。 | 调用 `run_decision_game(engine, controller, artifacts)`。 |
| `MonopolyAgentBattle_developer_docs/stage3-problems.md` | 记录负责人 2026-08-13 提出的 Stage 3 规则反馈和边界说明；问题 1–5（强制弃牌、自动建房、自动破产、自动免租、两步骤抢夺）已于 2026-08-14 完成代码实现与自动化回归，问题 6 的 Prompt 已于 2026-08-16 通过负责人人工验收。 | 作为 Stage 3 规则与交接记录，须结合开发看板中的实际验证结果阅读。 |
| `MonopolyAgentBattle_developer_docs/stage3-decision-prompt-template.md` | Stage 3 决策提示词六段式目标样板；记录每层目标形态、标识体系统一约定（玩家 `player_id` / 格子数字 / 颜色组英文键 / 机会卡 `card_id`）、实现状态与已确认决策；已记录 2026-08-16 的 Prompt 人工验收通过状态。 | 作为问题 6 Prompt 重做的设计、验收与交接基准。 |
| `MonopolyAgentBattle_developer_docs/history_context_supplement.md` | Stage 4 历史上下文系统方案、10 段 prompt 结构、可见性规则、固定句式目录与 4A/4B/4C/4D 子阶段拆分；其中 §五白名单句式已于 2026-08-18 通过项目负责人人工审核。 | 作为 4B/4C 上下文播报与会话构建实现的依据。 |


## LLM 抽象与 Agent（`src/monopoly_agent_battle/llm/`、`agents/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `llm/protocol.py` | 供应商无关 LLM 协议（消息列表请求/响应、用量、错误类型）。 | 由各客户端与适配器实现。 |
| `llm/mock_client.py` | 确定性可播种的 Mock LLM 客户端（含首项/种子/脚本策略）。 | 无凭据对局、CI 与测试使用。 |
| `llm/recording_client.py` | 包装任意客户端，逐次调用（含失败）写入 `llm_calls.jsonl` 并重抛异常。 | 由 `play`/集成测试组装；供调用统计与无效阈值。 |
| `llm/registry.py` | 按供应商别名注册/创建客户端（可插拔适配器）。 | `register_client_factory`/`create_client`；4A 仅注册 `"mock"`。 |
| `agents/baseline.py` | BaselineAgent：单模型玩家控制器，渲染决策 Prompt 并调用 LLM 返回响应；支持校验失败临时反馈。 | 由 `play`/实验组装为 `DispatchController` 输入。 |

## 历史上下文系统（`src/monopoly_agent_battle/context/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `context/broadcast.py` | 固定句式事件播报器（Stage 4B）。将 `GameEvent` 确定性渲染为中文固定句式；白名单 33 个事件、豁免 16 个事件，覆盖全部 49 个引擎事件类型。按 `viewer_id` 区分涉己/旁观渲染（机会卡抽取、弃置、被抢夺对观察者隐藏卡名；社区基金卡全员可见）。`BROADCAST_VERSION="v1"` 供 `sentence_template_version` 参考；未注册事件抛 `UnregisteredEventError`。 | `render_event(event, viewer_id) -> str \| None`；白名单事件返回句式,豁免事件返回 None。 |
| `context/conversation.py` | 会话管理器（Stage 4C）。为每个 Agent（玩家）维护独立的 `AgentConversation`，包含消息列表（`ConversationMessage`）、行动回合列表、每轮事件记录；支持窗口管理（最近 N 个该 Agent 行动回合，默认 3）和历史播报轮次管理（最近 N 轮全局历史，默认 10）；提供窗口边界计算、消息查询（窗口内/外）和轮次查询。Stage 4 中 `agent_id = player_id`；Stage 5 中 CourtAgent 的多个内部 AI 共享同一 `AgentConversation`。 | `AgentConversation` 实例化后调用 `add_decision_request()`、`add_decision_response()`、`add_round_events()`；查询方法供 composer 使用。 |
| `context/composer.py` | Prompt 组装器（Stage 4C）。实现 10 段 Prompt 结构：(1) 系统指令、(2) 游戏规则、(3) 历史事件播报（窗口外压缩）、(4) 会话历史（窗口内完整重放）、(5-7) 当前状态、(8) 当前决策、(9) 合法候选、(10) 输出要求。无历史时跳过段 3 和段 4；支持 Token 限制下的历史裁剪（优先裁剪段 3，其次段 4）。返回 OpenAI 格式的 `LLMMessage` 列表。 | `compose_prompt(conversation, decision_request, validation_feedback=None, token_cap=None) -> list[LLMMessage]`；由 BaselineAgent（4D）调用。 |
| `context/rules.py` | 游戏规则加载器（Stage 4C）。从 `doc/monopoly_rules_basic.md` 加载游戏规则并缓存（`@lru_cache`），供 Prompt 第 2 段使用。 | `load_game_rules() -> str`；由 composer 内部调用。 |
| `context/validation_feedback.py` | 校验反馈格式化器（Stage 4C）。将校验失败错误格式化为中文反馈文本，包含重试次数和错误信息，供临时附加到 Prompt 末尾。 | `format_validation_feedback(attempt, error_message) -> str`；由 BaselineAgent（4D）调用。 |
| `context/token_guard.py` | Token 预算保护器（Stage 4C）。使用简化 Token 估算（中文 1.5 tokens/字，其他 0.3 tokens/字符）；裁剪策略：优先裁剪历史播报（按轮次），其次裁剪会话历史（按回合），保护系统指令、游戏规则和当前状态；保护段超限时抛出 `TokenLimitExceededError`。 | `apply_token_limit(segments, token_cap, protected_segments) -> dict[str, str]`；由 composer 内部调用。 |


| 路径 | 用途 | 使用方式 |
|---|---|---|
| `logging/run_artifacts.py` | 创建单局运行目录，持久化冻结配置、JSONL 命令/领域事件、决策审计、LLM 调用记录、私有运行时重试/回退事件与结果快照。 | 由单局运行器调用。 |
| `cli/main.py` | 提供 `demo`（阶段 0 运行产物闭环演示）与 `play`（无凭据 Mock LLM baseline 完整对局）命令。 | `monopoly-agent-battle demo --config configs/games/phase0_demo.yaml` / `play --config configs/games/phase4_mock_demo.yaml` |

## 自动化测试（`tests/`）

| 路径 | 覆盖范围 | 使用方式 |
|---|---|---|
| `tests/unit/test_config.py` | 配置校验、YAML 加载和配置哈希。 | `python -m pytest tests/unit/test_config.py` |
| `tests/unit/test_decision_protocol.py` | 决策可见性隔离、`current_space.rent` 仅未付金额、实际决策阶段候选项、普通流程拒绝创建请求、响应 schema 拒绝、Prompt 审计字段隔离、监狱多选项、付款上下文不暴露内部操作 ID、抢夺选卡期间的临时目标手牌可见性及选后恢复隔离、候选 `response_format` 渲染，以及 Prompt 自然语言渲染（角色目标、你的状态、其他玩家状态、棋盘状态表、同盟与剩余监狱回合数）。 | `python -m pytest tests/unit/test_decision_protocol.py` |
| `tests/manual/render_decision_prompt.py` | 构造固定资产管理场景并打印完整实际 Prompt，覆盖无目标、单目标和双目标候选的 `response_format`，供负责人逐字人工检查；不是 pytest 自动化测试。 | `.venv/Scripts/python.exe tests/manual/render_decision_prompt.py` |
| `tests/integration/test_decision_runner.py` | 决策驱动完整对局、自动普通掷骰事件审计/回放、监狱掷骰 Prompt 选择、监狱等待的自动推进、连接重试、回退及原始校验错误保留。 | `python -m pytest tests/integration/test_decision_runner.py` |
| `tests/unit/test_llm_protocol.py` | LLM 协议与 Mock/录制客户端行为。 | `python -m pytest tests/unit/test_llm_protocol.py` |
| `tests/unit/test_baseline_agent.py` | BaselineAgent 请求构造、校验失败反馈段落与错误传播。 | `python -m pytest tests/unit/test_baseline_agent.py` |
| `tests/integration/test_llm_runner.py` | Mock LLM baseline 完整对局与审计/回放、重连超阈值判无效、校验反馈重试计数。 | `python -m pytest tests/integration/test_llm_runner.py` |
| `tests/unit/context/test_broadcast.py` | 上下文播报器单元测试（Stage 4B）：豁免事件返回 None、全引擎事件类型穷举（白名单+豁免覆盖全部 49 个事件）、未注册事件抛异常、确定性渲染、涉己/旁观差异（card_drawn、card_discarded、chance_card_stolen）、payment_made 银行/玩家、player_jailed 原因映射、棋盘名称回退。 | `python -m pytest tests/unit/context/test_broadcast.py` |
| `tests/unit/context/test_conversation.py` | 会话管理器单元测试（Stage 4C）：窗口边界计算（空历史、少于/正好/超过窗口大小）、消息添加和查询、窗口内/外消息区分、行动回合列表、历史播报轮次管理、事件记录。 | `python -m pytest tests/unit/context/test_conversation.py` |
| `tests/unit/context/test_composer.py` | Prompt 组装器单元测试（Stage 4C）：10 段结构完整性、无历史时跳过段 3+4、窗口内/外历史正确渲染、校验反馈附加、Token 限制下的裁剪、保护段不被裁剪、消息列表格式。 | `python -m pytest tests/unit/context/test_composer.py` |
| `tests/unit/context/test_rules.py` | 游戏规则加载器单元测试（Stage 4C）：规则文件加载、LRU 缓存机制、内容完整性。 | `python -m pytest tests/unit/context/test_rules.py` |
| `tests/unit/context/test_validation_feedback.py` | 校验反馈格式化器单元测试（Stage 4C）：反馈文本格式化、重试次数显示、多行错误信息。 | `python -m pytest tests/unit/context/test_validation_feedback.py` |
| `tests/unit/context/test_token_guard.py` | Token 保护器单元测试（Stage 4C）：Token 估算准确性（空/英文/中文/混合/CJK 范围）、预算管理、分段裁剪策略、保护段完整性、超限异常。 | `python -m pytest tests/unit/context/test_token_guard.py` |
| `tests/manual/render_history_broadcast.py` | 手动验收脚本（Stage 4B）：使用直接状态注入（从 `test_chance_cards.py` 习得的模式）创建 20 个独立场景，通过控制玩家位置、直接注入机会卡、设置产权归属和控制骰子序列，覆盖全部 33 个白名单事件（每个事件≥2次出现）。生成 `tests/manual/history_broadcast_report.txt` 完整事件日志供项目负责人人工审核中文句式质量。2026-08-19 运行通过，exit status 0，33/33 事件达标。 | `.venv/Scripts/python.exe tests/manual/render_history_broadcast.py` |
| `tests/unit/game/test_board.py` | 40 格棋盘数据完整性与产权数值。 | `python -m pytest tests/unit/game/test_board.py` |
| `tests/unit/game/test_engine.py` | 移动、租金、抵押、建造和双骰入狱等核心规则。 | `python -m pytest tests/unit/game/test_engine.py` |
| `tests/unit/game/test_turn_flow.py` | 双骰、阶段转换、付款处置以及无可用清算操作时自动破产的回合状态机。 | `python -m pytest tests/unit/game/test_turn_flow.py` |
| `tests/unit/game/test_jail_and_endgame.py` | 监狱付费/双骰/第三次失败、酒店出售、完整轮次与终局规则。 | `python -m pytest tests/unit/game/test_jail_and_endgame.py` |
| `tests/integration/test_scripted_game.py` | 脚本化控制器提交命令的端到端行为。 | `python -m pytest tests/integration/test_scripted_game.py` |
| `tests/unit/game/test_cards.py` | 验证社区基金抽取/弃置/持有、生日多方付款恢复（含前序付款者自动破产后保留后续付款、原持卡人行动权与阶段恢复）、GO 移动队列、不授予卡牌移动建房资格、双骰后抽牌移动，以及应付租金自动免除与查封租金不消耗免租次数。 | `python -m pytest tests/unit/game/test_cards.py` |
| `tests/unit/game/test_chance_cards.py` | 覆盖 16 张机会卡的 48 个文字验收场景（每张 2 个有效场景与 1 个无效场景），并覆盖现金/产权/建筑、C-028 均富和税额、购地价格、抢夺骰结果与成功后的两步选卡、持续效果精确期限与查封优先组合租金、自动免租、强制超限弃牌；无效路径使用完整状态快照验证原子拒绝。另含卡堆重洗、持有卡破产归还、产权转移后颜色组效果和查封+同盟边界回归。 | `python -m pytest tests/unit/game/test_chance_cards.py` |
| `tests/integration/test_scripted_runner.py` | 覆盖终局审计产物、固定序列回放、机会卡抽取/使用、双地产目标及事件编号篡改拒绝；所有调用 `verify_run()` 的场景均由冻结配置、固定随机结果和正式命令重建，不依赖测试前隐藏状态注入。 | `python -m pytest tests/integration/test_scripted_runner.py` |
| `tests/integration/test_cli_demo.py` | CLI 创建可审计运行目录的端到端闭环。 | `python -m pytest tests/integration/test_cli_demo.py` |

## 常用质量检查

```bash
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/ruff.exe check .
.venv/Scripts/pyright.exe
.venv/Scripts/python.exe -m pytest
```
