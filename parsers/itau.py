import re
from .base import parse_decimal_br, parse_data_br

# Ex: "26/10/2025 ..."
_RE_DATA_INICIO = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.*)$')

# Captura números monetários BR, com ou sem R$, e com "-" no começo ou no fim.
# Exemplos aceitos:
#  "1.234,56" | "-1.234,56" | "R$ 1.234,56" | "- R$ 1.234,56" | "1.234,56-"
_RE_MOEDA = re.compile(r'(-?\s*R?\$?\s*\d{1,3}(?:\.\d{3})*,\d{2}-?)')

# CNPJ e CPF em formatos comuns (com ou sem máscara)
_RE_CNPJ = re.compile(r'\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b')
_RE_CPF  = re.compile(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b')

def _parse_decimal_itau(valor_str: str):
    """
    Normaliza casos:
      "- R$ 1.234,56" -> "-R$ 1.234,56"
      "1.234,56-"     -> "-1.234,56"
    e usa parse_decimal_br.
    """
    s = valor_str.strip()
    s = re.sub(r'\s+', ' ', s)

    # Se o sinal vier no final (ex: "1.234,56-"), move pro começo
    if s.endswith('-'):
        s = '-' + s[:-1].strip()

    # "- R$" -> "-R$"
    s = s.replace('- R$ ', '-R$ ')
    s = s.replace('-R$ ', '-R$ ')

    # Se não tiver R$, parse_decimal_br também aceita (ele só remove se existir)
    return parse_decimal_br(s)

def parse_itau(linhas: list[str]) -> list[dict]:
    """
    Itaú (esperado):
      Data | Lançamentos | Razão Social | CNPJ/CPF | Valor (R$) | Saldo (R$)

    Regras:
    - Ignorar cabeçalhos/rodapés
    - Linha válida começa com data dd/mm/aaaa
    - Capturar valores monetários no fim:
        - se >=2: penúltimo = valor; último = saldo
        - se ==1: normalmente "SALDO ANTERIOR" ou linha incompleta -> ignorar
    - Capturar CNPJ/CPF (se existir) e colocar em documento (opcional)
    """
    transacoes = []

    for linha in linhas:
        linha = (linha or "").strip()
        if not linha:
            continue

        up = linha.upper()

        # Ignora linhas de cabeçalho/rodapé comuns
        if (
            up.startswith("SALDO TOTAL")
            or up.startswith("LANÇAMENTOS DO PERÍODO")
            or up.startswith("LANCAMENTOS DO PERIODO")
            or up.startswith("DATA LANÇAMENTOS")
            or up.startswith("DATA LANCAMENTOS")
            or up.startswith("AVISO:")
            or "EM CASO DE DÚVIDAS" in up
            or "EM CASO DE DUVIDAS" in up
            or "SAC" in up
            or "FALE CONOSCO" in up
            or "ATUALIZADO EM" in up
        ):
            continue

        m = _RE_DATA_INICIO.match(linha)
        if not m:
            continue

        data_str, resto = m.groups()
        dt_movimento = parse_data_br(data_str)

        # Se for "SALDO ANTERIOR", não é uma transação conciliável
        if "SALDO ANTERIOR" in resto.upper():
            continue

        # Captura valores monetários
        valores = list(_RE_MOEDA.finditer(resto))
        if len(valores) < 2:
            # no PDF que você enviou, aparece só saldo em "SALDO ANTERIOR"
            # para lançamentos reais, esperamos pelo menos 2 (valor e saldo)
            continue

        valor_str = valores[-2].group(1)
        saldo_str = valores[-1].group(1)

        try:
            valor = _parse_decimal_itau(valor_str)
            saldo = _parse_decimal_itau(saldo_str)
        except Exception:
            continue

        # Documento: tenta extrair CNPJ/CPF, se existir
        documento = None
        cnpj = _RE_CNPJ.search(resto)
        cpf = _RE_CPF.search(resto)
        if cnpj:
            documento = cnpj.group(0)
        elif cpf:
            documento = cpf.group(0)

        # Descrição: tudo antes do penúltimo valor
        descricao = resto[:valores[-2].start()].strip()
        descricao = re.sub(r'\s+', ' ', descricao)

        # Se tiver documento dentro da descrição, remove (pra não poluir)
        if documento:
            descricao = descricao.replace(documento, '').strip()
            descricao = re.sub(r'\s+', ' ', descricao)

        transacoes.append({
            "dt_movimento": dt_movimento,
            "descricao": descricao,
            "documento": documento,
            "valor": valor,     # entrada + / saída -
            "saldo": saldo
        })

    return transacoes
