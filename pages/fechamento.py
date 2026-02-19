from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from db import (
    list_fechamentos,
    get_fechamento,
    upsert_fechamento,
    get_despesas,
    replace_despesas,
    fetch_df_cached, # <- NOVO: Importamos para fazer a busca da DI
)

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
        if v is None or v == "":
            return Decimal("0")
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v).replace(",", "."))
    except (InvalidOperation, ValueError):
        return Decimal("0")

def _ensure_despesas_template(existing: pd.DataFrame) -> pd.DataFrame:
    if existing is not None and not existing.empty:
        return existing

    rows = []
    for i, (desc, estimado) in enumerate(DESPESAS_TEMPLATE, start=1):
        rows.append({"ordem": i, "descricao": desc, "valor_brl": 0.0, "estimado": estimado})
    return pd.DataFrame(rows)

# Função para buscar os dados do processo pela DI
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

def render_fechamento():
    st.title("📊 Fechamento (v1)")
    st.caption("1 fechamento por DI/DUIMP • Fluxo de Importação e Consolidação")
    st.divider()

    # ==========================================
    # 1. ÁREA DE IMPORTAÇÃO
    # ==========================================
    col_import, col_search = st.columns([1, 1], gap="large")

    with col_import:
        st.markdown("#### 📥 Importar Excel")
        uploaded_file = st.file_uploader("Selecione a planilha de fechamento (Rateio/Resumo)", type=["xlsx", "xls"])

    with col_search:
        st.markdown("#### 🔍 Histórico de Fechamentos")
        st.info("Aqui você poderá pesquisar se um fechamento já foi concluído anteriormente.")
        st.text_input("Pesquisar por Referência / DI:")

    st.divider()

    # Variáveis padrão para o formulário de Identificação
    val_di = ""
    val_empresa = ""
    val_cliente = ""
    val_referencia = ""
    val_data = date.today()
    status_msg = None

    # ==========================================
    # 2. PROCESSAMENTO DO EXCEL E BUSCA NO BANCO
    # ==========================================
    if uploaded_file is not None:
        try:
            # 1. Lê a DI da aba Resumo
            df_resumo = pd.read_excel(uploaded_file, sheet_name="Resumo", header=None)
            val_di = str(df_resumo.iloc[5, 0]).strip()
            
            # 2. Busca no Banco de Dados
            proc_info = _buscar_processo_por_di(val_di)
            
            if proc_info:
                status_msg = st.success(f"✅ **Processo Encontrado!** DI {val_di} carregada do banco.")
                val_empresa = proc_info.get("empresa_nome", "")
                val_cliente = proc_info.get("cliente_nome", "")
                val_referencia = proc_info.get("referencia", "")
                # Se houver data válida no banco, usamos ela
                dt_bd = proc_info.get("data_registro")
                if pd.notna(dt_bd):
                    val_data = dt_bd
            else:
                status_msg = st.warning(f"⚠️ **Processo não cadastrado** para a DI {val_di}. Preencha manualmente os dados de identificação abaixo.")

            # 3. Lê e calcula o Rateio (Aba Rateio de Produtos)
            df_bruto = pd.read_excel(uploaded_file, sheet_name="Rateio de Produtos", usecols="C, D, E, I, R, U, X, AA")
            df_bruto.columns = ["NCM", "PRODUTO", "QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]
            
            for col in ["QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]:
                df_bruto[col] = pd.to_numeric(df_bruto[col], errors="coerce").fillna(0)
            df_bruto = df_bruto.dropna(subset=["PRODUTO"])
                
            df_calc = df_bruto.copy()
            df_calc["II NACIONALIZACAO %"] = df_calc["II %"] / 100
            df_calc["II NACIONALIZACAO VALOR"] = df_calc["VALOR TOTAL R$"] * df_calc["II NACIONALIZACAO %"]
            df_calc["IPI NACIONALIZACAO %"] = df_calc["IPI %"] / 100
            df_calc["IPI NACIONALIZACAO VALOR"] = (df_calc["VALOR TOTAL R$"] + df_calc["II NACIONALIZACAO VALOR"]) * df_calc["IPI NACIONALIZACAO %"]
            df_calc["PIS NACIONALIZACAO %"] = df_calc["PIS %"] / 100
            df_calc["PIS NACIONALIZACAO VALOR"] = df_calc["VALOR TOTAL R$"] * df_calc["PIS NACIONALIZACAO %"]
            df_calc["CONFINS NACIONALIZACAO %"] = df_calc["CONFINS %"] / 100
            df_calc["CONFINS NACIONALIZACAO VALOR"] = df_calc["VALOR TOTAL R$"] * df_calc["CONFINS NACIONALIZACAO %"]
            
            colunas_finais = [
                "PRODUTO", "NCM", "QUANT", "VALOR TOTAL R$",
                "II NACIONALIZACAO %", "II NACIONALIZACAO VALOR",
                "IPI NACIONALIZACAO %", "IPI NACIONALIZACAO VALOR",
                "PIS NACIONALIZACAO %", "PIS NACIONALIZACAO VALOR",
                "CONFINS NACIONALIZACAO %", "CONFINS NACIONALIZACAO VALOR"
            ]
            df_final = df_calc[colunas_finais]

            with st.expander("👁️ Visualizar Rateio de Produtos Calculado", expanded=True):
                st.dataframe(df_final, use_container_width=True, hide_index=True)
                
        except Exception as e:
            st.error(f"Erro ao ler a planilha: {e}")

    # ==========================================
    # 3. ETAPA DE IDENTIFICAÇÃO (Com preenchimento Auto/Manual)
    # ==========================================
    st.markdown("### 1. Identificação do Processo")
    
    colA1, colA2 = st.columns(2, gap="large")
    with colA1:
        di_input = st.text_input("DI (Declaração de Importação)", value=val_di)
        empresa_input = st.text_input("Empresa", value=val_empresa)
        cliente_input = st.text_input("Cliente", value=val_cliente)
    with colA2:
        referencia_input = st.text_input("Referência (Ref Tree)", value=val_referencia)
        data_input = st.date_input("Data de Registro", value=val_data)
        
    # Botão isolado apenas para confirmar esta etapa
    if st.button("💾 Salvar Identificação", type="primary"):
        if not empresa_input or not cliente_input or not referencia_input or not di_input:
            st.error("Preencha DI, Empresa, Cliente e Referência para prosseguir.")
        else:
            # Aqui poderemos gravar o cabeçalho no banco futuramente
            st.success(f"Identificação da referência **{referencia_input}** confirmada! Pode prosseguir para os valores.")

    st.divider()

    # ==========================================
    # 4. VALORES BASE E LOGÍSTICA (Mantidos do original)
    # ==========================================
    st.markdown("### 2. Valores e Logística")
    colB1, colB2 = st.columns([1, 1], gap="large")

    with colB1:
        st.subheader("Valores base")
        valor_fob = st.number_input("Valor FOB (USD)", min_value=0.0, step=10.0)
        frete = st.number_input("Frete (USD)", min_value=0.0, step=10.0)
        adicional = st.number_input("Adicional (USD)", min_value=0.0, step=10.0)
        taxa = st.number_input("Taxa de conversão (USD→BRL)", min_value=0.0, step=0.01, format="%.6f")

        total_cfr = Decimal(str(valor_fob)) + Decimal(str(frete)) + Decimal(str(adicional))
        st.metric("TOTAL CFR (USD)", f"{total_cfr:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    with colB2:
        st.subheader("Despesas gerais")
        df_desp = _ensure_despesas_template(pd.DataFrame())
        edited_desp = st.data_editor(
            df_desp, use_container_width=True, hide_index=True,
            column_config={
                "ordem": st.column_config.NumberColumn("Ordem", disabled=True, width="small"),
                "descricao": st.column_config.TextColumn("Descrição", width="large"),
                "valor_brl": st.column_config.NumberColumn("Valor (BRL)", format="R$ %.2f"),
                "estimado": st.column_config.CheckboxColumn("Estimado", width="small"),
            },
            key="despesas_editor"
        )

        soma_despesas = sum(_to_decimal(r.get("valor_brl")) for _, r in edited_desp.iterrows())
        st.metric("Total despesas gerais (BRL)", f"{soma_despesas:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.write("")
    if st.button("📤 Exportar Fechamento Completo", use_container_width=True):
        st.info("Função de exportação do consolidado em breve.")