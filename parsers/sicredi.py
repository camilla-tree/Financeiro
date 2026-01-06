import re
from .base import parse_decimal_br, parse_data_br

def parse_sicredi(linhas: list[str]) -> list[dict]:
    """
    Sicredi:
      Data | Descrição | Documento | Valor (R$) | Saldo (R$)

    Regras:
    - Ignorar linhas tipo "SALDO ANTERIOR", cabeçalhos e rodapés
    - Data no início: dd/mm/aaaa
    - Documento costuma ser um token (ex: COB000013, PIX_DEB, etc.)
    - Valor e saldo ficam no fim da linha (2 últimos tokens numéricos)
    - Valor pode vir negativo (saída) ou positivo (entrada)
    """
    transacoes = []

    for linha in linhas:
        linha = linha.strip()
        if not linha:
            continue

        up = linha.upper()

        # Ignorar linhas que não são lançamentos
        if (
            up.startswith("DATA ")
            or "EXTRATO" in up
            or "SALDO ANTERIOR" in up
            or "LANÇAMENTOS FUTUROS" in up
            or "NAO HA" in up
            or "SAC" in up
            or "OUVIDORIA" in up
            or "SICREDI" in up
        ):
            continue

        # Precisa começar com data dd/mm/aaaa
        m = re.match(r'^(\d{2}/\d{2}/\d{4})\s+(.*)$', linha)
        if not m:
            continue

        data_str, resto = m.groups()
        dt_movimento = parse_data_br(data_str)

        # Quebrar em tokens e capturar os dois últimos valores (valor e saldo)
        partes = resto.split()
        if len(partes) < 3:
            continue

        # Últimos dois tokens devem ser números no padrão BR (-1.234,56 / 1.234,56)
        # Exemplo do PDF: "... COB000013 -34,19 461,68" :contentReference[oaicite:1]{index=1}
        try:
            saldo = parse_decimal_br(partes[-1])
            valor = parse_decimal_br(partes[-2])
        except Exception:
            # Caso não consiga, não é linha válida de lançamento
            continue

        # O token anterior ao valor normalmente é o "Documento"
        documento = None
        descricao_tokens = partes[:-2]  # padrão: sem doc (usa só valor/saldo no fim)

        # Heurística: Sicredi costuma ter 'Documento' como um token (ex: COB000013, PIX_DEB, etc.)
        # Se o -3 "parecer" documento, usamos; senão, deixamos None.
        if len(partes) >= 3:
            doc_cand = partes[-3]
            if re.match(r"^[A-Z0-9_/-]{3,}$", doc_cand, re.IGNORECASE):
                documento = doc_cand
                descricao_tokens = partes[:-3]
        descricao = " ".join(descricao_tokens).strip()

        # Se por algum motivo a descrição ficou vazia (muito raro), tenta recuperar
        if not descricao:
            descricao = resto

        transacoes.append({
            "dt_movimento": dt_movimento,
            "descricao": descricao,
            "documento": documento,
            "valor": valor,     # entrada + / saída -
            "saldo": saldo
        })

    return transacoes
