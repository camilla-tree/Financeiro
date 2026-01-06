# auth.py
import os
import secrets
import string
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from db import fetch_df, fresh_conn

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


def _get_client_ip_and_ua() -> tuple[str | None, str | None]:
    """
    Tenta obter IP real via headers.
    Em deploy geralmente vem em X-Forwarded-For.
    """
    ip = None
    ua = None

    headers = None
    try:
        headers = st.context.headers  # type: ignore[attr-defined]
    except Exception:
        headers = None

    if headers:
        ua = headers.get("user-agent")

        xff = headers.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[0].strip()

        if not ip:
            ip = headers.get("x-real-ip") or headers.get("client-ip")

    return ip, ua


def _registrar_login(usuario_id: int | None, is_admin: bool):
    ip, ua = _get_client_ip_and_ua()
    try:
        with fresh_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into usuario_login (usuario_id, is_admin, ip, user_agent)
                    values (%s, %s, %s, %s)
                    """,
                    (usuario_id, bool(is_admin), ip, ua),
                )
            conn.commit()
    except Exception:
        # não derruba o app se falhar log (MVP)
        pass


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
        if admin_key and key == admin_key:
            st.session_state["auth_ok"] = True
            st.session_state["is_admin"] = True
            st.session_state["usuario_id"] = None
            st.session_state["access_key"] = key

            _registrar_login(usuario_id=None, is_admin=True)
            st.rerun()

        # ---------- USUÁRIO NORMAL ----------
        df = fetch_df(
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

        usuario_id = int(df["id"].iloc[0])

        st.session_state["auth_ok"] = True
        st.session_state["is_admin"] = False
        st.session_state["usuario_id"] = usuario_id
        st.session_state["access_key"] = key

        _registrar_login(usuario_id=usuario_id, is_admin=False)
        st.rerun()

    st.stop()
