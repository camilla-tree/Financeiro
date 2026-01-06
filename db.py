from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterable, Optional, Tuple

import pandas as pd
import psycopg
import streamlit as st
from psycopg_pool import ConnectionPool


def _get_database_url() -> str:
    if "DATABASE_URL" in st.secrets:
        return str(st.secrets["DATABASE_URL"])
    url = os.getenv("DATABASE_URL")
    if url:
        return str(url)
    raise RuntimeError("DATABASE_URL não encontrado (st.secrets ou env var).")


@st.cache_resource
def _get_pool() -> ConnectionPool:
    url = _get_database_url()
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"

    # números conservadores pro Streamlit Cloud
    return ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=4,
        kwargs={"prepare_threshold": 0},
    )


@contextmanager
def fresh_conn():
    pool = _get_pool()
    with pool.connection() as conn:
        yield conn


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
            rc = cur.rowcount
        conn.commit()
    return rc


def executemany(sql: str, seq_of_params: Iterable[Tuple[Any, ...]]) -> None:
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, seq_of_params)
        conn.commit()


def run_sql(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    return execute(sql, params)


def run_sql_returning_id(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError("run_sql_returning_id: faltou RETURNING?")
    return int(row[0])

@st.cache_data(ttl=120)
def fetch_df_cached(sql: str, params: Optional[Tuple[Any, ...]] = None) -> pd.DataFrame:
    return fetch_df(sql, params)
