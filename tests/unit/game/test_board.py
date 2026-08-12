from monopoly_agent_battle.game.board_data.classic_us_40 import (
    BOARD,
    BOARD_BY_POSITION,
    COLOR_GROUPS,
)


def test_classic_board_has_all_forty_spaces() -> None:
    assert len(BOARD) == 40
    assert tuple(BOARD_BY_POSITION) == tuple(range(40))
    assert COLOR_GROUPS["brown"] == (1, 3)


def test_every_property_has_a_purchase_price() -> None:
    assert all(space.price is not None for space in BOARD if space.is_property)
