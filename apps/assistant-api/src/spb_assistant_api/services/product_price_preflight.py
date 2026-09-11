"""Component-level price capability and deterministic input checks (D1/D2)."""

from ..domain.agent_actions import RequiredInput
from ..domain.intents import Intent
from ..domain.product_price_slots import ProductPriceCommand
from ..domain.tooling import ToolDescriptor


PRODUCT_PRICE_DESCRIPTOR = ToolDescriptor(
    intent=Intent.PRODUCT_PRICE,
    tool_name="product_price",
    command_type=ProductPriceCommand,
    result_schema_name="ProductPriceData",
    required_slots=("conditions.kind",),
    capability_version="1",
)

PRICE_SLOT_LABELS = {
    "conditions.kind": "商品类目",
    "conditions.product_text": "设备型号",
    "conditions.brand": "设备品牌",
    "conditions.commodity": "生鲜品名",
    "conditions.variety": "品种",
    "conditions.region_text": "报价地区",
    "conditions.market_text": "报价市场",
    "conditions.price_nature": "批发或零售口径",
    "conditions.source_scope": "来源范围",
    "conditions.requested_unit": "商品计价单位",
    "conditions.specification.capacity": "存储容量",
    "conditions.specification.memory": "内存",
    "conditions.specification.color": "颜色",
    "conditions.specification": "设备规格及其含义",
    "time": "价格时间范围",
}

PRICE_AMBIGUITY_SLOTS = {
    "price_multiple_commodities": "conditions.commodity",
    "price_multiple_regions": "conditions.region_text",
    "price_unresolved_market": "conditions.market_text",
    "price_multiple_natures": "conditions.price_nature",
    "price_multiple_units": "conditions.requested_unit",
    "price_invalid_quantity": "conditions.requested_unit",
    "price_multiple_capacity": "conditions.specification.capacity",
    "price_multiple_memory": "conditions.specification.memory",
    "price_multiple_color": "conditions.specification.color",
    "price_ambiguous_specification": "conditions.specification",
    "price_identity_too_long": "conditions.product_text",
}


def price_required_input(name: str, *, confirmation_hint: str = "") -> RequiredInput:
    choices = {
        "conditions.kind": ["电子设备", "生鲜水果"],
        "conditions.price_nature": ["零售", "批发", "分别列出多个来源"],
        "conditions.source_scope": ["单一地区和口径", "分别列出多个来源"],
    }.get(name, [])
    return RequiredInput(
        name=name,
        label=PRICE_SLOT_LABELS.get(name, name),
        type="choice" if choices else "string",
        choices=choices,
        validation_hint=(
            "仅填写商品报价条件，不是寄递重量或收寄地址。" + confirmation_hint
        ),
    )
