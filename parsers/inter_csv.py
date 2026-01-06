from __future__ import annotations

import io
import pandas as pd


def _br_to_float(series: pd.Series) -> pd.Series:
    """
    Converte string numérica no formato pt-BR para float.
    Ex.: "2.035,39" -> 2035.39 | "-107,00" -> -107.0
    """
    s = series.astype(str).str.strip()
    s = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce")


def parse_inter_csv(file_bytes: bytes) -> pd.DataFrame:
    """
    Lê CSV do Banco Inter (Extrato Conta Corrente) e devolve DataFrame padronizado
    para o seu fluxo de importação.

    Espera cabeçalho:
      Data Lançamento;Histórico;Descrição;Valor;Saldo

    Retorna DF com colunas:
      data, historico, descricao, tipo, valor, saldo
    """
    text = file_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()

    # Acha a linha do cabeçalho real da tabela
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Data Lançamento;"):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            "CSV Inter inválido: não encontrei o cabeçalho 'Data Lançamento;Histórico;Descrição;Valor;Saldo'."
        )

    csv_text = "\n".join(lines[header_idx:])  # começa do cabeçalho
    df = pd.read_csv(io.StringIO(csv_text), sep=";", dtype=str)

    # Valida colunas
    required = {"Data Lançamento", "Histórico", "Descrição", "Valor", "Saldo"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV Inter inválido. Faltando colunas: {sorted(missing)}")

    # Normalização
    df["data"] = pd.to_datetime(df["Data Lançamento"], dayfirst=True, errors="coerce").dt.date
    df["historico"] = df["Histórico"].astype(str).str.strip()
    df["descricao"] = df["Descrição"].astype(str).str.strip()

    valor_signed = _br_to_float(df["Valor"])
    saldo_num = _br_to_float(df["Saldo"])

    df["tipo"] = valor_signed.apply(lambda x: "ENTRADA" if pd.notna(x) and x >= 0 else "SAIDA")
    df["valor"] = valor_signed.abs()
    df["saldo"] = saldo_num

    out = df[["data", "historico", "descricao", "tipo", "valor", "saldo"]].copy()
    out = out.dropna(subset=["data", "valor"])  # remove linhas quebradas
    return out
