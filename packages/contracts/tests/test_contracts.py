from __future__ import annotations

from spb_contracts import (
    COLLECTION_NAME,
    DENSE_FIELD,
    M3E_BASE_CONTRACT,
    REQUIRED_FIELDS,
    SPARSE_FIELD,
)


def test_embedding_contract_matches_existing_collection():
    assert M3E_BASE_CONTRACT.dimension == 768
    assert M3E_BASE_CONTRACT.normalized is True
    assert M3E_BASE_CONTRACT.metric == "COSINE"


def test_collection_contract_has_hybrid_fields():
    assert COLLECTION_NAME == "spb_policy_chunks"
    assert {DENSE_FIELD, SPARSE_FIELD}.issubset(REQUIRED_FIELDS)
