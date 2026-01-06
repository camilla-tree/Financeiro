import re
from .base import parse_decimal_br, parse_data_br

_RE_DATA_INICIO = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.*)$')

# captura um valor monetário no fim da linha
# suporta: "R$ 1.234,56" / "-R$ 1.234,56" / "- R$ 1.234,56" / "-1.234,56"
_RE_VALOR_FIM = re.compile(r'(-?\s*R?\$?\s*\d{1,3}(?:\.\d{3})*,\d{2})\s*$')

def _parse_valor_santander(valor_str: str):
    """
    Normaliza formatos comuns do Santander:
      '- R$ 1.234,56' -> '-R$ 1.234,56'
      '-1.234,56'     -> '-1.234,56'
    e reaproveita parse_decimal_br.
    """
    s = valor_str.strip()
    s = s.replace('R$', 'R$ ')  # garante espaço
    s = re.sub(r'\s+', ' ', s)

    # se for "- R$ ..." vira "-R$ ..."
    s = s.replace('- R$ ', '-R$ ')
    # se for "- 1.234,56" vira "-1.234,56"
    s = s.replace('- ', '-')

    # parse_decimal_br já remove R$, pontos e troca vírgula por ponto
    return parse_decimal_br(s)

def parse_santander(linhas: list[str]) -> list[dict]:
    """
    Santander:
      Data | Histórico | Valor

    Regras:
    - Ignorar cabeçalhos/rodapés e linhas de saldo/total
    - Linha válida começa com data dd/mm/aaaa
    - Valor está no final da linha (pode ser negativo ou positivo)
    - Não há saldo por transação: saldo = None
    """
    transacoes = []

    for linha in linhas:
        linha = linha.strip()
        if not linha:
            continue

        up = linha.upper()

        # Ignorar cabeçalhos/rodapés típicos (evitar filtros agressivos p/ não excluir lançamentos reais)
        if (
            up.startswith("DATA ")
            or up.startswith("TOTAL")
            or up.startswith("SANTANDER")
            or up.startswith("OUVIDORIA")
            or up.startswith("SAC")
            or "EXTRATO" in up
            or "SALDO DO DIA" in up
            or "SALDO ANTERIOR" in up
        ):
            continue

        m = _RE_DATA_INICIO.match(linha)
        if not m:
            continue

        data_str, resto = m.groups()
        dt_movimento = parse_data_br(data_str)

        mv = _RE_VALOR_FIM.search(resto)
        if not mv:
            # Sem valor no fim → provavelmente não é linha de lançamento
            continue

        valor_str = mv.group(1)
        try:
            valor = _parse_valor_santander(valor_str)
        except Exception:
            continue

        # Descrição = tudo antes do valor no campo "resto"
        descricao = resto[:mv.start()].strip()

        # se por algum motivo ficar vazio, preserva o resto completo
        if not descricao:
            descricao = resto.strip()

        transacoes.append({
            "dt_movimento": dt_movimento,
            "descricao": descricao,
            "documento": None,
            "valor": valor,   # entrada + / saída -
            "saldo": None
        })

    return transacoes
