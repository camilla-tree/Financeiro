from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Any, Tuple

import streamlit as st
import pandas as pd

from db import fetch_df_cached, fresh_conn


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
    with a1:
        if st.button("Mês atual", use_container_width=True):
            st.session_state["conc_dt_ini"] = primeiro_dia_mes
            st.session_state["conc_dt_fim"] = today
            st.session_state["conc_force_reload"] = True
            st.rerun()
    with a2:
        if st.button("Últimos 7d", use_container_width=True):
            st.session_state["conc_dt_ini"] = today - timedelta(days=7)
            st.session_state["conc_dt_fim"] = today
            st.session_state["conc_force_reload"] = True
            st.rerun()
    with a3:
        if st.button("Últimos 30d", use_container_width=True):
            st.session_state["conc_dt_ini"] = today - timedelta(days=30)
            st.session_state["conc_dt_fim"] = today
            st.session_state["conc_force_reload"] = True
            st.rerun()
    with a4:
        if st.button("Últimos 90d", use_container_width=True):
            st.session_state["conc_dt_ini"] = today - timedelta(days=90)
            st.session_state["conc_dt_fim"] = today
            st.session_state["conc_force_reload"] = True
            st.rerun()

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

      mb.categoria_id,
      cf.nome AS categoria_nome,

      mb.tipo_id,
      mt.nome AS tipo_nome,

      co.id AS conciliacao_id,
      co.status_id AS conciliacao_status_id,
      co.processo_id,
      co.observacao,

      p.referencia AS processo_ref,
      c.nome AS cliente_nome

    FROM movimento_bancario mb
    LEFT JOIN categoria_financeira cf ON cf.id = mb.categoria_id
    LEFT JOIN movimento_tipo mt ON mt.id = mb.tipo_id

    LEFT JOIN conciliacao co ON co.movimento_bancario_id = mb.id
    LEFT JOIN processo p ON p.id = co.processo_id
    LEFT JOIN cliente c ON c.id = p.cliente_id

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

    df_tbl = pd.DataFrame(
        {
            "ID": df_mov["movimento_id"].astype(int),
            "Data da Movimentação": df_mov["dt_movimento"],
            "Descrição": df_mov["descricao"].astype(str),
            "Valor": df_mov["valor"],

            "Categoria": df_mov["categoria_id"].apply(_cat_label),
            "Tipo": df_mov["tipo_id"].apply(_tipo_label),
            "Processo": df_mov["processo_id"].apply(_proc_label),

            "Cliente": df_mov["cliente_nome"].fillna("-"),

            # flags
            "Conciliado": (is_conciliado_series).astype(bool),
        }
    )

    edited = st.data_editor(
        df_tbl,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ID": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "Data da Movimentação": st.column_config.DateColumn("Data da Movimentação", disabled=True),
            "Descrição": st.column_config.TextColumn("Descrição", disabled=True),
            "Valor": st.column_config.NumberColumn("Valor", disabled=True, format="%.2f"),

            "Categoria": st.column_config.SelectboxColumn("Categoria", options=cat_labels, required=False),
            "Tipo": st.column_config.SelectboxColumn("Tipo", options=tipo_labels, required=False),
            "Processo": st.column_config.SelectboxColumn("Processo", options=proc_labels, required=False),

            "Cliente": st.column_config.TextColumn("Cliente", disabled=True),

            "Conciliado": st.column_config.CheckboxColumn("Conciliado"),
        },
        key="conc_editor",
    )

    salvar_tbl = st.button("Salvar alterações", type="primary")

    if not salvar_tbl:
        return

    # =========================
    # Salvar (aplica regras + grava)
    # =========================
    # Mapa do estado atual no DB (para travas de regra)
    current_is_conc = {int(r["movimento_id"]): bool(pd.notna(r["conciliacao_id"])) for _, r in df_mov.iterrows()}

    changes = []
    for i in range(len(edited)):
        mid = int(edited.loc[i, "ID"])

        new_cat_label = edited.loc[i, "Categoria"]
        new_tipo_label = edited.loc[i, "Tipo"]
        new_proc_label = edited.loc[i, "Processo"]

        new_cat_id = cat_id_by_label.get(new_cat_label)
        new_tipo_id = tipo_id_by_label.get(new_tipo_label)
        new_proc_id = proc_id_by_label.get(new_proc_label)

        flag_c = bool(edited.loc[i, "Conciliado"])
        want_conc = flag_c

        already_conc = current_is_conc.get(mid, False)

        # fase 1: se já conciliado no banco, não pode "voltar" pra não conciliado
        if already_conc and (not want_conc):
            want_conc = True


        # detecta mudanças contra o df_mov
        old_row = df_mov[df_mov["movimento_id"] == mid].iloc[0]
        old_cat = _safe_int(old_row.get("categoria_id"))
        old_tipo = _safe_int(old_row.get("tipo_id"))
        old_proc = _safe_int(old_row.get("processo_id"))
        old_conc = already_conc


        if (
            new_cat_id != old_cat
            or new_tipo_id != old_tipo
            or new_proc_id != old_proc
            or (want_conc != old_conc)  # mudança de status
        ):
            changes.append((mid, new_cat_id, new_tipo_id, new_proc_id, want_conc))

    if not changes:
        st.info("Nenhuma alteração detectada.")
        return

    with fresh_conn() as conn:
        with conn:
            with conn.cursor() as cur:
                for mid, new_cat_id, new_tipo_id, new_proc_id, want_conc in changes:
                    cur.execute(
                        """
                        UPDATE movimento_bancario
                        SET categoria_id = %s,
                            tipo_id = %s
                        WHERE id = %s
                        """,
                        (new_cat_id, new_tipo_id, int(mid)),
                    )

                    if want_conc:
                        cur.execute(
                            """
                            INSERT INTO conciliacao (
                                movimento_bancario_id, processo_id, status_id,
                                regra_aplicada, probabilidade,
                                usuario_confirmacao_id, dt_confirmacao, observacao
                            )
                            VALUES (%s,%s,%s,'MANUAL',1.0,%s,NOW(),NULL)
                            ON CONFLICT (movimento_bancario_id)
                            DO UPDATE SET
                                processo_id = EXCLUDED.processo_id,
                                status_id = EXCLUDED.status_id,
                                regra_aplicada = 'MANUAL',
                                probabilidade = 1.0,
                                usuario_confirmacao_id = EXCLUDED.usuario_confirmacao_id,
                                dt_confirmacao = NOW()
                            """,
                            (int(mid), new_proc_id, int(st_confirmada), usuario_id),
                        )
                    else:
                        cur.execute(
                            "DELETE FROM conciliacao WHERE movimento_bancario_id = %s",
                            (int(mid),),
                        )


                st.success(f"Salvo! {len(changes)} movimento(s) atualizado(s).")
                st.cache_data.clear()
                st.session_state["conc_force_reload"] = True
                st.rerun()