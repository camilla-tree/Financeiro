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
    # Filtros (sem botão Buscar)
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
        # ✅ NÃO chama st.rerun()

    with a1:
        if st.button("Mês atual", use_container_width=True):
            _set_periodo(primeiro_dia_mes, today)

    with a2:
        if st.button("Últimos 7d", use_container_width=True):
            _set_periodo(today - timedelta(days=7), today)

    with a3:
        if st.button("Últimos 30d", use_container_width=True):
            _set_periodo(today - timedelta(days=30), today)

    with a4:
        if st.button("Últimos 90d", use_container_width=True):
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

    # depois de montar conta_options (e antes do selectbox)
    if conta_options:
        if "conc_conta" in st.session_state and st.session_state["conc_conta"] not in conta_options:
            st.session_state["conc_conta"] = conta_options[0]

    with col_f2:
        conta_label = st.selectbox("Conta bancária", conta_options, key="conc_conta")
        conta_bancaria_id = int(conta_map[conta_label]["conta_bancaria_id"])


    with col_f3:
        dt_ini = st.date_input("Data início", key="conc_dt_ini")  # editável
    with col_f4:
        dt_fim = st.date_input("Data fim", key="conc_dt_fim")

    if dt_ini > dt_fim:
        st.warning("Data início maior que data fim — ajustei automaticamente.")
        st.session_state["conc_dt_ini"] = dt_fim
        dt_ini = dt_fim

    colA, colB = st.columns([2, 1])
    with colB:
        mostrar_todos = st.checkbox(
            "Mostrar também já conciliados",
            value=False,
            key="conc_show_all",
        )
        limite = st.number_input(
            "Limite de linhas",
            min_value=50,
            max_value=2000,
            value=300,
            step=50,
            key="conc_limit",
        )

    st.divider()

    # =========================
    # Usuário (mantém como estava)
    # =========================
    df_user = fetch_df_cached("SELECT id, nome FROM usuario WHERE ativo=true ORDER BY nome")
    usuario_id = None
    if not df_user.empty:
        opt_u = ["(Sem usuário)"] + df_user["nome"].tolist()
        u = st.selectbox("Usuário (para auditoria/confirm)", opt_u, index=0, key="conc_user")
        if u != "(Sem usuário)":
            usuario_id = int(df_user[df_user["nome"] == u]["id"].iloc[0])

    # Status CONFIRMADA (único usado na fase 1)
    st_confirmada = get_status_id("CONFIRMADA")

    # =========================
    # Apoio (Processo / Categoria / Tipo)
    # =========================
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
 
    # --- filtros adicionais ---
    clientes = sorted(df_processos["cliente"].dropna().unique().tolist()) if not df_processos.empty else []
    processos_ref = sorted(df_processos["referencia"].dropna().unique().tolist()) if not df_processos.empty else []

    cliente_opt = ["(Todos)"] + clientes
    proc_opt = ["(Todos)"] + processos_ref

    colX, colY = st.columns([2, 2])
    with colX:
        cliente_pick = st.selectbox("Cliente", cliente_opt, key="conc_cliente")
    with colY:
        processo_pick = st.selectbox("Processo", proc_opt, key="conc_processo")

    st.markdown("### Categorias financeiras")
    # --- Flash message (feedback fora do expander) ---
    flash = st.session_state.pop("catfin_flash", None)
    if flash:
        t = flash.get("type")
        msg = flash.get("msg", "")
        if t == "success":
            st.success(msg)
        elif t == "warning":
            st.warning(msg)
        else:
            st.error(msg)

    with st.expander("Adicionar / editar / excluir categorias", expanded=False):
        col1, col2 = st.columns([1, 2])

        with col1:
            st.markdown("#### Nova categoria")
            # --- antes dos widgets catfin_* ---
            if st.session_state.get("catfin_clear", False):
                st.session_state["catfin_nome"] = ""
                st.session_state["catfin_ativo"] = True
                st.session_state["catfin_clear"] = False


            if "catfin_nome" not in st.session_state:
                st.session_state["catfin_nome"] = ""

            if "catfin_ativo" not in st.session_state:
                st.session_state["catfin_ativo"] = True


            cat_nome = st.text_input("nome*", key="catfin_nome")
            cat_ativo = st.checkbox("ativo", key="catfin_ativo")

            if st.button("Cadastrar categoria", type="primary", key="catfin_btn"):
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

                    inserted = bool(row)

                    if inserted:
                        st.session_state["catfin_flash"] = {"type": "success", "msg": "Categoria cadastrada!"}
                    else:
                        st.session_state["catfin_flash"] = {"type": "warning", "msg": "Categoria já existe."}

                    # Atualiza lista suspensa e limpa campos (Plano A)
                    st.cache_data.clear()
                    st.session_state["catfin_clear"] = True
                    st.rerun()

                except Exception:
                    st.session_state["catfin_flash"] = {"type": "error", "msg": "Erro ao salvar categoria."}
                    st.rerun()



        with col2:
            st.markdown("#### Categorias (edite inline e clique em salvar)")
            df_cat_all = fetch_df_cached(
                "SELECT id, nome, ativo FROM categoria_financeira ORDER BY nome"
            )

            if df_cat_all.empty:
                st.info("Sem categorias.")
            else:
                df_view = df_cat_all.copy()
                if "_delete" not in df_view.columns:
                    df_view["_delete"] = False

                edited_cat = st.data_editor(
                    df_view,
                    use_container_width=True,
                    hide_index=True,
                    num_rows="fixed",
                    column_config={
                        "id": st.column_config.NumberColumn("id", disabled=True),
                        "nome": st.column_config.TextColumn("nome"),
                        "ativo": st.column_config.CheckboxColumn("ativo"),
                        "_delete": st.column_config.CheckboxColumn("Excluir?"),
                    },
                    key="catfin_editor",
                )

                if st.button("Salvar alterações (categorias)", key="catfin_save"):
                    # deletes
                    ids_delete = edited_cat.loc[edited_cat["_delete"] == True, "id"].tolist()
                    for _id in ids_delete:
                        _id = int(_id)
                        if _safe_delete_categoria(_id):
                            pass

                    # updates
                    upd = edited_cat.loc[edited_cat["_delete"] == False].drop(columns=["_delete"])
                    for _, r in upd.iterrows():
                        _run_sql(
                            """
                            UPDATE categoria_financeira
                            SET nome=%s, ativo=%s
                            WHERE id=%s
                            """,
                            (_norm_upper(r["nome"]), bool(r["ativo"]), int(r["id"])),
                        )

                    st.success("Alterações aplicadas.")
                    st.cache_data.clear()
                    st.rerun()


    df_cat = fetch_df_cached("SELECT id, nome FROM categoria_financeira WHERE ativo=true ORDER BY nome")
    df_tipo = fetch_df_cached("SELECT id, nome FROM movimento_tipo ORDER BY nome")

    # =========================
    # Recarregar só quando filtro muda / force_reload
    # =========================
    filters_key: Tuple[Any, ...] = (
        int(empresa_id),
        int(conta_bancaria_id),
        str(dt_ini),
        str(dt_fim),
        str(cliente_pick),
        str(processo_pick),
        bool(mostrar_todos),
        int(limite),
    )
    prev_key = st.session_state.get("conc_filters_key")
    if prev_key != filters_key:
        st.session_state["conc_filters_key"] = filters_key
        st.session_state["conc_force_reload"] = True

    if "conc_force_reload" not in st.session_state:
        st.session_state["conc_force_reload"] = True

    base_sql = """
        SELECT
        mb.id AS movimento_id,
        mb.dt_movimento,
        mb.descricao,
        mb.valor,
        mb.tipo_id,
        mt.nome AS tipo_nome,
        mb.categoria_id,
        cf.nome AS categoria_nome,
        co.id AS conciliacao_id,
        co.status_id AS conciliacao_status_id,
        co.observacao,
        -- Nova lógica para múltiplos processos
        (SELECT STRING_AGG(p.referencia, ', ') 
        FROM movimento_processo mp 
        JOIN processo p ON p.id = mp.processo_id 
        WHERE mp.movimento_bancario_id = mb.id) AS processo_ref,
        -- Cliente (pegamos o primeiro vinculado ou deixamos via conciliação)
        c.nome AS cliente_nome
        FROM movimento_bancario mb
        LEFT JOIN categoria_financeira cf ON cf.id = mb.categoria_id
        LEFT JOIN movimento_tipo mt ON mt.id = mb.tipo_id
        LEFT JOIN conciliacao co ON co.movimento_bancario_id = mb.id
        LEFT JOIN cliente c ON c.id = co.cliente_id
        WHERE mb.conta_bancaria_id = %s
        AND mb.dt_movimento BETWEEN %s AND %s
        """
    params = [conta_bancaria_id, dt_ini, dt_fim]

    if not mostrar_todos:
        base_sql += " AND co.id IS NULL "

    if cliente_pick != "(Todos)":
        base_sql += " AND c.nome = %s "
        params.append(cliente_pick)

    if processo_pick != "(Todos)":
        base_sql += " AND p.referencia = %s "
        params.append(processo_pick)


    base_sql += " ORDER BY mb.dt_movimento DESC, mb.id DESC LIMIT %s"
    params.append(int(limite))

    if st.session_state.get("conc_force_reload", True):
        st.session_state["conc_df_mov"] = fetch_df_cached(base_sql, tuple(params))
        st.session_state["conc_force_reload"] = False

    df_mov = st.session_state.get("conc_df_mov", pd.DataFrame())

    st.metric("Movimentos carregados", 0 if df_mov.empty else int(len(df_mov)))
    if df_mov.empty:
        st.info("Nenhum movimento para conciliar nesses filtros.")
        return

    # =========================
    # Maps / labels
    # =========================
    cat_label_by_id = {None: "(Sem categoria)"}
    for _, r in df_cat.iterrows():
        cat_label_by_id[int(r["id"])] = str(r["nome"])
    cat_id_by_label = {v: k for k, v in cat_label_by_id.items()}
    cat_labels = list(cat_id_by_label.keys())

    tipo_label_by_id = {None: "(Sem tipo)"}
    for _, r in df_tipo.iterrows():
        tipo_label_by_id[int(r["id"])] = str(r["nome"])
    tipo_id_by_label = {v: k for k, v in tipo_label_by_id.items()}
    tipo_labels = list(tipo_id_by_label.keys())

    proc_label_by_id = {None: "(Sem processo)"}
    for _, r in df_processos.iterrows():
        pid = int(r["id"])
        ref = str(r["referencia"])
        label = ref
        # evita duplicidade de label no select (caso exista referência repetida)
        if label in proc_label_by_id.values():
            label = f"{ref} (ID {pid})"
        proc_label_by_id[pid] = label

    proc_id_by_label = {v: k for k, v in proc_label_by_id.items()}
    proc_labels = list(proc_id_by_label.keys())

    def _cat_label(x):
        xi = _safe_int(x)
        return cat_label_by_id.get(xi, "(Sem categoria)")

    def _tipo_label(x):
        xi = _safe_int(x)
        return tipo_label_by_id.get(xi, "(Sem tipo)")

    def _proc_label(x):
        xi = _safe_int(x)
        return proc_label_by_id.get(xi, "(Sem processo)")
    
    maps = ConciliacaoMaps(
        cat_id_by_label=cat_id_by_label,
        proc_id_by_label=proc_id_by_label,
    )


    # =========================
    # Tabela com edição inline + flags
    # =========================
    st.markdown("### Movimentos")
    st.caption(
    "Edite Categoria, Tipo e Processo. Marque **Conciliado** para confirmar. "
    "Se estiver desmarcado, o movimento fica como **não conciliado**."
    )


    # considera conciliado se existir conciliacao_id (fase 1)
    is_conciliado_series = df_mov["conciliacao_id"].notna()

    df_tbl = pd.DataFrame({
        "ID": df_mov["movimento_id"].astype(int),
        "Data": df_mov["dt_movimento"],
        "Descrição": df_mov["descricao"].astype(str),
        "Valor": df_mov["valor"],
        "Tipo": df_mov["tipo_id"].apply(_tipo_label),
        "Processo": df_mov["processo_ref"].fillna(""), # Agora vindo da subquery
        "Observação": df_mov["observacao"].fillna("").astype(str),
        "Categoria": df_mov["categoria_id"].apply(_cat_label),
        "Cliente": df_mov["cliente_nome"].fillna("-"),
        "Conciliado": is_conciliado_series.astype(bool),
    })

    edited = st.data_editor(
        df_tbl,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "Data da Movimentação": st.column_config.DateColumn("Data da Movimentação", disabled=True),
            "Descrição": st.column_config.TextColumn("Descrição", disabled=True),
            "Valor": st.column_config.NumberColumn("Valor", disabled=True, format="%.2f"),
            "Tipo": st.column_config.SelectboxColumn("Tipo", options=tipo_labels, required=False),
            "Observação": st.column_config.TextColumn("Observação"),

            "Categoria": st.column_config.SelectboxColumn("Categoria", options=cat_labels, required=False),

            "Processo": st.column_config.SelectboxColumn("Processo", options=proc_labels, required=False),

            "Cliente": st.column_config.TextColumn("Cliente", disabled=True),

            "Conciliado": st.column_config.CheckboxColumn("Conciliado"),
        },
        key="conc_editor",
    )

    salvar_tbl = st.button("Salvar alterações", type="primary")

    if not salvar_tbl:
        return

    changes = calcular_changes(df_mov=df_mov, edited=edited, maps=maps)

    if not changes:
        st.info("Nenhuma alteração detectada.")
        return

    aplicar_changes_no_banco(
        changes,
        usuario_id=usuario_id,
        status_confirmada_id=int(st_confirmada),
    )

    # ✅ Agora sim: UI fora da transação
    st.success(f"Salvo! {len(changes)} movimento(s) atualizado(s).")
    st.cache_data.clear()
    st.session_state["conc_force_reload"] = True
    st.rerun()

