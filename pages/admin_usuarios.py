from datetime import date
import streamlit as st

from db import fetch_df_cached, run_sql, run_sql_returning_id
from auth import generate_access_key
from audit import log_action


# ⚠️ IMPORTANTE: esses nomes precisam bater 100% com o que o app usa no menu (app.py)
TELAS = [
    "Admin Usuários",
    "Cadastros",
    "Importar Extrato PDF",
    "Conciliação",
    "Relatórios de Cliente",
    "Fechamento",
]


def _has_tela(tela: str) -> bool:
    """Admin master vê tudo; caso contrário, valida em usuario_tela."""
    if st.session_state.get("is_admin"):
        return True

    usuario_id = st.session_state.get("usuario_id")
    if not usuario_id:
        return False

    dfp = fetch_df_cached(
        "SELECT 1 FROM usuario_tela WHERE usuario_id=%s AND tela=%s LIMIT 1",
        (usuario_id, tela),
    )
    return dfp is not None and not dfp.empty


def render_admin_usuarios():
    st.subheader("Admin • Usuários")

    # ✅ agora é por permissão
    if not _has_tela("Admin Usuários"):
        st.warning("Acesso restrito.")
        return

    colA, colB = st.columns([1, 2])

    # =========================
    # COLUNA ESQUERDA
    # =========================
    with colA:
        st.markdown("### Novo usuário")
        nome = st.text_input("nome*", key="u_nome")
        email = st.text_input("email*", key="u_email")
        ativo = st.checkbox("ativo", value=True, key="u_ativo")
        dt_inicio = st.date_input("dt_inicio", value=date.today(), key="u_dt")

        if st.button("Cadastrar usuário", type="primary", key="u_btn"):
            if not nome.strip() or not email.strip():
                st.error("nome e email são obrigatórios.")
            else:
                access_key = generate_access_key(12)
                new_id = run_sql_returning_id(
                    """
                    INSERT INTO usuario (nome, email, ativo, dt_inicio, access_key)
                    VALUES (%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (nome.strip(), email.strip().lower(), bool(ativo), dt_inicio, access_key),
                )
                log_action("INSERT", "usuario", new_id, {"nome": nome, "email": email, "ativo": bool(ativo)})
                st.success(f"Usuário cadastrado! id={new_id}")
                st.info("Chave gerada (copie e guarde):")
                st.code(access_key)
                st.cache_data.clear()

        st.divider()
        st.markdown("### Regenerar chave")
        df_users = fetch_df_cached("SELECT id, nome, email FROM usuario ORDER BY nome")
        if df_users.empty:
            st.info("Sem usuários.")
        else:
            options = [f'{int(r.id)} • {r.nome} • {r.email}' for r in df_users.itertuples(index=False)]
            picked = st.selectbox("Selecione o usuário", options, key="u_regen_pick")
            picked_id = int(picked.split("•")[0].strip())

            if st.button("Gerar nova chave", key="u_regen_btn"):
                new_key = generate_access_key(12)
                run_sql("UPDATE usuario SET access_key=%s WHERE id=%s", (new_key, picked_id))
                log_action("UPDATE", "usuario", picked_id, {"access_key_last4": new_key[-4:]})
                st.success("Chave regenerada. Copie e guarde:")
                st.code(new_key)
                st.cache_data.clear()

    # =========================
    # COLUNA DIREITA
    # =========================
    with colB:
        st.markdown("### Usuários (edite inline e clique em salvar)")

        df = fetch_df_cached("SELECT id, nome, email, ativo, dt_inicio, access_key FROM usuario ORDER BY nome")
        if df.empty:
            st.info("Sem usuários.")
            return

        df = df.copy()
        df["_delete"] = False

        edited = st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key="u_editor",
            column_config={
                "id": st.column_config.NumberColumn("id", disabled=True),
                "access_key": st.column_config.TextColumn("access_key", disabled=True),
                "_delete": st.column_config.CheckboxColumn("Excluir?"),
            },
        )

        st.divider()
        st.markdown("### Permissões de telas")

        df_users2 = fetch_df_cached("SELECT id, nome, email FROM usuario ORDER BY nome")
        if df_users2.empty:
            st.info("Sem usuários.")
            return

        opt = [f"{int(r.id)} • {r.nome} • {r.email}" for r in df_users2.itertuples(index=False)]
        picked_perm = st.selectbox("Usuário para configurar telas", opt, key="perm_user_pick")
        picked_id2 = int(picked_perm.split("•")[0].strip())

        df_perm = fetch_df_cached("SELECT tela FROM usuario_tela WHERE usuario_id=%s", (picked_id2,))
        current = df_perm["tela"].tolist() if df_perm is not None and not df_perm.empty else []

        # ✅ evita quebrar quando o banco tem telas antigas que não existem mais
        current = [t for t in current if t in TELAS]

        # ✅ key estável por usuário escolhido (evita estado “grudar” em outro usuário)
        selected = st.multiselect(
            "Telas liberadas",
            TELAS,
            default=current,
            key=f"perm_mult_{picked_id2}",
        )

        if st.button("Salvar permissões", type="primary", key="perm_save"):
            run_sql("DELETE FROM usuario_tela WHERE usuario_id=%s", (picked_id2,))
            for t in selected:
                run_sql("INSERT INTO usuario_tela (usuario_id, tela) VALUES (%s,%s)", (picked_id2, t))

            # ✅ se estou editando minhas permissões, recarrega na hora (pra refletir no menu)
            if picked_id2 == st.session_state.get("usuario_id"):
                st.cache_data.clear()
                st.rerun()

            st.success("Permissões salvas.")
            st.cache_data.clear()

        st.divider()
        st.markdown("### Histórico de login (últimos 200)")

        # obs: depende de existir a tabela usuario_login
        df_log = fetch_df_cached(
            """
            SELECT dt_evento, ip, user_agent
            FROM usuario_login
            WHERE usuario_id = %s
            ORDER BY dt_evento DESC
            LIMIT 200
            """,
            (picked_id2,),
        )

        if df_log is None or df_log.empty:
            st.caption("Sem logins registrados ainda.")
        else:
            st.dataframe(df_log, use_container_width=True)

        st.divider()

        if st.button("Salvar alterações", key="u_save"):
            ids_delete = edited.loc[edited["_delete"] == True, "id"].tolist()
            for _id in ids_delete:
                run_sql("DELETE FROM usuario WHERE id=%s", (int(_id),))
                log_action("DELETE", "usuario", int(_id))

            upd = edited.loc[edited["_delete"] == False].drop(columns=["_delete"])
            for _, r in upd.iterrows():
                run_sql(
                    """
                    UPDATE usuario
                    SET nome=%s, email=%s, ativo=%s, dt_inicio=%s
                    WHERE id=%s
                    """,
                    (
                        str(r["nome"]).strip(),
                        str(r["email"]).strip().lower(),
                        bool(r["ativo"]),
                        r["dt_inicio"],
                        int(r["id"]),
                    ),
                )
                log_action(
                    "UPDATE",
                    "usuario",
                    int(r["id"]),
                    {"nome": r["nome"], "email": r["email"], "ativo": bool(r["ativo"])},
                )

            st.success("Alterações aplicadas.")
            st.cache_data.clear()
