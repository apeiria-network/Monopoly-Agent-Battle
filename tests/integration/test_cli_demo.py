import json
from pathlib import Path

from monopoly_agent_battle.cli.main import run_demo


def test_demo_creates_auditable_run(tmp_path: Path) -> None:
    config_path = tmp_path / "demo.yaml"
    output_directory = tmp_path / "runs"
    config_path.write_text(
        f"""game_id: game-001
experiment_id: experiment-001
seed: 42
players:
  - player_id: a
    seat: 1
  - player_id: b
    seat: 2
rules_version: classic-level0-v1
rules_level: 0
board_data_version: classic-us-40-v1
card_data_version: classic-cards-v1
output_directory: {output_directory.as_posix()}
""",
        encoding="utf-8",
    )

    run_directory = run_demo(config_path)

    assert run_directory == output_directory / "experiment-001" / "game-001"
    assert {path.name for path in run_directory.iterdir()} == {
        "config.json",
        "events.jsonl",
        "result.json",
    }
    result = json.loads((run_directory / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "initialized"
