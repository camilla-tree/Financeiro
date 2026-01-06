# auth.py
import os
import secrets
import string
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
from db import fetch_df_cached

ALPHABET = string.ascii_letters + string.digits


def generate_access_key(length: int = 12) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def _get_admin_key() -> str:
    # 1) tenta .env
    v = os.getenv("ADMIN_ACCESS_KEY")
    if v:
        return v.strip()

    # 2) tenta st.secrets (sem quebrar se não existir)
    try:
        return (st.secrets.get("ADMIN_ACCESS_KEY") or "").strip()
    except StreamlitSecretNotFoundError:
        return ""


def require_access():
    if st.session_state.get("auth_ok"):
        return

    st.title("Treecomex • Acesso")

    key = st.text_input(
        "Chave de acesso (12 caracteres)",
        type="password",
        max_chars=12,
    )

    if st.button("Entrar", type="primary"):
        key = (key or "").strip()

        if len(key) != 12:
            st.error("A chave deve ter 12 caracteres.")
            st.stop()

        # ---------- ADMIN ----------
        admin_key = _get_admin_key()
        st.write("DEBUG admin_key:", _get_admin_key())
        if admin_key and key == admin_key:
            st.session_state["auth_ok"] = True
            st.session_state["is_admin"] = True
            st.session_state["usuario_id"] = None
            st.session_state["access_key"] = key
            st.rerun()

        # ---------- USUÁRIO NORMAL ----------
        df = fetch_df_cached(
            """
            SELECT id
            FROM usuario
            WHERE access_key = %s
              AND ativo = true
            LIMIT 1
            """,
            (key,),
        )

        if df.empty:
            st.error("Chave inválida ou usuário inativo.")
            st.stop()

        st.session_state["auth_ok"] = True
        st.session_state["is_admin"] = False
        st.session_state["usuario_id"] = int(df["id"].iloc[0])
        st.session_state["access_key"] = key
        st.rerun()

    st.stop()
