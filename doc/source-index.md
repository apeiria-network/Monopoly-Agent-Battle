# 脚本索引

本索引按项目目录和职责组织。测试文件仅用于验证对应模块；运行产物 `runs/` 不属于源代码。

## 配置与示例（`configs/`、`src/.../config/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `configs/games/example.yaml` | 商、秦、唐、明四朝廷 OpenAI 兼容接口示例；13 名官员分别绑定独立 URL、API Key 环境变量、模型及 `seed: 42`。 | 替换示例 URL 和模型名、设置对应环境变量后，作为 `monopoly-agent-battle play --config` 的输入。 |
| `configs/games/fake_llm_demo.yaml` | 四名普通 Fake LLM 玩家示例，不发送网络请求。 | 作为 `play --config` 的输入。 |
| `configs/games/four_courts_fake_demo.yaml` | 商、秦、唐、明四朝廷 Fake LLM 示例。 | 无需 API Key，可测试完整朝廷流程。 |
| `configs/games/phase0_demo.yaml` | 四玩家 Level 0 示例对局配置。 | 作为 `demo --config` 的输入。 |
| `configs/games/phase4_mock_demo.yaml` | 四玩家 Mock LLM baseline 对局配置（`provider: mock`，无凭据）。 | 作为 `monopoly-agent-battle play --config` 的输入。 |
| `configs/games/shang_court_mock_demo.yaml` | 商代双角色朝廷的无凭据短局配置：一名 `shang_court` 玩家分别绑定 `mock-priest` 与 `mock-emperor`，搭配随机玩家运行 Level 0 对局。 | 作为 `monopoly-agent-battle play --config` 的输入。 |
| `configs/games/tang_court_mock_demo.yaml` | 唐代三角色朝廷的无凭据短局配置：一名 `tang_court` 玩家分别绑定中书省、门下省与皇帝的 Mock 模型，搭配随机玩家运行 Level 0 对局。 | 作为 `monopoly-agent-battle play --config` 的输入。 |
| `configs/games/ming_court_mock_demo.yaml` | 明代四角色朝廷的无凭据短局配置：一名 `ming_court` 玩家绑定首辅、两名共用模型的大学士和皇帝，搭配随机玩家运行 Level 0 对局。 | 作为 `monopoly-agent-battle play --config` 的输入。 |
| `configs/games/random_baseline_demo.yaml` | 四玩家完全随机、非 LLM 的 Level 0 示例配置；显式使用 `controller_type: random_baseline`，不含 `model_profiles`。 | 作为 `monopoly-agent-battle play --config` 的输入；不产生 LLM 调用产物。 |
| `src/monopoly_agent_battle/config/models.py` | 定义并校验单局配置、控制器及模型绑定；每个玩家或官员的 profile 可独立配置 URL、API Key 环境变量、模型、LLM seed 和调用参数。 | 由配置加载器和对局入口调用；真实 API Key 不进入配置。 |
| `src/monopoly_agent_battle/config/loader.py` | 加载 YAML 配置，生成规范 JSON 及 SHA-256 `config_hash`。 | 由 CLI 或实验编排调用。 |

## 使用文档（`doc/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `doc/game-config-tutorial.md` | 面向普通使用者的游戏 YAML 配置教程，说明玩家控制器、朝廷官员 profile、OpenAI 兼容接口、环境变量 API Key 和 LLM seed 的写法。 | 编写或修改对局配置前阅读。 |
| `doc/manual-game-run-tutorial.md` | 手动启动对局、准备环境、查看运行产物和执行回放验证的教程。 | 按步骤运行 `play` 或 `demo`。 |

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
| `replay.py` | 从冻结配置和已记录命令重放运行产物，验证事件编号、事件内容和最终状态快照；状态比较忽略非状态统计键（包括 LLM 调用、重连、回退和有效性统计）。 | 调用 `verify_run(run_directory)`；不依赖运行时 RNG、LLM 或控制器。 |

