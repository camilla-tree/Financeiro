import streamlit as st
import pandas as pd
from db import get_historico_importacoes

def render_gerenciamento_extratos():
    st.title("📂 Gerenciamento de Extratos")

    st.markdown("Acompanhe o histórico dos extratos processados pelo sistema.")
    st.markdown("---")

    try:
        df = get_historico_importacoes()
    except Exception as e:
        st.error(f"Erro ao buscar histórico: {str(e)}")
        return

    if df.empty:
        st.info("Nenhum extrato importado até o momento.")
        return
        
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "Data Importação": st.column_config.DatetimeColumn("Data Importação", format="DD/MM/YYYY HH:mm"),
            "Data Inicial": st.column_config.DateColumn("Data Inicial", format="DD/MM/YYYY"),
            "Data Final": st.column_config.DateColumn("Data Final", format="DD/MM/YYYY"),
        }
    )

    st.markdown("---")
    st.subheader("Excluir Extrato Importado")
    st.info("Atenção: A exclusão removerá os lançamentos da conciliação permanentemente e o extrato sumirá desta lista.")
    
    with st.form("delete_extrato_form"):
        col1, col2 = st.columns([1, 2])
        with col1:
            extrato_id_delete = st.number_input("Digite o 'ID Extrato' desejado", min_value=1, step=1)
        with col2:
            st.write("")
            st.write("")
            submit_del = st.form_submit_button("Excluir Extrato permanentemente", type="primary")

        if submit_del:
            from db import check_extrato_conciliado, delete_movimentos_extrato
            
            # Verifica se o extrato informado realmente está na lista ativa para o usuário não deletar outra coisa sem querer
            if extrato_id_delete not in df["ID Extrato"].values:
                st.error("O ID Extrato informado não é válido ou já foi excluído.")
            elif check_extrato_conciliado(extrato_id_delete):
                st.error("Você não pode excluir esse extrato, pois esses dados já foram conciliados, caso seja necessário excluir contactar o administrador do sistema")
            else:
                delete_movimentos_extrato(extrato_id_delete)
                st.success(f"✔ Transações do extrato {extrato_id_delete} excluídas com sucesso!")
                st.rerun()
