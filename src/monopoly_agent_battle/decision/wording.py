"""决策候选的自然语言文案（临时占位，未经负责人逐条人工确认）。

集中存放每个候选的标题与规则效果说明，与候选生成、校验、渲染逻辑分离，
便于负责人逐条评审和替换。当前文案均为临时占位，不得视为最终提示词。
"""

from __future__ import annotations

from dataclasses import dataclass

from monopoly_agent_battle.domain.commands import DiscardChanceCard, GameCommand, UseChanceCard
from monopoly_agent_battle.game.cards.classic_cards import CARDS_BY_ID


@dataclass(frozen=True, slots=True)
class OptionWording:
    """一个候选的自然语言文案：标题与规则效果说明。"""

    title: str
    preview: str
    response_format: dict[str, object]


_COMMAND_WORDING: dict[str, OptionWording] = {
    "SellBuilding": OptionWording(
        "出售一层建筑",
        "出售一层你拥有的建筑，获得该建筑建造价的一半资金。",
        {
            "reason": "填写选择出售该建筑的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要出售的目标格子编号",
            },
        },
    ),
    "Mortgage": OptionWording(
        "抵押地产",
        "抵押一处你拥有的地产，获得其购买价；抵押期间该地产不收租，不能建造房屋，"
        "但是可以享受地产增益。赎回时需支付购买价的 110%。",
        {
            "reason": "填写选择抵押该地产的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要抵押的目标格子编号",
            },
        },
    ),
    "RedeemMortgage": OptionWording(
        "赎回抵押地产",
        "赎回一处你抵押地产，支付该地产购买价的110%，恢复收取租金。",
        {
            "reason": "填写选择赎回该抵押地产的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写选择赎回的目标格子编号；",
            },
        },
    ),
    "SelectStolenChanceCard": OptionWording(
        "拿走机会卡",
        "从目标玩家手中选择一张机会卡，并拿走该卡",
        {
            "reason": "填写选择拿走这张机会卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写选择拿走的目标机会卡 ID。",
            },
        },
    ),
    "RollDice": OptionWording(
        "掷骰出狱",
        "掷两枚骰子，若掷出双骰即出狱并以此点数移动。如果3次掷骰都未掷出双骰，则立刻支付50现金并出狱，依照本次掷骰结果移动。每回合只能掷骰一次。",
        {
            "reason": "填写选择掷骰出狱的理由。",
            "selected_option": {"option": "{option_id}"},
        },
    ),
    "PayJailFine": OptionWording(
        "支付罚款出狱",
        "支付 50 元罚款后出狱。并开始这回合的行动。",
        {
            "reason": "填写选择支付罚款出狱的理由。",
            "selected_option": {"option": "{option_id}"},
        },
    ),
    "EndTurn": OptionWording(
        "结束回合",
        "结束本回合，轮到下一位玩家。",
        {
            "reason": "填写选择结束回合的理由。",
            "selected_option": {"option": "{option_id}"},
        },
    ),
    "UseCommunityGetOutOfJailCard": OptionWording(
        "使用出狱卡",
        "消耗一张出狱卡后出狱。",
        {
            "reason": "填写选择使用出狱卡的理由。",
            "selected_option": {"option": "{option_id}"},
        },
    ),
}

