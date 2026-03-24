# auth.py
import os
import secrets
import string
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError
from db import fetch_df_cached, run_sql

ALPHABET = string.ascii_letters + string.digits


def generate_access_key(length: int = 12) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))





def require_access():
    if st.session_state.get("auth_ok"):
        return

    st.title("HURR PARTICIPAÇÕES • Acesso")

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



        # ---------- USUÁRIO NORMAL ----------
        df = fetch_df_cached(
            """
            SELECT id, nome
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
        usuario_nome = str(df["nome"].iloc[0])

        # Registrar no log
        try:
            ip = None
            user_agent = None
            if hasattr(st, "context") and hasattr(st.context, "headers"):
                # Streamlit >= 1.37
                headers = getattr(st.context, "headers", {})
                ip = headers.get("X-Forwarded-For", headers.get("Remote-Addr", ""))
                if ip and "," in ip:
                    ip = ip.split(",")[0].strip()
                user_agent = headers.get("User-Agent", "")
            
            run_sql(
                "INSERT INTO usuario_login (usuario_id, ip, user_agent) VALUES (%s, %s, %s)",
                (usuario_id, ip, user_agent)
            )
        except Exception as e:
            print("Erro ao registrar login:", e)

        st.session_state["auth_ok"] = True
        st.session_state["is_admin"] = False
        st.session_state["usuario_id"] = usuario_id
        st.session_state["user_nome"] = usuario_nome
        st.session_state["access_key"] = key
        st.rerun()

    st.stop()
