from __future__ import annotations

import io
import pandas as pd


def parse_nubank_csv(file_bytes: bytes) -> pd.DataFrame:
    """
    Lê CSV do Nubank e devolve um DataFrame padronizado.
    Saída sugerida:
      - data (date)
      - descricao (str)
      - tipo ('ENTRADA'|'SAIDA')
      - valor (float, sempre positivo)
      - external_id (str)  # Identificador do Nubank
    """
    df = pd.read_csv(io.BytesIO(file_bytes))

    required = {"Data", "Valor", "Identificador", "Descrição"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV Nubank inválido. Faltando colunas: {sorted(missing)}")

    # Normaliza
    df["data"] = pd.to_datetime(df["Data"], format="%d/%m/%Y", errors="coerce").dt.date
    df["descricao"] = df["Descrição"].astype(str).str.strip()
    df["external_id"] = df["Identificador"].astype(str).str.strip()

    # Valor vem com sinal: positivo = entrada, negativo = saída
    df["valor_num"] = pd.to_numeric(df["Valor"], errors="coerce")
    df["tipo"] = df["valor_num"].apply(lambda x: "ENTRADA" if x >= 0 else "SAIDA")
    df["valor"] = df["valor_num"].abs()

    out = df[["data", "descricao", "tipo", "valor", "external_id"]].copy()

    # valida linhas ruins
    out = out.dropna(subset=["data", "valor"])
    return out
