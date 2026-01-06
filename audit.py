import json
import streamlit as st
from db import run_sql


def _mask_key(k: str | None) -> str | None:
    if not k:
        return None
    return "********" + k[-4:]


def log_action(action: str, table: str, record_id=None, payload: dict | None = None):
    usuario_id = st.session_state.get("usuario_id")
    access_key = st.session_state.get("access_key")

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
            json.dumps(payload) if payload is not None else None,
        ),
    )
