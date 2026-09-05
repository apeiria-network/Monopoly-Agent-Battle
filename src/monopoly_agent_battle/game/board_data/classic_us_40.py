"""Classic US 40-space board data, version classic-us-40-v1."""

from monopoly_agent_battle.domain.models import BoardSpace, SpaceKind


def _street(
    position: int, name: str, color: str, price: int, cost: int, rents: tuple[int, ...]
) -> BoardSpace:
    return BoardSpace(position, name, SpaceKind.STREET, price, color, cost, rents)


BOARD: tuple[BoardSpace, ...] = (
    BoardSpace(0, "GO", SpaceKind.GO),
    _street(1, "Mediterranean Avenue", "brown", 60, 50, (2, 10, 30, 90, 160, 250)),
    BoardSpace(2, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(3, "Baltic Avenue", "brown", 60, 50, (4, 20, 60, 180, 320, 450)),
    BoardSpace(4, "Income Tax", SpaceKind.TAX, tax=200),
    BoardSpace(5, "Reading Railroad", SpaceKind.RAILROAD, 200),
    _street(6, "Oriental Avenue", "light_blue", 100, 50, (6, 30, 90, 270, 400, 550)),
    BoardSpace(7, "Chance", SpaceKind.CHANCE),
    _street(8, "Vermont Avenue", "light_blue", 100, 50, (6, 30, 90, 270, 400, 550)),
    _street(9, "Connecticut Avenue", "light_blue", 120, 50, (8, 40, 100, 300, 450, 600)),
    BoardSpace(10, "Jail / Just Visiting", SpaceKind.JAIL),
    _street(11, "St. Charles Place", "pink", 140, 100, (10, 50, 150, 450, 625, 750)),
    BoardSpace(12, "Electric Company", SpaceKind.UTILITY, 150),
    _street(13, "States Avenue", "pink", 140, 100, (10, 50, 150, 450, 625, 750)),
    _street(14, "Virginia Avenue", "pink", 160, 100, (12, 60, 180, 500, 700, 900)),
    BoardSpace(15, "Pennsylvania Railroad", SpaceKind.RAILROAD, 200),
    _street(16, "St. James Place", "orange", 180, 100, (14, 70, 200, 550, 750, 950)),
    BoardSpace(17, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(18, "Tennessee Avenue", "orange", 180, 100, (14, 70, 200, 550, 750, 950)),
    _street(19, "New York Avenue", "orange", 200, 100, (16, 80, 220, 600, 800, 1000)),
    BoardSpace(20, "Chance", SpaceKind.CHANCE),
    _street(21, "Kentucky Avenue", "red", 220, 150, (18, 90, 250, 700, 875, 1050)),
    BoardSpace(22, "Chance", SpaceKind.CHANCE),
    _street(23, "Indiana Avenue", "red", 220, 150, (18, 90, 250, 700, 875, 1050)),
    _street(24, "Illinois Avenue", "red", 240, 150, (20, 100, 300, 750, 925, 1100)),
    BoardSpace(25, "B. & O. Railroad", SpaceKind.RAILROAD, 200),
    _street(26, "Atlantic Avenue", "yellow", 260, 150, (22, 110, 330, 800, 975, 1150)),
    _street(27, "Ventnor Avenue", "yellow", 260, 150, (22, 110, 330, 800, 975, 1150)),
    BoardSpace(28, "Water Works", SpaceKind.UTILITY, 150),
    _street(29, "Marvin Gardens", "yellow", 280, 150, (24, 120, 360, 850, 1025, 1200)),
    BoardSpace(30, "Go To Jail", SpaceKind.GO_TO_JAIL),
    _street(31, "Pacific Avenue", "green", 300, 200, (26, 130, 390, 900, 1100, 1275)),
    _street(32, "North Carolina Avenue", "green", 300, 200, (26, 130, 390, 900, 1100, 1275)),
    BoardSpace(33, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(34, "Pennsylvania Avenue", "green", 320, 200, (28, 150, 450, 1000, 1200, 1400)),
    BoardSpace(35, "Short Line", SpaceKind.RAILROAD, 200),
    BoardSpace(36, "Chance", SpaceKind.CHANCE),
    _street(37, "Park Place", "dark_blue", 350, 200, (35, 175, 500, 1100, 1300, 1500)),
    BoardSpace(38, "Luxury Tax", SpaceKind.TAX, tax=75),
    _street(39, "Boardwalk", "dark_blue", 400, 200, (50, 200, 600, 1400, 1700, 2000)),
)

BOARD_BY_POSITION = {space.position: space for space in BOARD}
COLOR_GROUPS = {
    "brown": (1, 3),
    "light_blue": (6, 8, 9),
    "pink": (11, 13, 14),
    "orange": (16, 18, 19),
    "red": (21, 23, 24),
    "yellow": (26, 27, 29),
    "green": (31, 32, 34),
    "dark_blue": (37, 39),
}
RAILROAD_RENTS = (25, 50, 100, 200)


def validate_board() -> None:
    if tuple(BOARD_BY_POSITION) != tuple(range(40)):
        raise ValueError("board positions must be complete and ordered")
    for space in BOARD:
        if space.is_property and space.price is None:
            raise ValueError(f"property at {space.position} requires a price")


validate_board()
