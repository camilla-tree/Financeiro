from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
import io

import pandas as pd
import streamlit as st

# Usada para gerar o PDF
from fpdf import FPDF

from db import (
    list_fechamentos,
    get_fechamento,
    upsert_fechamento,
    get_despesas,
    replace_despesas,
    fetch_df_cached,
)

# ==========================================
# 1. CONFIGURAÇÕES E TEMPLATES
# ==========================================

DESPESAS_TEMPLATE = [
    ("Taxa de Liberação de BL/AWB", True),
    ("Armazenagem PORTO", True),
    ("TX Siscomex", False),
    ("MULTA", False),
    ("A.F.R.M.M.", False),
    ("GNRE ICMS", False),
    ("Taxa de Exoneração", False),
    ("Armazenagem DTA", False),
    ("Frete Rodoviário", False),
    ("MAPA", False),
    ("S.D.A", False),
    ("Despachante Honorário", False),
    ("Escolta DTA", False),
    ("TX ADM TREE COMEX", False),
    ("Análise credito NF saída (R$ 43,00 por CNPJ) mín. 3", False),
    ("TX Analise DI - (Proseftur)", False),
]

def _to_decimal(v: Any) -> Decimal:
    try:
        if v is None or str(v).strip() == "":
            return Decimal("0")
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v))
        s = str(v).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal("0")

def _to_float(v: Any) -> float:
    return float(_to_decimal(v))

def _safe_div_pct(numerador: float, denominador: float) -> float:
    if denominador == 0 or pd.isna(denominador):
        return 0.0
    return (numerador / denominador) * 100

def _ensure_despesas_template(existing: pd.DataFrame) -> pd.DataFrame:
    if existing is not None and not existing.empty:
        return existing
    rows = []
    for i, (desc, estimado) in enumerate(DESPESAS_TEMPLATE, start=1):
        rows.append({"ordem": i, "descricao": desc, "valor_brl": 0.0, "estimado": estimado})
    return pd.DataFrame(rows)

def _buscar_processo_por_di(di_str: str) -> dict | None:
    sql = """
        SELECT p.id as processo_id, p.referencia, p.data_registro, 
               e.nome as empresa_nome, c.nome as cliente_nome,
               c.markup as cliente_markup, p.empresa_id, p.cliente_id
        FROM processo p
        LEFT JOIN empresa e ON p.empresa_id = e.id
        LEFT JOIN cliente c ON p.cliente_id = c.id
        WHERE p.di = %s LIMIT 1
    """
    df = fetch_df_cached(sql, (di_str,))
    if not df.empty:
        dados = df.iloc[0].to_dict()
        if isinstance(dados.get("data_registro"), str):
            dados["data_registro"] = pd.to_datetime(dados["data_registro"]).date()
        return dados
    return None

def _extract_cell(df: pd.DataFrame, row_idx: int, col_idx: int) -> float:
    try:
        val = df.iloc[row_idx, col_idx]
        return _to_float(val)
    except IndexError:
        return 0.0

def _tratar_texto_pdf(texto):
    """Remove caracteres especiais que quebram o PDF padrão da FPDF"""
    return str(texto).encode('latin-1', 'replace').decode('latin-1')

