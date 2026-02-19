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
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                rows = cur.fetchall()
                cols = [c.name for c in cur.description] if cur.description else []
            # O commit aqui avisa ao banco que a leitura acabou e ele pode liberar a transação
            conn.commit() 
            return pd.DataFrame(rows, columns=cols)
        except Exception as e:
            # ESSA É A MÁGICA: Se der erro, desfaz tudo e devolve a conexão limpa!
            conn.rollback()
            raise e


def execute(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    with fresh_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                rowcount = cur.rowcount
            conn.commit()
            return rowcount
        except Exception:
            conn.rollback()
            raise



def executemany(sql: str, seq_of_params: Iterable[Tuple[Any, ...]]) -> None:
    with fresh_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.executemany(sql, seq_of_params)
            conn.commit()
        except Exception:
            conn.rollback()
            raise



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
def run_sql(sql: str, params=None):
    # fresh_conn() é um context manager
    with fresh_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
            conn.commit()
        except Exception:
            conn.rollback()
            raise



def run_sql_returning_id(sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
    with fresh_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                row = cur.fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise

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
    # 1. Cria uma cópia segura dos dados e mapeia as nomenclaturas do Python para o Banco
    db_payload = data.copy()
    
    # Previne erro de nulo nos IDs obrigatórios da tabela fechamento
    db_payload["empresa_id"] = data.get("empresa_id") or 1 
    db_payload["cliente_id"] = data.get("cliente_id") or 1
    db_payload["processo_id"] = data.get("processo_id")
    
    # Mapeia nomes do dicionário gerado na tela para os nomes exatos das colunas SQL
    db_payload["adicional_brl"] = data.get("adic_brl", 0)
    db_payload["custo_aquisicao_brl"] = data.get("custo_aquisicao", 0)
    db_payload["pis_venda_pct"] = data.get("pis_v_pct", 0)
    db_payload["cofins_venda_pct"] = data.get("cofins_v_pct", 0)
    db_payload["icms_venda_pct"] = data.get("icms_v_pct", 0)
    db_payload["markup_venda_pct"] = data.get("markup_v_pct", 0)
    db_payload["ipi_venda_pct"] = data.get("ipi_v_pct", 0)
    
    # Tratamento de segurança para dados ausentes (mantém retrocompatibilidade)
    db_payload["di"] = data.get("di", "")
    for key in ["origem", "modal", "destino"]:
        if key not in db_payload: db_payload[key] = ""
    for num_key in ["qtde_container", "taxa_conversao", "fob_brl", "frete_brl", 
                    "seguro_brl", "cif_brl", "ii_brl", "ipi_brl", "pis_brl", 
                    "cofins_brl", "icms_brl", "total_despesas_brl", "bc_normal", "total_nf_saida"]:
        if num_key not in db_payload: db_payload[num_key] = 0.0

    # 2. Se já tem ID, atualiza (UPDATE)
    if data.get("id"):
        sql = """
        update fechamento
        set
          data = %(data)s,
          empresa = %(empresa)s,
          cliente = %(cliente)s,
          referencia = %(referencia)s,
          di = %(di)s,
          origem = %(origem)s,
          modal = %(modal)s,
          destino = %(destino)s,
          qtde_container = %(qtde_container)s,
          taxa_conversao = %(taxa_conversao)s,
          fob_brl = %(fob_brl)s,
          frete_brl = %(frete_brl)s,
          seguro_brl = %(seguro_brl)s,
          adicional_brl = %(adicional_brl)s,
          cif_brl = %(cif_brl)s,
          ii_brl = %(ii_brl)s,
          ipi_brl = %(ipi_brl)s,
          pis_brl = %(pis_brl)s,
          cofins_brl = %(cofins_brl)s,
          icms_brl = %(icms_brl)s,
          total_despesas_brl = %(total_despesas_brl)s,
          custo_aquisicao_brl = %(custo_aquisicao_brl)s,
          pis_venda_pct = %(pis_venda_pct)s,
          cofins_venda_pct = %(cofins_venda_pct)s,
          icms_venda_pct = %(icms_venda_pct)s,
          markup_venda_pct = %(markup_venda_pct)s,
          ipi_venda_pct = %(ipi_venda_pct)s,
          bc_normal = %(bc_normal)s,
          total_nf_saida = %(total_nf_saida)s,
          empresa_id = %(empresa_id)s,
          cliente_id = %(cliente_id)s,
          processo_id = %(processo_id)s
        where id = %(id)s
        returning id;
        """
        df = fetch_df(sql, db_payload)
        return int(df.iloc[0]["id"])

    # 3. Se não tem ID, cria um novo (INSERT)
    sql = """
    insert into fechamento (
      data, empresa, cliente, referencia, di,
      origem, modal, destino, qtde_container, taxa_conversao,
      fob_brl, frete_brl, seguro_brl, adicional_brl, cif_brl,
      ii_brl, ipi_brl, pis_brl, cofins_brl, icms_brl,
      total_despesas_brl, custo_aquisicao_brl,
      pis_venda_pct, cofins_venda_pct, icms_venda_pct, markup_venda_pct, ipi_venda_pct,
      bc_normal, total_nf_saida,
      empresa_id, cliente_id, processo_id
    ) values (
      %(data)s, %(empresa)s, %(cliente)s, %(referencia)s, %(di)s,
      %(origem)s, %(modal)s, %(destino)s, %(qtde_container)s, %(taxa_conversao)s,
      %(fob_brl)s, %(frete_brl)s, %(seguro_brl)s, %(adicional_brl)s, %(cif_brl)s,
      %(ii_brl)s, %(ipi_brl)s, %(pis_brl)s, %(cofins_brl)s, %(icms_brl)s,
      %(total_despesas_brl)s, %(custo_aquisicao_brl)s,
      %(pis_venda_pct)s, %(cofins_venda_pct)s, %(icms_venda_pct)s, %(markup_venda_pct)s, %(ipi_venda_pct)s,
      %(bc_normal)s, %(total_nf_saida)s,
      %(empresa_id)s, %(cliente_id)s, %(processo_id)s
    )
    returning id;
    """
    df = fetch_df(sql, db_payload)
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


def get_lista_clientes() -> List[str]:
    """Retorna lista de nomes da tabela cliente (singular)."""
    # Seu SQL usa 'cliente' no singular
    df = fetch_df_cached("SELECT nome FROM cliente WHERE ativo = true ORDER BY nome")
    if df.empty:
        return []
    return df["nome"].tolist()

def get_lista_empresas() -> List[str]:
    """Retorna lista de nomes da tabela empresa (singular)."""
    # Seu SQL usa 'empresa' no singular
    df = fetch_df_cached("SELECT nome FROM empresa ORDER BY nome")
    if df.empty:
        return []
    return df["nome"].tolist()

def get_dados_relatorio_filtrado(data_inicio, data_fim, tipo_filtro, valor_filtro) -> pd.DataFrame:
    """
    Usa a VIEW vw_movimento_bancario_conciliado para gerar o relatório.
    - Faz JOIN com conciliacao para pegar Observação personalizada.
    - Faz JOIN com empresa para mostrar de quem é a conta bancária.
    """
    
    sql = """
        SELECT 
            -- Nova Coluna solicitada:
            e.nome as "Empresa",
            
            v.banco_nome as "Banco", 
            v.dt_movimento as "Data", 
            
            -- Histórico Original
            v.descricao as "Movimentação",
            
            -- Descrição Personalizada (Observação da Conciliação)
            COALESCE(tbl_c.observacao, v.descricao) as "Descrição",
            
            v.tipo_movimento as "Tipo", 
            v.categoria_financeira as "Categoria", 
            
            -- Valores separados
            CASE WHEN v.valor > 0 THEN v.valor ELSE 0 END as "Entrada",
            CASE WHEN v.valor < 0 THEN ABS(v.valor) ELSE 0 END as "Saída",
            
            v.saldo as "Saldo",
            
            -- Campos para filtro (ocultos na lógica, mas usados no WHERE)
            v.cliente_nome
            
        FROM vw_movimento_bancario_conciliado v
        JOIN conta_bancaria cb ON v.conta_bancaria_id = cb.id
        JOIN empresa e ON cb.empresa_id = e.id
        LEFT JOIN conciliacao tbl_c ON v.conciliacao_id = tbl_c.id
        
        WHERE v.dt_movimento BETWEEN %s AND %s
    """
    
    params = [data_inicio, data_fim]

    # Filtros
    if tipo_filtro == "Cliente" and valor_filtro != "Todos":
        sql += " AND v.cliente_nome = %s"
        params.append(valor_filtro)
        
    elif tipo_filtro == "Empresa" and valor_filtro != "Todas":
        sql += " AND e.nome = %s"
        params.append(valor_filtro)
    
    sql += " ORDER BY e.nome ASC, v.dt_movimento ASC, v.movimento_id ASC"

    return fetch_df(sql, tuple(params))