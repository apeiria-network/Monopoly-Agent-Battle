"""Classic US 40-space board data, version classic-us-40-v1."""

from monopoly_agent_battle.domain.models import BoardSpace, SpaceKind


def _street(
    position: int, name: str, color: str, price: int, cost: int, rents: tuple[int, ...]
) -> BoardSpace:
    return BoardSpace(position, name, SpaceKind.STREET, price, color, cost, rents)


BOARD: tuple[BoardSpace, ...] = (
    BoardSpace(0, "GO", SpaceKind.GO),
    _street(1, "Mediterranean Avenue", "brown", 60, 50, (5, 10, 30, 50, 70, 90)),
    BoardSpace(2, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(3, "Baltic Avenue", "brown", 60, 50, (10, 20, 60, 100, 140, 180)),
    BoardSpace(4, "Income Tax", SpaceKind.TAX, tax=200),
    BoardSpace(5, "Reading Railroad", SpaceKind.RAILROAD, 200),
    _street(6, "Oriental Avenue", "light_blue", 100, 50, (15, 30, 90, 150, 210, 270)),
    BoardSpace(7, "Chance", SpaceKind.CHANCE),
    _street(8, "Vermont Avenue", "light_blue", 100, 50, (15, 30, 90, 150, 210, 270)),
    _street(9, "Connecticut Avenue", "light_blue", 120, 50, (20, 40, 120, 200, 280, 360)),
    BoardSpace(10, "Jail / Just Visiting", SpaceKind.JAIL),
    _street(11, "St. Charles Place", "pink", 140, 100, (25, 50, 150, 250, 350, 450)),
    BoardSpace(12, "Electric Company", SpaceKind.UTILITY, 150),
    _street(13, "States Avenue", "pink", 140, 100, (25, 50, 150, 250, 350, 450)),
    _street(14, "Virginia Avenue", "pink", 160, 100, (30, 60, 180, 300, 420, 540)),
    BoardSpace(15, "Pennsylvania Railroad", SpaceKind.RAILROAD, 200),
    _street(16, "St. James Place", "orange", 180, 100, (35, 70, 210, 350, 490, 630)),
    BoardSpace(17, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(18, "Tennessee Avenue", "orange", 180, 100, (35, 70, 210, 350, 490, 630)),
    _street(19, "New York Avenue", "orange", 200, 100, (40, 80, 240, 400, 560, 720)),
    BoardSpace(20, "Chance", SpaceKind.CHANCE),
    _street(21, "Kentucky Avenue", "red", 220, 150, (45, 90, 270, 450, 630, 810)),
    BoardSpace(22, "Chance", SpaceKind.CHANCE),
    _street(23, "Indiana Avenue", "red", 220, 150, (45, 90, 270, 450, 630, 810)),
    _street(24, "Illinois Avenue", "red", 240, 150, (50, 100, 300, 500, 700, 900)),
    BoardSpace(25, "B. & O. Railroad", SpaceKind.RAILROAD, 200),
    _street(26, "Atlantic Avenue", "yellow", 260, 150, (55, 110, 330, 550, 770, 990)),
    _street(27, "Ventnor Avenue", "yellow", 260, 150, (55, 110, 330, 550, 770, 990)),
    BoardSpace(28, "Water Works", SpaceKind.UTILITY, 150),
    _street(29, "Marvin Gardens", "yellow", 280, 150, (60, 120, 360, 600, 840, 1080)),
    BoardSpace(30, "Go To Jail", SpaceKind.GO_TO_JAIL),
    _street(31, "Pacific Avenue", "green", 300, 200, (65, 130, 390, 650, 910, 1170)),
    _street(32, "North Carolina Avenue", "green", 300, 200, (65, 130, 390, 650, 910, 1170)),
    BoardSpace(33, "Community Chest", SpaceKind.COMMUNITY_CHEST),
    _street(34, "Pennsylvania Avenue", "green", 320, 200, (75, 150, 450, 750, 1050, 1350)),
    BoardSpace(35, "Short Line", SpaceKind.RAILROAD, 200),
    BoardSpace(36, "Chance", SpaceKind.CHANCE),
    _street(37, "Park Place", "dark_blue", 350, 200, (88, 175, 525, 875, 1225, 1575)),
    BoardSpace(38, "Luxury Tax", SpaceKind.TAX, tax=75),
    _street(39, "Boardwalk", "dark_blue", 400, 200, (100, 200, 600, 1000, 1400, 1800)),
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