## 决策协议（`src/monopoly_agent_battle/decision/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `decision/models.py` | 定义决策请求、候选项、响应和校验结果；审计记录包含目标字段映射、规范化目标及错误类别。 | 决策生成、提示词渲染、运行器和审计记录共享。 |
| `decision/wording.py` | 集中定义每个普通命令与机会卡候选的 `OptionWording(title, preview, response_format)`；候选文本与单/双目标输出格式已通过项目负责人逐项人工验收，不与候选生成逻辑混放。 | `requests.py` 以候选代表命令调用 `option_wording(command)` 取得完整候选文案。 |
| `decision/requests.py` | 从当前引擎状态投影完整允许的玩家可见视图：公开棋盘格名称/类型/价格/建造成本/租金/税、所有公开资产与状态、当前落点、持续效果及自己的卡牌。以克隆引擎预执行过滤候选，并按「命令形状」折叠（`command_type + 固定参数`），`DecisionOption.target` 记录目标字段与合法取值；只为付款处置、资产管理、监狱滚骰、强制弃牌和抢夺选卡创建请求。仅抢夺成功后的单次请求临时显示目标机会卡；牌堆、RNG、审计 ID、其他玩家实时手牌和运行时信息不会进入普通视图。候选文案由 `wording.py` 透传。 | 每个实际决策点调用 `build_decision_request(engine, sequence)`。 |
| `decision/prompts.py` | Stage 4C 提示词渲染：角色、规则与固定输出要求构成 system；当前局面、决策与合法候选构成动态 user；候选专属 `response_format` 留在候选 JSON。 | `compose_prompt()` 分别调用 system 与当前 user 渲染器；Mock 客户端以 `options_from_prompt()` 读取候选；兼容旧调用方可使用 `render_decision_prompt()` 取得相同顺序的单字符串视图。 |
| `decision/protocol.py` | 解析并严格校验不可信 JSON 响应；`selected_option` 为 `{"option","target"}` 对象，校验 `option` 与 `target` 合法取值后合并参数并重建引擎命令；`option_json()` 统一将指定合法目标元组编码为单字段标量或多字段对象，供默认回退和随机 baseline 复用；复用领域层唯一的完整 `GameCommand` 联合类型。 | 由决策运行器在调用引擎前使用。 |
| `decision/runner.py` | 以决策协议运行完整对局，并维护每位 Agent 的行动回合、事件历史、校验重试和默认回退；区分全部决策回退与 LLM 触发的默认回退，并按后者占 LLM 调用数的比例计算对局有效性；朝廷玩家的绩效证据与窗口由此接入运行流程。 | 调用 `run_decision_game(engine, controller, artifacts, conversations=..., performance_tracker=...)`；CLI 通过 `DispatchController` 分派至各 Agent。 |
| `MonopolyAgentBattle_developer_docs/stage3-problems.md` | 记录负责人 2026-08-13 提出的 Stage 3 规则反馈和边界说明；问题 1–5（强制弃牌、自动建房、自动破产、自动免租、两步骤抢夺）已于 2026-08-14 完成代码实现与自动化回归，问题 6 的 Prompt 已于 2026-08-16 通过负责人人工验收。 | 作为 Stage 3 规则与交接记录，须结合开发看板中的实际验证结果阅读。 |
| `MonopolyAgentBattle_developer_docs/stage3-decision-prompt-template.md` | Stage 3 决策提示词六段式目标样板；记录每层目标形态、标识体系统一约定（玩家 `player_id` / 格子数字 / 颜色组英文键 / 机会卡 `card_id`）、实现状态与已确认决策；已记录 2026-08-16 的 Prompt 人工验收通过状态。 | 作为问题 6 Prompt 重做的设计、验收与交接基准。 |
| `MonopolyAgentBattle_developer_docs/history_context_supplement.md` | Stage 4 历史上下文系统方案、10 段 prompt 结构、可见性规则、固定句式目录与 4A/4B/4C/4D 子阶段拆分；其中 §五白名单句式已于 2026-08-18 通过项目负责人人工审核。 | 作为 4B/4C 上下文播报与会话构建实现的依据。 |


