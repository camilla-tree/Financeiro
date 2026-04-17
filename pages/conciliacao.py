from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Any, Tuple

import psycopg
import re


import streamlit as st
import pandas as pd

from db import fetch_df_cached, fresh_conn

from services.conciliacao_service import (
    ConciliacaoMaps,
    calcular_changes,
    aplicar_changes_no_banco,
)


@st.cache_data(ttl=60)
def get_status_id(nome: str) -> int:
    df = fetch_df_cached("SELECT id FROM conciliacao_status WHERE nome = %s", (nome,))
    if df.empty:
        raise RuntimeError(f"Seed faltando em conciliacao_status: {nome}")
    return int(df.iloc[0]["id"])


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if pd.isna(v):
            return None
        return int(v)
    except Exception:
        return None
    
def _norm_upper(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.upper()

def _run_sql(sql: str, params=None):
    with fresh_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
            conn.commit()
        except Exception:
            conn.rollback()
            raise

def _safe_delete_categoria(cat_id: int) -> bool:
    try:
        _run_sql("DELETE FROM categoria_financeira WHERE id=%s", (int(cat_id),))
        return True
    except psycopg.errors.ForeignKeyViolation:
        st.warning(
            f"Não é possível excluir a categoria (id={cat_id}) pois há lançamentos que dependem dela. "
            f"Considere desativar."
        )
        return False



def render_conciliacao():
    st.subheader("Conciliação • v1 (manual)")

    # =========================
    # Filtros
    # =========================
    df_emp = fetch_df_cached("SELECT id, nome FROM empresa ORDER BY nome")
    if df_emp.empty:
        st.warning("Cadastre empresas antes (Admin).")
        return

    today = date.today()
    primeiro_dia_mes = date(today.year, today.month, 1)

    # defaults por sessão
    if "conc_dt_ini" not in st.session_state:
        st.session_state["conc_dt_ini"] = primeiro_dia_mes
    if "conc_dt_fim" not in st.session_state:
        st.session_state["conc_dt_fim"] = today


    # Atalhos
    st.caption("Atalhos de período")
    a1, a2, a3, a4 = st.columns([1, 1, 1, 1])

    def _set_periodo(dt_ini, dt_fim):
        st.session_state["conc_dt_ini"] = dt_ini
        st.session_state["conc_dt_fim"] = dt_fim
        st.session_state["conc_force_reload"] = True

    with a1:
        if st.button("Mês atual", width="stretch"):
            _set_periodo(primeiro_dia_mes, today)
    with a2:
        if st.button("Últimos 7d", width="stretch"):
            _set_periodo(today - timedelta(days=7), today)
    with a3:
        if st.button("Últimos 30d", width="stretch"):
            _set_periodo(today - timedelta(days=30), today)
    with a4:
        if st.button("Últimos 90d", width="stretch"):
            _set_periodo(today - timedelta(days=90), today)


    col_f1, col_f2, col_f3, col_f4 = st.columns([2, 2, 2, 2])

    with col_f1:
        emp_nome = st.selectbox("Empresa", df_emp["nome"].tolist(), key="conc_emp")
        empresa_id = int(df_emp[df_emp["nome"] == emp_nome]["id"].iloc[0])

    df_contas = fetch_df_cached(
        """
        SELECT cb.id AS conta_bancaria_id, cb.apelido, cb.agencia, cb.numero,
               b.codigo AS banco_codigo
        FROM conta_bancaria cb
        JOIN banco b ON b.id = cb.banco_id
        WHERE cb.empresa_id = %s AND cb.ativa = true
        ORDER BY b.codigo, cb.apelido NULLS LAST, cb.numero
        """,
        (empresa_id,),
    )
    if df_contas.empty:
        st.warning("Essa empresa não tem conta bancária ativa.")
        return

    conta_options = []
    conta_map = {}
    for _, r in df_contas.iterrows():
        label = (
            f"{r['banco_codigo']} • {r.get('apelido') or 'Sem apelido'} • "
            f"Ag {r.get('agencia') or '-'} • Cc {r.get('numero') or '-'} "
            f"(ID {r['conta_bancaria_id']})"
        )
        conta_options.append(label)
        conta_map[label] = r

    if conta_options:
        if "conc_conta" in st.session_state and st.session_state["conc_conta"] not in conta_options:
            st.session_state["conc_conta"] = conta_options[0]

    with col_f2:
        conta_label = st.selectbox("Conta bancária", conta_options, key="conc_conta")
        conta_bancaria_id = int(conta_map[conta_label]["conta_bancaria_id"])


    with col_f3:
        dt_ini = st.date_input("Data início", key="conc_dt_ini")
    with col_f4:
        dt_fim = st.date_input("Data fim", key="conc_dt_fim")

    if dt_ini > dt_fim:
        st.warning("Data início maior que data fim — ajustei automaticamente.")
        st.session_state["conc_dt_ini"] = dt_fim
        dt_ini = dt_fim

    colA, colB = st.columns([2, 1])
    with colB:
        mostrar_todos = st.checkbox("Mostrar também já conciliados", value=False, key="conc_show_all")
        limite = st.number_input("Limite de linhas", min_value=50, max_value=2000, value=300, step=50, key="conc_limit")

    st.divider()

    # =========================
    # Dados de Apoio
    # =========================
    usuario_id = None

    st_confirmada = get_status_id("CONFIRMADA")

    df_processos = fetch_df_cached(
        """
        SELECT p.id, p.referencia, c.nome AS cliente, ps.nome AS status
        FROM processo p
        JOIN cliente c ON c.id = p.cliente_id
        LEFT JOIN processo_status ps ON ps.id = p.status_id
        WHERE p.empresa_id = %s
        ORDER BY p.id DESC
        LIMIT 2000
        """,
        (empresa_id,),
    )
 
    clientes = sorted(df_processos["cliente"].dropna().unique().tolist()) if not df_processos.empty else []
    processos_ref = sorted(df_processos["referencia"].dropna().unique().tolist()) if not df_processos.empty else []

    # =========================================================================
    # 1) Categorias Financeiras (Expander)
    # =========================================================================
    st.markdown("### Configurações e Vínculos")
    
    # Feedback Flash
    flash = st.session_state.pop("catfin_flash", None)
    if flash:
        t = flash.get("type")
        msg = flash.get("msg", "")
        if t == "success": st.success(msg)
        elif t == "warning": st.warning(msg)
        else: st.error(msg)

    with st.expander("📂 Categorias Financeiras", expanded=False):
        col1, col2 = st.columns([1, 2])

        with col1:
            st.markdown("#### Nova categoria")
            if st.session_state.get("catfin_clear", False):
                st.session_state["catfin_nome"] = ""
                st.session_state["catfin_ativo"] = True
                st.session_state["catfin_clear"] = False

            if "catfin_nome" not in st.session_state: st.session_state["catfin_nome"] = ""
            if "catfin_ativo" not in st.session_state: st.session_state["catfin_ativo"] = True

            cat_nome = st.text_input("Nome*", key="catfin_nome")
            cat_ativo = st.checkbox("Ativo", key="catfin_ativo")

            if st.button("Cadastrar", type="primary", key="catfin_btn"):
                if not cat_nome.strip():
                    st.session_state["catfin_flash"] = {"type": "error", "msg": "nome é obrigatório."}
                    st.rerun()

                nome_norm = _norm_upper(cat_nome)
                try:
                    inserted = False
                    with fresh_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                INSERT INTO categoria_financeira (nome, ativo)
                                VALUES (%s, %s)
                                ON CONFLICT (nome) DO NOTHING
                                RETURNING id
                                """,
                                (nome_norm, bool(cat_ativo)),
                            )
                            row = cur.fetchone()
                        conn.commit()

                    if row:
                        st.session_state["catfin_flash"] = {"type": "success", "msg": "Categoria cadastrada!"}
                    else:
                        st.session_state["catfin_flash"] = {"type": "warning", "msg": "Categoria já existe."}

                    st.cache_data.clear()
                    st.session_state["catfin_clear"] = True
                    st.rerun()

                except Exception:
                    st.session_state["catfin_flash"] = {"type": "error", "msg": "Erro ao salvar categoria."}
                    st.rerun()

        with col2:
            st.markdown("#### Editar Categorias")
            df_cat_all = fetch_df_cached("SELECT id, nome, ativo FROM categoria_financeira ORDER BY nome")
            if df_cat_all.empty:
                st.info("Sem categorias.")
            else:
                df_view = df_cat_all.copy()
                df_view["_delete"] = False
                edited_cat = st.data_editor(
                    df_view,
                    width="stretch",
                    hide_index=True,
                    num_rows="fixed",
                    column_config={
                        "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
                        "nome": st.column_config.TextColumn("Nome"),
                        "ativo": st.column_config.CheckboxColumn("Ativo"),
                        "_delete": st.column_config.CheckboxColumn("Excluir?"),
                    },
                    key="catfin_editor",
                )
                if st.button("Salvar categorias", key="catfin_save"):
                    # deletes
                    ids_delete = edited_cat.loc[edited_cat["_delete"] == True, "id"].tolist()
                    for _id in ids_delete:
                        _safe_delete_categoria(int(_id))
                    # updates
                    upd = edited_cat.loc[edited_cat["_delete"] == False].drop(columns=["_delete"])
                    for _, r in upd.iterrows():
                        _run_sql(
                            "UPDATE categoria_financeira SET nome=%s, ativo=%s WHERE id=%s",
                            (_norm_upper(r["nome"]), bool(r["ativo"]), int(r["id"])),
                        )
                    st.success("Categorias atualizadas.")
                    st.cache_data.clear()
                    st.rerun()

    
    # =========================================================================
    # Preparação de Dados de Apoio (Categorias e Tipos)
    # =========================================================================
    
    # 1. Busca Categorias
    df_cat = fetch_df_cached("SELECT id, nome FROM categoria_financeira WHERE ativo=true ORDER BY nome")
    cat_label_by_id = {None: "(Sem categoria)"}
    for _, r in df_cat.iterrows(): 
        cat_label_by_id[int(r["id"])] = str(r["nome"])
    cat_id_by_label = {v: k for k, v in cat_label_by_id.items()}
    cat_labels = list(cat_id_by_label.keys())

    # 2. Busca Tipos de Movimento
    df_tipo = fetch_df_cached("SELECT id, nome FROM movimento_tipo ORDER BY nome")
    tipo_label_by_id = {None: "(Sem tipo)"}
    for _, r in df_tipo.iterrows(): 
        tipo_label_by_id[int(r["id"])] = str(r["nome"])
        
    # 3. Funções auxiliares de formatação para a tabela
    def _cat_label(x): return cat_label_by_id.get(_safe_int(x), "(Sem categoria)")
    def _tipo_label(x): return tipo_label_by_id.get(_safe_int(x), "(Sem tipo)")

    # =========================================================================
    # 2) Gerenciar Múltiplos Processos (RATEIO DINÂMICO)
    # =========================================================================
    with st.expander("🔗 Gerenciar Rateio e Processos (N:N)", expanded=False):
        st.info("Digite o ID do movimento para buscar os dados. Adicione linhas para dividir o valor.")
        
        c_busca, c_info = st.columns([1, 3])
        with c_busca:
            vinc_mid = st.number_input("Buscar ID Movimento", min_value=0, step=1, key="rateio_mid")
            btn_buscar = st.button("Buscar Lançamento", width="stretch")
            
        if btn_buscar and vinc_mid > 0:
            st.session_state["active_rateio_mid"] = vinc_mid
            
        active_mid = st.session_state.get("active_rateio_mid", 0)
        
        if active_mid > 0:
            # 1. Busca os dados originais e VALIDA A CONTA BANCÁRIA
            df_mov_orig = fetch_df_cached("SELECT valor, descricao, conta_bancaria_id FROM movimento_bancario WHERE id = %s", (active_mid,))
            
            if df_mov_orig.empty:
                st.warning("Movimento não encontrado no banco de dados.")
            elif df_mov_orig.iloc[0]["conta_bancaria_id"] != conta_bancaria_id:
                # Regra: Lançamento não pertence à conta filtrada
                st.error(f"Erro: Este lançamento pertence a outra conta bancária, e não à conta atualmente selecionada.")
            else:
                valor_total = float(df_mov_orig.iloc[0]["valor"])
                st.write(f"**Lançamento Original:** {df_mov_orig.iloc[0]['descricao']} | **Valor Total:** R$ {valor_total:.2f}")
                
                # 2. Busca os vínculos já existentes
                df_vinculos = fetch_df_cached("""
                    SELECT mp.id as vinculo_id, p.referencia as processo, mp.valor_atribuido as valor, 
                           mp.observacao, c.nome as categoria
                    FROM movimento_processo mp
                    LEFT JOIN processo p ON p.id = mp.processo_id
                    LEFT JOIN categoria_financeira c ON c.id = mp.categoria_id
                    WHERE mp.movimento_bancario_id = %s
                """, (active_mid,))
                
                # Prepara os dados para a tabela
                if df_vinculos.empty:
                    df_edit = pd.DataFrame([{"processo": "(Sem processo)", "valor": valor_total, "observacao": "", "categoria": None}])
                else:
                    df_edit = df_vinculos.copy()
                    # Transforma os NULLs do banco em "(Sem processo)" para o Streamlit entender
                    df_edit["processo"] = df_edit["processo"].fillna("(Sem processo)")

                st.caption("Edite os valores ou adicione novas linhas clicando no `+` (canto inferior da tabela).")
                
                # Lista de processos com a opção nula
                opcoes_processos = ["(Sem processo)"] + df_processos["referencia"].tolist()

                # 3. Data Editor Dinâmico
                edited_rateio = st.data_editor(
                    df_edit,
                    width="stretch",
                    num_rows="dynamic",
                    hide_index=True,
                    column_config={
                        "vinculo_id": None, 
                        "processo": st.column_config.SelectboxColumn("Processo", options=opcoes_processos, required=True),
                        "valor": st.column_config.NumberColumn("Valor Rateado", format="R$ %.2f", required=True),
                        "observacao": st.column_config.TextColumn("Observação"),
                        "categoria": st.column_config.SelectboxColumn("Categoria", options=cat_labels),
                    },
                    key=f"editor_rateio_{active_mid}"
                )
                
                # 4. Cálculo do Saldo Restante
                soma_rateio = float(edited_rateio["valor"].sum()) if not edited_rateio.empty else 0.0
                saldo = valor_total - soma_rateio
                
                cor_saldo = "normal" if round(saldo, 2) == 0 else "inverse"
                st.metric("Saldo a Ratear (Deve ser Zero)", f"R$ {saldo:.2f}", delta_color=cor_saldo)
                
                # 5. Salvar Rateio com Validações e Proteção de Transação
                if st.button("💾 Salvar Rateio", type="primary", key="btn_save_rateio"):
                    
                    proc_reais = edited_rateio[edited_rateio["processo"] != "(Sem processo)"]["processo"]
                    
                    if round(saldo, 2) != 0:
                        st.error(f"O saldo a ratear precisa ser exatamente R$ 0,00. Restante: R$ {saldo:.2f}")
                        
                    elif proc_reais.duplicated().any():
                        duplicados = proc_reais[proc_reais.duplicated()].unique()
                        st.error(f"Erro: O processo '{duplicados[0]}' está duplicado. Some os valores ou use '(Sem processo)'.")
                        
                    else:
                        try:
                            # Transação atômica e blindada
                            with fresh_conn() as conn:
                                try:
                                    with conn.cursor() as cur:
                                        # 1. Limpa os vínculos antigos
                                        cur.execute("DELETE FROM movimento_processo WHERE movimento_bancario_id = %s", (active_mid,))
                                        
                                        # 2. Insere as novas linhas de rateio
                                        for _, row in edited_rateio.iterrows():
                                            p_ref = row["processo"]
                                            
                                            if p_ref == "(Sem processo)":
                                                p_id = None
                                            else:
                                                p_row = df_processos[df_processos["referencia"] == p_ref]
                                                p_id = int(p_row["id"].iloc[0])
                                            
                                            c_nome = row["categoria"]
                                            c_id = cat_id_by_label.get(c_nome) if pd.notna(c_nome) else None
                                            
                                            obs_text = str(row.get("observacao", "")).strip() or None

                                            cur.execute("""
                                                INSERT INTO movimento_processo 
                                                (movimento_bancario_id, processo_id, valor_atribuido, observacao, categoria_id)
                                                VALUES (%s, %s, %s, %s, %s)
                                            """, (active_mid, p_id, float(row["valor"]), obs_text, c_id))
                                            
                                    # 3. Confirma a transação (Salva no banco!)
                                    conn.commit() 
                                    
                                except Exception as db_err:
                                    # Se o banco rejeitar algo, desfaz a transação para não sujar a conexão
                                    conn.rollback() 
                                    raise db_err
                                        
                            st.success("Rateio salvo com sucesso!")
                            st.cache_data.clear()
                            st.session_state["conc_force_reload"] = True # Manda a tabela principal atualizar
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"Erro interno ao salvar: {e}")
                        

    st.divider()

    # =========================
    # Carregamento dos Movimentos
    # =========================
    filters_key: Tuple[Any, ...] = (
        int(empresa_id),
        int(conta_bancaria_id),
        str(dt_ini),
        str(dt_fim),
        bool(mostrar_todos),
        int(limite),
    )
    if st.session_state.get("conc_filters_key") != filters_key:
        st.session_state["conc_filters_key"] = filters_key
        st.session_state["conc_force_reload"] = True

    base_sql = """
        SELECT
            mb.id AS movimento_id,
            mb.dt_movimento,
            mb.descricao,
            mb.valor AS valor_total,              -- Valor original do lançamento
            mp.valor_atribuido AS valor_rateio,   -- Valor específico deste rateio
            mb.tipo_id,
            mb.is_cliente,
            mb.tipo_relatorio,
            mt.nome AS tipo_nome,
            COALESCE(mp.categoria_id, mb.categoria_id) AS categoria_id,
            COALESCE(cf_mp.nome, cf_mb.nome) AS categoria_nome,
            p.referencia AS processo_ref,
            COALESCE(mp.observacao, co.observacao) AS observacao,
            c.nome AS cliente_nome,
            co.id AS conciliacao_id,
            co.status_id AS conciliacao_status_id,
            mp.id AS mp_id
        FROM movimento_bancario mb
        LEFT JOIN movimento_processo mp ON mp.movimento_bancario_id = mb.id
        LEFT JOIN processo p ON p.id = mp.processo_id
        LEFT JOIN categoria_financeira cf_mb ON cf_mb.id = mb.categoria_id
        LEFT JOIN categoria_financeira cf_mp ON cf_mp.id = mp.categoria_id
        LEFT JOIN movimento_tipo mt ON mt.id = mb.tipo_id
        LEFT JOIN conciliacao co ON co.movimento_bancario_id = mb.id
        LEFT JOIN cliente c ON c.id = co.cliente_id
        WHERE mb.conta_bancaria_id = %s
        AND mb.dt_movimento BETWEEN %s AND %s
    """
    params = [conta_bancaria_id, dt_ini, dt_fim]

    if not mostrar_todos:
        base_sql += " AND co.id IS NULL "

    # Filtros de cliente e processo removidos da UI


    base_sql += " ORDER BY mb.dt_movimento DESC, mb.id DESC LIMIT %s"
    params.append(int(limite))

    if st.session_state.get("conc_force_reload", True):
        st.session_state["conc_df_mov"] = fetch_df_cached(base_sql, tuple(params))
        st.session_state["conc_force_reload"] = False

    df_mov = st.session_state.get("conc_df_mov", pd.DataFrame())

    st.markdown("---")
    
    # Criamos duas colunas: uma para os textos/métricas e outra para o botão
    col_header, col_btn_refresh = st.columns([3, 1])
    
    with col_header:
        st.markdown("### Movimentos Bancários")
        st.caption("Edite Categoria e Observação na tabela. Para Processos, use a seção acima.")
        st.metric("Total Listado", 0 if df_mov.empty else len(df_mov))
        
    with col_btn_refresh:
        st.write("") # Espaçamento para alinhar com o título
        if st.button("🔄 Atualizar Tabela", width="stretch"):
            st.session_state["conc_force_reload"] = True
            st.cache_data.clear()
            st.rerun()

    if df_mov.empty:
        st.info("Nenhum movimento encontrado.")
        return

    proc_id_by_label = {"-": None}
    for _, r in df_processos.iterrows():
        proc_id_by_label[str(r["referencia"])] = int(r["id"])
        
    cliente_id_by_label = {"-": None}
    df_clientes = fetch_df_cached("SELECT id, nome FROM cliente ORDER BY nome")
    for _, r in df_clientes.iterrows():
        cliente_id_by_label[str(r["nome"])] = int(r["id"])

    maps = ConciliacaoMaps(
        cat_id_by_label=cat_id_by_label,
        proc_id_by_label=proc_id_by_label,
        cliente_id_by_label=cliente_id_by_label
    )

    is_conciliado_series = df_mov["conciliacao_id"].notna()

    def _make_uid(r):
        o_mp = r.get("mp_id")
        mp_v = 0 if pd.isna(o_mp) else int(o_mp)
        return f"{int(r['movimento_id'])}_{mp_v}"

    if not df_mov.empty and "uid" not in df_mov.columns:
        df_mov["uid"] = df_mov.apply(_make_uid, axis=1)

    def _map_destino(val):
        v = str(val).upper()
        if v == "CLIENTE": return "Cliente"
        if v == "DIVERSOS": return "Diversos"
        return "Sócio"
        
    if not df_mov.empty and "destino_str" not in df_mov.columns:
        df_mov["destino_str"] = df_mov["tipo_relatorio"].apply(_map_destino)
        
    df_tbl = pd.DataFrame({
        "UID": df_mov["uid"],
        "ID": df_mov["movimento_id"].astype(int),
        "Data": df_mov["dt_movimento"],
        "Descrição": df_mov["descricao"].astype(str),
        "Valor": df_mov["valor_total"],
        "Observação": df_mov["observacao"].fillna("").astype(str),
        # Se a linha não tem rateio, repetimos o valor total para não ficar vazio
        "Valor Rateio": df_mov["valor_rateio"].fillna(df_mov["valor_total"]), 
        "Processo": df_mov["processo_ref"].fillna(""),
        "Categoria": df_mov["categoria_id"].apply(_cat_label),
        "Cliente": df_mov["cliente_nome"].fillna("-"),
        "Conciliado": is_conciliado_series.astype(bool),
        "Relatorio Destino": df_mov["destino_str"] if not df_mov.empty else [],
    })

    edited = st.data_editor(
        df_tbl,
        width="stretch",
        hide_index=True,
        column_config={
            "UID": None,
            "ID": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "Data": st.column_config.DateColumn("Data", disabled=True),
            "Descrição": st.column_config.TextColumn("Descrição", disabled=True),
            "Valor": st.column_config.NumberColumn("Valor Total", disabled=True, format="R$ %.2f"),
            "Observação": st.column_config.TextColumn("Observação"),
            "Valor Rateio": st.column_config.NumberColumn("Valor Rateio", disabled=True, format="R$ %.2f"),
            "Processo": st.column_config.SelectboxColumn("Processo", options=["-"] + processos_ref),
            "Categoria": st.column_config.SelectboxColumn("Categoria", options=cat_labels),
            "Cliente": st.column_config.SelectboxColumn("Cliente", options=["-"] + clientes),
            "Conciliado": st.column_config.CheckboxColumn("Conciliado"),
            "Relatorio Destino": st.column_config.SelectboxColumn("Relatorio Destino", options=["Sócio", "Cliente", "Diversos"]),
        },
        key="conc_editor",
    )

    if st.button("💾 Salvar Alterações da Tabela", type="primary", width="stretch"):
        changes = calcular_changes(df_mov=df_mov, edited=edited, maps=maps)
        if not changes:
            st.info("Nenhuma alteração detectada na tabela.")
        else:
            aplicar_changes_no_banco(
                changes,
                usuario_id=usuario_id,
                status_confirmada_id=int(st_confirmada),
            )
            st.success(f"Sucesso! {len(changes)} movimento(s) atualizado(s).")
            st.cache_data.clear()
            st.session_state["conc_force_reload"] = True
            st.rerun()