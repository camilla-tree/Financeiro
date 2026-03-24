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
        conn = psycopg.connect(url, prepare_threshold=None, options="-c plan_cache_mode=force_generic_plan")
    except TypeError:
        # tentativa 2: ambiente que não aceita "options"
        # O Supabase Transaction Pooler (PgBouncer) requer prepare_threshold=None
        conn = psycopg.connect(url, prepare_threshold=None)

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


def get_lista_clientes() -> List[str]:
    """Retorna lista de nomes da tabela cliente (singular)."""
    # Seu SQL usa 'cliente' no singular
    df = fetch_df_cached("SELECT nome FROM cliente WHERE ativo = true ORDER BY nome")
    if df.empty:
        return []
    return df["nome"].tolist()

def get_empresas_por_cliente(cliente_nome: str) -> List[str]:
    """Retorna lista de nomes das empresas que possuem processos para um cliente especifico."""
    sql = """
        SELECT DISTINCT e.nome
        FROM empresa e
        JOIN processo p ON p.empresa_id = e.id
        JOIN cliente c ON c.id = p.cliente_id
        WHERE c.nome = %s
        ORDER BY e.nome
    """
    df = fetch_df_cached(sql, (cliente_nome,))
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

def get_dados_relatorio_filtrado(data_inicio, data_fim, tipo_filtro, valor_filtro, empresa_selecionada=None) -> pd.DataFrame:
    """
    Usa a VIEW vw_movimento_bancario_conciliado para gerar o relatório.
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
            
            -- Valores da Lógica de Cálculo de Saldo (Vêm da transação root)
            mb.valor as "Valor_Original",
            mb.is_cliente as "Is_Cliente",
            
            -- Valores rateados separados
            CASE WHEN v.valor > 0 THEN v.valor ELSE 0 END as "Entrada",
            CASE WHEN v.valor < 0 THEN ABS(v.valor) ELSE 0 END as "Saída",
            
            v.saldo as "Saldo"
            
        FROM vw_movimento_bancario_conciliado v
        JOIN conta_bancaria cb ON v.conta_bancaria_id = cb.id
        JOIN empresa e ON cb.empresa_id = e.id
        -- Re-join with conciliacao
        LEFT JOIN conciliacao tbl_c ON v.conciliacao_id = tbl_c.id
        -- Re-join root movement to get is_cliente and original value
        JOIN movimento_bancario mb ON mb.id = v.movimento_id
        
        WHERE v.dt_movimento BETWEEN %s AND %s
    """
    
    params = [data_inicio, data_fim]

    # Filtros Opcionais Adicionais
    if tipo_filtro == "Cliente":
        # Se for para o relatorio de cliente, o is_cliente TEM que ser obrigatorio (só aparece is_cliente=true na view do cliente)
        sql += " AND mb.is_cliente = true "
        
        if valor_filtro != "Todos":
            sql += " AND v.cliente_nome = %s "
            params.append(valor_filtro)
            
        if empresa_selecionada and empresa_selecionada != "Todas":
            sql += " AND e.nome = %s "
            params.append(empresa_selecionada)
            
    elif tipo_filtro == "Empresa":
        if valor_filtro != "Todas":
            sql += " AND e.nome = %s "
            params.append(valor_filtro)
    
    sql += " ORDER BY e.nome ASC, v.dt_movimento ASC, v.movimento_id ASC"

    return fetch_df(sql, tuple(params))

def get_historico_importacoes() -> pd.DataFrame:
    """Retorna o histórico de extratos importados com dados de auditoria."""
    sql = """
        SELECT
            ei.id as "ID Extrato",
            COALESCE(u.nome, 'Sistema') as "Usuário",
            ei.dt_importacao as "Data Importação",
            e.nome as "Empresa",
            cb.apelido as "Conta Bancária",
            MIN(mb.dt_movimento) as "Data Inicial",
            MAX(mb.dt_movimento) as "Data Final",
            ei.status as "Status"
        FROM extrato_importacao ei
        LEFT JOIN usuario u ON ei.usuario_id = u.id
        JOIN conta_bancaria cb ON ei.conta_bancaria_id = cb.id
        JOIN empresa e ON cb.empresa_id = e.id
        -- Usando JOIN interno para esconder os extratos que tiverem seus movimentos excluídos
        JOIN movimento_bancario mb ON mb.importacao_id = ei.id
        GROUP BY ei.id, u.nome, ei.dt_importacao, e.nome, cb.apelido, ei.status
        ORDER BY ei.dt_importacao DESC
    """
    return fetch_df(sql)

def check_extrato_conciliado(importacao_id: int) -> bool:
    """Retorna True se existe algum movimento bancário deste extrato que já foi conciliado."""
    sql = """
        SELECT 1
        FROM movimento_bancario mb
        LEFT JOIN conciliacao c ON c.movimento_bancario_id = mb.id
        LEFT JOIN movimento_processo mp ON mp.movimento_bancario_id = mb.id
        WHERE mb.importacao_id = %s AND (c.id IS NOT NULL OR mp.id IS NOT NULL)
        LIMIT 1
    """
    df = fetch_df(sql, (importacao_id,))
    return not df.empty

def delete_movimentos_extrato(importacao_id: int) -> None:
    """Deleta os movimentos bancários associados à importação. As linhas raw e a importação são mantidas."""
    # Como garantimos antes que não há correlação nas tabelas de conciliação, é seguro deletar movimentos diretos.
    sql = "DELETE FROM movimento_bancario WHERE importacao_id = %s;"
    run_sql(sql, (importacao_id,))