## LLM 抽象与 Agent（`src/monopoly_agent_battle/llm/`、`agents/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `llm/protocol.py` | 供应商无关 LLM 协议，统一消息、模型、LLM seed、采样参数、响应、用量和错误类型。 | 由各客户端与 Agent 使用。 |
| `llm/mock_client.py` | 确定性可播种的 Mock LLM 客户端（含首项/种子/脚本策略）。 | 无凭据对局、CI 与测试使用。 |
| `llm/fake_client.py` | 接收完整上下文并本地随机生成协议合法回复，不发送网络请求。 | 配置 `provider: fake` 后由 `play` 创建。 |
| `llm/openai_compatible_client.py` | 调用 OpenAI 兼容 `/chat/completions` 接口；按 profile 读取独立 URL 和 API Key 环境变量，并归一化响应与 token 用量。 | 配置 `provider: openai_compatible` 后由 `play` 创建。 |
| `llm/recording_client.py` | 包装任意客户端，逐次调用（含失败）写入 `llm_calls.jsonl` 并重抛异常。 | 由 `play`/集成测试组装；供调用统计与无效阈值。 |
| `llm/registry.py` | 按供应商别名注册和创建客户端。 | `play` 注册 `mock`、`fake` 与 `openai_compatible`。 |
| `agents/baseline.py` | BaselineAgent（Stage 4D）：每次决策由 `compose_prompt()` 构造完整消息列表；段 3 告警仅作私有运行时记录，不进入 LLM 消息；标记为 LLM 控制器供运行器计量。 | 由 `play`/实验组装为 `DispatchController` 输入。 |
| `agents/shang.py` | 商代双角色 CourtAgent：大祭司仅根据当前问题生成神谕，皇帝结合既有上下文和神谕作出最终协议回复；支持分阶段重试与私有审计。提示词为暂定版本，待人工重写审核。 | `play` 为 `shang_court` 玩家组装使用。 |
| `agents/qin.py` | 秦代四角色 CourtAgent：丞相与太尉并行独立进言，御史大夫综合评价两者建议，皇帝最后裁决并产出唯一引擎决策；已结算的真实绩效仅提供给御史大夫。 | `play` 为 `qin_court` 玩家组装使用。 |
| `agents/tang.py` | 唐代三角色 CourtAgent：中书省起草、门下省审核、皇帝终裁；最多三轮，皇帝按否决次数读取最后一轮或完整三轮内部记录。内部消息使用可信角色元数据，门下省严格限制为 `agree` / `disagree` 审核 JSON，非法输出按角色重试并安全回退。 | `play` 为 `tang_court` 玩家组装使用。 |
| `agents/ming.py` | 明代四角色 CourtAgent：首辅与两名大学士并行草拟，分歧时依据其他已完成草案并行重拟，仍不一致时按首辅 1.5、两名大学士各 1.0 加权投票；首辅 advice 的选项由系统强制采用一致或投票结果并支持重试，皇帝读取 advice 后最终裁决。当前决策与历史决策按角色隔离投递，首辅自身 advice 保留为 assistant 消息。提示词为暂定版本，待人工重写审核。 | `play` 为 `ming_court` 玩家组装使用。 |
| `performance/random_generator.py` | 保留旧随机官员绩效文本生成逻辑，供兼容或独立测试使用；不参与当前真实绩效生产流程。 | 生产对局不调用。 |
| `performance/scoring.py` | 定义决策签名、官员意见证据、1 回合/3 回合窗口结果及一致率差评规则。 | 由绩效跟踪器调用，结果可写入 `performance.jsonl`。 |
| `performance/evidence.py` | 将协议校验后的决策回复转换为标准化绩效证据。 | 由决策运行器和绩效跟踪器调用。 |
| `performance/tracker.py` | 按朝廷玩家自身行动回合保存净资产快照和官员意见，结算基础及长期绩效窗口。 | 由 CLI 创建并传入决策运行器。 |
| `agents/random_baseline.py` | 可复现的完全随机非 LLM 控制器：从请求的合法候选及对应合法目标元组中选择，并生成标准决策 JSON；不依赖 Prompt、会话、LLM 客户端、模型配置或凭据。 | `play` 为每个 `random_baseline` 玩家注入独立稳定派生 RNG 后组装使用。 |

