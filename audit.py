import json
import streamlit as st
import pandas as pd
from db import run_sql


def _mask_key(k: str | None) -> str | None:
    if not k:
        return None
    return "********" + k[-4:]

def _sanitize_payload(payload):
    if payload is None:
        return None
    if isinstance(payload, dict):
        return {k: _sanitize_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_sanitize_payload(v) for v in payload]
    
    try:
        # Check if it's a scalar missing value (NaN, None, pd.NA)
        if pd.isna(payload):
            return None
    except Exception:
        pass
        
    # Handle numpy/pandas scalars (like np.int64)
    if hasattr(payload, 'item') and callable(getattr(payload, 'item')):
        return payload.item()
        
    return payload


def log_action(action: str, table: str, record_id=None, payload: dict | None = None):
    usuario_id = st.session_state.get("usuario_id")
    access_key = st.session_state.get("access_key")

    clean_payload = _sanitize_payload(payload)

    run_sql(
        """
        INSERT INTO audit_log (actor_usuario_id, actor_key, action, table_name, record_id, payload)
        VALUES (%s,%s,%s,%s,%s,%s)
        """,
        (
            usuario_id,
            _mask_key(access_key),
            action,
            table,
            str(record_id) if record_id is not None else None,
            json.dumps(clean_payload) if clean_payload is not None else None,
        ),
    )
