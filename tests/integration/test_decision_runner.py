import json
from pathlib import Path

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.context.conversation import AgentConversation
from monopoly_agent_battle.decision.models import DecisionRequest
from monopoly_agent_battle.decision.protocol import default_option_json
from monopoly_agent_battle.decision.runner import (
    DeterministicPolicyController,
    run_decision_game,
)
from monopoly_agent_battle.domain.models import GameEvent, JailStatus
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.replay import verify_run
from monopoly_agent_battle.logging.run_artifacts import RunArtifacts


def make_config(output_directory: Path) -> GameConfig:
    return GameConfig(
        game_id="protocol-game",
        experiment_id="protocol-experiment",
        seed=3,
        players=(PlayerConfig(player_id="a", seat=1), PlayerConfig(player_id="b", seat=2)),
        max_complete_rounds=1,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=output_directory,
    )


def test_deterministic_decision_runner_writes_auditable_commands_and_replays(
    tmp_path: Path,
) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)

    result = run_decision_game(GameEngine(config), DeterministicPolicyController(), artifacts)

    decisions = [
        json.loads(line)
        for line in (artifacts.run_directory / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    events = [
        json.loads(line)
        for line in (artifacts.run_directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert result.status == "completed"
    assert decisions
    assert all(record["validation"]["validation_error"] is None for record in decisions)
    assert all(record["executed_command"]["command_type"] for record in decisions)
    assert all(record["executed_command"]["command_type"] != "roll_dice" for record in decisions)
    assert any(
        record["event_type"] == "command_executed"
        and record["payload"]["command_type"] == "RollDice"
        for record in events
    )
    verify_run(artifacts.run_directory)


def test_connection_failures_are_retried_then_recorded_as_fallback(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    attempts = 0

    def disconnected_controller(_request: DecisionRequest, _feedback: str | None = None) -> str:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("service unavailable")

    run_decision_game(GameEngine(config), disconnected_controller, artifacts)

    runtime = [
        json.loads(line)
        for line in (artifacts.run_directory / "runtime.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert attempts >= 3
    assert runtime[0]["event_type"] == "controller_connection_error"
    assert runtime[2]["event_type"] == "controller_connection_error"
    assert runtime[3]["event_type"] == "decision_fallback"
    decisions = [
        json.loads(line)
        for line in (artifacts.run_directory / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert decisions[0]["fallback"] is True
    assert decisions[0]["attempted_validation"]["validation_error"] == "response is not valid JSON"


def test_invalid_output_is_retried_with_feedback_then_falls_back(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    feedbacks: list[str | None] = []

    def invalid_controller(request: DecisionRequest, feedback: str | None = None) -> str:
        feedbacks.append(feedback)
        return '{"selected_option": {"option": "not-a-candidate"}, "reason": "x"}'

    run_decision_game(GameEngine(config), invalid_controller, artifacts)

    decisions = [
        json.loads(line)
        for line in (artifacts.run_directory / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert decisions[0]["fallback"] is True
    assert decisions[0]["validation_retries"] == 2
    assert decisions[0]["validation_errors"] == [
        "selected_option is not a legal candidate",
        "selected_option is not a legal candidate",
    ]
    assert feedbacks[0] is None
    assert feedbacks[1] is not None and "Error: 不合法的选项id" in feedbacks[1]
    assert feedbacks[2] is not None and "Error: 不合法的选项id" in feedbacks[2]
    result = json.loads((artifacts.run_directory / "result.json").read_text(encoding="utf-8"))
    assert result["decision_fallbacks"] > 0
    assert result["llm_fallbacks"] > 0
    assert result["validity_status"] == "invalid"


def test_segment3_overflow_is_logged_once_per_action_turn(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    conversations = {
        player_id: AgentConversation(agent_id=player_id, window_turns=1) for player_id in ("a", "b")
    }
    conversations["a"].start_turn(0)
    for _ in range(50):
        conversations["a"].append_event(
            GameEvent(event_type="dice_rolled", payload={"player_id": "a", "dice": (6, 6)})
        )

    run_decision_game(
        GameEngine(config), DeterministicPolicyController(), artifacts, conversations=conversations
    )

    runtime = [
        json.loads(line)
        for line in (artifacts.run_directory / "runtime.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    segment3_records = [record for record in runtime if record["event_type"] == "segment3_overflow"]
    assert len(segment3_records) == 1
    assert segment3_records[0]["payload"]["agent_id"] == "a"
    assert segment3_records[0]["payload"]["turn_num"] == 1
    assert conversations["a"].segment3_warning is not None


def test_jail_waiting_is_advanced_without_a_jail_prompt(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    artifacts = RunArtifacts.create(config)
    engine = GameEngine(config)
    engine.state.players["a"].jail_status = JailStatus.WAITING
    requests: list[DecisionRequest] = []

    def controller(request: DecisionRequest, _feedback: str | None = None) -> str:
        requests.append(request)
        default = next(option for option in request.options if option.is_default)
        return json.dumps(
            {"selected_option": default_option_json(default), "reason": "选择系统默认合法操作。"}
        )

    run_decision_game(engine, controller, artifacts)

    decisions = [
        json.loads(line)
        for line in (artifacts.run_directory / "decisions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    events = [
        json.loads(line)
        for line in (artifacts.run_directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert requests
    assert decisions[0]["request"]["kind"] == "asset_management"
    assert any(record["event_type"] == "jail_wait_completed" for record in events)
