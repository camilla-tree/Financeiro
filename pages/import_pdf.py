from __future__ import annotations

import io
import os
import re
import hashlib
from decimal import Decimal
from typing import List, Optional

import streamlit as st
import pandas as pd
import pdfplumber
import psycopg

from db import fetch_df_cached, fresh_conn
from parsers.btg import parse_btg
from parsers.inter import parse_inter
from parsers.inter_csv import parse_inter_csv
from parsers.itau import parse_itau
from parsers.nubank_csv import parse_nubank_csv
from parsers.santander import parse_santander
from parsers.sicredi import parse_sicredi
from parsers.bb import parse_bb



PARSERS = {
    "BTG": parse_btg,
    "INTER": parse_inter,
    "ITAU": parse_itau,
    "NUBANK": parse_nubank_csv,
    "SANTANDER": parse_santander,
    "SICREDI": parse_sicredi,
    "BB": parse_bb,

}


def normalize_text(s: str) -> str:
    s = (s or "").strip()
    return re.sub(r"\s+", " ", s)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def extract_lines_pdf_with_page(file_bytes: bytes) -> List[dict]:
    """
    Retorna lista de dicts: {pagina, texto_raw, linha_ordem_global}
    """
    out = []
    ordem = 0
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for p_idx, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for ln in text.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                ordem += 1
                out.append({"linha_ordem": ordem, "pagina": p_idx, "texto_raw": ln})
    return out