### Agent 提示词文档（`src/monopoly_agent_battle/agents/agent_prompt_list/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `normal_output_requirement.txt` | 秦代普通角色的段 3 通用 JSON 输出要求。 | 由 `agents/qin.py` 为丞相、太尉和皇帝加载；秦代手动渲染脚本同步加载。 |
| `Qin/Qin_chancellor.txt` | 秦代丞相的段 1 角色身份与职责提示词。 | 由 `agents/qin.py` 和秦代手动渲染脚本加载。 |
| `Qin/Qin_grand_marshal.txt` | 秦代太尉的段 1 角色身份与职责提示词。 | 由 `agents/qin.py` 和秦代手动渲染脚本加载。 |
| `Qin/Qin_imperial_counsellor.txt` | 秦代御史大夫的段 1 角色身份与职责提示词。 | 由 `agents/qin.py` 和秦代手动渲染脚本加载。 |
| `Qin/Qin_emperor.txt` | 秦代皇帝的段 1 角色身份与职责提示词。 | 由 `agents/qin.py` 和秦代手动渲染脚本加载。 |
| `Qin/Qin_cousellor_output_requirement.txt` | 秦代御史大夫的专属段 3 JSON 评价输出要求。 | 由 `agents/qin.py` 和秦代手动渲染脚本为御史大夫加载。 |
| `Qin/Qin_cousellor_candidates.txt` | 秦代御史大夫的第 11 段特殊候选项及评价 JSON 格式。 | 由 `agents/qin.py` 和秦代手动渲染脚本为御史大夫加载。 |
| `Tang/Tang_zhongshu.txt` | 唐代中书省的角色身份提示词。 | 由 `agents/tang.py` 加载。 |
| `Tang/Tang_menxia.txt` | 唐代门下省的角色身份提示词。 | 由 `agents/tang.py` 加载。 |
| `Tang/Tang_emperor.txt` | 唐代皇帝的角色身份提示词。 | 由 `agents/tang.py` 加载。 |
| `Tang/Tang_menxia_output_requirement.txt` | 唐代门下省专属审核 JSON 输出要求，仅允许 `agree` 或 `disagree`，不含 `target`。 | 由 `agents/tang.py` 为门下省加载。 |
| `Tang/Tang_menxia_candidates.txt` | 唐代门下省的第 11 段特殊候选项及审核 JSON 格式。 | 由 `agents/tang.py` 和唐代手动渲染脚本为门下省加载。 |
| `Ming/chief_grand_secretary.txt` | 明代首辅的角色身份与职责提示词。 | 由 `agents/ming.py` 为首辅加载。 |
| `Ming/grand_secretary.txt` | 明代两名大学士共用的角色身份与职责提示词。 | 由 `agents/ming.py` 为 `grand_secretary_1` 和 `grand_secretary_2` 加载。 |
| `Ming/emperor.txt` | 明代皇帝的角色身份与职责提示词。 | 由 `agents/ming.py` 为皇帝加载。 |

