"""Deterministic core rules for the classic Level 0 board."""

from monopoly_agent_battle.domain.models import GameState, JailStatus, PlayerState, SpaceKind
from monopoly_agent_battle.game.board_data.classic_us_40 import (
    BOARD_BY_POSITION,
    COLOR_GROUPS,
    RAILROAD_RENTS,
)


def net_worth(player: PlayerState, state: GameState) -> int:
    property_value = sum(BOARD_BY_POSITION[position].price or 0 for position in player.properties)
    building_value = sum(
        (BOARD_BY_POSITION[position].building_cost or 0) * state.properties[position].building_level
        for position in player.properties
    )
    mortgage_debt = sum(
        BOARD_BY_POSITION[position].price or 0
        for position in player.properties
        if state.properties[position].mortgaged
    )
    return player.cash + property_value + building_value - mortgage_debt


def rent_due(position: int, owner: PlayerState, state: GameState, dice_total: int) -> int:
    space = BOARD_BY_POSITION[position]
    property_state = state.properties[position]
    if property_state.mortgaged or owner.jail_status is not JailStatus.FREE:
        return 0
    if space.kind is SpaceKind.STREET:
        rent = space.rents[property_state.building_level]
        color_group = space.color_group
        if color_group is None:
            raise AssertionError("street must have a color group")
        group = COLOR_GROUPS[color_group]
        if property_state.building_level == 0 and all(
            state.properties[item].owner_id == owner.player_id for item in group
        ):
            return rent * 2
        return rent
    if space.kind is SpaceKind.RAILROAD:
        count = sum(BOARD_BY_POSITION[item].kind is SpaceKind.RAILROAD for item in owner.properties)
        return RAILROAD_RENTS[count - 1]
    count = sum(BOARD_BY_POSITION[item].kind is SpaceKind.UTILITY for item in owner.properties)
    return dice_total * (10 if count == 2 else 4)
