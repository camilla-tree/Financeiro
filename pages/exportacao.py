from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple, List

import pandas as pd
import streamlit as st

from db import fetch_df_cached

# PDF (reportlab)
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors


@dataclass
class RelatorioContext:
    cliente_nome: str
    empresa_nome: str
    mes_label: str
    saldo_anterior: float
    total_entrada: float
    total_saida: float


MESES = [
    ("JANEIRO", 1),
    ("FEVEREIRO", 2),
    ("MARÇO", 3),
    ("ABRIL", 4),
    ("MAIO", 5),
    ("JUNHO", 6),
    ("JULHO", 7),
    ("AGOSTO", 8),
    ("SETEMBRO", 9),
    ("OUTUBRO", 10),
    ("NOVEMBRO", 11),
    ("DEZEMBRO", 12),
]


def _dt_ini_fim(ano: int, mes_num: int):
    dt_ini = date(ano, mes_num, 1)
    if mes_num == 12:
        dt_fim = date(ano + 1, 1, 1)
    else:
        dt_fim = date(ano, mes_num + 1, 1)
    return dt_ini, dt_fim


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _add_month(d: date) -> date:
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _mes_label(dt_ini: date) -> str:
    return f"{dt_ini.month:02d}-{dt_ini.year}"


def _fmt_brl(v) -> str:
    try:
        vv = float(v)
    except Exception:
        return ""
    s = f"{vv:,.2f}"
    s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def _fmt_date(d) -> str:
    if d is None:
        return ""
    if isinstance(d, (datetime,)):
        d = d.date()
    if isinstance(d, (date,)):
        return d.strftime("%d/%m/%Y")
    # fallback
    return str(d)


def _get_status_confirmada_id() -> int:
    df = fetch_df_cached("SELECT id FROM conciliacao_status WHERE nome='CONFIRMADA' LIMIT 1")
    if df.empty:
        return 0
    return int(df.iloc[0]["id"])


def _fetch_clientes() -> pd.DataFrame:
    return fetch_df_cached("SELECT id, nome FROM cliente WHERE ativo=true ORDER BY nome")


def _fetch_empresas() -> pd.DataFrame:
    return fetch_df_cached("SELECT id, nome FROM empresa ORDER BY nome")


def _fetch_saldo_anterior(
    cliente_id: int,
    dt_ini: date,
    status_confirmada_id: int,
    empresa_id: Optional[int] = None,
) -> float:
    params = [status_confirmada_id, cliente_id, dt_ini]
    filtro_empresa = ""
    if empresa_id is not None:
        filtro_empresa = " AND e.id = %s "
        params.append(empresa_id)

    sql = f"""
    SELECT
      COALESCE(SUM(
        CASE
          WHEN mt.nome='ENTRADA' THEN mb.valor
          WHEN mt.nome='SAIDA' THEN -mb.valor
          ELSE 0
        END
      ), 0) AS saldo_anterior
    FROM conciliacao co
    JOIN conciliacao_status cs ON cs.id = co.status_id
    JOIN cliente cl ON cl.id = co.cliente_id

    JOIN movimento_bancario mb ON mb.id = co.movimento_bancario_id
    JOIN conta_bancaria cb ON cb.id = mb.conta_bancaria_id
    JOIN empresa e ON e.id = cb.empresa_id
    LEFT JOIN movimento_tipo mt ON mt.id = mb.tipo_id

    WHERE cs.id = %s
      AND cl.id = %s
      AND mb.dt_movimento < %s
      {filtro_empresa}
    """
    df = fetch_df_cached(sql, tuple(params))
    try:
        return float(df.iloc[0]["saldo_anterior"]) if not df.empty else 0.0
    except Exception:
        return 0.0


def _fetch_movimentos_conciliados(
    cliente_id: int,
    dt_ini: date,
    dt_fim: date,
    status_confirmada_id: int,
    empresa_id: Optional[int] = None,
) -> pd.DataFrame:
    params = [status_confirmada_id, cliente_id, dt_ini, dt_fim]
    filtro_empresa = ""
    if empresa_id is not None:
        filtro_empresa = " AND e.id = %s "
        params.append(empresa_id)

    sql = f"""
    SELECT
      e.id AS empresa_id,
      e.nome AS empresa_nome,
      cl.nome AS cliente_nome,

      b.codigo AS banco_codigo,
      mb.dt_movimento,
      mb.descricao,
      mb.valor,
      mb.saldo,

      mt.nome AS tipo,                 -- ENTRADA / SAIDA (do banco)
      cf.nome AS categoria_nome

    FROM conciliacao co
    JOIN conciliacao_status cs ON cs.id = co.status_id
    JOIN cliente cl ON cl.id = co.cliente_id

    JOIN movimento_bancario mb ON mb.id = co.movimento_bancario_id
    JOIN conta_bancaria cb ON cb.id = mb.conta_bancaria_id
    JOIN empresa e ON e.id = cb.empresa_id
    JOIN banco b ON b.id = mb.banco_id
    LEFT JOIN movimento_tipo mt ON mt.id = mb.tipo_id
    LEFT JOIN categoria_financeira cf ON cf.id = mb.categoria_id

    WHERE cs.id = %s
      AND cl.id = %s
      AND mb.dt_movimento >= %s
      AND mb.dt_movimento < %s
      {filtro_empresa}

    ORDER BY e.nome, mb.dt_movimento, mb.id
    """
    return fetch_df_cached(sql, tuple(params))