# --- Nova Seção para Vínculos N:N ---
    st.divider()
    st.subheader("🔗 Gerenciar Múltiplos Processos")
    st.caption("Use esta seção para atribuir um lançamento a um ou mais processos específicos.")

    c1, c2, c3 = st.columns([1, 2, 1])
    with c1:
        vinc_mov_id = st.number_input("ID do Movimento", min_value=0, step=1, key="vinc_mid")
    with c2:
        proc_list = ["Selecionar..."] + df_processos["referencia"].tolist()
        vinc_proc_ref = st.selectbox("Vincular ao Processo", proc_list, key="vinc_pref")
    with c3:
        vinc_valor = st.number_input("Valor do Rateio (Opcional)", min_value=0.0, key="vinc_val")

    if st.button("Confirmar Vínculo", use_container_width=True):
        if vinc_mov_id > 0 and vinc_proc_ref != "Selecionar...":
            p_id = int(df_processos[df_processos["referencia"] == vinc_proc_ref]["id"].iloc[0])
            _run_sql("""
                INSERT INTO movimento_processo (movimento_bancario_id, processo_id, valor_atribuido)
                VALUES (%s, %s, %s)
                ON CONFLICT (movimento_bancario_id, processo_id) DO NOTHING
            """, (int(vinc_mov_id), p_id, vinc_valor))
            st.success(f"Vínculo criado para o Movimento {vinc_mov_id}!")
            st.cache_data.clear()
            st.rerun()