from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterable, Optional, Tuple

import pandas as pd
import psycopg
import streamlit as st
from typing import Any, Dict, List, Optional


# -----------------------------
# URL / conexão
# -----------------------------
def _get_database_url() -> str:
    if "DATABASE_URL" in st.secrets:
        return str(st.secrets["DATABASE_URL"])
    url = os.getenv("DATABASE_URL")
    if url:
        return str(url)
    raise RuntimeError("DATABASE_URL não encontrado (st.secrets ou env var).")


def _normalize_url(url: str) -> str:
    # Supabase normalmente precisa de SSL
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


def _new_conn() -> psycopg.Connection:
    url = _normalize_url(_get_database_url())

    try:
        # tentativa 1: com options
        conn = psycopg.connect(url, prepare_threshold=0, options="-c plan_cache_mode=force_generic_plan")
    except TypeError:
        # tentativa 2: ambiente que não aceita "options"
        conn = psycopg.connect(url, prepare_threshold=0)

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



def _get_session_conn() -> psycopg.Connection:
    """
    Retorna UMA conexão por sessão (st.session_state).
    Isso elimina o custo de abrir conexão a cada rerun/query, que é o que deixa lento no Cloud.
    """
    conn = st.session_state.get("_db_conn")

    # psycopg tem conn.closed (0 = aberta). Se estiver None ou fechada, cria de novo.
    if conn is None or getattr(conn, "closed", 1) != 0:
        st.session_state["_db_conn"] = _new_conn()

    return st.session_state["_db_conn"]


@contextmanager
def fresh_conn():
    """
    Use sempre:
        with fresh_conn() as conn:
            with conn.cursor() as cur:
                ...
    Nota: NÃO fechamos a conexão aqui, pois ela é reusada pela sessão.
    """
    conn = _get_session_conn()
    yield conn


# -----------------------------
# Helpers de consulta
# -----------------------------
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


# -----------------------------
# Cache (para você NÃO refatorar tudo de novo)
# -----------------------------
@st.cache_data(ttl=120, show_spinner=False)
def fetch_df_cached(sql: str, params: Optional[Tuple[Any, ...]] = None) -> pd.DataFrame:
    # params precisa ser tupla (hashável) para o cache funcionar bem
    return fetch_df(sql, params)


# -----------------------------
# Compatibilidade
# -----------------------------
def run_sql(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    return execute(sql, params)


def run_sql_returning_id(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
        conn.commit()

    if row is None:
        raise RuntimeError("run_sql_returning_id: query não retornou nada. Faltou RETURNING?")
    return int(row[0])


# -----------------------------
# (Opcional) botão de emergência: resetar conexão da sessão
# -----------------------------
def reset_conn():
    conn = st.session_state.get("_db_conn")
    try:
        if conn is not None and getattr(conn, "closed", 1) == 0:
            conn.close()
    except Exception:
        pass
    st.session_state["_db_conn"] = None



# FECHAMENTO
def upsert_fechamento(data: Dict[str, Any]) -> int:
    """
    Cria/atualiza fechamento na tabela existente (PK = id).
    Retorna id.
    """
    if data.get("id"):
        sql = """
        update fechamento
        set
          data = %(data)s,
          empresa = %(empresa)s,
          cliente = %(cliente)s,
          referencia = %(referencia)s,
          valor_fob_usd = %(valor_fob_usd)s,
          frete_usd = %(frete_usd)s,
          adicional_usd = %(adicional_usd)s,
          seguro_usd = %(seguro_usd)s,
          taxa_conversao = %(taxa_conversao)s,
          origem = %(origem)s,
          modal = %(modal)s,
          destino = %(destino)s,
          qtde_container = %(qtde_container)s,
          bl_awb = %(bl_awb)s
        where id = %(id)s
        returning id;
        """
        df = fetch_df(sql, data)
        return int(df.iloc[0]["id"])

    sql = """
    insert into fechamento (
      data, empresa, cliente, referencia,
      valor_fob_usd, frete_usd, adicional_usd, seguro_usd, taxa_conversao,
      origem, modal, destino, qtde_container, bl_awb
    ) values (
      %(data)s, %(empresa)s, %(cliente)s, %(referencia)s,
      %(valor_fob_usd)s, %(frete_usd)s, %(adicional_usd)s, %(seguro_usd)s, %(taxa_conversao)s,
      %(origem)s, %(modal)s, %(destino)s, %(qtde_container)s, %(bl_awb)s
    )
    returning id;
    """
    df = fetch_df(sql, data)
    return int(df.iloc[0]["id"])


def list_fechamentos(limit: int = 50) -> pd.DataFrame:
    sql = """
    select
      id,
      data,
      empresa,
      cliente,
      referencia,
      valor_fob_usd,
      frete_usd,
      adicional_usd,
      seguro_usd,
      taxa_conversao,
      origem,
      modal,
      destino,
      qtde_container,
      bl_awb,
      updated_at
    from fechamento
    order by coalesce(updated_at, now()) desc
    limit %(limit)s;
    """
    return fetch_df(sql, {"limit": limit})


def get_fechamento(id_: int) -> Optional[Dict[str, Any]]:
    df = fetch_df("select * from fechamento where id = %(id)s", {"id": id_})
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def get_despesas(fechamento_id: int) -> pd.DataFrame:
    return fetch_df(
        """
        select id, ordem, descricao, valor_brl, estimado
        from fechamento_despesa
        where fechamento_id = %(id)s
        order by ordem asc, id asc;
        """,
        {"id": fechamento_id},
    )


def replace_despesas(fechamento_id: int, despesas: List[Dict[str, Any]]) -> None:
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from fechamento_despesa where fechamento_id = %s", (fechamento_id,))
            for d in despesas:
                cur.execute(
                    """
                    insert into fechamento_despesa (fechamento_id, ordem, descricao, valor_brl, estimado)
                    values (%s, %s, %s, %s, %s)
                    """,
                    (
                        fechamento_id,
                        int(d.get("ordem", 0) or 0),
                        str(d.get("descricao", "")).strip(),
                        float(d.get("valor_brl", 0) or 0),
                        bool(d.get("estimado", False)),
                    ),
                )
        conn.commit()
