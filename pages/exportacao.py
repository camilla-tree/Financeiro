import streamlit as st
import pandas as pd
from datetime import date
from db import get_lista_clientes, get_lista_empresas, get_dados_relatorio_filtrado, get_empresas_por_cliente
import zipfile

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

            tipo_filtro = st.radio("Filtrar por:", ["Cliente", "Empresa"], key="exp_tipo")

        with col_select:
            saldo_inicial = 0.0
            selecionado_emp = None
            if tipo_filtro == "Cliente":
                opcoes = ["Todos"] + get_lista_clientes()
                selecionado = st.selectbox("Selecione o Cliente", opcoes)
                if selecionado != "Todos":
                    opcoes_emp = ["Todas"] + get_empresas_por_cliente(selecionado)
                    selecionado_emp = st.selectbox("Selecione a Empresa", opcoes_emp)
                else:
                    selecionado_emp = None
                
                saldo_inicial = st.number_input("Saldo Inicial", value=0.00, step=100.0)
            else:
                opcoes = ["Todas"] + get_lista_empresas()
                selecionado = st.selectbox("Selecione a Empresa", opcoes)

    st.markdown("---")

    if st.button("Buscar Dados", width="stretch"):
        df_resultado = get_dados_relatorio_filtrado(dt_inicio, dt_fim, tipo_filtro, selecionado, selecionado_emp)
        
        # Calculate running Saldo if Cliente
        if tipo_filtro == "Cliente" and not df_resultado.empty:
            if selecionado_emp == "Todas":
                # Separa saldo por empresa
                novo_saldo_col = []
                saldos_atuais = {} 
                for idx, row in df_resultado.iterrows():
                    emp = row["Empresa"]
                    if emp not in saldos_atuais:
                        saldos_atuais[emp] = float(saldo_inicial)
                    val = float(row.get("Valor_Original", 0.0))
                    saldos_atuais[emp] += val
                    novo_saldo_col.append(saldos_atuais[emp])
                df_resultado["Saldo"] = novo_saldo_col
            else:
                saldo_atual = float(saldo_inicial)
                novo_saldo_col = []
                for idx, row in df_resultado.iterrows():
                    val = float(row.get("Valor_Original", 0.0))
                    saldo_atual += val
                    novo_saldo_col.append(saldo_atual)
                df_resultado["Saldo"] = novo_saldo_col

        st.session_state['relatorio_cache'] = df_resultado
        desc_emp = f" - {selecionado_emp}" if selecionado_emp else ""
        st.session_state['filtro_atual'] = f"{tipo_filtro}: {selecionado}{desc_emp}"
        st.session_state['filtro_cli_emp'] = (selecionado, selecionado_emp)
        st.session_state['saldo_inicial'] = saldo_inicial
        st.session_state['is_cliente_mode'] = (tipo_filtro == "Cliente")

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
            fc, fe = st.session_state.get('filtro_cli_emp', (None, None))
            is_cliente_mode = st.session_state.get('is_cliente_mode', False)
            sal_ini = st.session_state.get('saldo_inicial', 0.0)
            
            if is_cliente_mode and fe == "Todas":
                if st.button("📦 Gerar ZIP (Múltiplas Empresas)", width="stretch"):
                    buffer_zip = BytesIO()
                    with zipfile.ZipFile(buffer_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
                        for emp_name, group_df in df_exibir.groupby("Empresa"):
                            pdf_bytes = gerar_pdf_hurr(group_df, titulo=f"{fc} - {emp_name}",
                                saldo_inicial=sal_ini, is_cliente=True)
                            zip_file.writestr(f"Relatorio_{fc}_{emp_name}.pdf", pdf_bytes)
                    
                    st.download_button(
                        label="⬇️ Download ZIP Pronto",
                        data=buffer_zip.getvalue(),
                        file_name=f"Relatorios_{fc}.zip",
                        mime="application/zip",
                    )
            else:
                if st.button(f"📄 Gerar Relatório", width="stretch"):
                    pdf_bytes = gerar_pdf_hurr(df_exibir, titulo=f"{st.session_state['filtro_atual']}",
                        saldo_inicial=sal_ini, is_cliente=is_cliente_mode)
                    st.download_button(
                        label="⬇️ Download PDF Pronto",
                        data=pdf_bytes,
                        file_name="Relatorio.pdf",
                        mime="application/pdf"
                    )

        with c2:
            if st.button("⚖️ Baixar Relatório de Licitação", width="stretch"):
                # Filtra Licitação
                df_licitacao = df_exibir[df_exibir['Categoria'].astype(str).str.contains('Licitação', case=False, na=False)]
                
                if not df_licitacao.empty:
                    pdf_bytes = gerar_pdf_hurr(df_licitacao, titulo=f"Relatório Licitação - {st.session_state['filtro_atual']}")
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
def gerar_pdf_hurr(df, titulo="Relatório", saldo_inicial=0.0, is_cliente=False):
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
        logo = Paragraph("HURR PARTICIPAÇÕES", styles['Normal'])

    if is_cliente:
        saldo_atual = float(df["Saldo"].iloc[-1]) if not df.empty else saldo_inicial
        
        mes_str = ""
        if not df.empty and pd.notnull(df["Data"].iloc[0]):
            try:
                dt_obj = pd.to_datetime(df["Data"].iloc[0])
                mes_str = dt_obj.strftime("%m/%Y")
            except:
                pass
                
        header_html = f"""
        <font size="14"><b>{titulo}</b></font><br/>
        <font size="10">Mês: {mes_str} | Saldo Anterior: R$ {saldo_inicial:,.2f} | Saldo Atual: R$ {saldo_atual:,.2f}</font>
        """
        titulo_text = Paragraph(header_html, styles['Normal'])
    else:
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
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#ffbd59")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
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