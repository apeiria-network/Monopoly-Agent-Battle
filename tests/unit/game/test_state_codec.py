from __future__ import annotations

# Pytest parameterized mutation callbacks are dynamically typed by design.
# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownParameterType=false, reportUnknownMemberType=false
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from monopoly_agent_battle.config.models import GameConfig, PlayerConfig
from monopoly_agent_battle.domain.models import (
    CardDeck,
    EndReason,
    JailStatus,
    OngoingEffect,
    OngoingEffectKind,
    SettlementOperation,
    SettlementOperationKind,
    SettlementOperationStatus,
    TurnPhase,
)
from monopoly_agent_battle.game.engine import GameEngine
from monopoly_agent_battle.game.state_codec import encode_checkpoint, restore_checkpoint


def config(tmp_path: Path) -> GameConfig:
    return GameConfig(
        game_id="codec",
        experiment_id="resume",
        seed=41,
        players=(
            PlayerConfig(player_id="a", seat=1, controller_type="random_baseline"),
            PlayerConfig(player_id="b", seat=2, controller_type="random_baseline"),
        ),
        max_complete_rounds=2,
        rules_version="classic-level0-v1",
        board_data_version="classic-us-40-v1",
        card_data_version="classic-cards-v1",
        output_directory=tmp_path,
    )


def test_checkpoint_round_trip_is_json_and_restores_rng(tmp_path: Path) -> None:
    value = config(tmp_path)
    source = GameEngine(value)
    source.state.players["a"].cash = 875
    source.state.players["a"].position = 24
    source.state.players["a"].properties = {1, 6}
    source.state.players["a"].jail_status = JailStatus.ROLLING
    source.state.players["a"].jail_roll_attempts = 2
    source.state.players["a"].bankrupt = True
    source.state.players["a"].survived_turns = 7
    source.state.players["a"].chance_cards = ["chance_01", "chance_09"]
    source.state.players["a"].community_get_out_of_jail_cards = ["community_06"]
    source.state.players["a"].rent_waivers = 2
    source.state.properties[1].owner_id = "a"
    source.state.properties[1].building_level = 4
    source.state.properties[1].mortgaged = True
    source.state.settlement_operations = [
        SettlementOperation(
            operation_id=4,
            kind=SettlementOperationKind.PAYMENT,
            player_id="a",
            source="test-payment",
            status=SettlementOperationStatus.BLOCKED,
            recipient_id="b",
            amount=150,
            steps=3,
            destination=18,
            dice_total=9,
            collect_go_salary=True,
            allow_build=True,
            resume_phase=TurnPhase.PAYMENT_RESOLUTION,
            resume_player_id="a",
            deck=CardDeck.CHANCE,
            alliance_partner_id="b",
        )
    ]
    source.state.next_settlement_operation_id = 5
    source.state.chance_draw_pile = ["chance_02"]
    source.state.chance_discard_pile = ["chance_03"]
    source.state.community_chest_draw_pile = ["community_01"]
    source.state.community_chest_discard_pile = ["community_02"]
    source.state.ongoing_effects = [
        OngoingEffect(
            kind=OngoingEffectKind.ALLIANCE,
            source_player_id="a",
            remaining_turns=3,
            activation_turn=2,
            target_player_id="b",
            color_group="red",
        )
    ]
    source.state.pending_theft_thief_id = "a"
    source.state.pending_theft_target_id = "b"
    source.state.pending_theft_source_card_id = "chance_13"
    source.state.consecutive_doubles = 1
    source.state.round_player_ids = ("a", "b")
    source.state.completed_round_player_ids = {"a"}
    source.state.complete_rounds = 2
    source.state.finished = True
    source.state.end_reason = EndReason.ROUND_LIMIT
    source.state.rankings = ("b", "a")
    source.state.turn_phase = TurnPhase.THEFT_CARD_SELECTION
    source.random.gauss(0, 1)
    document = encode_checkpoint(source.state, source.random)
    serialized = json.loads(json.dumps(document))

    restored = GameEngine(value)
    restore_checkpoint(serialized, restored.state, restored.random)

    assert encode_checkpoint(restored.state, restored.random) == serialized
    assert [restored.random.randint(1, 6) for _ in range(8)] == [
        source.random.randint(1, 6) for _ in range(8)
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            cast(
                Callable[[dict[str, Any]], object],
                lambda document: document.update(schema="wrong"),
            ),
            "unsupported checkpoint schema",
        ),
        (lambda document: document.pop("state"), "checkpoint state must be an object"),
        (
            lambda document: document["state"]["players"].pop("b"),
            "checkpoint players do not match configuration",
        ),
        (
            lambda document: document["state"]["players"]["a"].pop("cash"),
            "checkpoint contains invalid state",
        ),
        (
            lambda document: document["state"].update(turn_phase="not-a-phase"),
            "checkpoint contains invalid state",
        ),
        (lambda document: document.update(rng_state="not-an-array"), "invalid rng_state"),
        (lambda document: document.update(rng_state=[1, [], None]), "invalid rng_state"),
        (lambda document: document.update(rng_state=[3, [1, 2], True]), "invalid rng_state"),
    ],
)
def test_checkpoint_rejects_malformed_documents(
    tmp_path: Path, mutate: Callable[[dict[str, Any]], object], message: str
) -> None:
    value = config(tmp_path)
    document = encode_checkpoint(GameEngine(value).state, random.Random(value.seed))
    mutate(document)

    with pytest.raises(ValueError, match=message):
        restore_checkpoint(document, GameEngine(value).state, random.Random())
