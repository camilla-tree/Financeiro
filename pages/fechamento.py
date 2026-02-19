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
        # Garante que a data venha como objeto date
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

# ==========================================
# RENDERIZAÇÃO DA TELA
# ==========================================

def render_fechamento():
    st.title("📊 Fechamento (v1)")
    st.caption("Fluxo de Importação: Identificação > Rateio > Valores > Logística")
    st.divider()

    # Variáveis de Estado (Persistência)
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
            # A. Leitura da Aba RESUMO (Valores Totais e DI)
            df_resumo = pd.read_excel(uploaded_file, sheet_name="Resumo", header=None)
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

            # B. Busca DI no Banco (Identificação)
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

            # C. Leitura da Aba RATEIO (Produtos e Impostos Detalhados)
            df_bruto = pd.read_excel(uploaded_file, sheet_name="Rateio de Produtos", usecols="C, D, E, I, R, U, X, AA")
            df_bruto.columns = ["NCM", "PRODUTO", "QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]
            
            for col in ["QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]:
                df_bruto[col] = pd.to_numeric(df_bruto[col], errors="coerce").fillna(0)
            
            df_bruto = df_bruto.dropna(subset=["PRODUTO"])
            
            # Cálculos Matemáticos (Mantendo % visual e calculando valor)
            df_calc = df_bruto.copy()
            
            # II
            df_calc["II %"] = df_calc["II %"]
            df_calc["II VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["II %"] / 100)
            
            # IPI (Base = Valor + II)
            df_calc["IPI %"] = df_calc["IPI %"]
            base_ipi = df_calc["VALOR TOTAL R$"] + df_calc["II VALOR"]
            df_calc["IPI VALOR"] = base_ipi * (df_calc["IPI %"] / 100)
            
            # PIS e COFINS
            df_calc["PIS %"] = df_calc["PIS %"]
            df_calc["PIS VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["PIS %"] / 100)
            
            df_calc["COFINS %"] = df_calc["CONFINS %"]
            df_calc["COFINS VALOR"] = df_calc["VALOR TOTAL R$"] * (df_calc["CONFINS %"] / 100)

            colunas_finais = [
                "PRODUTO", "NCM", "QUANT", "VALOR TOTAL R$",
                "II %", "II VALOR",
                "IPI %", "IPI VALOR",
                "PIS %", "PIS VALOR",
                "COFINS %", "COFINS VALOR"
            ]
            st.session_state["f_df_rateio"] = df_calc[colunas_finais]

        except Exception as e:
            st.error(f"Erro ao processar planilha: {e}")

    # ==========================================
    # 3. IDENTIFICAÇÃO (Com Data)
    # ==========================================
    st.markdown("### 1. Identificação")
    
    # Layout ajustado com Data
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
    # 4. TABELA DE RATEIO E IMPOSTOS (Visível)
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
                    "VALOR TOTAL R$": st.column_config.NumberColumn("Valor Produto", format="R$ %.2f"),
                    
                    # Colunas de Impostos: % e Valor lado a lado
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
            st.info("Aguardando importação do Excel para exibir os produtos.")

    st.divider()

    # ==========================================
    # 5. LOGÍSTICA DE IMPORTAÇÃO
    # ==========================================
    st.markdown("### 2. Logística de Entrada")
    l1, l2, l3, l4 = st.columns(4)
    with l1: origem = st.text_input("Origem", value="CHINA")
    with l2: modal = st.selectbox("Modal", ["MARITIMO", "AEREO", "RODOVIARIO"], index=0)
    with l3: destino = st.text_input("Destino", value="RIO DE JANEIRO")
    with l4: qtde_cnt = st.number_input("Qtde Container", value=1, min_value=1)

    st.divider()

    # ==========================================
    # 6. VALORES TOTAIS (Resumo)
    # ==========================================
    memoria = st.session_state.get("f_dados_excel", {})
    
    st.markdown("### 3. Valores Consolidados")
    col_brl, col_tax, col_usd = st.columns([1.2, 1.2, 1], gap="large")

    with col_brl:
        st.markdown("#### 🇧🇷 Valores da Mercadoria")
        v_fob_brl = st.number_input("Valor F.O.B. (R$)", value=memoria.get("fob_brl", 0.0), format="%.2f")
        v_frete_brl = st.number_input("Frete Intl. (R$)", value=memoria.get("frete_brl", 0.0), format="%.2f")
        v_seguro_brl = st.number_input("Seguro (R$)", value=memoria.get("seguro_brl", 0.0), format="%.2f")
        v_adic_brl = st.number_input("Adicional / Despesas Extras (R$)", value=0.0, format="%.2f")
        
        v_cif_brl = st.number_input("VALOR CIF TOTAL (Excel)", value=memoria.get("cif_brl", 0.0), format="%.2f", disabled=True)

    # --- COLUNA 2: IMPOSTOS (Com % Calculado) ---
    with col_tax:
        st.markdown("#### 🏛️ Totais de Impostos")
        
        # Recupera os valores da memória (igual antes)
        t_ii = memoria.get("ii_brl", 0.0)
        t_ipi = memoria.get("ipi_brl", 0.0)
        t_pis = memoria.get("pis_brl", 0.0)
        t_cofins = memoria.get("cofins_brl", 0.0)
        t_icms = memoria.get("icms_brl", 0.0)

        # Pequena função auxiliar interna para calcular % sem divisão por zero
        def _calc_pct(val, base):
            return (val / base * 100) if base > 0 else 0.0

        # Cria colunas para cabeçalho
        h1, h2 = st.columns([0.8, 1.2])
        h1.caption("**% Calc.**")
        h2.caption("**Valor (R$)**")

        # --- II (Base = CIF) ---
        c1, c2 = st.columns([0.8, 1.2])
        c1.text_input("II%", value=f"{_calc_pct(t_ii, v_cif_brl):.2f}%", disabled=True, label_visibility="collapsed")
        c2.number_input("II", value=t_ii, format="%.2f", disabled=True, label_visibility="collapsed")

        # --- IPI (Base = CIF + II) ---
        c1, c2 = st.columns([0.8, 1.2])
        base_ipi = v_cif_brl + t_ii
        c1.text_input("IPI%", value=f"{_calc_pct(t_ipi, base_ipi):.2f}%", disabled=True, label_visibility="collapsed")
        c2.number_input("IPI", value=t_ipi, format="%.2f", disabled=True, label_visibility="collapsed")

        # --- PIS (Base = CIF) ---
        c1, c2 = st.columns([0.8, 1.2])
        c1.text_input("PIS%", value=f"{_calc_pct(t_pis, v_cif_brl):.2f}%", disabled=True, label_visibility="collapsed")
        c2.number_input("PIS", value=t_pis, format="%.2f", disabled=True, label_visibility="collapsed")

        # --- COFINS (Base = CIF) ---
        c1, c2 = st.columns([0.8, 1.2])
        c1.text_input("COF%", value=f"{_calc_pct(t_cofins, v_cif_brl):.2f}%", disabled=True, label_visibility="collapsed")
        c2.number_input("COF", value=t_cofins, format="%.2f", disabled=True, label_visibility="collapsed")

        # --- ICMS (Base = CIF - apenas referência visual) ---
        c1, c2 = st.columns([0.8, 1.2])
        c1.text_input("ICMS%", value=f"{_calc_pct(t_icms, v_cif_brl):.2f}%", disabled=True, label_visibility="collapsed")
        c2.number_input("ICMS", value=t_icms, format="%.2f", disabled=True, label_visibility="collapsed")
        
        # Totais finais
        total_impostos = t_ii + t_ipi + t_pis + t_cofins + t_icms
        st.markdown("---")
        st.metric("Total Impostos", f"R$ {total_impostos:,.2f}")
        
        total_geral_brl = v_cif_brl + total_impostos
        st.metric("TOTAL (CIF + Impostos)", f"R$ {total_geral_brl:,.2f}", delta="Custo Total")

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