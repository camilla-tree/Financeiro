import streamlit as st
import pandas as pd
from datetime import date
from db import get_lista_clientes, get_lista_empresas, get_dados_relatorio_filtrado

# Bibliotecas para PDF
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Image, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from io import BytesIO

def render_exportacao():
    # CSS Específico para deixar bonito igual ao Dashboard
    st.markdown("""
        <style>
            .stSelectbox div[data-baseweb="select"] > div {
                background-color: #E0E2E6 !important;
                border-radius: 8px !important;
                color: #31333F;
            }
            .stDateInput input {
                background-color: #E0E2E6 !important;
                border-radius: 8px !important;
            }
        </style>
    """, unsafe_allow_html=True)

    st.title("📊 Relatórios Financeiros")

    with st.container():
        st.write("### Selecione os filtros")
        
        col_dates, col_type, col_select = st.columns([2, 1, 2])
        
        with col_dates:
            c1, c2 = st.columns(2)
            dt_inicio = c1.date_input("Início", value=date.today().replace(day=1))
            dt_fim = c2.date_input("Fim", value=date.today())

        with col_type:
            tipo_filtro = st.radio("Filtrar por:", ["Cliente", "Empresa"])

        with col_select:
            if tipo_filtro == "Cliente":
                # Busca do banco correto agora (tabela cliente)
                opcoes = ["Todos"] + get_lista_clientes()
                selecionado = st.selectbox("Selecione o Cliente", opcoes)
            else:
                # Busca do banco correto agora (tabela empresa)
                opcoes = ["Todas"] + get_lista_empresas()
                selecionado = st.selectbox("Selecione a Empresa", opcoes)

    st.markdown("---")

    # Botão de busca
    if st.button("Buscar Dados", use_container_width=True):
        # Chama a nova função otimizada do DB
        df_resultado = get_dados_relatorio_filtrado(dt_inicio, dt_fim, tipo_filtro, selecionado)
        st.session_state['relatorio_cache'] = df_resultado
        st.session_state['filtro_atual'] = f"{tipo_filtro}: {selecionado}"

    # Recupera dados do cache
    df = st.session_state.get('relatorio_cache', pd.DataFrame())

    if not df.empty:
        # Define quais colunas mostrar na tabela (Removemos as colunas auxiliares de filtro)
        cols_view = ["Banco", "Data", "Movimentação", "Descrição", "Tipo", "Categoria", "Entrada", "Saída", "Saldo"]
        
        # Garante que todas as colunas existem (caso a query retorne vazio em alguma)
        for col in cols_view:
            if col not in df.columns:
                df[col] = ""

        df_exibir = df[cols_view]

        st.subheader(f"Pré-visualização ({len(df)} registros)")
        
        st.dataframe(
            df_exibir, 
            use_container_width=True,
            hide_index=True,
            column_config={
                "Entrada": st.column_config.NumberColumn(format="R$ %.2f"),
                "Saída": st.column_config.NumberColumn(format="R$ %.2f"),
                "Saldo": st.column_config.NumberColumn(format="R$ %.2f"),
                "Data": st.column_config.DateColumn(format="DD/MM/YYYY"),
            }
        )

        st.markdown("### Exportação")
        c1, c2 = st.columns(2)

        # Botão Relatório Geral
        with c1:
            if st.button(f"📄 Baixar Relatório ({st.session_state['filtro_atual']})", use_container_width=True):
                pdf_bytes = gerar_pdf_treecomex(df_exibir, titulo=f"Relatório - {st.session_state['filtro_atual']}")
                st.download_button(
                    label="⬇️ Download PDF",
                    data=pdf_bytes,
                    file_name="Relatorio_Geral.pdf",
                    mime="application/pdf"
                )

        # Botão Relatório Licitação
        with c2:
            if st.button("⚖️ Baixar Relatório de Licitação", use_container_width=True):
                # Filtra onde a Categoria contem "Licitação" (case insensitive)
                df_licitacao = df_exibir[df_exibir['Categoria'].astype(str).str.contains('Licitação', case=False, na=False)]
                
                if not df_licitacao.empty:
                    pdf_bytes = gerar_pdf_treecomex(df_licitacao, titulo=f"Relatório Licitação - {st.session_state['filtro_atual']}")
                    st.download_button(
                        label="⬇️ Download PDF Licitação",
                        data=pdf_bytes,
                        file_name="Relatorio_Licitacao.pdf",
                        mime="application/pdf"
                    )
                else:
                    st.warning("Nenhum registro de Licitação encontrado nestes dados.")

    elif 'relatorio_cache' in st.session_state:
        st.info("Nenhum dado encontrado para os filtros selecionados.")


# --- FUNÇÃO DO PDF (Mantida e Ajustada) ---
def gerar_pdf_treecomex(df, titulo="Relatório"):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=10*mm, leftMargin=10*mm, topMargin=15*mm, bottomMargin=15*mm)
    
    elementos = []
    styles = getSampleStyleSheet()

    # Logo e Título
    try:
        logo = Image("assets/logo.png", width=40*mm, height=15*mm)
        logo.hAlign = 'RIGHT'
    except:
        logo = Paragraph("Treecomex", styles['Normal'])

    titulo_text = Paragraph(titulo, styles['Title'])
    
    data_header = [[titulo_text, logo]]
    t_header = Table(data_header, colWidths=[200*mm, 70*mm])
    t_header.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
    elementos.append(t_header)
    elementos.append(Spacer(1, 10*mm))

    # Dados da Tabela
    # Converter colunas numéricas para string formatada R$
    data_export = df.copy()
    for col in ["Entrada", "Saída", "Saldo"]:
        data_export[col] = data_export[col].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) and x != '' else "0.00")
    
    # Prepara lista para o ReportLab
    lista_dados = [data_export.columns.to_list()]
    
    for index, row in data_export.iterrows():
        linha = []
        for item in row:
            # Envolve texto longo em Paragraph para quebrar linha
            if isinstance(item, str) and len(item) > 25:
                 linha.append(Paragraph(item, styles['Normal']))
            else:
                 linha.append(str(item))
        lista_dados.append(linha)

    # Larguras manuais ajustadas
    col_widths = [30*mm, 25*mm, 40*mm, 50*mm, 20*mm, 30*mm, 25*mm, 25*mm, 25*mm]
    
    t = Table(lista_dados, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#58A6D8")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
    ]))
    
    elementos.append(t)
    doc.build(elementos)
    buffer.seek(0)
    return buffer.getvalue()