def _totais_entrada_saida(df: pd.DataFrame) -> Tuple[float, float]:
    if df.empty:
        return 0.0, 0.0
    df2 = df.copy()
    df2["tipo"] = df2["tipo"].astype(str).str.upper().str.strip()
    entrada = float(df2.loc[df2["tipo"] == "ENTRADA", "valor"].sum())
    saida = float(df2.loc[df2["tipo"] == "SAIDA", "valor"].sum())
    return entrada, saida


def _build_pdf_relatorio(df: pd.DataFrame, ctx: RelatorioContext) -> bytes:
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=36,
        rightMargin=36,
        topMargin=28,
        bottomMargin=28,
    )
    styles = getSampleStyleSheet()
    story = []

    title = Paragraph(f"<b>RELATÓRIO DE CLIENTE</b>", styles["Title"])
    story.append(title)
    story.append(Spacer(1, 8))

    header = Paragraph(
        f"<b>Cliente:</b> {ctx.cliente_nome} &nbsp;&nbsp; "
        f"<b>Empresa:</b> {ctx.empresa_nome} &nbsp;&nbsp; "
        f"<b>Mês:</b> {ctx.mes_label}",
        styles["Normal"],
    )
    story.append(header)
    story.append(Spacer(1, 10))

    resumo = [
        ["Saldo anterior", _fmt_brl(ctx.saldo_anterior)],
        ["Total de entrada", _fmt_brl(ctx.total_entrada)],
        ["Total de saída", _fmt_brl(ctx.total_saida)],
    ]
    resumo_tbl = Table(resumo, colWidths=[140, 160])
    resumo_tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.whitesmoke),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story.append(resumo_tbl)
    story.append(Spacer(1, 14))

    # Tabela
    cols = ["BANCO", "DATA", "HISTÓRICO", "TIPO DE LANÇAMENTO", "CATEGORIA", "ENTRADA", "SAÍDA", "SALDO"]

    rows = [cols]
    for _, r in df.iterrows():
        tipo = str(r.get("tipo") or "").upper().strip()
        valor = float(r.get("valor") or 0.0)
        saldo = r.get("saldo")

        entrada = valor if tipo == "ENTRADA" else 0.0
        saida = valor if tipo == "SAIDA" else 0.0
        tipo_lanc = "RECEITA" if tipo == "ENTRADA" else "DESPESA"

        rows.append(
            [
                str(r.get("banco_codigo") or ""),         # ✅ BANCO
                _fmt_date(r.get("dt_movimento")),         # DATA
                str(r.get("descricao") or ""),            # HISTÓRICO
                tipo_lanc,                                # TIPO DE LANÇAMENTO
                str(r.get("categoria_nome") or ""),       # CATEGORIA
                _fmt_brl(entrada) if entrada else "",     # ENTRADA
                _fmt_brl(saida) if saida else "",         # SAÍDA
                _fmt_brl(saldo) if saldo is not None else "",  # ✅ SALDO
            ]
        )

    table = Table(
        rows,
        repeatRows=1,
        colWidths=[60, 70, 300, 110, 140, 95, 95, 95],  # ✅ inclui BANCO e SALDO
    )

    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9ead3")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTSIZE", (0, 1), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 1), (1, -1), "CENTER"),     # DATA
                ("ALIGN", (5, 1), (7, -1), "RIGHT"),      # ENTRADA / SAÍDA / SALDO
            ]
        )
    )


    story.append(table)
    doc.build(story)

    return buffer.getvalue()


