import re
from datetime import date
from .base import parse_decimal_br

# Ex: "27 de Outubro de 2025 Saldo do dia: R$ 323.028,22"
_RE_DATA_HEADER = re.compile(
    r'^\s*(\d{1,2})\s+de\s+([A-Za-zÇçÁÉÍÓÚÂÊÔÃÕàáéíóúâêôãõ]+)\s+de\s+(\d{4})\b',
    re.IGNORECASE
)

# Captura valores monetários no padrão do Inter:
# "R$ 1.234,56" ou "-R$ 1.234,56"
_RE_MOEDA = re.compile(r'(-?\s*R\$\s*\d{1,3}(?:\.\d{3})*,\d{2})')

_MESES_PT = {
    "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "MARÇO": 3, "ABRIL": 4,
    "MAIO": 5, "JUNHO": 6, "JULHO": 7, "AGOSTO": 8, "SETEMBRO": 9,
    "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12
}

def _parse_data_header_inter(linha: str) -> date | None:
    m = _RE_DATA_HEADER.match(linha.strip())
    if not m:
        return None
    dia = int(m.group(1))
    mes_nome = m.group(2).strip().upper()
    ano = int(m.group(3))

    mes = _MESES_PT.get(mes_nome)
    if not mes:
        return None
    return date(ano, mes, dia)

def parse_inter(linhas: list[str]) -> list[dict]:
    """
    Inter:
    - Data aparece como cabeçalho: "27 de Outubro de 2025 Saldo do dia: ..."
    - Lançamentos herdam a data_contexto
    - Cada lançamento tem 2 valores monetários:
        penúltimo = valor do lançamento
        último    = saldo após o lançamento
    - Se só houver 1 valor monetário, é linha de data/resumo → ignorar
    """
    transacoes = []
    data_contexto: date | None = None

    for linha in linhas:
        linha = (linha or "").strip()
        if not linha:
            continue

        up = linha.upper()

        # Ignorar cabeçalhos genéricos
        if (
            up.startswith("SOLICITADO EM:")
            or up.startswith("PERÍODO:")
            or up.startswith("PERIODO:")
            or up.startswith("SALDO TOTAL")
            or "VALOR SALDO POR TRANSAÇÃO" in up
            or "VALOR SALDO POR TRANSACAO" in up
        ):
            # Pode ter data + "Valor Saldo por transação" na mesma linha,
            # então não damos continue aqui sem antes tentar detectar data:
            dt = _parse_data_header_inter(linha)
            if dt:
                data_contexto = dt
            continue

        # Detecta cabeçalho de data (e atualiza data_contexto)
        dt = _parse_data_header_inter(linha)
        if dt:
            data_contexto = dt
            continue

        # Sem data definida, não dá pra registrar lançamento
        if data_contexto is None:
            continue

        # Captura valores monetários
        matches = list(_RE_MOEDA.finditer(linha))
        if len(matches) < 2:
            # Linha de data/resumo ou lixo → ignora
            continue

        # Penúltimo = valor; último = saldo
        valor_str = matches[-2].group(1)
        saldo_str = matches[-1].group(1)

        try:
            valor = parse_decimal_br(valor_str)
            saldo = parse_decimal_br(saldo_str)
        except Exception:
            continue

        # Descrição = tudo antes do penúltimo valor
        descricao = linha[:matches[-2].start()].strip()

        # Normalizações leves (opcional)
        descricao = re.sub(r'\s+', ' ', descricao)

        transacoes.append({
            "dt_movimento": data_contexto,
            "descricao": descricao,
            "documento": None,
            "valor": valor,   # entrada + / saída -
            "saldo": saldo
        })

    return transacoes