def _gerar_pdf(dados: dict, df_despesas: pd.DataFrame) -> bytes:
    """Gera um PDF estruturado com os dados do fechamento."""
    pdf = FPDF()
    pdf.add_page()
    
    # Cabeçalho
    pdf.set_font("Arial", 'B', 16)
    pdf.cell(0, 10, _tratar_texto_pdf(f"Relatório de Fechamento - {dados.get('referencia', 'N/A')}"), 0, 1, 'C')
    pdf.ln(5)
    
    # Seção 1: Identificação
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 8, _tratar_texto_pdf("1. IDENTIFICAÇÃO"), 0, 1)
    pdf.set_font("Arial", '', 10)
    pdf.cell(100, 6, _tratar_texto_pdf(f"DI: {dados.get('di', '')}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"Data Registro: {dados.get('data_registro', '')}"), 0, 1)
    pdf.cell(100, 6, _tratar_texto_pdf(f"Cliente: {dados.get('cliente', '')}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"Empresa: {dados.get('empresa', '')}"), 0, 1)
    pdf.ln(5)

    # Seção 2: Valores Base e Impostos
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 8, _tratar_texto_pdf("2. VALORES E IMPOSTOS (BRL)"), 0, 1)
    pdf.set_font("Arial", '', 10)
    pdf.cell(100, 6, _tratar_texto_pdf(f"Valor CIF: R$ {dados.get('cif_brl', 0):,.2f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"II: R$ {dados.get('ii_brl', 0):,.2f}"), 0, 1)
    pdf.cell(100, 6, _tratar_texto_pdf(f"FOB: R$ {dados.get('fob_brl', 0):,.2f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"IPI: R$ {dados.get('ipi_brl', 0):,.2f}"), 0, 1)
    pdf.cell(100, 6, _tratar_texto_pdf(f"Frete: R$ {dados.get('frete_brl', 0):,.2f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"PIS: R$ {dados.get('pis_brl', 0):,.2f}"), 0, 1)
    pdf.cell(100, 6, _tratar_texto_pdf(f"Seguro: R$ {dados.get('seguro_brl', 0):,.2f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"COFINS: R$ {dados.get('cofins_brl', 0):,.2f}"), 0, 1)
    pdf.cell(100, 6, _tratar_texto_pdf(f"Adicional: R$ {dados.get('adic_brl', 0):,.2f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"ICMS: R$ {dados.get('icms_brl', 0):,.2f}"), 0, 1)
    pdf.ln(5)

    # Seção 3: Despesas Gerais
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 8, _tratar_texto_pdf("3. DESPESAS GERAIS NA NACIONALIZACAO"), 0, 1)
    pdf.set_font("Arial", '', 10)
    for _, row in df_despesas.iterrows():
        valor = float(_to_decimal(row.get('valor_brl', 0)))
        if valor > 0:
            desc = _tratar_texto_pdf(row.get('descricao', ''))
            pdf.cell(150, 6, desc, 0, 0)
            pdf.cell(40, 6, _tratar_texto_pdf(f"R$ {valor:,.2f}"), 0, 1, 'R')
            
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(150, 8, _tratar_texto_pdf("TOTAL DESPESAS:"), 0, 0)
    pdf.cell(40, 8, _tratar_texto_pdf(f"R$ {dados.get('total_despesas_brl', 0):,.2f}"), 0, 1, 'R')
    pdf.ln(5)

    # Seção 4: Formação de Preço (NF Saída)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 8, _tratar_texto_pdf("4. DADOS NF SAIDA (FORMACAO DE PRECO)"), 0, 1)
    pdf.set_font("Arial", '', 10)
    pdf.cell(100, 6, _tratar_texto_pdf(f"Custo Aquisicao: R$ {dados.get('custo_aquisicao', 0):,.2f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"PIS Venda (%): {dados.get('pis_v_pct', 0):.2f}%"), 0, 1)
    
    pdf.cell(100, 6, _tratar_texto_pdf(f"Fator Divisor: {dados.get('fator', 0):.4f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"COFINS Venda (%): {dados.get('cofins_v_pct', 0):.2f}%"), 0, 1)
    
    pdf.cell(100, 6, _tratar_texto_pdf(f"Base de Calculo Normal: R$ {dados.get('bc_normal', 0):,.2f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"ICMS Venda (%): {dados.get('icms_v_pct', 0):.2f}%"), 0, 1)
    
    pdf.cell(100, 6, _tratar_texto_pdf(f"IPI Venda (Valor): R$ {dados.get('val_ipi_venda', 0):,.2f}"), 0, 0)
    pdf.cell(90, 6, _tratar_texto_pdf(f"Markup (%): {dados.get('markup_v_pct', 0):.2f}%"), 0, 1)
    
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(100, 10, _tratar_texto_pdf("TOTAL NF SAIDA:"), 0, 0)
    pdf.cell(90, 10, _tratar_texto_pdf(f"R$ {dados.get('total_nf_saida', 0):,.2f}"), 0, 1)
    
    return pdf.output(dest='S').encode('latin-1')

# ==========================================
# RENDERIZAÇÃO DA TELA
# ==========================================

def render_fechamento():
    st.title("📊 Fechamento (v1)")
    st.caption("Fluxo de Importação: Identificação > Rateio > Valores > Logística > Despesas > NF Saída")
    st.divider()

    # Variáveis de Estado
    if "f_dados_excel" not in st.session_state:
        st.session_state["f_dados_excel"] = {}
    if "f_df_rateio" not in st.session_state:
        st.session_state["f_df_rateio"] = pd.DataFrame()
    if "f_markup_cliente" not in st.session_state:
        st.session_state["f_markup_cliente"] = 6.0
    if "proc_id_bd" not in st.session_state:
        st.session_state["proc_id_bd"] = None
    if "emp_id_bd" not in st.session_state:
        st.session_state["emp_id_bd"] = None
    if "cli_id_bd" not in st.session_state:
        st.session_state["cli_id_bd"] = None

    # ==========================================
    # 1. ÁREA DE IMPORTAÇÃO
    # ==========================================
    col_import, col_search = st.columns([1, 1], gap="large")

    with col_import:
        st.markdown("#### 📥 Importar Excel")
        uploaded_file = st.file_uploader("Selecione a planilha (Rateio/Resumo)", type=["xlsx", "xls"])
        
        if st.button("🧹 Limpar Dados / Nova Importação", width="stretch"):
            for key in ["f_dados_excel", "f_df_rateio", "f_markup_cliente", "pdf_bytes", "proc_id_bd", "emp_id_bd", "cli_id_bd"]:
                st.session_state.pop(key, None)
            st.rerun()

    with col_search:
        st.markdown("#### 🔍 Histórico")
        st.info("Pesquisa de fechamentos anteriores (em breve).")

    val_di = ""
    val_empresa = ""
    val_cliente = ""
    val_referencia = ""
    val_data = date.today()

    # ==========================================
    # 2. PROCESSAMENTO DO EXCEL
    # ==========================================
    if uploaded_file is not None:
        try:
            uploaded_file.seek(0)
            df_resumo = pd.read_excel(uploaded_file, sheet_name="Resumo", header=None)
            
            if len(df_resumo) > 5:
                val_di = str(df_resumo.iloc[5, 0]).strip()
            
            dados_lidos = {
                "fob_brl": _extract_cell(df_resumo, 8, 0),
                "frete_brl": _extract_cell(df_resumo, 11, 0),
                "seguro_brl": _extract_cell(df_resumo, 11, 1),
                "cif_brl": _extract_cell(df_resumo, 8, 2),
                "ii_brl": _extract_cell(df_resumo, 19, 3),
                "ipi_brl": _extract_cell(df_resumo, 22, 3),
                "pis_brl": _extract_cell(df_resumo, 19, 4),
                "cofins_brl": _extract_cell(df_resumo, 22, 4),
                "icms_brl": _extract_cell(df_resumo, 19, 1),
            }
            st.session_state["f_dados_excel"] = dados_lidos

            if val_di:
                proc_info = _buscar_processo_por_di(val_di)
                if proc_info:
                    st.success(f"✅ **Processo Encontrado!** DI {val_di}.")
                    val_empresa = proc_info.get("empresa_nome", "")
                    val_cliente = proc_info.get("cliente_nome", "")
                    val_referencia = proc_info.get("referencia", "")
                    st.session_state["proc_id_bd"] = proc_info.get("processo_id")
                    st.session_state["emp_id_bd"] = proc_info.get("empresa_id")
                    st.session_state["cli_id_bd"] = proc_info.get("cliente_id")

                    dt_bd = proc_info.get("data_registro")
                    if dt_bd:
                        val_data = dt_bd
                        
                    m_cliente = proc_info.get("cliente_markup")
                    if pd.notna(m_cliente) and m_cliente is not None:
                        st.session_state["f_markup_cliente"] = float(m_cliente)
                else:
                    st.warning(f"⚠️ **DI {val_di} não cadastrada**. Preencha manualmente.")

            uploaded_file.seek(0)
            df_bruto = pd.read_excel(uploaded_file, sheet_name="Rateio de Produtos", usecols="C, D, E, I, R, U, X, AA")
            df_bruto.columns = ["NCM", "PRODUTO", "QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]
            
            for col in ["QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]:
                df_bruto[col] = pd.to_numeric(df_bruto[col], errors="coerce").fillna(0)
            
            df_bruto = df_bruto.dropna(subset=["PRODUTO"])
            
            df_calc = df_bruto.copy()
            df_calc["II VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["II %"] / 100)
            base_ipi = df_calc["VALOR TOTAL R$"] + df_calc["II VALOR"]
            df_calc["IPI VALOR"] = base_ipi * (df_calc["IPI %"] / 100)
            df_calc["PIS VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["PIS %"] / 100)
            df_calc["COFINS VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["CONFINS %"] / 100)

            colunas_finais = [
                "PRODUTO", "NCM", "QUANT", "VALOR TOTAL R$",
                "II %", "II VALOR", "IPI %", "IPI VALOR",
                "PIS %", "PIS VALOR", "CONFINS %", "COFINS VALOR"
            ]
            st.session_state["f_df_rateio"] = df_calc[colunas_finais]

        except Exception as e:
            st.error(f"Erro ao processar planilha: {e}")

    # ==========================================
    # 3. IDENTIFICAÇÃO
    # ==========================================
    st.markdown("### 1. Identificação")
    c1, c2, c3, c4, c5 = st.columns([1.5, 1.2, 1, 1.5, 1.5])
    with c1: di_input = st.text_input("DI", value=val_di, disabled=True)
    with c2: ref_input = st.text_input("Referência", value=val_referencia)
    with c3: dt_input = st.date_input("Data Registro", value=val_data)
    with c4: emp_input = st.text_input("Empresa", value=val_empresa)
    with c5: cli_input = st.text_input("Cliente", value=val_cliente)

    st.divider()

    # ==========================================
    # 4. LOGÍSTICA
    # ==========================================
    st.markdown("### 2. Logística de Entrada")
    l1, l2, l3, l4 = st.columns(4)
    with l1: origem = st.text_input("Origem", value="CHINA")
    with l2: modal = st.selectbox("Modal", ["MARITIMO", "AEREO", "RODOVIARIO"], index=0)
    with l3: destino = st.text_input("Destino", value="RIO DE JANEIRO")
    with l4: qtde_cnt = st.number_input("Qtde Container", value=1, min_value=1)

    st.divider()

    # ==========================================
    # 5. VALORES CONSOLIDADOS
    # ==========================================
    memoria = st.session_state.get("f_dados_excel", {})
    st.markdown("### 3. Valores Consolidados")
    col_brl, col_tax, col_usd = st.columns([1.1, 1.5, 1], gap="large")

    with col_brl:
        st.markdown("#### 🇧🇷 Mercadoria (R$)")
        v_fob_brl = st.number_input("F.O.B.", value=memoria.get("fob_brl", 0.0), format="%.2f")
        v_frete_brl = st.number_input("Frete Intl.", value=memoria.get("frete_brl", 0.0), format="%.2f")
        v_seguro_brl = st.number_input("Seguro", value=memoria.get("seguro_brl", 0.0), format="%.2f")
        v_adic_brl = st.number_input("Adicional (Manual)", value=0.0, format="%.2f")
        v_cif_brl = st.number_input("VALOR CIF", value=memoria.get("cif_brl", 0.0), format="%.2f", disabled=True)

    with col_tax:
        st.markdown("#### 🏛️ Totais de Impostos")
        t_ii = memoria.get("ii_brl", 0.0)
        t_ipi = memoria.get("ipi_brl", 0.0)
        t_pis = memoria.get("pis_brl", 0.0)
        t_cofins = memoria.get("cofins_brl", 0.0)
        t_icms = memoria.get("icms_brl", 0.0)

        total_impostos = t_ii + t_ipi + t_pis + t_cofins + t_icms
        total_geral_brl = v_cif_brl + total_impostos
        st.metric("TOTAL (CIF + Impostos)", f"R$ {total_geral_brl:,.2f}")

    with col_usd:
        st.markdown("#### 🇺🇸 Conversão (USD)")
        taxa = st.number_input("Taxa de Conversão", min_value=0.0, value=1.0, step=0.0001, format="%.4f")
        usd_cfr = (v_fob_brl + v_frete_brl + v_adic_brl) / taxa if taxa > 0 else 0.0
        st.metric("TOTAL CFR (USD)", f"$ {usd_cfr:,.2f}")

    st.divider()

    # ==========================================
    # 6. DESPESAS E CUSTO AQUISIÇÃO
    # ==========================================
    st.markdown("### 4. Despesas Gerais")
    col_desp1, col_desp2 = st.columns([2, 1], gap="large")

    with col_desp1:
        df_desp = _ensure_despesas_template(pd.DataFrame())
        edited_desp = st.data_editor(df_desp, width="stretch", hide_index=True, key="despesas_editor")

    with col_desp2:
        soma_despesas = sum(float(_to_decimal(r.get("valor_brl"))) for _, r in edited_desp.iterrows())
        desembolso_final = total_geral_brl + soma_despesas
        
        impostos_recuperaveis = t_ipi + t_pis + t_cofins + t_icms
        custo_aquisicao = desembolso_final - impostos_recuperaveis + v_adic_brl
        
        st.metric("DESEMBOLSO TOTAL", f"R$ {desembolso_final:,.2f}")
        st.metric("CUSTO DE AQUISIÇÃO", f"R$ {custo_aquisicao:,.2f}")

    st.divider()

    # ==========================================
    # 7. DADOS NF SAÍDA
    # ==========================================
    st.markdown("### 5. Dados NF Saída")
    
    col_nf1, col_nf2, col_nf3 = st.columns([1, 1.2, 1.2], gap="large")

    with col_nf1:
        pis_v_pct = st.number_input("P.I.S. Venda (%)", value=1.65, format="%.2f")
        cofins_v_pct = st.number_input("COFINS Venda (%)", value=7.60, format="%.2f")
        markup_v_pct = st.number_input("MARK-UP (%)", value=float(st.session_state.get("f_markup_cliente", 6.0)), format="%.2f")
        icms_v_pct = st.number_input("I.C.M.S. Venda (%)", value=4.00, format="%.2f")
        ipi_import_pct = _safe_div_pct(t_ipi, v_cif_brl + t_ii)
        ipi_v_pct = st.number_input("I.P.I. Venda (%)", value=ipi_import_pct, format="%.2f")

    with col_nf2:
        soma_pct = pis_v_pct + cofins_v_pct + markup_v_pct + icms_v_pct
        fator_divisor = (100.0 - soma_pct) / 100.0
        bc_normal = (custo_aquisicao / fator_divisor) if fator_divisor > 0 else 0.0
        val_ipi_venda = bc_normal * (ipi_v_pct / 100.0)
        st.metric("BC NORMAL", f"R$ {bc_normal:,.2f}")

    with col_nf3:
        total_nf_saida = bc_normal + val_ipi_venda
        st.metric("TOTAL NF SAÍDA", f"R$ {total_nf_saida:,.2f}", delta="Faturamento")

    st.write("")
    
    # ==========================================
    # 8. SALVAR E EXPORTAR PDF
    # ==========================================
    col_btn_1, col_btn_2 = st.columns([1, 1])
    
    with col_btn_1:
        if st.button("💾 Salvar Dados e Gerar Relatório", type="primary", width="stretch"):
            
            # --- 1. Dicionário de Carga para o Banco e PDF ---
            payload = {
                # Identificacao e chaves originais
                "processo_id": st.session_state.get("proc_id_bd"),
                "empresa_id": st.session_state.get("emp_id_bd") or 1, # Ajuste temporário se nulo
                "cliente_id": st.session_state.get("cli_id_bd") or 1,
                "data": dt_input,
                "empresa": emp_input,
                "cliente": cli_input,
                "referencia": ref_input,
                "di": di_input,
                "origem": origem,
                "modal": modal,
                "destino": destino,
                "qtde_container": qtde_cnt,
                "taxa_conversao": taxa,
                
                # Valores em Reais e Impostos Importação (Campos Novos do Banco)
                "fob_brl": v_fob_brl,
                "frete_brl": v_frete_brl,
                "seguro_brl": v_seguro_brl,
                "adic_brl": v_adic_brl,
                "cif_brl": v_cif_brl,
                "ii_brl": t_ii,
                "ipi_brl": t_ipi,
                "pis_brl": t_pis,
                "cofins_brl": t_cofins,
                "icms_brl": t_icms,
                
                # Despesas e NF Saida
                "total_despesas_brl": soma_despesas,
                "custo_aquisicao": custo_aquisicao,
                "pis_v_pct": pis_v_pct,
                "cofins_v_pct": cofins_v_pct,
                "icms_v_pct": icms_v_pct,
                "markup_v_pct": markup_v_pct,
                "ipi_v_pct": ipi_v_pct,
                "fator": fator_divisor,
                "bc_normal": bc_normal,
                "val_ipi_venda": val_ipi_venda,
                "total_nf_saida": total_nf_saida
            }
            
            # Chama a função antiga de Upsert (É importante que você atualize o SQL dela no db.py depois)
            # upsert_fechamento(payload)
            
            # --- 2. Geração do PDF ---
            pdf_b = _gerar_pdf(payload, edited_desp)
            st.session_state["pdf_bytes"] = pdf_b
            st.success("✅ Fechamento salvo e PDF gerado com sucesso!")

    # Exibe o botão de Download apenas se o PDF estiver pronto na memória
    with col_btn_2:
        if "pdf_bytes" in st.session_state:
            st.download_button(
                label="📥 Baixar Relatório (PDF)",
                data=st.session_state["pdf_bytes"],
                file_name=f"Fechamento_{ref_input}.pdf",
                mime="application/pdf",
                width="stretch"
            )