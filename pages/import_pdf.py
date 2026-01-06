from __future__ import annotations

import hashlib
from datetime import date
from typing import Optional

import pandas as pd
import streamlit as st
import psycopg

from db import fetch_df, fresh_conn
from parsers.base import extract_lines_pdf_with_page
from parsers.inter import parse_inter
from parsers.itau import parse_itau
from parsers.santander import parse_santander
from parsers.sicredi import parse_sicredi
from parsers.btg import parse_btg
from parsers.inter_csv import parse_inter_csv
from parsers.nubank_csv import parse_nubank_csv


# =========================
# PARSERS
# =========================
PDF_PARSERS = {
    "INTER": parse_inter,
    "ITAU": parse_itau,
    "SANTANDER": parse_santander,
    "SICREDI": parse_sicredi,
    "BTG": parse_btg,
}

CSV_PARSERS = {
    "INTER": parse_inter_csv,
    "NUBANK": parse_nubank_csv,
}


# =========================
# UTILS
# =========================
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _normalize_transacoes_for_db(transacoes: list[dict]) -> list[dict]:
    """
    Normaliza transações para gravação no DB.
    - valor sempre positivo
    - tipo_id: 1=ENTRADA | 2=SAIDA
    """
    out = []

    for t in transacoes or []:
        x = dict(t or {})

        # data
        x["dt_movimento"] = (
            x.get("dt_movimento")
            or x.get("data")
            or x.get("dt")
        )

        # descricao
        x["descricao"] = (
            x.get("descricao")
            or x.get("historico")
            or ""
        ).strip()

        # valor (signed)
        signed = (
            x.get("valor_signed")
            if x.get("valor_signed") is not None
            else x.get("valor")
        )

        try:
            signed = float(signed)
        except Exception:
            signed = 0.0

        tipo = str(x.get("tipo") or "").upper().strip()
        if tipo not in ("ENTRADA", "SAIDA"):
            tipo = "ENTRADA" if signed >= 0 else "SAIDA"

        x["tipo"] = tipo
        x["tipo_id"] = 1 if tipo == "ENTRADA" else 2
        x["valor"] = abs(signed)

        # saldo
        try:
            x["saldo"] = float(x.get("saldo")) if x.get("saldo") is not None else None
        except Exception:
            x["saldo"] = None

        x["documento"] = x.get("documento")

        out.append(x)

    return out


def _hash_unico(conta_id: int, t: dict) -> str:
    base = f"{conta_id}|{t['dt_movimento']}|{t['valor']}|{t['descricao']}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


