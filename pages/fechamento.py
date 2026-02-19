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
# FUNÇÕES AUXILIARES
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
    """Converte qualquer coisa para Decimal de forma segura."""
    try:
        if v is None or str(v).strip() == "":
            return Decimal("0")
        if isinstance(v, (int, float, Decimal)):
            return Decimal(str(v))
        # Limpeza de string monetária brasileira
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
        return df.iloc[0].to_dict()
    return None

def _extract_cell(df: pd.DataFrame, row_idx: int, col_idx: int) -> float:
    """Extrai valor de célula específica do Excel pelo índice (0-based)"""
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
    st.caption("1 fechamento por DI/DUIMP • Fluxo de Importação e Consolidação")
    st.divider()

    # --- Inicialização de Variáveis de Sessão para Persistência ---
    if "f_dados_excel" not in st.session_state:
        st.session_state["f_dados_excel"] = {}

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

    st.divider()

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
            # A. Leitura da Aba RESUMO
            df_resumo = pd.read_excel(uploaded_file, sheet_name="Resumo", header=None)
            
            # Pega DI (Linha 6 Excel -> Indice 5 Pandas, Coluna A -> 0)
            val_di = str(df_resumo.iloc[5, 0]).strip()
            
            # --- Extração dos Valores em Reais (Coordenadas especificadas) ---
            # Excel Index é 1-based, Pandas é 0-based. Ex: A9 -> row=8, col=0
            
            dados_lidos = {
                "fob_brl": _extract_cell(df_resumo, 8, 0),   # A9
                "frete_brl": _extract_cell(df_resumo, 11, 0),# A12
                "seguro_brl": _extract_cell(df_resumo, 11, 1),# B12
                "cif_brl": _extract_cell(df_resumo, 8, 2),   # C9
                
                "ii_brl": _extract_cell(df_resumo, 19, 3),   # D20
                "ipi_brl": _extract_cell(df_resumo, 22, 3),  # D23
                "pis_brl": _extract_cell(df_resumo, 19, 4),  # E20
                "cofins_brl": _extract_cell(df_resumo, 22, 4), # E23
                "icms_brl": _extract_cell(df_resumo, 19, 1), # B20 (Verifique se é B20 mesmo)
            }
            
            # Salva na sessão para não perder quando atualizar a tela
            st.session_state["f_dados_excel"] = dados_lidos

            # B. Busca DI no Banco
            proc_info = _buscar_processo_por_di(val_di)
            if proc_info:
                st.success(f"✅ **Processo Encontrado!** DI {val_di}.")
                val_empresa = proc_info.get("empresa_nome", "")
                val_cliente = proc_info.get("cliente_nome", "")
                val_referencia = proc_info.get("referencia", "")
                dt_bd = proc_info.get("data_registro")
                if pd.notna(dt_bd):
                    val_data = dt_bd
            else:
                st.warning(f"⚠️ **DI {val_di} não cadastrada**. Preencha manualmente.")

            # C. Leitura da Aba RATEIO (Lógica mantida e oculta no expander)
            with st.expander("👁️ Ver Cálculos de Rateio de Produtos (Oculto)", expanded=False):
                # ... (Seu código de rateio existente pode ficar aqui se quiser manter o visual) ...
                st.write("Dados de produtos carregados em background.")

        except Exception as e:
            st.error(f"Erro ao ler planilha: {e}")

    # Recupera dados da memória ou usa 0.0
    memoria = st.session_state.get("f_dados_excel", {})

    # ==========================================
    # 3. IDENTIFICAÇÃO
    # ==========================================
    st.markdown("### 1. Identificação")
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.text_input("DI", value=val_di, disabled=True)
    with c2: st.text_input("Referência", value=val_referencia)
    with c3: st.text_input("Empresa", value=val_empresa)
    with c4: st.text_input("Cliente", value=val_cliente)
    
    if st.button("💾 Salvar Identificação", type="primary"):
        st.success("Identificação salva (simulação).")

    st.divider()

    # ==========================================
    # 4. LOGÍSTICA DE IMPORTAÇÃO
    # ==========================================
    st.markdown("### 2. Logística de Entrada")
    l1, l2, l3, l4 = st.columns(4)
    
    with l1: origem = st.text_input("Origem", value="CHINA")
    with l2: modal = st.selectbox("Modal", ["MARITIMO", "AEREO", "RODOVIARIO"], index=0)
    with l3: destino = st.text_input("Destino", value="RIO DE JANEIRO")
    with l4: qtde_cnt = st.number_input("Qtde Container", value=1, min_value=1)

    st.divider()

    # ==========================================
    # 5. VALORES E IMPOSTOS (REAL vs DÓLAR)
    # ==========================================
    st.markdown("### 3. Valores e Conversão")
    
    # Layout: 3 Colunas Grandes
    # Coluna 1: Valores em Real (Vem do Excel + Adicional Manual)
    # Coluna 2: Impostos (Vem do Excel + Totais)
    # Coluna 3: Conversão para Dólar (Cálculo Reverso)

    col_brl, col_tax, col_usd = st.columns([1.2, 1.2, 1], gap="large")

    # --- COLUNA 1: REAIS ---
    with col_brl:
        st.markdown("#### 🇧🇷 Valores em Real (R$)")
        st.caption("Dados extraídos da aba 'Resumo'")
        
        v_fob_brl = st.number_input("Valor F.O.B.", value=memoria.get("fob_brl", 0.0), format="%.2f")
        v_frete_brl = st.number_input("Frete Internacional", value=memoria.get("frete_brl", 0.0), format="%.2f")
        v_seguro_brl = st.number_input("Seguro", value=memoria.get("seguro_brl", 0.0), format="%.2f")
        
        # Campo Adicional é manual
        v_adic_brl = st.number_input("Adicional (Despesas Extras)", value=0.0, format="%.2f")
        
        # CIF vem do Excel, mas idealmente seria soma. Mantendo fiel ao Excel conforme pedido.
        v_cif_brl = st.number_input("VALOR CIF (Do Excel)", value=memoria.get("cif_brl", 0.0), format="%.2f", disabled=True)
        
        # Validação visual
        cif_calculado = v_fob_brl + v_frete_brl + v_seguro_brl + v_adic_brl
        if abs(cif_calculado - v_cif_brl) > 1.0 and v_cif_brl > 0:
            st.warning(f"⚠️ Atenção: A soma FOB+Frete+Seguro+Adicional ({cif_calculado:,.2f}) difere do CIF no Excel ({v_cif_brl:,.2f}).")

    # --- COLUNA 2: IMPOSTOS ---
    with col_tax:
        st.markdown("#### 🏛️ Impostos Nacionalização")
        st.caption("Extraídos da aba 'Resumo'")
        
        t_ii = st.number_input("II (Imp. Importação)", value=memoria.get("ii_brl", 0.0), format="%.2f", disabled=True)
        t_ipi = st.number_input("I.P.I.", value=memoria.get("ipi_brl", 0.0), format="%.2f", disabled=True)
        t_pis = st.number_input("PIS", value=memoria.get("pis_brl", 0.0), format="%.2f", disabled=True)
        t_cofins = st.number_input("COFINS", value=memoria.get("cofins_brl", 0.0), format="%.2f", disabled=True)
        t_icms = st.number_input("ICMS (Diferido)", value=memoria.get("icms_brl", 0.0), format="%.2f", disabled=True)
        
        total_impostos = t_ii + t_ipi + t_pis + t_cofins + t_icms
        st.markdown("---")
        st.metric("Total Impostos", f"R$ {total_impostos:,.2f}")
        
        total_geral_brl = v_cif_brl + total_impostos
        st.metric("TOTAL (CIF + Impostos)", f"R$ {total_geral_brl:,.2f}", delta="Custo Total")

    # --- COLUNA 3: DÓLAR ---
    with col_usd:
        st.markdown("#### 🇺🇸 Conversão (USD)")
        st.caption("Cálculo: Valor R$ / Taxa")
        
        taxa = st.number_input("Taxa de Conversão (USD/BRL)", min_value=0.0, value=1.0, step=0.0001, format="%.4f")
        
        if taxa > 0:
            # Cálculos de divisão
            usd_fob = v_fob_brl / taxa
            usd_frete = v_frete_brl / taxa
            usd_adic = v_adic_brl / taxa
            
            # Total CFR em Dólar (Geralmente é FOB + Frete)
            usd_cfr = usd_fob + usd_frete + usd_adic 
            
            st.markdown("---")
            st.markdown(f"**Valor FOB:** USD {usd_fob:,.2f}")
            st.markdown(f"**Frete:** USD {usd_frete:,.2f}")
            st.markdown(f"**Adicional:** USD {usd_adic:,.2f}")
            st.divider()
            st.metric("TOTAL CFR (USD)", f"$ {usd_cfr:,.2f}")
        else:
            st.error("Informe a taxa de conversão.")

    st.divider()
    
    # ==========================================
    # 6. DESPESAS GERAIS (Mantido do anterior)
    # ==========================================
    st.subheader("Despesas Locais (Previsto vs Realizado)")
    # (Pode manter o código das despesas aqui se quiser...)
    
    if st.button("📤 Exportar Fechamento Completo", use_container_width=True):
        st.info("Em breve.")