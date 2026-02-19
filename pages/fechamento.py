from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from db import (
    list_fechamentos,
    get_fechamento,
    upsert_fechamento,
    get_despesas,
    replace_despesas,
    fetch_df_cached,
)

# ==========================================
# CONFIGURAÇÕES E FUNÇÕES AUXILIARES
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

def _to_float(v: Any) -> float:
    """Converte valores do Excel para float de forma resiliente."""
    if v is None:
        return 0.0
    try:
        # Tenta converter direto (se for número no Excel)
        return float(v)
    except (ValueError, TypeError):
        # Se for string, tenta limpar formatação
        s = str(v).strip()
        if not s:
            return 0.0
        # Se tiver "R$" ou ",", assume formatação BR e converte
        if "R$" in s or "," in s:
            s = s.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        return float(s)

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
               e.nome as empresa_nome, c.nome as cliente_nome
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
    """Extrai valor pelo índice (0-based)"""
    try:
        val = df.iloc[row_idx, col_idx]
        if pd.isna(val):
            return 0.0
        return _to_float(val)
    except IndexError:
        return 0.0

# ==========================================
# RENDERIZAÇÃO DA TELA
# ==========================================

def render_fechamento():
    st.title("📊 Fechamento (v1)")
    st.caption("Fluxo de Importação: Identificação > Rateio > Valores > Logística")
    st.divider()

    # Variáveis de Estado
    if "f_dados_excel" not in st.session_state:
        st.session_state["f_dados_excel"] = {}
    if "f_df_rateio" not in st.session_state:
        st.session_state["f_df_rateio"] = pd.DataFrame()

    # ==========================================
    # 1. ÁREA DE IMPORTAÇÃO
    # ==========================================
    col_import, col_search = st.columns([1, 1], gap="large")

    with col_import:
        st.markdown("#### 📥 Importar Excel")
        uploaded_file = st.file_uploader("Selecione a planilha (Rateio/Resumo)", type=["xlsx", "xls"])

    with col_search:
        st.markdown("#### 🔍 Histórico")
        st.info("Pesquisa de fechamentos anteriores (em breve).")

    # Variáveis padrão
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
            # --- A. Leitura da Aba RESUMO ---
            uploaded_file.seek(0) # Rebobina o arquivo para garantir leitura limpa
            df_resumo = pd.read_excel(uploaded_file, sheet_name="Resumo", header=None)
            
            # Pega DI (Linha 6 Excel -> Indice 5 Pandas, Coluna A -> 0)
            if len(df_resumo) > 5:
                val_di = str(df_resumo.iloc[5, 0]).strip()
            
            # Extração dos Valores em Reais (Indices Pandas = Excel - 1)
            # A9 -> 8, 0 | C9 -> 8, 2
            dados_lidos = {
                "fob_brl": _extract_cell(df_resumo, 8, 0),
                "frete_brl": _extract_cell(df_resumo, 11, 0),
                "seguro_brl": _extract_cell(df_resumo, 11, 1),
                "cif_brl": _extract_cell(df_resumo, 8, 2),
                
                "ii_brl": _extract_cell(df_resumo, 19, 3),   # D20
                "ipi_brl": _extract_cell(df_resumo, 22, 3),  # D23
                "pis_brl": _extract_cell(df_resumo, 19, 4),  # E20
                "cofins_brl": _extract_cell(df_resumo, 22, 4), # E23
                "icms_brl": _extract_cell(df_resumo, 19, 1), # B20
            }
            st.session_state["f_dados_excel"] = dados_lidos
            
            # --- DEBUG: Verificador de Dados ---
            with st.expander("🕵️ Debug: Verificar Leitura do Excel (Resumo)", expanded=False):
                st.write("Verifique se os dados estão nas linhas corretas (Índice Pandas = Linha Excel - 1):")
                st.dataframe(df_resumo.head(25)) # Mostra as primeiras 25 linhas para conferência
                st.write("**Dados Extraídos:**", dados_lidos)

            # --- B. Busca DI no Banco ---
            if val_di:
                proc_info = _buscar_processo_por_di(val_di)
                if proc_info:
                    st.success(f"✅ **Processo Encontrado!** DI {val_di}.")
                    val_empresa = proc_info.get("empresa_nome", "")
                    val_cliente = proc_info.get("cliente_nome", "")
                    val_referencia = proc_info.get("referencia", "")
                    dt_bd = proc_info.get("data_registro")
                    if dt_bd:
                        val_data = dt_bd
                else:
                    st.warning(f"⚠️ **DI {val_di} não cadastrada**. Preencha manualmente.")

            # --- C. Leitura da Aba RATEIO ---
            uploaded_file.seek(0) # Rebobina DE NOVO para ler a outra aba
            df_bruto = pd.read_excel(uploaded_file, sheet_name="Rateio de Produtos", usecols="C, D, E, I, R, U, X, AA")
            df_bruto.columns = ["NCM", "PRODUTO", "QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]
            
            # Converter colunas numéricas com coerce para evitar erro de string
            cols_num = ["QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]
            for col in cols_num:
                df_bruto[col] = pd.to_numeric(df_bruto[col], errors="coerce").fillna(0)
            
            df_bruto = df_bruto.dropna(subset=["PRODUTO"])
            
            # Cálculos Matemáticos
            df_calc = df_bruto.copy()
            
            # II
            df_calc["II VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["II %"] / 100)
            
            # IPI
            base_ipi = df_calc["VALOR TOTAL R$"] + df_calc["II VALOR"]
            df_calc["IPI VALOR"] = base_ipi * (df_calc["IPI %"] / 100)
            
            # PIS/COFINS
            df_calc["PIS VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["PIS %"] / 100)
            df_calc["COFINS VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["CONFINS %"] / 100)

            colunas_finais = [
                "PRODUTO", "NCM", "QUANT", "VALOR TOTAL R$",
                "II %", "II VALOR",
                "IPI %", "IPI VALOR",
                "PIS %", "PIS VALOR",
                "CONFINS %", "COFINS VALOR"
            ]
            st.session_state["f_df_rateio"] = df_calc[colunas_finais]

        except Exception as e:
            st.error(f"Erro ao processar planilha: {e}")

    # ==========================================
    # 3. IDENTIFICAÇÃO
    # ==========================================
    st.markdown("### 1. Identificação")
    c1, c2, c3, c4, c5 = st.columns([1.5, 1.2, 1, 1.5, 1.5])
    with c1: st.text_input("DI", value=val_di, disabled=True)
    with c2: st.text_input("Referência", value=val_referencia)
    with c3: st.date_input("Data Registro", value=val_data)
    with c4: st.text_input("Empresa", value=val_empresa)
    with c5: st.text_input("Cliente", value=val_cliente)
    
    if st.button("💾 Salvar Identificação", type="primary"):
        st.success("Identificação salva.")

    st.divider()

    # ==========================================
    # 4. TABELA DE RATEIO
    # ==========================================
    df_rateio = st.session_state.get("f_df_rateio", pd.DataFrame())
    
    with st.expander("📦 Rateio de Produtos e Impostos Detalhados", expanded=False):
        if not df_rateio.empty:
            st.dataframe(
                df_rateio, 
                use_container_width=True,
                hide_index=True,
                column_config={
                    "PRODUTO": st.column_config.TextColumn("Produto", width="large"),
                    "VALOR TOTAL R$": st.column_config.NumberColumn("Valor", format="R$ %.2f"),
                    "II %": st.column_config.NumberColumn("II %", format="%.2f%%"),
                    "II VALOR": st.column_config.NumberColumn("II (R$)", format="R$ %.2f"),
                    "IPI %": st.column_config.NumberColumn("IPI %", format="%.2f%%"),
                    "IPI VALOR": st.column_config.NumberColumn("IPI (R$)", format="R$ %.2f"),
                    "PIS %": st.column_config.NumberColumn("PIS %", format="%.2f%%"),
                    "PIS VALOR": st.column_config.NumberColumn("PIS (R$)", format="R$ %.2f"),
                    "COFINS %": st.column_config.NumberColumn("COFINS %", format="%.2f%%"),
                    "COFINS VALOR": st.column_config.NumberColumn("COFINS (R$)", format="R$ %.2f"),
                }
            )
        else:
            st.info("Aguardando importação do Excel.")

    st.divider()

    # ==========================================
    # 5. LOGÍSTICA
    # ==========================================
    st.markdown("### 2. Logística de Entrada")
    l1, l2, l3, l4 = st.columns(4)
    with l1: origem = st.text_input("Origem", value="CHINA")
    with l2: modal = st.selectbox("Modal", ["MARITIMO", "AEREO", "RODOVIARIO"], index=0)
    with l3: destino = st.text_input("Destino", value="RIO DE JANEIRO")
    with l4: qtde_cnt = st.number_input("Qtde Container", value=1, min_value=1)

    st.divider()

    # ==========================================
    # 6. VALORES CONSOLIDADOS
    # ==========================================
    memoria = st.session_state.get("f_dados_excel", {})
    
    st.markdown("### 3. Valores Consolidados")
    
    col_brl, col_tax, col_usd = st.columns([1.1, 1.5, 1], gap="large")

    # --- 1. Valores Mercadoria (R$) ---
    with col_brl:
        st.markdown("#### 🇧🇷 Mercadoria (R$)")
        v_fob_brl = st.number_input("F.O.B.", value=memoria.get("fob_brl", 0.0), format="%.2f")
        v_frete_brl = st.number_input("Frete Intl.", value=memoria.get("frete_brl", 0.0), format="%.2f")
        v_seguro_brl = st.number_input("Seguro", value=memoria.get("seguro_brl", 0.0), format="%.2f")
        v_adic_brl = st.number_input("Adicional", value=0.0, format="%.2f")
        
        st.markdown("---")
        v_cif_brl = st.number_input("VALOR CIF", value=memoria.get("cif_brl", 0.0), format="%.2f", disabled=True)

    # --- 2. Impostos Detalhados (% e Valor) ---
    with col_tax:
        st.markdown("#### 🏛️ Impostos (Nacionalização)")
        
        h1, h2 = st.columns([1, 1.5])
        h1.caption("**Alíquota Efetiva (%)**")
        h2.caption("**Valor (R$)**")

        def row_tax(label, val, base_calc):
            c_pct, c_val = st.columns([1, 1.5])
            pct_calc = _safe_div_pct(val, base_calc)
            with c_pct:
                st.number_input(f"% {label}", value=pct_calc, format="%.2f", disabled=True, key=f"p_{label}", label_visibility="collapsed")
            with c_val:
                st.number_input(f"{label}", value=val, format="%.2f", disabled=True, key=f"v_{label}", label_visibility="collapsed")
            return val

        # Dados
        val_ii = memoria.get("ii_brl", 0.0)
        val_ipi = memoria.get("ipi_brl", 0.0)
        val_pis = memoria.get("pis_brl", 0.0)
        val_cofins = memoria.get("cofins_brl", 0.0)
        val_icms = memoria.get("icms_brl", 0.0)

        # Base 1: CIF
        row_tax("II", val_ii, v_cif_brl)
        
        # Base 2: CIF + II (Para IPI)
        base_ipi_total = v_cif_brl + val_ii
        row_tax("IPI", val_ipi, base_ipi_total)
        
        # Base 3: CIF (PIS/COFINS)
        row_tax("PIS", val_pis, v_cif_brl)
        row_tax("COFINS", val_cofins, v_cif_brl)
        
        # Base 4: CIF (ICMS)
        row_tax("ICMS", val_icms, v_cif_brl)

        total_impostos = val_ii + val_ipi + val_pis + val_cofins + val_icms
        st.markdown("---")
        st.metric("Total Impostos", f"R$ {total_impostos:,.2f}")
        
        total_geral_brl = v_cif_brl + total_impostos
        st.metric("Custo Total (CIF + Impostos)", f"R$ {total_geral_brl:,.2f}")

    # --- 3. Conversão Dólar ---
    with col_usd:
        st.markdown("#### 🇺🇸 Conversão (USD)")
        taxa = st.number_input("Taxa de Conversão", min_value=0.0, value=1.0, step=0.0001, format="%.4f")
        
        if taxa > 0:
            usd_fob = v_fob_brl / taxa
            usd_frete = v_frete_brl / taxa
            usd_adic = v_adic_brl / taxa
            usd_cfr = usd_fob + usd_frete + usd_adic 
            
            st.markdown(f"**FOB:** USD {usd_fob:,.2f}")
            st.markdown(f"**Frete:** USD {usd_frete:,.2f}")
            st.divider()
            st.metric("TOTAL CFR (USD)", f"$ {usd_cfr:,.2f}")

    st.write("")
    if st.button("📤 Exportar Relatório Final", use_container_width=True):
        st.info("Exportação em breve.")