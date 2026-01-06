from datetime import date
import re
import streamlit as st
from db import fetch_df_cached, run_sql, run_sql_returning_id
from audit import log_action


def norm_upper(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.upper()


def _editor_with_delete(df, key: str, extra_column_config=None):
    if df is None or df.empty:
        st.info("Sem registros.")
        return None

    df = df.copy()
    if "_delete" not in df.columns:
        df["_delete"] = False

    base_config = {
        "id": st.column_config.NumberColumn("id", disabled=True),
        "_delete": st.column_config.CheckboxColumn(
            "Excluir?", help="Marque para excluir e clique em Salvar alterações"
        ),
    }
    if extra_column_config:
        base_config.update(extra_column_config)

    return st.data_editor(
        df,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        key=key,
        column_config=base_config,
    )


def render_admin():
    st.subheader("Admin de Cadastros")
    tabs = st.tabs(["Empresa", "Cliente", "Processo", "Conta Bancária"])

    # =========================
    # EMPRESA
    # =========================
    with tabs[0]:
        colA, colB = st.columns([1, 2])

        with colA:
            st.markdown("### Nova empresa")
            nome = st.text_input("nome*", key="emp_nome")
            cnpj = st.text_input("cnpj (opcional)", key="emp_cnpj")
            situacao = st.text_input("situacao (opcional)", key="emp_situacao")
            diretor = st.text_input("diretor (opcional)", key="emp_diretor")

            if st.button("Cadastrar empresa", type="primary", key="emp_btn"):
                if not nome.strip():
                    st.error("nome é obrigatório.")
                else:
                    new_id = run_sql_returning_id(
                        """
                        INSERT INTO empresa (nome, cnpj, situacao, diretor)
                        VALUES (%s,%s,%s,%s)
                        RETURNING id
                        """,
                        (norm_upper(nome), cnpj.strip() or None, situacao.strip() or None, diretor.strip() or None),
                    )
                    log_action("INSERT", "empresa", new_id, {"nome": nome, "cnpj": cnpj, "situacao": situacao, "diretor": diretor})
                    st.success(f"Empresa cadastrada! id={new_id}")
                    st.cache_data.clear()

        with colB:
            st.markdown("### Empresas (edite inline e clique em salvar)")
            df = fetch_df_cached("SELECT id, nome, cnpj, situacao, diretor FROM empresa ORDER BY nome")
            edited = _editor_with_delete(df, key="emp_editor")

            if edited is not None and st.button("Salvar alterações", key="emp_save"):
                ids_delete = edited.loc[edited["_delete"] == True, "id"].tolist()
                for _id in ids_delete:
                    run_sql("DELETE FROM empresa WHERE id=%s", (int(_id),))
                    log_action("DELETE", "empresa", int(_id))

                upd = edited.loc[edited["_delete"] == False].drop(columns=["_delete"])
                for _, r in upd.iterrows():
                    run_sql(
                        """
                        UPDATE empresa
                        SET nome=%s, cnpj=%s, situacao=%s, diretor=%s
                        WHERE id=%s
                        """,
                        (norm_upper(r["nome"]), (r["cnpj"] or None), (r["situacao"] or None), (r["diretor"] or None), int(r["id"])),
                    )
                    log_action("UPDATE", "empresa", int(r["id"]), {"nome": r["nome"], "cnpj": r["cnpj"], "situacao": r["situacao"], "diretor": r["diretor"]})

                st.success("Alterações aplicadas.")
                st.cache_data.clear()

    # =========================
    # CLIENTE (SEM CNPJ)
    # =========================
    with tabs[1]:
        colA, colB = st.columns([1, 2])

        with colA:
            st.markdown("### Novo cliente")
            nome = st.text_input("nome*", key="cli_nome")
            dt_inicio = st.date_input("dt_inicio_contrato", value=date.today(), key="cli_dt")
            ativo = st.checkbox("ativo", value=True, key="cli_ativo")

            if st.button("Cadastrar cliente", type="primary", key="cli_btn"):
                if not nome.strip():
                    st.error("nome é obrigatório.")
                else:
                    new_id = run_sql_returning_id(
                        """
                        INSERT INTO cliente (nome, dt_inicio_contrato, ativo)
                        VALUES (%s,%s,%s)
                        RETURNING id
                        """,
                        (norm_upper(nome), dt_inicio, bool(ativo)),
                    )
                    log_action("INSERT", "cliente", new_id, {"nome": nome, "dt_inicio_contrato": str(dt_inicio), "ativo": bool(ativo)})
                    st.success(f"Cliente cadastrado! id={new_id}")
                    st.cache_data.clear()

        with colB:
            st.markdown("### Clientes (edite inline e clique em salvar)")
            df = fetch_df_cached("SELECT id, nome, dt_inicio_contrato, ativo FROM cliente ORDER BY nome")
            edited = _editor_with_delete(df, key="cli_editor")

            if edited is not None and st.button("Salvar alterações", key="cli_save"):
                ids_delete = edited.loc[edited["_delete"] == True, "id"].tolist()
                for _id in ids_delete:
                    run_sql("DELETE FROM cliente WHERE id=%s", (int(_id),))
                    log_action("DELETE", "cliente", int(_id))

                upd = edited.loc[edited["_delete"] == False].drop(columns=["_delete"])
                for _, r in upd.iterrows():
                    run_sql(
                        """
                        UPDATE cliente
                        SET nome=%s, dt_inicio_contrato=%s, ativo=%s
                        WHERE id=%s
                        """,
                        (norm_upper(r["nome"]), r["dt_inicio_contrato"], bool(r["ativo"]), int(r["id"])),
                    )
                    log_action("UPDATE", "cliente", int(r["id"]), {"nome": r["nome"], "dt_inicio_contrato": str(r["dt_inicio_contrato"]), "ativo": bool(r["ativo"])})

                st.success("Alterações aplicadas.")
                st.cache_data.clear()

    # =========================
    # PROCESSO
    # =========================
    with tabs[2]:
        df_emp = fetch_df_cached("SELECT id, nome FROM empresa ORDER BY nome")
        df_cli = fetch_df_cached("SELECT id, nome FROM cliente ORDER BY nome")
        df_status = fetch_df_cached("SELECT id, nome FROM processo_status ORDER BY nome")

        if df_emp.empty or df_cli.empty or df_status.empty:
            st.warning("Cadastre antes: empresa, cliente e seeds de processo_status.")
        else:
            emp_names = df_emp["nome"].tolist()
            cli_names = df_cli["nome"].tolist()
            status_names = df_status["nome"].tolist()

            emp_id_by_name = dict(zip(df_emp["nome"], df_emp["id"]))
            cli_id_by_name = dict(zip(df_cli["nome"], df_cli["id"]))
            status_id_by_name = dict(zip(df_status["nome"], df_status["id"]))

            emp_name_by_id = dict(zip(df_emp["id"], df_emp["nome"]))
            cli_name_by_id = dict(zip(df_cli["id"], df_cli["nome"]))
            status_name_by_id = dict(zip(df_status["id"], df_status["nome"]))

            colA, colB = st.columns([1, 2])

            with colA:
                st.markdown("### Novo processo")
                referencia = st.text_input("referencia*", key="p_ref")
                emp_nome = st.selectbox("empresa*", emp_names, key="p_emp")
                cli_nome = st.selectbox("cliente*", cli_names, key="p_cli")
                status_nome = st.selectbox("status*", status_names, key="p_status")

                data_registro = st.date_input("data_registro", value=date.today(), key="p_dt")
                di = st.text_input("di (opcional)", key="p_di")
                canal = st.text_input("canal (opcional)", key="p_canal")
                bl = st.text_input("bl (opcional)", key="p_bl")
                invoice = st.text_input("invoice (opcional)", key="p_invoice")
                obs = st.text_area("observacao (opcional)", key="p_obs")

                if st.button("Cadastrar processo", type="primary", key="p_btn"):
                    if not referencia.strip():
                        st.error("referencia é obrigatória.")
                    else:
                        new_id = run_sql_returning_id(
                            """
                            INSERT INTO processo (
                              referencia, empresa_id, cliente_id,
                              data_registro, di, canal, bl, invoice,
                              status_id, observacao
                            )
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            RETURNING id
                            """,
                            (
                                norm_upper(referencia),
                                int(emp_id_by_name[emp_nome]),
                                int(cli_id_by_name[cli_nome]),
                                data_registro,
                                di.strip() or None,
                                canal.strip() or None,
                                bl.strip() or None,
                                invoice.strip() or None,
                                int(status_id_by_name[status_nome]),
                                obs.strip() or None,
                            ),
                        )
                        log_action("INSERT", "processo", new_id, {"referencia": referencia, "empresa": emp_nome, "cliente": cli_nome, "status": status_nome})
                        st.success(f"Processo cadastrado! id={new_id}")
                        st.cache_data.clear()

            with colB:
                st.markdown("### Processos (edite inline e clique em salvar)")
                df = fetch_df_cached(
                    """
                    SELECT
                      p.id,
                      p.referencia,
                      p.empresa_id,
                      p.cliente_id,
                      p.status_id,
                      p.data_registro,
                      p.di,
                      p.canal,
                      p.bl,
                      p.invoice,
                      p.observacao
                    FROM processo p
                    ORDER BY p.id DESC
                    LIMIT 300
                    """
                )

                if df.empty:
                    st.info("Sem processos.")
                else:
                    df["empresa"] = df["empresa_id"].map(emp_name_by_id)
                    df["cliente"] = df["cliente_id"].map(cli_name_by_id)
                    df["status"] = df["status_id"].map(status_name_by_id)

                    view = df[
                        ["id", "referencia", "empresa", "cliente", "status",
                         "data_registro", "di", "canal", "bl", "invoice", "observacao"]
                    ].copy()

                    edited = _editor_with_delete(
                        view,
                        key="proc_editor",
                        extra_column_config={
                            "empresa": st.column_config.SelectboxColumn("empresa", options=emp_names),
                            "cliente": st.column_config.SelectboxColumn("cliente", options=cli_names),
                            "status": st.column_config.SelectboxColumn("status", options=status_names),
                        },
                    )

                    if edited is not None and st.button("Salvar alterações", key="proc_save"):
                        ids_delete = edited.loc[edited["_delete"] == True, "id"].tolist()
                        for _id in ids_delete:
                            run_sql("DELETE FROM processo WHERE id=%s", (int(_id),))
                            log_action("DELETE", "processo", int(_id))

                        upd = edited.loc[edited["_delete"] == False].drop(columns=["_delete"])
                        for _, r in upd.iterrows():
                            run_sql(
                                """
                                UPDATE processo
                                SET
                                  referencia=%s,
                                  empresa_id=%s,
                                  cliente_id=%s,
                                  status_id=%s,
                                  data_registro=%s,
                                  di=%s,
                                  canal=%s,
                                  bl=%s,
                                  invoice=%s,
                                  observacao=%s
                                WHERE id=%s
                                """,
                                (
                                    norm_upper(r["referencia"]),
                                    int(emp_id_by_name[r["empresa"]]),
                                    int(cli_id_by_name[r["cliente"]]),
                                    int(status_id_by_name[r["status"]]),
                                    r["data_registro"],
                                    (r["di"] or None),
                                    (r["canal"] or None),
                                    (r["bl"] or None),
                                    (r["invoice"] or None),
                                    (r["observacao"] or None),
                                    int(r["id"]),
                                ),
                            )
                            log_action("UPDATE", "processo", int(r["id"]), {"referencia": r["referencia"], "empresa": r["empresa"], "cliente": r["cliente"], "status": r["status"]})

                        st.success("Alterações aplicadas.")
                        st.cache_data.clear()

    # =========================
    # CONTA BANCÁRIA (numero opcional)
    # =========================
    with tabs[3]:
        df_emp = fetch_df_cached("SELECT id, nome FROM empresa ORDER BY nome")
        df_b = fetch_df_cached("SELECT id, codigo, nome FROM banco ORDER BY codigo")

        if df_emp.empty or df_b.empty:
            st.warning("Cadastre primeiro: banco (seeds) e empresa.")
        else:
            emp_names = df_emp["nome"].tolist()
            banco_codes = df_b["codigo"].tolist()

            emp_id_by_name = dict(zip(df_emp["nome"], df_emp["id"]))
            banco_id_by_code = dict(zip(df_b["codigo"], df_b["id"]))

            emp_name_by_id = dict(zip(df_emp["id"], df_emp["nome"]))
            banco_code_by_id = dict(zip(df_b["id"], df_b["codigo"]))

            colA, colB = st.columns([1, 2])

            with colA:
                st.markdown("### Nova conta")
                emp_nome = st.selectbox("empresa*", emp_names, key="cb_emp")
                banco_codigo = st.selectbox("banco*", banco_codes, key="cb_banco")
                apelido = st.text_input("apelido (opcional)", key="cb_apelido")
                agencia = st.text_input("agencia (opcional)", key="cb_agencia")
                numero = st.text_input("numero (opcional)", key="cb_numero")
                ativa = st.checkbox("ativa", value=True, key="cb_ativa")

                if st.button("Cadastrar conta", type="primary", key="cb_btn"):
                    new_id = run_sql_returning_id(
                        """
                        INSERT INTO conta_bancaria (banco_id, empresa_id, apelido, agencia, numero, ativa)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        RETURNING id
                        """,
                        (
                            int(banco_id_by_code[banco_codigo]),
                            int(emp_id_by_name[emp_nome]),
                            apelido.strip() or None,
                            agencia.strip() or None,
                            numero.strip() or None,
                            bool(ativa),
                        ),
                    )
                    log_action("INSERT", "conta_bancaria", new_id, {"empresa": emp_nome, "banco": banco_codigo, "apelido": apelido, "agencia": agencia, "numero": numero})
                    st.success(f"Conta cadastrada! id={new_id}")
                    st.cache_data.clear()

            with colB:
                st.markdown("### Contas (edite inline e clique em salvar)")
                df = fetch_df_cached(
                    """
                    SELECT
                      cb.id,
                      cb.empresa_id,
                      cb.banco_id,
                      cb.apelido,
                      cb.agencia,
                      cb.numero,
                      cb.ativa
                    FROM conta_bancaria cb
                    ORDER BY cb.id DESC
                    LIMIT 500
                    """
                )

                if df.empty:
                    st.info("Sem contas.")
                else:
                    df["empresa"] = df["empresa_id"].map(emp_name_by_id)
                    df["banco"] = df["banco_id"].map(banco_code_by_id)

                    view = df[["id", "empresa", "banco", "apelido", "agencia", "numero", "ativa"]].copy()

                    edited = _editor_with_delete(
                        view,
                        key="cb_editor",
                        extra_column_config={
                            "empresa": st.column_config.SelectboxColumn("empresa", options=emp_names),
                            "banco": st.column_config.SelectboxColumn("banco", options=banco_codes),
                        },
                    )

                    if edited is not None and st.button("Salvar alterações", key="cb_save"):
                        ids_delete = edited.loc[edited["_delete"] == True, "id"].tolist()
                        for _id in ids_delete:
                            run_sql("DELETE FROM conta_bancaria WHERE id=%s", (int(_id),))
                            log_action("DELETE", "conta_bancaria", int(_id))

                        upd = edited.loc[edited["_delete"] == False].drop(columns=["_delete"])
                        for _, r in upd.iterrows():
                            run_sql(
                                """
                                UPDATE conta_bancaria
                                SET
                                  empresa_id=%s,
                                  banco_id=%s,
                                  apelido=%s,
                                  agencia=%s,
                                  numero=%s,
                                  ativa=%s
                                WHERE id=%s
                                """,
                                (
                                    int(emp_id_by_name[r["empresa"]]),
                                    int(banco_id_by_code[r["banco"]]),
                                    (r["apelido"] or None),
                                    (r["agencia"] or None),
                                    (r["numero"] or None),
                                    bool(r["ativa"]),
                                    int(r["id"]),
                                ),
                            )
                            log_action("UPDATE", "conta_bancaria", int(r["id"]), {"empresa": r["empresa"], "banco": r["banco"], "apelido": r["apelido"], "agencia": r["agencia"], "numero": r["numero"], "ativa": bool(r["ativa"])})

                        st.success("Alterações aplicadas.")
                        st.cache_data.clear()