## 历史上下文系统（`src/monopoly_agent_battle/context/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `context/broadcast.py` | 固定句式事件播报器（Stage 4B）。将 `GameEvent` 确定性渲染为中文固定句式；白名单 33 个事件、豁免 16 个事件，覆盖全部 49 个引擎事件类型。按 `viewer_id` 区分涉己/旁观渲染（机会卡抽取、弃置、被抢夺对观察者隐藏卡名；社区基金卡全员可见）。`BROADCAST_VERSION="v1"` 供 `sentence_template_version` 参考；未注册事件抛 `UnregisteredEventError`。 | `render_event(event, viewer_id) -> str \| None`；白名单事件返回句式，豁免事件返回 None。 |
| `context/rules.py` | Stage 4C 段 2 游戏规则文本加载器。运行时读 `doc/monopoly_rules_basic.md`，模块级缓存。`GAME_RULES_VERSION="v1"`；测试用 `reset_cache()` 清缓存。 | `load_game_rules() -> str`。 |
| `context/conversation.py` | 每 Agent 独立会话，按行动回合保存事件、决策和校验错误；回合开始重建段 3 历史缓存，独立严格限制为 500 token，并保持至该行动回合结束。 | 创建 `AgentConversation(agent_id, window_turns=1)`；runner 在行动回合开始调用 `start_turn()`，composer 读取其历史和当回合记录。 |
| `context/token_guard.py` | 估算文本 token 并裁剪段 3 历史：计入事件间换行，从最旧完整事件开始删除，确保保留内容严格满足预算；发生裁剪时返回运行时警告。 | `estimate_tokens(text)` 用于检查估算长度；`truncate_events_to_budget(rendered, budget)` 返回保留事件及可选 `ContextWarning`。 |
| `context/composer.py` | 将私有会话与当前决策组合为角色分离的 LLM messages：system 放固定指令，user 放历史、回放和当前请求；连续可见事件用单换行，语义块之间保留空行；重试按 assistant 回复后接 user 反馈回放；同一决策 ID 的历史问题只渲染一次，同时保留各条 assistant 回复、可信内部消息、错误反馈和事件顺序。 | BaselineAgent 每次请求调用 `compose_prompt(conversation, request)`，取得 messages 及段 3 的可选运行时警告。 |
| `context/validation_feedback.py` | Stage 4C 校验失败用户可见反馈模板；依据 `DecisionValidation.error_category` 选择模板。 | `build_feedback(validation, request) -> str`。 |
| `MonopolyAgentBattle_developer_docs/history_context_supplement.md` | Stage 4 历史上下文规范及 4C-remake 确认设计：system/user 消息归属、段 3 独立严格 500-token 裁剪、事件换行、运行时告警隔离和校验反馈生命周期。 | 作为 4C-remake 的实现与人工审核依据；4D 开发前应先查阅。 |

## 运行产物与 CLI（`src/monopoly_agent_battle/logging/`、`cli/`）

| 路径 | 用途 | 使用方式 |
|---|---|---|
| `logging/run_artifacts.py` | 创建单局运行目录，持久化冻结配置、JSONL 领域事件、决策审计、LLM 调用、私有运行时事件、绩效窗口、结果快照及对局播报。 | 由单局运行器调用。 |
| `cli/main.py` | 提供 `demo` 和完整对局 `play`；按配置组装随机、普通 LLM、商、秦、唐、明控制器，并注册 Mock 或 OpenAI 兼容客户端。 | `.venv/Scripts/monopoly-agent-battle.exe play --config configs/games/example.yaml`。 |

## 自动化测试（`tests/`）

