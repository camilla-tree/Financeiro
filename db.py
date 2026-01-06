import os
import psycopg
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError



load_dotenv()


def _get_secret(name: str, default: str = "") -> str:
    v = os.getenv(name)
    if v:
        return v
    try:
        return st.secrets.get(name, default)
    except StreamlitSecretNotFoundError:
        return default


def _make_conn():
    url = _get_secret("DATABASE_URL")
    if not url:
        raise RuntimeError("Defina DATABASE_URL via .env ou st.secrets.")

    conn = psycopg.connect(
        url,
        sslmode="require",
        prepare_threshold=0,  # desliga prepared statements automáticos
    )
    conn.autocommit = True
    return conn


def db_conn():
    conn = st.session_state.get("db_conn")
    if conn is None:
        conn = _make_conn()
        st.session_state["db_conn"] = conn
    return conn


def _reset_conn():
    try:
        conn = st.session_state.get("db_conn")
        if conn is not None:
            conn.close()
    except Exception:
        pass
    st.session_state["db_conn"] = _make_conn()


def fetch_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    try:
        conn = db_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cols else []
        return pd.DataFrame(rows, columns=cols)

    except psycopg.errors.DuplicatePreparedStatement:
        # conexão entrou em estado ruim -> recria e tenta 1 vez
        _reset_conn()
        conn = db_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cols else []
        return pd.DataFrame(rows, columns=cols)


def run_sql(sql: str, params: tuple = ()) -> None:
    try:
        conn = db_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)

    except psycopg.errors.DuplicatePreparedStatement:
        _reset_conn()
        conn = db_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)


def run_sql_returning_id(sql: str, params: tuple = ()) -> int:
    try:
        conn = db_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            new_id = cur.fetchone()[0]
        return int(new_id)

    except psycopg.errors.DuplicatePreparedStatement:
        _reset_conn()
        conn = db_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            new_id = cur.fetchone()[0]
        return int(new_id)

def fresh_conn():
    """Conexão nova para operações pesadas (ex.: import)."""
    url = _get_secret("DATABASE_URL")
    if not url:
        raise RuntimeError("Defina DATABASE_URL via .env ou st.secrets.")

    conn = psycopg.connect(
        url,
        sslmode="require",
        prepare_threshold=0,
    )
    conn.autocommit = False  # vamos controlar commit/rollback no import
    return conn
