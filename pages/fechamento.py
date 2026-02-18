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
        rows.append(
            {
                "ordem": i,
                "descricao": desc,
                "valor_brl": 0.0,
                "estimado": estimado,
            }
        )
    return pd.DataFrame(rows)


def render_fechamento():
    st.title("📊 Fechamento (v1)")
    st.caption("1 fechamento por DI/DUIMP • TOTAL CFR é calculado automaticamente (FOB + Frete + Adicional).")
    st.divider()

    # ==========================================
    # 1. NOVA ÁREA: Importação e Pesquisa (Topo)
    # ==========================================
    col_import, col_search = st.columns([1, 1], gap="large")

    with col_import:
        st.markdown("#### 📥 Importar Excel")
        uploaded_file = st.file_uploader("Selecione a planilha de fechamento", type=["xlsx", "xls"])

    with col_search:
        st.markdown("#### 🔍 Verificar Existência")
        st.info("Pesquise se este fechamento já foi cadastrado no sistema.")
        busca_fechamento = st.text_input("Referência do Processo ou ID:")
        if st.button("Pesquisar", use_container_width=True):
            st.warning("A lógica de pesquisa será implementada em breve.")

    # Seção de processamento do Excel
    if uploaded_file is not None:
        try:
            # 1. Obter a DI da aba "Resumo" (Célula A6 = Linha 5, Coluna 0 no Pandas)
            df_resumo = pd.read_excel(uploaded_file, sheet_name="Resumo", header=None)
            numero_di = df_resumo.iloc[5, 0]
            
            st.success(f"✅ DI encontrada: **{numero_di}**")

            # 2. Obter os dados da aba "Rateio de Produtos"
            # O parâmetro usecols permite ler apenas as colunas exatas do Excel
            df_bruto = pd.read_excel(
                uploaded_file, 
                sheet_name="Rateio de Produtos", 
                usecols="C, D, E, I, R, U, X, AA"
            )
            
            # Renomear as colunas na ordem exata (C, D, E, I, R, U, X, AA)
            df_bruto.columns = ["NCM", "PRODUTO", "QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]
            
            # 3. Limpeza: Garantir que tudo é número (ignorar linhas em branco ou textos perdidos)
            for col in ["QUANT", "VALOR TOTAL R$", "II %", "IPI %", "PIS %", "CONFINS %"]:
                df_bruto[col] = pd.to_numeric(df_bruto[col], errors="coerce").fillna(0)
                
            # Filtra removendo linhas onde não tem Produto preenchido
            df_bruto = df_bruto.dropna(subset=["PRODUTO"])
                
            # 4. Cálculos Matemáticos de Nacionalização
            df_calc = df_bruto.copy()
            
            # II
            df_calc["II NACIONALIZACAO %"] = df_calc["II %"] / 100
            df_calc["II NACIONALIZACAO VALOR"] = df_calc["VALOR TOTAL R$"] * df_calc["II NACIONALIZACAO %"]
            
            # IPI (Base = Valor Total + II)
            df_calc["IPI NACIONALIZACAO %"] = df_calc["IPI %"] / 100
            df_calc["IPI NACIONALIZACAO VALOR"] = (df_calc["VALOR TOTAL R$"] + df_calc["II NACIONALIZACAO VALOR"]) * df_calc["IPI NACIONALIZACAO %"]
            
            # PIS
            df_calc["PIS NACIONALIZACAO %"] = df_calc["PIS %"] / 100
            df_calc["PIS NACIONALIZACAO VALOR"] = df_calc["VALOR TOTAL R$"] * df_calc["PIS NACIONALIZACAO %"]
            
            # COFINS
            df_calc["CONFINS NACIONALIZACAO %"] = df_calc["CONFINS %"] / 100
            df_calc["CONFINS NACIONALIZACAO VALOR"] = df_calc["VALOR TOTAL R$"] * df_calc["CONFINS NACIONALIZACAO %"]
            
            # 5. Organizar as colunas na ordem solicitada
            colunas_finais = [
                "PRODUTO", "NCM", "QUANT", "VALOR TOTAL R$",
                "II NACIONALIZACAO %", "II NACIONALIZACAO VALOR",
                "IPI NACIONALIZACAO %", "IPI NACIONALIZACAO VALOR",
                "PIS NACIONALIZACAO %", "PIS NACIONALIZACAO VALOR",
                "CONFINS NACIONALIZACAO %", "CONFINS NACIONALIZACAO VALOR"
            ]
            df_final = df_calc[colunas_finais]
            
            # Guardamos na memória para usar no formulário abaixo depois
            st.session_state["fechamento_di"] = str(numero_di)
            st.session_state["fechamento_df_rateio"] = df_final

            # 6. Exibir o resultado final mastigado
            with st.expander("👁️ Visualizar Rateio e Impostos Calculados", expanded=False):
                st.dataframe(
                    df_final, 
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "VALOR TOTAL R$": st.column_config.NumberColumn(format="R$ %.2f"),
                        "II NACIONALIZACAO %": st.column_config.NumberColumn(format="%.4f"),
                        "II NACIONALIZACAO VALOR": st.column_config.NumberColumn(format="R$ %.2f"),
                        "IPI NACIONALIZACAO %": st.column_config.NumberColumn(format="%.4f"),
                        "IPI NACIONALIZACAO VALOR": st.column_config.NumberColumn(format="R$ %.2f"),
                        "PIS NACIONALIZACAO %": st.column_config.NumberColumn(format="%.4f"),
                        "PIS NACIONALIZACAO VALOR": st.column_config.NumberColumn(format="R$ %.2f"),
                        "CONFINS NACIONALIZACAO %": st.column_config.NumberColumn(format="%.4f"),
                        "CONFINS NACIONALIZACAO VALOR": st.column_config.NumberColumn(format="R$ %.2f"),
                    }
                )
                
        except Exception as e:
            st.error(f"Erro ao ler a planilha. Verifique se as abas 'Resumo' e 'Rateio de Produtos' existem no arquivo. Detalhe técnico: {e}")
    st.divider()

    # ==========================================
    # 2. Sidebar: escolher fechamento existente 
    # ==========================================
    with st.sidebar:
        st.subheader("Fechamentos")
        df_list = list_fechamentos(limit=50)

        options = ["➕ Novo fechamento"]
        map_id = {}

        for _, r in df_list.iterrows():
            label = f'{int(r["id"])} • {r["data"]} • {r["empresa"]} • {r["cliente"]} • {r["referencia"]}'
            options.append(label)
            map_id[label] = int(r["id"])

        choice = st.selectbox("Selecionar", options, index=0)
        selected_id = map_id.get(choice)

    # ===== Carregar dados se existe
    initial: Dict[str, Any] = {}
    if selected_id:
        loaded = get_fechamento(selected_id)
        if loaded:
            initial = loaded

    # ==========================================
    # 3. MANTIDO: Form principal (Colunas A e B)
    # ==========================================
    colA, colB = st.columns([1, 1], gap="large")

    with colA:
        st.subheader("Identificação (manual / auto)")
        empresa = st.text_input("Empresa", value=str(initial.get("empresa", "")))
        cliente = st.text_input("Cliente", value=str(initial.get("cliente", "")))
        referencia = st.text_input("Referência", value=str(initial.get("referencia", "")))

        data_fech = st.date_input(
            "Data",
            value=initial.get("data") or date.today(),
        )

        st.subheader("Valores base (manual / auto)")
        valor_fob = st.number_input("Valor FOB (USD)", min_value=0.0, value=float(initial.get("valor_fob_usd") or 0), step=10.0)
        frete = st.number_input("Frete (USD)", min_value=0.0, value=float(initial.get("frete_usd") or 0), step=10.0)
        adicional = st.number_input("Adicional (USD)", min_value=0.0, value=float(initial.get("adicional_usd") or 0), step=10.0)
        seguro = st.number_input("Seguro (USD)", min_value=0.0, value=float(initial.get("seguro_usd") or 0), step=10.0)
        taxa = st.number_input("Taxa de conversão (USD→BRL)", min_value=0.0, value=float(initial.get("taxa_conversao") or 0), step=0.01, format="%.6f")

        total_cfr = Decimal(str(valor_fob)) + Decimal(str(frete)) + Decimal(str(adicional))
        st.metric("TOTAL CFR (USD) = FOB + Frete + Adicional", f"{total_cfr:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        total_cfr_brl = total_cfr * Decimal(str(taxa or 0))
        st.metric("TOTAL CFR (BRL) (estimado)", f"{total_cfr_brl:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    with colB:
        st.subheader("Logística (manual / auto)")
        origem = st.text_input("Origem", value=str(initial.get("origem") or ""))
        modal = st.text_input("Modal", value=str(initial.get("modal") or ""))
        destino = st.text_input("Destino", value=str(initial.get("destino") or ""))
        qtde_container = st.number_input("Qtde de container", min_value=0, value=int(initial.get("qtde_container") or 0), step=1)
        bl_awb = st.text_input("BL/AWB", value=str(initial.get("bl_awb") or ""))

        st.subheader("Despesas gerais (manual / auto)")
        if selected_id:
            df_desp = get_despesas(selected_id)
        else:
            df_desp = pd.DataFrame()

        df_desp = _ensure_despesas_template(df_desp)

        edited = st.data_editor(
            df_desp,
            use_container_width=True,
            hide_index=True,
            column_config={
                "ordem": st.column_config.NumberColumn("Ordem", width="small"),
                "descricao": st.column_config.TextColumn("Descrição", width="large"),
                "valor_brl": st.column_config.NumberColumn("Valor (BRL)", format="R$ %.2f"),
                "estimado": st.column_config.CheckboxColumn("Estimado", width="small"),
            },
            disabled=["ordem"],  # ordem fixa no template
            key="despesas_editor",
        )

        soma_despesas = Decimal("0")
        for _, r in edited.iterrows():
            soma_despesas += _to_decimal(r.get("valor_brl"))

        st.metric("Total despesas gerais (BRL)", f"{soma_despesas:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

    st.divider()

    # ==========================================
    # 4. Ações (Salvar, Limpar, Exportar)
    # ==========================================
    col1, col2, col3 = st.columns([1, 1, 2])

    with col1:
        salvar = st.button("💾 Salvar fechamento", type="primary", use_container_width=True)

    with col2:
        reset = st.button("🧹 Limpar formulário", use_container_width=True)
        
    with col3:
        # NOVO: Botão de exportar
        exportar = st.button("📤 Exportar Fechamento", use_container_width=True)

    # Lógicas de clique de botão
    if reset:
        st.session_state.pop("despesas_editor", None)
        st.rerun()
        
    if exportar:
        st.info("A lógica de exportação (PDF/Excel) será implementada em breve.")

    if salvar:
        # validação mínima
        if not empresa.strip() or not cliente.strip() or not referencia.strip():
            st.error("Preencha Empresa, Cliente e Referência.")
            st.stop()

        payload = {
            "id": selected_id,
            "id_di": initial.get("id_di"),  # por enquanto
            "empresa": empresa.strip(),
            "cliente": cliente.strip(),
            "referencia": referencia.strip(),
            "data": data_fech,
            "valor_fob_usd": float(valor_fob or 0),
            "frete_usd": float(frete or 0),
            "adicional_usd": float(adicional or 0),
            "seguro_usd": float(seguro or 0),
            "taxa_conversao": float(taxa or 0),
            "origem": origem.strip() or None,
            "modal": modal.strip() or None,
            "destino": destino.strip() or None,
            "qtde_container": int(qtde_container or 0),
            "bl_awb": bl_awb.strip() or None,
        }

        new_id = upsert_fechamento(payload)

        despesas_to_save: List[Dict[str, Any]] = []
        for _, r in edited.iterrows():
            despesas_to_save.append(
                {
                    "ordem": int(r.get("ordem", 0) or 0),
                    "descricao": str(r.get("descricao", "")).strip(),
                    "valor_brl": float(r.get("valor_brl", 0) or 0),
                    "estimado": bool(r.get("estimado", False)),
                }
            )

        replace_despesas(new_id, despesas_to_save)

        st.success(f"Fechamento salvo com sucesso (ID {new_id}).")
        st.rerun()

    st.divider()

    # ==========================================
    # 5. NOVA ÁREA: Histórico de Fechamentos
    # ==========================================
    with st.expander("📋 Histórico de Relatórios de Fechamentos", expanded=False):
        st.caption("Abaixo estarão listados outros relatórios e consolidações históricas.")
        # Placeholder visual
        st.dataframe(pd.DataFrame({
            "Processo": ["REF-001", "REF-002"],
            "Cliente": ["Cliente A", "Cliente B"],
            "Data": ["18/02/2026", "15/02/2026"],
            "Status": ["Finalizado", "Em Análise"]
        }), hide_index=True, use_container_width=True)