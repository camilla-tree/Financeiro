import re
from .base import parse_decimal_br, parse_data_br

def parse_btg(linhas: list[str]) -> list[dict]:
    transacoes = []

    for linha in linhas:
        linha = linha.strip()

        if not linha:
            continue

        # ignora cabeçalhos e totais
        if (
            'SALDO DE ABERTURA' in linha.upper()
            or 'SALDO DE FECHAMENTO' in linha.upper()
            or 'TOTAL DE ENTRADAS' in linha.upper()
            or 'TOTAL DE SAÍDAS' in linha.upper()
        ):
            continue

        # data no início
        match = re.match(r'(\d{2}/\d{2}/\d{4})\s+(.*)', linha)
        if not match:
            continue

        data_str, resto = match.groups()
        dt_movimento = parse_data_br(data_str)

        partes = resto.split()
        try:
            saldo = parse_decimal_br(partes[-1])
            valor = parse_decimal_br(partes[-2])
            descricao = ' '.join(partes[:-2]).strip()
        except Exception:
            continue

        transacoes.append({
            "dt_movimento": dt_movimento,
            "descricao": descricao,
            "documento": None,
            "valor": valor,
            "saldo": saldo
        })

    return transacoes

