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
    s = (valor_str or "").strip()
    s = re.sub(r'\s+', ' ', s)

    # Se o sinal vier no final (ex: "1.234,56-"), move pro começo
    if s.endswith('-'):
        s = '-' + s[:-1].strip()

    # "- R$" -> "-R$"
    s = s.replace('- R$ ', '-R$ ')

    return parse_decimal_br(s)

def _merge_wrapped_lines(linhas: list[str]) -> list[str]:
    """
    PDFs do Itaú frequentemente quebram a mesma linha/lançamento em múltiplas linhas
    (ex.: descrição + razão social em uma linha, e CNPJ/valor na próxima).

    Esta função junta linhas de continuação que NÃO começam com data, mas evita
    colar rodapés (aviso, SAC, etc.) na última linha útil.
    """
    merged: list[str] = []

    def _is_footer_piece(up: str) -> bool:
        return (
            up.startswith("AVISO:")
            or up.startswith("EM CASO DE DÚVIDAS")
            or up.startswith("EM CASO DE DUVIDAS")
            or up.startswith("ATUALIZADO EM")
            or "RECLAMAÇÕES" in up
            or "RECLAMACOES" in up
            or "SAC" in up
            or "OUVIDORIA" in up
            or "FALE CONOSCO" in up
        )

    for raw in (linhas or []):
        line = (raw or "").strip()
        if not line:
            continue

        up = line.upper()
        if _is_footer_piece(up):
            # descarta pedaços de rodapé soltos
            continue

        if _RE_DATA_INICIO.match(line):
            merged.append(line)
        else:
            # continuação: anexa à última linha válida
            if merged:
                merged[-1] = f"{merged[-1]} {line}".strip()
            else:
                merged.append(line)

    return merged


def parse_itau(linhas: list[str]) -> list[dict]:
    """
    Itaú (observado no PDF enviado):
      Data | Lançamentos | Razão Social | CNPJ/CPF | Valor (R$) | Saldo (R$)

    Padrão importante do extrato:
    - Muitas linhas de LANÇAMENTO vêm SÓ com o "Valor (R$)".
    - O "Saldo (R$)" aparece em linhas separadas, como:
        "dd/mm/aaaa SALDO TOTAL DISPONÍVEL DIA <saldo>"

    Estratégia:
    - Parseia lançamentos (valor) e registra com saldo=None
    - Quando encontrar "SALDO TOTAL DISPONÍVEL DIA", atribui esse saldo ao ÚLTIMO
      lançamento do mesmo dia que ainda está com saldo=None.
    - Ignora "SALDO ANTERIOR" e demais cabeçalhos/rodapés.
    """
    transacoes: list[dict] = []
    linhas = _merge_wrapped_lines(linhas)

    def _is_footer_or_header(up: str) -> bool:
        return (
            up.startswith("LANÇAMENTOS DO PERÍODO")
            or up.startswith("LANCAMENTOS DO PERIODO")
            or up.startswith("DATA LANÇAMENTOS")
            or up.startswith("DATA LANCAMENTOS")
            or up.startswith("AVISO:")
            or "EM CASO DE DÚVIDAS" in up
            or "EM CASO DE DUVIDAS" in up
            or "SAC" in up
            or "FALE CONOSCO" in up
            or "ATUALIZADO EM" in up
        )

    def _assign_saldo(dt_movimento, saldo):
        # atribui ao último lançamento do mesmo dia ainda sem saldo
        for i in range(len(transacoes) - 1, -1, -1):
            tx = transacoes[i]
            if tx.get("dt_movimento") == dt_movimento and tx.get("saldo") is None:
                tx["saldo"] = saldo
                return True
        return False

    for linha in linhas:
        linha = (linha or "").strip()
        if not linha:
            continue

        up = linha.upper()
        if _is_footer_or_header(up):
            continue

        m = _RE_DATA_INICIO.match(linha)
        if not m:
            continue

        data_str, resto = m.groups()
        dt_movimento = parse_data_br(data_str)
        resto_up = resto.upper()

        # "SALDO ANTERIOR" não é uma transação conciliável
        if "SALDO ANTERIOR" in resto_up:
            continue

        # Linha de saldo do dia (vem separada do lançamento)
        if resto_up.startswith("SALDO TOTAL"):
            valores = list(_RE_MOEDA.finditer(resto))
            if not valores:
                continue
            try:
                saldo = _parse_decimal_itau(valores[-1].group(1))
            except Exception:
                continue
            _assign_saldo(dt_movimento, saldo)
            continue

        # Demais lançamentos: normalmente têm 1 valor (coluna Valor)
        valores = list(_RE_MOEDA.finditer(resto))
        if not valores:
            continue

        # Se por acaso vierem 2 valores na mesma linha (alguns PDFs trazem Valor+Saldo),
        # mantém a lógica antiga.
        valor_str = valores[-2].group(1) if len(valores) >= 2 else valores[-1].group(1)
        saldo_str = valores[-1].group(1) if len(valores) >= 2 else None

        try:
            valor = _parse_decimal_itau(valor_str)
            saldo = _parse_decimal_itau(saldo_str) if saldo_str and len(valores) >= 2 else None
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

        # Descrição: tudo antes do valor (ou do penúltimo valor quando houver 2)
        corte_idx = valores[-2].start() if len(valores) >= 2 else valores[-1].start()
        descricao = resto[:corte_idx].strip()
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
            "saldo": saldo      # pode vir None; tentamos preencher no "SALDO TOTAL ..."
        })

    return transacoes