def render_exportacao():
    st.subheader("Relatórios de Cliente")

    status_confirmada_id = _get_status_confirmada_id()
    if status_confirmada_id == 0:
        st.error("Não encontrei o status 'CONFIRMADA' em conciliacao_status.")
        return

    df_cli = _fetch_clientes()
    if df_cli.empty:
        st.warning("Cadastre clientes primeiro.")
        return

    df_emp = _fetch_empresas()

    # filtros
    colA, colB, colC = st.columns([1.2, 1.4, 1.4])

    with colA:
        mes_nome = st.selectbox("Mês", [m[0] for m in MESES], index=date.today().month - 1)
        mes_num = dict(MESES)[mes_nome]

    with colB:
        ano_atual = date.today().year
        anos = list(range(2023, ano_atual + 1))
        ano = st.selectbox("Ano", anos, index=len(anos) - 1)

    dt_ini, dt_fim = _dt_ini_fim(int(ano), int(mes_num))

    with colC:
        cli_nome = st.selectbox("Cliente", df_cli["nome"].tolist())
        cliente_id = int(df_cli.loc[df_cli["nome"] == cli_nome, "id"].iloc[0])

    empresa_id: Optional[int] = None
    empresa_nome = "(todas as empresas)"
    if not df_emp.empty:
        opt_emp = ["(Todas)"] + df_emp["nome"].tolist()
        emp = st.selectbox("Empresa (opcional)", opt_emp, index=0)
        if emp != "(Todas)":
            empresa_id = int(df_emp.loc[df_emp["nome"] == emp, "id"].iloc[0])
            empresa_nome = emp

    st.caption("Gera relatório apenas de movimentações com conciliação CONFIRMADA.")

    gerar = st.button("Gerar relatório", type="primary")
    if not gerar:
        return

    # busca dados
    df = _fetch_movimentos_conciliados(
        cliente_id=cliente_id,
        dt_ini=dt_ini,
        dt_fim=dt_fim,
        status_confirmada_id=status_confirmada_id,
        empresa_id=empresa_id,
    )

    if df.empty:
        st.warning("Nenhuma movimentação conciliada (CONFIRMADA) encontrada no período/filtros.")
        return

    saldo_anterior = _fetch_saldo_anterior(
        cliente_id=cliente_id,
        dt_ini=dt_ini,
        status_confirmada_id=status_confirmada_id,
        empresa_id=empresa_id,
    )
    total_entrada, total_saida = _totais_entrada_saida(df)

    # Caso 1: empresa selecionada => 1 PDF
    if empresa_id is not None:
        ctx = RelatorioContext(
            cliente_nome=cli_nome,
            empresa_nome=empresa_nome,
            mes_label=_mes_label(dt_ini),
            saldo_anterior=saldo_anterior,
            total_entrada=total_entrada,
            total_saida=total_saida,
        )

        pdf_bytes = _build_pdf_relatorio(df, ctx)

        st.success("Relatório gerado.")

        df_view = df.copy()
        df_view = df_view.rename(
            columns={
                "empresa_nome": "Empresa",
                "cliente_nome": "Cliente",
                "banco_codigo": "Banco",
                "dt_movimento": "Data",
                "descricao": "Histórico",
                "tipo": "Tipo",
                "categoria_nome": "Categoria",
                "valor": "Valor",
                "saldo": "Saldo",
            }
        )
        cols_order = [c for c in ["Empresa", "Cliente", "Banco", "Data", "Histórico", "Tipo", "Categoria", "Valor", "Saldo"] if c in df_view.columns]
        df_view = df_view[cols_order]
        st.dataframe(df_view, use_container_width=True)

        nome_arquivo = (
            f"relatorio_{cli_nome}_{ctx.empresa_nome}_{_mes_label(dt_ini)}.pdf"
            .replace("/", "-")
            .replace(" ", "_")
        )
        st.download_button(
            "Baixar PDF",
            data=pdf_bytes,
            file_name=nome_arquivo,
            mime="application/pdf",
        )
        return

    # Caso 2: empresa NÃO selecionada => 1 PDF por empresa => ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for emp_nome, df_emp_mov in df.groupby("empresa_nome"):
            saldo_ant_emp = _fetch_saldo_anterior(
                cliente_id=cliente_id,
                dt_ini=dt_ini,
                status_confirmada_id=status_confirmada_id,
                empresa_id=int(df_emp.loc[df_emp["nome"] == emp_nome, "id"].iloc[0]),
            )
            ent, sai = _totais_entrada_saida(df_emp_mov)

            ctx = RelatorioContext(
                cliente_nome=cli_nome,
                empresa_nome=str(emp_nome),
                mes_label=_mes_label(dt_ini),
                saldo_anterior=saldo_ant_emp,
                total_entrada=ent,
                total_saida=sai,
            )
            pdf_bytes = _build_pdf_relatorio(df_emp_mov, ctx)
            fn = f"relatorio_{cli_nome}_{emp_nome}_{_mes_label(dt_ini)}.pdf".replace("/", "-").replace(" ", "_")
            z.writestr(fn, pdf_bytes)

    st.success("Relatórios gerados (um por empresa).")
    zip_buffer.seek(0)
    nome_zip = f"relatorios_{cli_nome}_{_mes_label(dt_ini)}.zip".replace("/", "-").replace(" ", "_")
    st.download_button(
        "Baixar ZIP",
        data=zip_buffer.getvalue(),
        file_name=nome_zip,
        mime="application/zip",
    )
