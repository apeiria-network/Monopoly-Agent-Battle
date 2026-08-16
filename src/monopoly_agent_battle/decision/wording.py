"""决策候选的自然语言文案（临时占位，未经负责人逐条人工确认）。

集中存放每个候选的标题与规则效果说明，与候选生成、校验、渲染逻辑分离，
便于负责人逐条评审和替换。当前文案均为临时占位，不得视为最终提示词。
"""

from __future__ import annotations

from dataclasses import dataclass

from monopoly_agent_battle.domain.commands import GameCommand, UseChanceCard


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
        "抵押一处你拥有的地产，获得其购买价；抵押期间该地产不收租，不能建造房屋，但是可以享受地产增益。赎回时需支付购买价的 110%。",
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
    "DiscardChanceCard": OptionWording(
        "弃置机会卡",
        "选择手牌中的一张机会卡并弃置该卡。",
        {
            "reason": "填写选择弃置该机会卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写选择弃置的目标机会卡 ID。",
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
        "掷一枚骰子，点数不小于 4 时查看目标玩家手牌并选择获得其中一张；否则无事发生，抢夺卡回到手牌。",
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
        "收取目标玩家当前现金的 35%。",
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
        "将目标玩家的一处无建筑且未抵押的普通地块按原价强制出售给银行。",
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
        "目标颜色组内每块已被所有玩家拥有的普通地块各增加一层建设。",
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
        "用自己的一处无建筑且未抵押的普通地块与目标玩家的同条件地块交换所有权。",
        {
            "reason": "填写选择使用换地卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": {"swap_in_position": "填写换入的目标格子id",
                           "swap_out_position": "填写换出的目标格子id"},
            },
        },
    ),
    "chance-equalize": OptionWording(
        "使用机会卡「均富卡」",
        "与目标玩家平分双方当前现金。",
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
        "交换两处普通地块的完整建设等级，所有权不变。",
        {
            "reason": "填写选择使用换屋卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": {"swap_in_position": "填写换入的目标格子id",
                           "swap_out_position": "填写换出的目标格子id"},
            },
        },
    ),
    "chance-jail": OptionWording(
        "使用机会卡「陷害卡」",
        "将目标玩家送入监狱。",
        {
            "reason": "填写选择使用陷害卡的理由。",
            "selected_option": {
                "option": "{option_id}",
                "target": "填写需要送入监狱的目标玩家 ID。",
            },
        },
    ),
    "chance-nuclear": OptionWording(
        "使用机会卡「核弹卡」",
        "以掷骰结果对应格子为中心，将中心及前后各一格内的普通地块重置为无主、无建筑、无抵押。",
        {
            "reason": "填写选择使用核弹卡的理由。",
            "selected_option": {"option": "{option_id}"},
        },
    ),
    "chance-alliance": OptionWording(
        "使用机会卡「同盟卡」",
        "与目标玩家同盟，持续 3 个后续回合；期间任一方收租时双方平分。",
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
        "目标颜色组内每块普通地块各降低一层建设（酒店降为 4 栋房屋）。",
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
        "目标颜色组租金翻倍，持续 3 个后续回合。",
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
        "支付目标地产购买价的 150% 取得其产权；资金不足则退回卡并取消。",
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
        "目标颜色组暂停收取租金，持续 2 个后续回合。",
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
        "在自己的一处未抵押普通地块上免费加建一层，不超过酒店。",
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