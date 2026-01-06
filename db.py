from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterable, Optional, Tuple

import pandas as pd
import psycopg
import streamlit as st


def _get_database_url() -> str:
    """
    Prioridade:
    1) Streamlit Cloud: st.secrets["DATABASE_URL"]
    2) Local: variável de ambiente DATABASE_URL
    """
    if "DATABASE_URL" in st.secrets:
        return str(st.secrets["DATABASE_URL"])

    url = os.getenv("DATABASE_URL")
    if url:
        return str(url)

    raise RuntimeError("DATABASE_URL não encontrado (st.secrets ou env var).")


def _make_conn() -> psycopg.Connection:
    """
    Abre conexão nova (segura) e faz uma 'limpeza' defensiva para evitar
    problemas com prepared statements quando usando poolers (ex.: Supabase).
    """
    url = _get_database_url()

    # Se não tiver sslmode na URL, força require (Supabase normalmente precisa)
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"

    # prepare_threshold=0 ajuda a reduzir problemas de prepared statements em poolers
    conn = psycopg.connect(url, prepare_threshold=0)

    # Segurança extra: se a sessão foi reaproveitada por algum pooler,
    # remove prepared statements pendurados (pode ser um pouco mais lento, mas evita bugs).
    try:
        with conn.cursor() as cur:
            cur.execute("DEALLOCATE ALL;")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass

    return conn


@contextmanager
def fresh_conn():
    """
    Use sempre assim:
        with fresh_conn() as conn:
            with conn.cursor() as cur:
                ...
    """
    conn = _make_conn()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_df(sql: str, params: Optional[Tuple[Any, ...]] = None) -> pd.DataFrame:
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
            cols = [c.name for c in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


def execute(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            rowcount = cur.rowcount
        conn.commit()
    return rowcount


def executemany(sql: str, seq_of_params: Iterable[Tuple[Any, ...]]) -> None:
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, seq_of_params)
        conn.commit()


# Compatibilidade com imports antigos
def run_sql(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    return execute(sql, params)


def run_sql_returning_id(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    """
    Use com:
      INSERT ... RETURNING id
    ou:
      INSERT ... RETURNING alguma_coluna_id
    """
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise RuntimeError("run_sql_returning_id: query não retornou nada. Faltou RETURNING?")

    # row pode ser tupla; pegamos o primeiro campo
    return int(row[0])