# =========================
# DB GRAVAÇÃO
# =========================
def _gravar_importacao(
    conta_bancaria_id: int,
    banco_id: int,
    origem_formato: str,
    arquivo_nome: str,
    hash_arquivo: str,
    usuario_id: Optional[int],
    raw_lines: list,
    transacoes: list[dict],
):
    with fresh_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO extrato_importacao (
                  conta_bancaria_id, banco_id, origem_formato,
                  arquivo_nome, hash_arquivo, usuario_id,
                  status, qtd_linhas_raw, qtd_linhas_validas
                )
                VALUES (%s,%s,%s,%s,%s,%s,'PROCESSANDO',%s,%s)
                RETURNING id
                """,
                (
                    conta_bancaria_id,
                    banco_id,
                    origem_formato,
                    arquivo_nome,
                    hash_arquivo,
                    usuario_id,
                    len(raw_lines),
                    len(transacoes),
                ),
            )
            importacao_id = cur.fetchone()[0]

            # raw
            for i, r in enumerate(raw_lines or []):
                cur.execute(
                    """
                    INSERT INTO extrato_linha_raw (
                      importacao_id, linha_ordem, pagina, texto_raw
                    )
                    VALUES (%s,%s,%s,%s)
                    """,
                    (
                        importacao_id,
                        i,
                        r.get("pagina"),
                        r.get("texto_raw"),
                    ),
                )

            # movimentos
            rows = []
            for t in transacoes:
                rows.append(
                    (
                        conta_bancaria_id,
                        banco_id,
                        importacao_id,
                        t["dt_movimento"],
                        t["descricao"],
                        t.get("documento"),
                        t["valor"],
                        t.get("saldo"),
                        t["tipo_id"],
                        _hash_unico(conta_bancaria_id, t),
                    )
                )

            cur.executemany(
                """
                INSERT INTO movimento_bancario (
                  conta_bancaria_id, banco_id, importacao_id,
                  dt_movimento, descricao, documento,
                  valor, saldo, tipo_id, hash_unico
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (hash_unico) DO NOTHING
                """,
                rows,
            )

            cur.execute(
                "UPDATE extrato_importacao SET status='OK' WHERE id=%s",
                (importacao_id,),
            )

        conn.commit()
    return importacao_id


# =========================
# UI
# =========================
def render_import_pdf():
    st.subheader("Importar Extrato")

    # empresa
    df_emp = fetch_df("SELECT id, nome FROM empresa ORDER BY nome")
    if df_emp.empty:
        st.warning("Cadastre uma empresa primeiro.")
        return

    emp_nome = st.selectbox("Empresa", df_emp["nome"].tolist())
    empresa_id = int(df_emp.loc[df_emp["nome"] == emp_nome, "id"].iloc[0])

    # conta
    df_contas = fetch_df(
        """
        SELECT cb.id AS conta_bancaria_id, cb.apelido,
               b.id AS banco_id, b.codigo AS banco_codigo
        FROM conta_bancaria cb
        JOIN banco b ON b.id = cb.banco_id
        WHERE cb.empresa_id = %s AND cb.ativa = true
        ORDER BY b.codigo, cb.apelido
        """,
        (empresa_id,),
    )
    if df_contas.empty:
        st.warning("Empresa sem conta bancária ativa.")
        return

    options = {
        f"{r.banco_codigo} • {r.apelido or 'Sem apelido'} (ID {r.conta_bancaria_id})": r
        for r in df_contas.itertuples(index=False)
    }
    conta_label = st.selectbox("Conta bancária", list(options.keys()))
    conta = options[conta_label]

    banco_codigo = conta.banco_codigo.upper()

    # upload
    uploaded = st.file_uploader("Extrato (PDF ou CSV)", type=["pdf", "csv"])
    if not uploaded:
        return

    is_csv = uploaded.name.lower().endswith(".csv")

    if banco_codigo == "NUBANK" and not is_csv:
        st.error("Para Nubank aceitamos apenas CSV.")
        return

    if is_csv and banco_codigo not in CSV_PARSERS:
        st.error(f"O banco {banco_codigo} não possui parser CSV.")
        return

    if not is_csv and banco_codigo not in PDF_PARSERS:
        st.error(f"O banco {banco_codigo} não possui parser PDF.")
        return

    file_bytes = uploaded.read()
    hash_arquivo = sha256_bytes(file_bytes)

    # parse
    if is_csv:
        df = CSV_PARSERS[banco_codigo](uploaded)
        transacoes = _normalize_transacoes_for_db(df.to_dict(orient="records"))
        raw_lines = []
        origem = "CSV"
    else:
        raw_lines = extract_lines_pdf_with_page(file_bytes)
        lines = [r["texto_raw"] for r in raw_lines]
        parsed = PDF_PARSERS[banco_codigo](lines)
        transacoes = _normalize_transacoes_for_db(parsed)
        origem = "PDF"

    if not transacoes:
        st.warning("Nenhuma transação encontrada.")
        return

    # preview
    df_prev = pd.DataFrame(transacoes)
    st.markdown("### Preview")
    st.dataframe(df_prev, use_container_width=True)

    if st.button("Confirmar importação", type="primary"):
        importacao_id = _gravar_importacao(
            conta_bancaria_id=int(conta.conta_bancaria_id),
            banco_id=int(conta.banco_id),
            origem_formato=origem,
            arquivo_nome=uploaded.name,
            hash_arquivo=hash_arquivo,
            usuario_id=st.session_state.get("usuario_id"),
            raw_lines=raw_lines,
            transacoes=transacoes,
        )
        st.success(f"Importação concluída (ID {importacao_id})")
        st.cache_data.clear()
