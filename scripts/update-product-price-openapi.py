"""Refresh the additive public price schemas, then run generate:agent-types.

This deliberately leaves the curated V1/logistics descriptions intact.
"""

import json
from pathlib import Path

from spb_assistant_api.api.product_price_schemas import (
    ProductPriceResponseData,
    PriceCandidateOption,
)
from spb_assistant_api.domain.product_price_execution import PriceSelectionInput


path = (
    Path(__file__).resolve().parents[1] / "docs/openapi/assistant-agent-v2.openapi.json"
)
document = json.loads(path.read_text())
schemas = document["components"]["schemas"]
for model in (ProductPriceResponseData, PriceCandidateOption, PriceSelectionInput):
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    schemas.update(schema.pop("$defs", {}))
    schemas[model.__name__] = schema
schemas["PublicIntent"]["enum"] = [
    x for x in schemas["Intent"]["enum"] if x != "unknown"
]
request = schemas["AgentMessageRequest"]
request["anyOf"] = [
    {"required": [x]} for x in ("message", "explicit_intent", "price_selection")
]
request["properties"]["explicit_intent"]["description"] = (
    "unknown is not accepted. In catalog_v2 mode, use product_price; device_price remains a historical result type."
)
request["properties"]["price_selection"] = {
    "anyOf": [{"$ref": "#/components/schemas/PriceSelectionInput"}, {"type": "null"}],
    "default": None,
    "description": "Requires conversation_id; excludes message, explicit_intent and confirm_overwrite. Token is bound to owner/conversation/query/conditions and expires within 10 minutes.",
}
schemas["RequiredInput"]["properties"]["price_candidates"] = {
    "type": "array",
    "items": {"$ref": "#/components/schemas/PriceCandidateOption"},
    "maxItems": 20,
    "default": [],
}
schemas["AgentResult"]["properties"]["data"]["description"] = (
    "For product_price success/partial, validate ProductPriceResponseData (strict whitelist); no_match has null data. Other result types retain their existing payloads."
)
schemas["AgentResult"]["allOf"] = [
    {
        "if": {"properties": {"type": {"const": "product_price"}}},
        "then": {
            "properties": {
                "data": {
                    "anyOf": [
                        {"$ref": "#/components/schemas/ProductPriceResponseData"},
                        {"type": "null"},
                    ]
                }
            }
        },
    }
]
document["components"]["parameters"]["AgentClientContract"] = {
    "name": "X-Agent-Contract",
    "in": "header",
    "required": False,
    "schema": {"type": "string", "const": "product-price-v1"},
    "description": "Required for browser clients in catalog_v2 mode. Missing/old contract returns 409 before SSE or state writes. Service credentials and V1 are unchanged.",
}
conversation = document["paths"]["/v2/agent/conversations/{conversation_id}"]
conversation["get"] = {
    "operationId": "readAgentConversation",
    "summary": "Read an owned durable snapshot without executing or extending candidate expiry",
    "parameters": conversation["delete"]["parameters"].copy(),
    "responses": {
        "200": {
            "description": "Durable state",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/AgentResponse"}
                }
            },
        },
        "404": {"$ref": "#/components/responses/NotFound"},
        "409": {"$ref": "#/components/responses/Conflict"},
    },
}
for route, method in (
    ("/v2/agent/messages", "post"),
    ("/v2/agent/capabilities", "get"),
    ("/v2/agent/conversations/{conversation_id}", "get"),
):
    parameters = document["paths"][route][method].setdefault("parameters", [])
    ref = {"$ref": "#/components/parameters/AgentClientContract"}
    if ref not in parameters:
        parameters.append(ref)
path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
