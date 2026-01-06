from __future__ import annotations

from datetime import date
import json
from typing import Optional, Dict, Any, Tuple

import pandas as pd
import streamlit as st

from db import fetch_df_cached, fresh_conn, run_sql_returning_id
from audit import log_action


# =========================
# Excel -> DF (Rateio)
# =========================
EXPECTED_SHEET = "Rateio de Produtos"

RATEIO_COLS_ORDER = [
    "Adição",
    "Código",
    "NCM",
    "Produto",
    "Qtde",
    "Unidade de Medida",
    "PU (R$)",
    "Valor (R$)",
    "Outras Despesas",
    "Frete",
    "Seguro",
    "Base de Cálculo II",
    "Alíquota (%) II",
    "Valor II",
    "Base de Cálculo IPI",
    "Alíquota (%) IPI",
    "Valor IPI",
    "Base de Cálculo PIS",
    "Alíquota (%) PIS",
    "Valor PIS",
    "Base de Cálculo COFINS",
    "Alíquota (%) COFINS",
    "Valor COFINS",
    "Total",
]


def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def ler_rateio_excel(uploaded_file, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(uploaded_file, sheet_name=sheet_name)
    df.columns = [str(c).strip() for c in df.columns]

    # mantém colunas conhecidas na ordem esperada (se existirem)
    keep = [c for c in RATEIO_COLS_ORDER if c in df.columns]
    if not keep:
        # fallback: deixa tudo, mas isso é raro
        keep = list(df.columns)
    df = df[keep].copy()

    # normalização numérica
    numeric_cols = [
        "Qtde",
        "PU (R$)",
        "Valor (R$)",
        "Outras Despesas",
        "Frete",
        "Seguro",
        "Base de Cálculo II",
        "Alíquota (%) II",
        "Valor II",
        "Base de Cálculo IPI",
        "Alíquota (%) IPI",
        "Valor IPI",
        "Base de Cálculo PIS",
        "Alíquota (%) PIS",
        "Valor PIS",
        "Base de Cálculo COFINS",
        "Alíquota (%) COFINS",
        "Valor COFINS",
        "Total",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = _to_num(df[c])

    # converte alíquotas para decimal (ex: 13,5 vira 0.135)
    for c in ["Alíquota (%) II", "Alíquota (%) IPI", "Alíquota (%) PIS", "Alíquota (%) COFINS"]:
        if c in df.columns:
            df[c] = df[c] / 100.0

    # remove linhas vazias
    if "Produto" in df.columns:
        df = df[df["Produto"].notna()]
    if "Total" in df.columns:
        df = df[df["Total"].fillna(0) != 0].copy()

    df = df.reset_index(drop=True)
    return df


def resumir_rateio(df: pd.DataFrame) -> Dict[str, Any]:
    def s(col: str) -> float:
        if col not in df.columns:
            return 0.0
        return float(df[col].fillna(0).sum())

    resumo = {
        "qtd_itens": int(len(df)),
        "totais": {
            "valor_produtos": round(s("Valor (R$)"), 2),
            "outras_despesas": round(s("Outras Despesas"), 2),
            "frete": round(s("Frete"), 2),
            "seguro": round(s("Seguro"), 2),
            "valor_ii": round(s("Valor II"), 2),
            "valor_ipi": round(s("Valor IPI"), 2),
            "valor_pis": round(s("Valor PIS"), 2),
            "valor_cofins": round(s("Valor COFINS"), 2),
            "total_geral": round(s("Total"), 2),
        },
        "por_ncm": {},
        "por_adicao": {},
    }

    if "NCM" in df.columns and "Total" in df.columns:
        resumo["por_ncm"] = (
            df.groupby("NCM")["Total"]
            .sum()
            .sort_values(ascending=False)
            .head(50)
            .round(2)
            .to_dict()
        )

    if "Adição" in df.columns and "Total" in df.columns:
        resumo["por_adicao"] = (
            df.groupby("Adição")["Total"]
            .sum()
            .sort_values(ascending=False)
            .round(2)
            .to_dict()
        )

    return resumo


# =========================
# DB helpers (selects)
# =========================
@st.cache_data(ttl=60)
def get_empresas() -> pd.DataFrame:
    return fetch_df_cached("SELECT id, nome FROM empresa ORDER BY nome")


@st.cache_data(ttl=60)
def get_clientes() -> pd.DataFrame:
    return fetch_df_cached("SELECT id, nome FROM cliente ORDER BY nome")


@st.cache_data(ttl=60)
def get_processos() -> pd.DataFrame:
    # processo tem: id, referencia, empresa_id, cliente_id, data_registro, di, ...
    return fetch_df_cached(
        """
        SELECT p.id, p.referencia, p.empresa_id, p.cliente_id, p.di, p.data_registro,
               e.nome as empresa_nome, c.nome as cliente_nome
        FROM processo p
        JOIN empresa e ON e.id = p.empresa_id
        JOIN cliente c ON c.id = p.cliente_id
        ORDER BY p.data_registro DESC, p.referencia
        """
    )


@st.cache_data(ttl=60)
def listar_fechamentos(empresa_id: int, limit: int = 50) -> pd.DataFrame:
    return fetch_df_cached(
        """
        SELECT f.id, f.data_registro, e.nome as empresa,
               f.referencia_processo, f.di, f.cliente_di_texto,
               f.fechamento_valor, f.comissao_1, f.comissao_2,
               f.rateio_total_geral,
               f.criado_em
        FROM fechamento f
        JOIN empresa e ON e.id = f.empresa_id
        WHERE f.empresa_id = %s
        ORDER BY f.data_registro DESC, f.id DESC
        LIMIT %s
        """,
        (empresa_id, limit),
    )


# =========================
# Main page
# =========================
def render_fechamento():
    st.subheader("Fechamento • MVP (Resumo + Import Excel)")

    df_emp = get_empresas()
    if df_emp.empty:
        st.warning("Cadastre empresas antes (Admin).")
        return

    df_cli = get_clientes()
    df_proc = get_processos()

    colA, colB, colC = st.columns([2, 2, 2])
    with colA:
        emp_nome = st.selectbox("Empresa", df_emp["nome"].tolist(), key="fech_emp")
        empresa_id = int(df_emp.loc[df_emp["nome"] == emp_nome, "id"].iloc[0])
    with colB:
        data_registro = st.date_input("Data de registro", value=date.today(), key="fech_dt")
    with colC:
        usar_processo = st.checkbox("Vincular a um processo?", value=True, key="fech_use_proc")

    processo_id: Optional[int] = None
    referencia_processo: Optional[str] = None
    cliente_id: Optional[int] = None
    di: str = ""

    if usar_processo:
        # filtra processos da empresa
        dfp = df_proc[df_proc["empresa_id"] == empresa_id].copy()
        if dfp.empty:
            st.info("Sem processos para essa empresa (cadastre em Admin).")
        else:
            # label: só referência (se repetir, inclui ID)
            labels = []
            id_by_label = {}
            for r in dfp.itertuples(index=False):
                ref = str(r.referencia)
                label = ref
                if label in id_by_label:
                    label = f"{ref} (ID {int(r.id)})"
                labels.append(label)
                id_by_label[label] = int(r.id)

            pick = st.selectbox("Processo (referência)", labels, key="fech_proc_pick")
            processo_id = id_by_label[pick]
            row = dfp[dfp["id"] == processo_id].iloc[0]

            referencia_processo = str(row["referencia"])
            cliente_id = int(row["cliente_id"])
            di = str(row["di"] or "")

            st.caption(f"Cliente do processo: {row['cliente_nome']} • DI: {di or '-'}")
    else:
        # seleção manual
        if df_cli.empty:
            st.warning("Cadastre clientes antes (Admin).")
            return
        cli_nome = st.selectbox("Cliente", df_cli["nome"].tolist(), key="fech_cli")
        cliente_id = int(df_cli.loc[df_cli["nome"] == cli_nome, "id"].iloc[0])
        di = st.text_input("DI (opcional)", value="", key="fech_di_manual")
        referencia_processo = st.text_input("Referência do processo (opcional)", value="", key="fech_ref_manual").strip() or None

    # cliente/DI texto (como no Excel)
    cliente_nome_for_text = None
    if cliente_id is not None and not df_cli.empty:
        m = df_cli[df_cli["id"] == int(cliente_id)]
        if not m.empty:
            cliente_nome_for_text = str(m["nome"].iloc[0])

    cliente_di_texto = ""
    if cliente_nome_for_text:
        cliente_di_texto = f"{cliente_nome_for_text} / {di}".strip(" /")

    st.divider()

    # =======================
    # Import Excel (Rateio)
    # =======================
    st.markdown("### Importar Rateio de Produtos (Excel)")
    up = st.file_uploader("Envie o Excel da DI", type=["xlsx", "xlsm"], key="fech_up")

    sheet_pick = None
    if up:
        try:
            xls = pd.ExcelFile(up)
            sheets = xls.sheet_names
            default_ix = sheets.index(EXPECTED_SHEET) if EXPECTED_SHEET in sheets else 0
            sheet_pick = st.selectbox("Aba (sheet)", sheets, index=default_ix, key="fech_sheet")
            df_rateio = ler_rateio_excel(up, sheet_pick)
            st.session_state["fech_rateio_df"] = df_rateio
            st.success(f"Rateio carregado: {len(df_rateio)} linhas.")
        except Exception as e:
            st.error(f"Falha ao ler o Excel: {e}")

    df_rateio = st.session_state.get("fech_rateio_df")
    resumo = None
    if isinstance(df_rateio, pd.DataFrame) and not df_rateio.empty:
        st.caption("Você pode ajustar os dados aqui. As linhas NÃO são gravadas no banco — só o resumo do fechamento.")
        edited = st.data_editor(
            df_rateio,
            use_container_width=True,
            num_rows="dynamic",
            key="fech_rateio_editor",
        )
        st.session_state["fech_rateio_df"] = edited

        resumo = resumir_rateio(edited)

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.metric("Itens", resumo["qtd_itens"])
        with c2:
            st.metric("Total Produtos", f"{resumo['totais']['valor_produtos']:.2f}")
        with c3:
            st.metric("Total II", f"{resumo['totais']['valor_ii']:.2f}")
        with c4:
            st.metric("Total IPI", f"{resumo['totais']['valor_ipi']:.2f}")
        with c5:
            st.metric("Total Geral", f"{resumo['totais']['total_geral']:.2f}")

        with st.expander("Ver resumo por NCM / Adição"):
            colx, coly = st.columns(2)
            with colx:
                st.write("Por NCM (top 50)")
                st.dataframe(
                    pd.DataFrame(
                        [{"NCM": k, "Total": v} for k, v in resumo["por_ncm"].items()]
                    ),
                    use_container_width=True,
                )
            with coly:
                st.write("Por Adição")
                st.dataframe(
                    pd.DataFrame(
                        [{"Adição": k, "Total": v} for k, v in resumo["por_adicao"].items()]
                    ),
                    use_container_width=True,
                )
    else:
        st.info("Envie o Excel para carregar o Rateio de Produtos.")

    st.divider()

    # =======================
    # Campos do Fechamento
    # =======================
    st.markdown("### Dados do Fechamento (Resumo)")
    col1, col2, col3 = st.columns(3)
    with col1:
        fechamento_valor = st.number_input("Fechamento (valor)", value=0.0, step=0.01, key="fech_val")
        comissao_1 = st.number_input("Comissão 1", value=0.0, step=0.01, key="fech_c1")
        comissao_2 = st.number_input("Comissão 2", value=0.0, step=0.01, key="fech_c2")
    with col2:
        icms = st.number_input("ICMS", value=0.0, step=0.01, key="fech_icms")
        pis = st.number_input("PIS", value=0.0, step=0.01, key="fech_pis")
        cofins = st.number_input("COFINS", value=0.0, step=0.01, key="fech_cofins")
    with col3:
        ipi = st.number_input("IPI", value=0.0, step=0.01, key="fech_ipi")
        irpj_csll = st.number_input("IRPJ/CSLL", value=0.0, step=0.01, key="fech_irpj")
        markup = st.number_input("Markup", value=0.0, step=0.0001, format="%.6f", key="fech_markup")

    rep1 = st.text_input("Representante 1 (opcional)", value="", key="fech_rep1").strip()
    rep2 = st.text_input("Representante 2 (opcional)", value="", key="fech_rep2").strip()

    st.divider()

    # =======================
    # Registrar
    # =======================
    st.markdown("### Registrar Fechamento")
    colbtn1, colbtn2 = st.columns([1, 2])
    with colbtn1:
        confirmar = st.checkbox("Confirmo que os dados estão corretos", key="fech_confirm")

    if st.button("Registrar Fechamento", type="primary", disabled=not confirmar, key="fech_save"):
        if not isinstance(df_rateio, pd.DataFrame) or df_rateio.empty:
            st.error("Importe o Excel (Rateio de Produtos) antes de registrar o fechamento.")
            return

        # garante resumo atualizado (pode ter editado)
        edited = st.session_state.get("fech_rateio_df")
        if not isinstance(edited, pd.DataFrame) or edited.empty:
            st.error("O rateio está vazio.")
            return
        resumo = resumir_rateio(edited)

        # dados de auditoria (se existir)
        usuario_id = st.session_state.get("usuario_id")
        try:
            usuario_id_int = int(usuario_id) if usuario_id is not None else None
        except Exception:
            usuario_id_int = None

        # grava em transação
        conn = fresh_conn()
        try:
            with conn.cursor() as cur:
                fechamento_id = run_sql_returning_id(
                    """
                    INSERT INTO fechamento (
                      data_registro, empresa_id,
                      processo_id, referencia_processo,
                      cliente_id, cliente_di_texto, di,
                      fechamento_valor, comissao_1, comissao_2,
                      icms, pis, cofins, ipi, irpj_csll, markup,
                      representante_1, representante_2,
                      rateio_resumo, rateio_qtd_itens,
                      rateio_total_valor_produtos, rateio_total_outras_despesas,
                      rateio_total_frete, rateio_total_seguro,
                      rateio_total_valor_ii, rateio_total_valor_ipi,
                      rateio_total_valor_pis, rateio_total_valor_cofins,
                      rateio_total_geral,
                      criado_por_usuario_id
                    )
                    VALUES (
                      %s,%s,
                      %s,%s,
                      %s,%s,%s,
                      %s,%s,%s,
                      %s,%s,%s,%s,%s,%s,
                      %s,%s,
                      %s::jsonb,%s,
                      %s,%s,
                      %s,%s,
                      %s,%s,
                      %s,%s,
                      %s,
                      %s
                    )
                    RETURNING id
                    """,
                    (
                        data_registro, empresa_id,
                        processo_id, (referencia_processo or None),
                        cliente_id, (cliente_di_texto or None), (di or None),
                        float(fechamento_valor) if fechamento_valor is not None else None,
                        float(comissao_1) if comissao_1 is not None else None,
                        float(comissao_2) if comissao_2 is not None else None,
                        float(icms) if icms is not None else None,
                        float(pis) if pis is not None else None,
                        float(cofins) if cofins is not None else None,
                        float(ipi) if ipi is not None else None,
                        float(irpj_csll) if irpj_csll is not None else None,
                        float(markup) if markup is not None else None,
                        (rep1 or None),
                        (rep2 or None),
                        json.dumps(resumo),
                        int(resumo["qtd_itens"]),
                        float(resumo["totais"]["valor_produtos"]),
                        float(resumo["totais"]["outras_despesas"]),
                        float(resumo["totais"]["frete"]),
                        float(resumo["totais"]["seguro"]),
                        float(resumo["totais"]["valor_ii"]),
                        float(resumo["totais"]["valor_ipi"]),
                        float(resumo["totais"]["valor_pis"]),
                        float(resumo["totais"]["valor_cofins"]),
                        float(resumo["totais"]["total_geral"]),
                        usuario_id_int,
                    ),
                )

                # espelha seu histórico Excel (1 linha por fechamento)
                cur.execute(
                    """
                    INSERT INTO fechamento_pagto_importadora (
                      fechamento_id, data_registro, empresa_id,
                      cliente_id, cliente_di_texto, di,
                      referencia_processo, processo_id,
                      markup, data_recebimento
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        fechamento_id, data_registro, empresa_id,
                        cliente_id, (cliente_di_texto or None), (di or None),
                        (referencia_processo or None), processo_id,
                        float(markup) if markup is not None else None,
                        None,
                    ),
                )

                # representante 1 (sempre grava 1 linha como seu VBA; se quiser gravar 2, eu adapto)
                cur.execute(
                    """
                    INSERT INTO fechamento_pagto_representante (
                      fechamento_id, data_registro, empresa_id,
                      cliente_id, cliente_di_texto, di,
                      referencia_processo, processo_id,
                      representante, comissao_1, comissao_2, data_recebimento
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        fechamento_id, data_registro, empresa_id,
                        cliente_id, (cliente_di_texto or None), (di or None),
                        (referencia_processo or None), processo_id,
                        (rep1 or rep2 or None),
                        float(comissao_1) if comissao_1 is not None else None,
                        float(comissao_2) if comissao_2 is not None else None,
                        None,
                    ),
                )

            conn.commit()

            log_action(
                "INSERT",
                "fechamento",
                int(fechamento_id),
                {
                    "empresa_id": empresa_id,
                    "processo_id": processo_id,
                    "cliente_id": cliente_id,
                    "di": di,
                    "fechamento_valor": float(fechamento_valor),
                    "rateio_itens": int(resumo["qtd_itens"]),
                    "rateio_total_geral": float(resumo["totais"]["total_geral"]),
                },
            )

            st.success(f"Fechamento registrado com sucesso! ID: {fechamento_id}")
            st.cache_data.clear()

        except Exception as e:
            conn.rollback()
            st.error(f"Erro ao registrar fechamento: {e}")
        finally:
            conn.close()

    st.divider()

    # =======================
    # Lista de fechamentos
    # =======================
    st.markdown("### Fechamentos já registrados")
    df_hist = listar_fechamentos(empresa_id, limit=50)
    if df_hist.empty:
        st.info("Nenhum fechamento registrado ainda para essa empresa.")
    else:
        st.dataframe(df_hist, use_container_width=True)