| 路径 | 覆盖范围 | 使用方式 |
|---|---|---|
| `tests/unit/test_config.py` | 配置校验、YAML 加载和配置哈希；覆盖显式随机/LLM 控制器的模型配置约束、商代与秦代 `court_role_profiles` 的接受/缺失/角色不匹配校验，以及控制器类型对哈希的影响。 | `python -m pytest tests/unit/test_config.py` |
| `tests/unit/test_decision_audit_schema.py` | 决策审计字段、目标映射和错误类别回归测试。 | `.venv/Scripts/python.exe -m pytest -q --no-cov tests/unit/test_decision_audit_schema.py` |
| `tests/unit/test_decision_protocol.py` | 决策可见性隔离、`current_space.rent` 仅未付金额、实际决策阶段候选项、普通流程拒绝创建请求、响应 schema 拒绝、Prompt 审计字段隔离、监狱多选项、付款上下文不暴露内部操作 ID、抢夺选卡期间的临时目标手牌可见性及选后恢复隔离、候选 `response_format` 渲染、随机 baseline 复用的合法多字段目标 JSON 编码，以及 Prompt 自然语言渲染（角色目标、你的状态、其他玩家状态、棋盘状态表、同盟与剩余监狱回合数）。 | `python -m pytest tests/unit/test_decision_protocol.py` |
| `tests/unit/test_shang_agent.py` / `tests/integration/test_shang_runner.py` | 商代角色边界、分阶段重试、私有 trace、LLM 计量、隐私隔离与回放验证。 | `.venv/Scripts/python.exe -m pytest tests/unit/test_shang_agent.py tests/integration/test_shang_runner.py` |
| `tests/unit/test_qin_agent.py` | 秦代四角色调用顺序与第 5 段内部消息可见性、御史大夫结构校验重试与 neutral 安全回退、丞相/太尉角色级重试与默认回退、当前决策隐藏皇帝最终裁决、最终决策幂等广播。 | `.venv/Scripts/python.exe -m pytest tests/unit/test_qin_agent.py` |
| `tests/unit/test_tang_agent.py` | 唐代三角色串行调用、三轮上限、门下省非对象/非法 JSON 安全重试与回退，以及最终决策幂等广播、多轮自身回复保留、皇帝最终决策历史持久化及可信投递。 | `.venv/Scripts/python.exe -m pytest tests/unit/test_tang_agent.py` |
| `tests/unit/test_ming_agent.py` | 明代四角色并行草案、分歧重拟、加权投票、首辅 advice 强制结果与重试、首辅 advice assistant 历史、其他角色 advice/投票历史可见性及重拟草案隔离。 | `.venv/Scripts/python.exe -m pytest tests/unit/test_ming_agent.py` |
| `tests/integration/test_tang_runner.py` | 唐代运行器集成、连接失败、审计产物、court trace 和 `verify_run()` 回放验证。 | `.venv/Scripts/python.exe -m pytest tests/integration/test_tang_runner.py` |
| `tests/unit/test_random_baseline.py` | 完全随机非 LLM 控制器的确定性响应序列、协议合法性、合法多字段目标编码和非 LLM 计量标识。 | `python -m pytest tests/unit/test_random_baseline.py` |
| `tests/integration/test_random_baseline_runner.py` | 纯随机和随机/Mock-LLM 混合完整对局：审计、回放、跨运行复现、无 LLM 产物、LLM 计量隔离及连接失败阈值。 | `python -m pytest tests/integration/test_random_baseline_runner.py` |
| `tests/integration/test_decision_runner.py` | 决策驱动完整对局、自动普通掷骰事件审计/回放、监狱掷骰 Prompt 选择、监狱等待的自动推进、连接重试、回退及原始校验错误保留。 | `python -m pytest tests/integration/test_decision_runner.py` |
| `tests/integration/test_stage6_fault_audit.py` | Stage 6 决策故障审计：非法响应、重试、回退、跨产物关联、统计隔离、10% 阈值及无效局完成和回放。 | `.venv/Scripts/python.exe -m pytest -q --no-cov tests/integration/test_stage6_fault_audit.py` |
| `tests/integration/test_stage6_golden_replay.py` | 固定种子四人整局的事件、决策、结果确定性及完整回放。 | `.venv/Scripts/python.exe -m pytest -q --no-cov tests/integration/test_stage6_golden_replay.py` |
| `tests/unit/test_stage6_protocol_performance.py` | 协议异常结构、reason 截断、额外字段及绩效 0%/50%/100% 和零决策边界。 | `.venv/Scripts/python.exe -m pytest -q --no-cov tests/unit/test_stage6_protocol_performance.py` |
| `tests/unit/test_llm_protocol.py` | LLM 协议及 Mock、Fake、录制客户端；覆盖成功/失败记录和调用 ID 连续性。 | `python -m pytest tests/unit/test_llm_protocol.py` |
| `tests/unit/test_baseline_agent.py` / `tests/integration/test_llm_runner.py` / `tests/integration/test_decision_runner.py` | 覆盖真实 LLM 请求的 system/user 边界、候选格式、运行时信息隔离、完整 Mock 对局，以及段 3 溢出警告的私有且按行动回合去重记录。 | `.venv/Scripts/python.exe -m pytest tests/unit/test_baseline_agent.py tests/integration/test_llm_runner.py tests/integration/test_decision_runner.py` |
| `tests/unit/context/test_broadcast.py` | 上下文播报器单元测试（Stage 4B）：豁免事件返回 None、全引擎事件类型穷举（白名单+豁免覆盖全部 49 个事件）、未注册事件抛异常、确定性渲染、涉己/旁观差异（card_drawn、card_discarded、chance_card_stolen）、payment_made 银行/玩家、player_jailed 原因映射、棋盘名称回退。 | `python -m pytest tests/unit/context/test_broadcast.py` |
| `tests/unit/context/test_rules.py` / `test_token_guard.py` / `test_conversation.py` / `test_composer.py` / `test_validation_feedback.py` | 覆盖规则加载、段 3 严格 500-token 裁剪及同回合缓存稳定性、system/user 消息归属、相邻事件换行、重试消息顺序、校验反馈、同一决策 ID 的问题折叠及多条 assistant 回复保留。 | `.venv/Scripts/python.exe -m pytest tests/unit/context/` |
| `tests/manual/render_decision_prompt.py` | Stage 4D 人工审阅脚本：生成 Baseline 上下文确认清单及 A–G 实际 messages；覆盖角色边界、历史裁剪、校验重试与运行时隔离。`ContextWarning` 仅作为报告中的私有审计证据展示。 | 运行 `.venv/Scripts/python.exe tests/manual/render_decision_prompt.py` 后审阅生成的 `tests/manual/render_decision_prompt_report.txt`。 |
| `tests/manual/render_history_broadcast.py` | 手动验收脚本（Stage 4B）：使用直接状态注入（从 `test_chance_cards.py` 习得的模式）创建 20 个独立场景，通过控制玩家位置、直接注入机会卡、设置产权归属和控制骰子序列，覆盖全部 33 个白名单事件（每个事件≥2次出现）。生成 `tests/manual/history_broadcast_report.txt` 完整事件日志供项目负责人人工审核中文句式质量。2026-08-19 运行通过，exit status 0，33/33 事件达标。 | `.venv/Scripts/python.exe tests/manual/render_history_broadcast.py` |
| `tests/manual/render_tang_decision_prompt.py` | 唐代十个朝廷上下文场景手动渲染，复用生产 `TangCourtAgent`、`compose_prompt()`、引擎决策请求和角色提示词，输出完整 `LLMMessage` 供人工审核。 | `.venv/Scripts/python.exe tests/manual/render_tang_decision_prompt.py`；报告为 `tests/manual/render_tang_decision_prompt_report.txt`。 |
| `tests/manual/render_ming_decision_prompt.py` | 明代九个朝廷上下文场景手动渲染，使用固定回复的 fake LLM 驱动真实 `MingCourtAgent`，捕获四个角色每次实际 `LLMRequest.messages`；通过真实 `GameEngine.execute()` 注入第一次决策后的事件，并覆盖首轮一致、分歧重拟、加权投票、同一行动回合第二次决策及角色历史可见性。脚本重复执行并比较完整报告，验证相同引擎状态和相同 LLM 回复产生字节级一致的上下文；报告校验投票标题、第一次汇总指令位置及首辅指令的角色隔离。 | `.venv/Scripts/python.exe tests/manual/render_ming_decision_prompt.py`；报告为 `tests/manual/render_ming_decision_prompt_report.txt`。 |
| `tests/unit/game/test_stage6_integrity.py` | Stage 6 第一批完整性测试：固定种子确定性、32 张卡牌唯一性、产权双向一致性、核心状态不变量及基础资金可追溯性。 | `.venv/Scripts/python.exe -m pytest -q --no-cov tests/unit/game/test_stage6_integrity.py` |
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
