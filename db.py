from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterable, Optional, Tuple

import pandas as pd
import psycopg
import streamlit as st


def _get_database_url() -> str:
    # prioridade: Streamlit secrets -> env var -> fallback
    if "DATABASE_URL" in st.secrets:
        return str(st.secrets["DATABASE_URL"])
    if os.getenv("DATABASE_URL"):
        return str(os.getenv("DATABASE_URL"))
    raise RuntimeError("DATABASE_URL não encontrado (st.secrets ou env var).")


def _make_conn() -> psycopg.Connection:
    url = _get_database_url()
    kwargs = dict(prepare_threshold=0)

    # Só força sslmode se não tiver na URL
    if "sslmode=" not in url:
        kwargs["sslmode"] = "require"

    conn = psycopg.connect(url, **kwargs)


    # Segurança extra: se a sessão foi reaproveitada pelo pooler,
    # isso remove qualquer prepared statement pendurado.
    try:
        with conn.cursor() as cur:
            cur.execute("DEALLOCATE ALL;")
        conn.commit()
    except Exception:
        # Se não suportar por algum motivo, ignora.
        try:
            conn.rollback()
        except Exception:
            pass

    return conn


@contextmanager
def fresh_conn():
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


def run_sql(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    # compatibilidade com imports antigos
    return execute(sql, params)


def run_sql_returning_id(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    """
    Use com INSERT ... RETURNING id (ou RETURNING alguma_coluna_id).
    """
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise RuntimeError("run_sql_returning_id: query não retornou nada. Faltou RETURNING?")

    # Se vier dict-like, tenta chave 'id', senão pega o primeiro valor
    try:
        if isinstance(row, dict) and "id" in row:
            return int(row["id"])
    except Exception:
        pass

    # tupla/lista
    return int(row[0])