def to_df(transacoes: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(transacoes)
    if df.empty:
        return df
    for col in ["dt_movimento", "descricao", "documento", "valor", "saldo"]:
        if col not in df.columns:
            df[col] = None
    df["descricao"] = df["descricao"].astype(str).map(normalize_text)
    return df[["dt_movimento", "descricao", "documento", "valor", "saldo"]]


def make_hash_unico(
    conta_bancaria_id: int,
    banco_id: int,
    dt_mov,
    desc: str,
    doc: Optional[str],
    valor: Decimal,
    saldo: Optional[Decimal],
) -> str:
    base = "|".join(
        [
            str(conta_bancaria_id),
            str(banco_id),
            str(dt_mov),
            normalize_text(desc).upper(),
            (doc or "").strip().upper(),
            str(valor),
            "" if saldo is None else str(saldo),
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _gravar_importacao(
    *,
    conta_bancaria_id: int,
    banco_id: int,
    origem_formato: str,
    arquivo_nome: str,
    hash_arquivo: str,
    usuario_id: Optional[int],
    raw_lines: List[dict],
    transacoes: List[dict],
    salvar_raw: bool,
) -> int:
    """
    Grava tudo em uma transação usando UMA conexão fresh e UM cursor.
    Retorna importacao_id.
    """
    with fresh_conn() as conn:
        with conn:  # commit/rollback automático
            with conn.cursor() as cur:
                # 1) cria importação PROCESSANDO
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
                importacao_id = int(cur.fetchone()[0])

                # 2) raw (com pagina)
                if salvar_raw:
                    cur.executemany(
                        """
                        INSERT INTO extrato_linha_raw (importacao_id, linha_ordem, pagina, texto_raw)
                        VALUES (%s,%s,%s,%s)
                        """,
                        [
                            (importacao_id, r["linha_ordem"], r["pagina"], r["texto_raw"])
                            for r in raw_lines
                        ],
                    )

                # 3) movimentos (dedup por hash_unico)
                rows = []
                for t in transacoes:
                    dt_mov = t["dt_movimento"]
                    desc = normalize_text(t["descricao"])
                    doc = (t.get("documento") or None)
                    val = t["valor"]
                    sal = t.get("saldo", None)
                    h = make_hash_unico(
                        conta_bancaria_id, banco_id, dt_mov, desc, doc, val, sal
                    )
                    rows.append(
                        (
                            conta_bancaria_id,
                            banco_id,
                            importacao_id,
                            dt_mov,
                            desc,
                            doc,
                            val,
                            sal,
                            int(t.get("tipo_id") or (1 if str(t.get("tipo") or "").upper().strip() == "ENTRADA" else 2)),
                            h,
                        )
                    )


                cur.executemany(
                    """
                    INSERT INTO movimento_bancario (
                    conta_bancaria_id, banco_id, importacao_id,
                    dt_movimento, descricao, documento, valor, saldo,
                    tipo_id,
                    hash_unico
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (hash_unico) DO NOTHING
                    """,
                    rows,
                )


                # 4) marca OK
                cur.execute(
                    "UPDATE extrato_importacao SET status='OK', mensagem_erro=NULL WHERE id=%s",
                    (importacao_id,),
                )

        return importacao_id


def _marcar_erro(importacao_id: Optional[int], hash_arquivo: str, msg: str):
    with fresh_conn() as conn:
        with conn:
            with conn.cursor() as cur:
                if importacao_id:
                    cur.execute(
                        "UPDATE extrato_importacao SET status='ERRO', mensagem_erro=%s WHERE id=%s",
                        (msg, int(importacao_id)),
                    )
                else:
                    cur.execute(
                        "UPDATE extrato_importacao SET status='ERRO', mensagem_erro=%s WHERE hash_arquivo=%s",
                        (msg, hash_arquivo),
                    )


def _normalize_transacoes_for_db(transacoes: list[dict]) -> list[dict]:
    """
    Normaliza transações (CSV/PDF) para o padrão do DB.

    Garante, no mínimo:
      - dt_movimento
      - descricao
      - documento (opcional)
      - valor (SEMPRE positivo)
      - tipo (ENTRADA|SAIDA)
      - tipo_id (1=ENTRADA, 2=SAIDA)
      - saldo (opcional)
    """
    out: list[dict] = []

    for t in transacoes or []:
        x = dict(t or {})

        # ---- data ----
        if "dt_movimento" not in x or x.get("dt_movimento") in (None, ""):
            x["dt_movimento"] = x.get("data") or x.get("dt") or x.get("data_movimento")

        # ---- descrição ----
        if "descricao" not in x or x.get("descricao") is None:
            x["descricao"] = x.get("historico") or x.get("descr") or ""

        # ---- saldo ----
        if "saldo" not in x:
            x["saldo"] = x.get("saldo_num")

        # ---- tipo ----
        tipo = str(x.get("tipo") or "").strip().upper()
        if tipo in ("SAÍDA", "SAIDA "):
            tipo = "SAIDA"
        if tipo in ("ENTRADA ",):
            tipo = "ENTRADA"

        # ---- valor (sempre positivo) / inferência de tipo pelo sinal ----
        signed = x.get("valor_signed")
        if signed is None:
            signed = x.get("valor_num")
        if signed is None:
            signed = x.get("valor_movimentacao")
        if signed is None:
            signed = x.get("valor")

        v_signed = None
        try:
            if signed not in (None, ""):
                v_signed = float(signed)
        except Exception:
            v_signed = None

        # se não veio tipo, infere
        if tipo not in ("ENTRADA", "SAIDA"):
            if v_signed is not None:
                tipo = "ENTRADA" if v_signed >= 0 else "SAIDA"
            else:
                tipo = "SAIDA"

        # valor final sempre positivo
        if v_signed is not None:
            x["valor"] = abs(v_signed)
        else:
            try:
                x["valor"] = abs(float(x.get("valor") or 0))
            except Exception:
                x["valor"] = 0.0

        x["tipo"] = tipo
        x["tipo_id"] = 1 if tipo == "ENTRADA" else 2

        if "documento" not in x:
            x["documento"] = None

        out.append(x)

    return out


def render_import_pdf():
    st.subheader("Importar Extrato (PDF/CSV)")

    df_emp = fetch_df_cached("SELECT id, nome FROM empresa ORDER BY nome")
    if df_emp.empty:
        st.warning("Cadastre uma empresa primeiro (Admin Cadastros).")
        return

    emp_nome = st.selectbox("Empresa", df_emp["nome"].tolist())
    empresa_id = int(df_emp[df_emp["nome"] == emp_nome]["id"].iloc[0])

    df_contas = fetch_df_cached(
        """
        SELECT cb.id AS conta_bancaria_id, cb.apelido, cb.agencia, cb.numero,
               b.id AS banco_id, b.codigo AS banco_codigo, b.nome AS banco_nome
        FROM conta_bancaria cb
        JOIN banco b ON b.id = cb.banco_id
        WHERE cb.empresa_id = %s AND cb.ativa = true
        ORDER BY b.codigo, cb.apelido NULLS LAST, cb.numero
        """,
        (empresa_id,),
    )
    if df_contas.empty:
        st.warning("Essa empresa não tem conta bancária ativa cadastrada.")
        return

    options = []
    map_opt = {}
    for _, r in df_contas.iterrows():
        label = (
            f"{r['banco_codigo']} • {r.get('apelido') or 'Sem apelido'} • "
            f"Ag {r.get('agencia') or '-'} • Cc {r.get('numero') or '-'} "
            f"(ID {r['conta_bancaria_id']})"
        )
        options.append(label)
        map_opt[label] = r

    conta_label = st.selectbox("Conta bancária", options)
    conta = map_opt[conta_label]
    conta_bancaria_id = int(conta["conta_bancaria_id"])
    banco_codigo = str(conta["banco_codigo"]).upper()
    banco_id = int(conta["banco_id"])

    st.caption("Banco definido pela conta")
    st.code(banco_codigo, language="text")

    # usuário: pega automaticamente da sessão (usuário normal)
    usuario_id = st.session_state.get("usuario_id")

    # opcional: admin pode escolher outro "usuario_id" para registrar a importação
    if st.session_state.get("is_admin"):
        df_user = fetch_df_cached("SELECT id, nome FROM usuario WHERE ativo=true ORDER BY nome")
        if not df_user.empty:
            opt = ["(Usar sessão / Sem usuário)"] + df_user["nome"].tolist()
            u = st.selectbox("Usuário (opcional)", opt, index=0)
            if u != "(Usar sessão / Sem usuário)":
                usuario_id = int(df_user[df_user["nome"] == u]["id"].iloc[0])

    # --- Upload: PDF/CSV ---
    uploaded = st.file_uploader("Extrato (PDF/CSV)", type=["pdf", "csv"])
    salvar_raw = True

    if not uploaded:
        st.info("Envie um arquivo (PDF ou CSV) para habilitar a importação.")
        return

    file_bytes = uploaded.read()
    arquivo_nome = uploaded.name
    ext = os.path.splitext(arquivo_nome.lower())[1]
    is_csv = ext == ".csv"
    is_pdf = ext == ".pdf"
    hash_arquivo = sha256_bytes(file_bytes)

    # --- Regras por banco/formato ---
    # Nubank: SOMENTE CSV
    if banco_codigo == "NUBANK" and not is_csv:
        st.error("Para o Nubank, neste sistema aceitamos apenas arquivo CSV.")
        return

    # Inter: CSV ou PDF (CSV novo; PDF continua como está)
    # Outros bancos: apenas PDF (por enquanto)
    if banco_codigo not in ("NUBANK", "INTER") and not is_pdf:
        st.error(f"Para o banco {banco_codigo}, neste momento aceitamos apenas PDF.")
        return

    # Se trocou o arquivo, limpa preview antigo
    prev = st.session_state.get("import_preview")
    if prev and prev.get("hash_arquivo") != hash_arquivo:
        st.session_state.pop("import_preview", None)
        prev = None

    # =========================
    # Etapa 1: Importar (processa e cria preview)
    # =========================
    importar = st.button("Importar", type="primary")

    if importar:
        origem_formato = "CSV" if is_csv else "PDF"

        # ---------- CSV path ----------
        if is_csv:
            # raw_lines para auditoria (uma "linha raw" por linha do arquivo)
            raw_text = file_bytes.decode("utf-8", errors="replace")

            raw_lines = []
            for i, line in enumerate(raw_text.splitlines(), start=1):
                raw_lines.append({
                    "pagina": 1,
                    "linha": i,         # se você quiser manter
                    "linha_ordem": i, 
                    "texto_raw": line
                })


            if banco_codigo == "NUBANK":
                parsed = parse_nubank_csv(file_bytes)
            elif banco_codigo == "INTER":
                parsed = parse_inter_csv(file_bytes)
            else:
                st.error(f"Não existe parser CSV cadastrado para o banco: {banco_codigo}")
                return

           # Suporta parser que retorna DF ou lista de dicts
            if isinstance(parsed, pd.DataFrame):
                transacoes = parsed.to_dict(orient="records")
            else:
                transacoes = parsed

            # NORMALIZA SEMPRE (CSV e PDF)
            transacoes = _normalize_transacoes_for_db(transacoes)

            # DataFrame final para preview
            df = to_df(transacoes)


        # ---------- PDF path ----------
        else:
            if banco_codigo not in PARSERS:
                st.error(f"Não existe parser cadastrado para o banco: {banco_codigo}")
                return

            raw_lines = extract_lines_pdf_with_page(file_bytes)
            lines_only = [x["texto_raw"] for x in raw_lines]

            transacoes = PARSERS[banco_codigo](lines_only)
            df = to_df(transacoes)

        st.session_state["import_preview"] = {
            "raw_lines": raw_lines,
            "transacoes": transacoes,
            "df": df,
            "arquivo_nome": arquivo_nome,
            "hash_arquivo": hash_arquivo,
            "conta_bancaria_id": conta_bancaria_id,
            "banco_id": banco_id,
            "empresa_id": empresa_id,
            "usuario_id": usuario_id,
            "banco_codigo": banco_codigo,
            "emp_nome": emp_nome,
            "origem_formato": origem_formato,
        }
        prev = st.session_state["import_preview"]

    if not prev:
        st.caption(
            "1) Clique em Importar para processar e ver o preview.\n"
            "2) Depois clique em Confirmar importação para gravar no banco."
        )
        return

    # Segurança: garante que o preview é do mesmo arquivo
    if prev.get("hash_arquivo") != hash_arquivo:
        st.info("Arquivo alterado. Clique em Importar novamente para gerar um novo preview.")
        return

    raw_lines = prev["raw_lines"]
    transacoes = prev["transacoes"]
    df = prev["df"]
    origem_formato = prev.get("origem_formato", "PDF" if is_pdf else "CSV")

    # =========================
    # Mostra preview
    # =========================
    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown("### Preview")
        if df is None or getattr(df, "empty", True):
            st.warning("Nenhuma transação encontrada.")
        else:
            st.dataframe(df, use_container_width=True)

    with col2:
        st.markdown("### Resumo")
        st.metric("Empresa", prev.get("emp_nome", emp_nome))
        st.metric("Conta ID", prev.get("conta_bancaria_id", conta_bancaria_id))
        st.metric("Banco", prev.get("banco_codigo", banco_codigo))
        st.metric("Formato", origem_formato)
        st.metric("Linhas raw", len(raw_lines) if raw_lines else 0)
        st.metric("Transações", len(transacoes) if transacoes else 0)
        if df is not None and not df.empty:
            if "valor" in df.columns:
                try:
                    st.metric("Soma valores", float(pd.to_numeric(df["valor"], errors="coerce").fillna(0).sum()))
                except Exception:
                    pass
            if "saldo" in df.columns:
                try:
                    st.metric("Qtd com saldo", int(df["saldo"].notna().sum()))
                except Exception:
                    pass

    # =========================
    # Etapa 2: Confirmar importação (grava no banco)
    # =========================
    confirmar = st.button(
        "Confirmar importação",
        type="primary",
        disabled=(not transacoes) or (len(transacoes) == 0),
    )

    if not confirmar:
        return

    importacao_id: Optional[int] = None

    try:
        importacao_id = _gravar_importacao(
            conta_bancaria_id=int(prev["conta_bancaria_id"]),
            banco_id=int(prev["banco_id"]),
            origem_formato=str(origem_formato),
            arquivo_nome=str(prev["arquivo_nome"]),
            hash_arquivo=str(prev["hash_arquivo"]),
            usuario_id=prev.get("usuario_id"),
            raw_lines=raw_lines,
            transacoes=transacoes,
            salvar_raw=salvar_raw,
        )
        st.success(f"Importação concluída! importacao_id={importacao_id}")
        st.cache_data.clear()

        # limpa preview pra evitar gravar duas vezes sem querer
        st.session_state.pop("import_preview", None)

    except psycopg.errors.DuplicatePreparedStatement:
        # retry 1 vez (nova conexão fresh por baixo)
        try:
            importacao_id = _gravar_importacao(
                conta_bancaria_id=int(prev["conta_bancaria_id"]),
                banco_id=int(prev["banco_id"]),
                origem_formato=str(origem_formato),
                arquivo_nome=str(prev["arquivo_nome"]),
                hash_arquivo=str(prev["hash_arquivo"]),
                usuario_id=prev.get("usuario_id"),
                raw_lines=raw_lines,
                transacoes=transacoes,
                salvar_raw=salvar_raw,
            )
            st.success(f"Importação concluída! importacao_id={importacao_id}")
            st.cache_data.clear()
            st.session_state.pop("import_preview", None)

        except Exception as e2:
            _marcar_erro(importacao_id, str(prev["hash_arquivo"]), str(e2))
            st.error(f"Falha ao gravar no banco: {e2}")

    except Exception as e:
        _marcar_erro(importacao_id, str(prev["hash_arquivo"]), str(e))
        st.error(f"Falha ao gravar no banco: {e}")