_CARD_WORDING: dict[str, OptionWording] = {
    "chance-steal": OptionWording(
        "使用机会卡「抢夺卡」",
        "对距自身 5 格以内的目标玩家使用，查看其手牌并选择获得其中一张。",
        {
            "reason": "填写选择使用抢夺卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写要抢夺的目标玩家 ID。",
            },
        },
    ),
    "chance-tax": OptionWording(
        "使用机会卡「查税卡」",
        "对距自身 5 格以内的目标玩家使用，收取其当前现金的 35%。",
        {
            "reason": "填写选择使用查税卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写要查税目标玩家 ID。",
            },
        },
    ),
    "chance-vacate": OptionWording(
        "使用机会卡「空地卡」",
        "指定距自身 5 格以内的一处其他玩家无建筑且未抵押的普通地块，按原价强制出售给银行。",
        {
            "reason": "填写选择使用空地卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写要强制出售的目标格子编号。",
            },
        },
    ),
    "chance-angel": OptionWording(
        "使用机会卡「天使卡」",
        "指定一个颜色组（组内任一地块距自身 5 格以内即可），"
        "该组内每块已被玩家拥有且未抵押的普通地块各增加一层建设；抵押中地块跳过。",
        {
            "reason": "填写选择使用天使卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要增加建设的目标颜色组。",
            },
        },
    ),
    "chance-swap-property": OptionWording(
        "使用机会卡「换地卡」",
        "指定距自身 5 格以内的一处其他玩家无建筑且未抵押的普通地块，"
        "与自己的一处同条件地块交换所有权。",
        {
            "reason": "填写选择使用换地卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": {
                    "swap_in_position": "填写换入的目标格子id",
                    "swap_out_position": "填写换出的目标格子id",
                },
            },
        },
    ),
    "chance-equalize": OptionWording(
        "使用机会卡「均富卡」",
        "对距自身 5 格以内的目标玩家使用，平分双方当前现金。",
        {
            "reason": "填写选择使用均富卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写与自己平分现金的目标玩家 ID。",
            },
        },
    ),
    "chance-swap-buildings": OptionWording(
        "使用机会卡「换屋卡」",
        "指定距自身 5 格以内的一处有主且未抵押的普通地块（不论归属），"
        "与自己拥有的一处未抵押普通地块交换完整建设等级，所有权不变。",
        {
            "reason": "填写选择使用换屋卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": {
                    "swap_in_position": "填写换入的目标格子id",
                    "swap_out_position": "填写换出的目标格子id",
                },
            },
        },
    ),
    "chance-jail": OptionWording(
        "使用机会卡「陷害卡」",
        "对距自身 5 格以内的目标玩家使用，将其送入监狱。",
        {
            "reason": "填写选择使用陷害卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要送入监狱的目标玩家 ID。",
            },
        },
    ),
    "chance-nuclear": OptionWording(
        "使用机会卡「核弹卡」（已禁用）",
        "此卡已禁用，不可使用。",
        {
            "reason": "填写选择使用核弹卡的理由。",
            "selected_option": {"option": "{option_id}"},
        },
    ),
    "chance-taxi": OptionWording(
        "使用机会卡「出租车卡」",
        "移动到前方 1–6 格内的指定地块。",
        {
            "reason": "填写选择使用出租车卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要移动到的目标格子编号（前方 1–6 格内）。",
            },
        },
    ),
    "chance-alliance": OptionWording(
        "使用机会卡「同盟卡」",
        "对距自身 5 格以内的目标玩家使用，同盟持续 3 个后续回合；期间任一方收租时双方平分。",
        {
            "reason": "填写选择使用同盟卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要与自己同盟的目标玩家 ID。",
            },
        },
    ),
    "chance-waiver": OptionWording(
        "使用机会卡「免费卡」",
        "获得 2 次租金支付豁免，可累计。",
        {
            "reason": "填写选择使用免费卡的理由。",
            "selected_option": {"option": "{option_id}"},
        },
    ),
    "chance-monster": OptionWording(
        "使用机会卡「怪兽卡」",
        "指定一个颜色组（组内任一地块距自身 5 格以内即可），"
        "该组内每块普通地块各降低一层建设（酒店降为 4 栋房屋）。",
        {
            "reason": "填写选择使用怪兽卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要降低建设的目标颜色组。",
            },
        },
    ),
    "chance-surge": OptionWording(
        "使用机会卡「涨价卡」",
        "指定一个颜色组（组内任一地块距自身 5 格以内即可），该组租金翻倍，持续 3 个后续回合。",
        {
            "reason": "填写选择使用涨价卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要提高租金的目标颜色组。",
            },
        },
    ),
    "chance-buy": OptionWording(
        "使用机会卡「购地卡」",
        "指定距自身 5 格以内的一处其他玩家无建筑且未抵押的普通地块，"
        "支付原价的 150% 取得产权；资金不足则退回卡并取消。",
        {
            "reason": "填写选择使用购地卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要购买的目标格子编号。",
            },
        },
    ),
    "chance-freeze": OptionWording(
        "使用机会卡「查封卡」",
        "指定一个颜色组（组内任一地块距自身 5 格以内即可），该组暂停收取租金，持续 2 个后续回合。",
        {
            "reason": "填写选择使用查封卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要查封的目标颜色组。",
            },
        },
    ),
    "chance-build": OptionWording(
        "使用机会卡「建房卡」",
        "在距自身 5 格以内的一处自己拥有的未抵押普通地块上免费加建一层，不超过酒店。",
        {
            "reason": "填写选择使用建房卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要免费加建一层的目标格子编号。",
            },
        },
    ),
}


def option_wording(command: GameCommand) -> OptionWording:
    """Return the wording for a candidate command."""
    if isinstance(command, UseChanceCard):
        return _CARD_WORDING[command.card_id]
    if isinstance(command, DiscardChanceCard):
        card_name = CARDS_BY_ID[command.card_id].name
        return OptionWording(
            f"弃置机会卡「{card_name}」",
            f"弃置你持有的机会卡「{card_name}」。",
            {
                "reason": "填写选择弃置该机会卡的理由。",
                "selected_option": {"option": "{option_id}"},
            },
        )
    return _COMMAND_WORDING.get(
        type(command).__name__,
        OptionWording(
            f"执行 {type(command).__name__}",
            "执行该操作。",
            {
                "reason": "【待负责人确认】填写选择本选项的理由。",
                "selected_option": {"option": "{option_id}"},
            },
        ),
    )
