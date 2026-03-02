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
    # CSS para Inputs Arredondados (Estilo Dashboard)
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
                opcoes = ["Todos"] + get_lista_clientes()
                selecionado = st.selectbox("Selecione o Cliente", opcoes)
            else:
                opcoes = ["Todas"] + get_lista_empresas()
                selecionado = st.selectbox("Selecione a Empresa", opcoes)

    st.markdown("---")

    if st.button("Buscar Dados", width="stretch"):
        df_resultado = get_dados_relatorio_filtrado(dt_inicio, dt_fim, tipo_filtro, selecionado)
        st.session_state['relatorio_cache'] = df_resultado
        st.session_state['filtro_atual'] = f"{tipo_filtro}: {selecionado}"

    # Recupera do Cache
    df = st.session_state.get('relatorio_cache', pd.DataFrame())

    if not df.empty:
        # --- DEFINIÇÃO DAS COLUNAS PARA EXIBIÇÃO ---
        # Adicionamos "Empresa" no início
        cols_view = ["Empresa", "Banco", "Data", "Movimentação", "Descrição", "Tipo", "Categoria", "Entrada", "Saída", "Saldo"]
        
        # Garante integridade das colunas
        for col in cols_view:
            if col not in df.columns:
                df[col] = ""

        df_exibir = df[cols_view]

        st.subheader(f"Pré-visualização ({len(df)} registros)")
        
        st.dataframe(
            df_exibir, 
            width="stretch",
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

        with c1:
            if st.button(f"📄 Baixar Relatório ({st.session_state['filtro_atual']})", width="stretch"):
                pdf_bytes = gerar_pdf_treecomex(df_exibir, titulo=f"Relatório - {st.session_state['filtro_atual']}")
                st.download_button(
                    label="⬇️ Download PDF",
                    data=pdf_bytes,
                    file_name="Relatorio_Geral.pdf",
                    mime="application/pdf"
                )

        with c2:
            if st.button("⚖️ Baixar Relatório de Licitação", width="stretch"):
                # Filtra Licitação
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
                    st.warning("Nenhum registro de Licitação encontrado.")

    elif 'relatorio_cache' in st.session_state:
        st.info("Nenhum dado encontrado.")


# --- FUNÇÃO PDF AJUSTADA PARA NOVA COLUNA ---
def gerar_pdf_treecomex(df, titulo="Relatório"):
    buffer = BytesIO()
    # Margens menores para caber mais colunas
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=5*mm, leftMargin=5*mm, topMargin=10*mm, bottomMargin=10*mm)
    
    elementos = []
    styles = getSampleStyleSheet()

    # Logo
    try:
        logo = Image("assets/logo.png", width=40*mm, height=15*mm)
        logo.hAlign = 'RIGHT'
    except:
        logo = Paragraph("Treecomex", styles['Normal'])

    titulo_text = Paragraph(titulo, styles['Title'])
    
    data_header = [[titulo_text, logo]]
    t_header = Table(data_header, colWidths=[200*mm, 80*mm])
    t_header.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE')]))
    elementos.append(t_header)
    elementos.append(Spacer(1, 5*mm))

    # Formatação dos Dados
    data_export = df.copy()
    for col in ["Entrada", "Saída", "Saldo"]:
        data_export[col] = data_export[col].apply(lambda x: f"{float(x):.2f}" if pd.notnull(x) and x != '' else "0.00")
    
    # Prepara dados para Table
    lista_dados = [data_export.columns.to_list()]
    
    style_normal = styles['Normal']
    style_normal.fontSize = 7  # Fonte menor para caber texto
    
    for index, row in data_export.iterrows():
        linha = []
        for item in row:
            # Paragraph apenas em textos longos para quebrar linha
            txt = str(item)
            if len(txt) > 20:
                 linha.append(Paragraph(txt, style_normal))
            else:
                 linha.append(txt)
        lista_dados.append(linha)

    # --- NOVAS LARGURAS (Total Disponível ~285mm) ---
    # [Empresa, Banco, Data, Movimentação, Descrição, Tipo, Categoria, Entrada, Saída, Saldo]
    # Ajustei diminuindo um pouco cada uma para caber a Empresa
    col_widths = [
        20*mm,  # Empresa (curto)
        25*mm,  # Banco
        22*mm,  # Data
        38*mm,  # Movimentação (reduzido levemente)
        45*mm,  # Descrição (reduzido levemente)
        15*mm,  # Tipo (C/D curto)
        25*mm,  # Categoria
        22*mm,  # Entrada
        22*mm,  # Saída
        22*mm   # Saldo
    ]
    
    t = Table(lista_dados, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#58A6D8")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 6.5), # Fonte reduzida
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
    ]))
    
    elementos.append(t)
    doc.build(elementos)
    buffer.seek(0)
    return buffer.getvalue()