from __future__ import annotations

from enum import StrEnum


class Intent(StrEnum):
    POLICY = "policy"
    DEVICE_PRICE = "device_price"
    PRODUCT_PRICE = "product_price"
    TRACKING = "tracking"
    DELIVERY_TIME = "delivery_time"
    POSTAGE = "postage"
    UNKNOWN = "unknown"
