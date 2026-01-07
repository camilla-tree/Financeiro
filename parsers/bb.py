# parsers/bb.py
import re
from .base import parse_decimal_br, parse_data_br

# Linha principal do BB começa com data completa:
# "05/01/2026 0000 13105 144 Pix - Enviado 10.501 2.277,00 D"
_RE_DATA_INICIO = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.*)$')

# Continuação comum (beneficiário) vem como:
# "05/01 12:55 D C ASSESSORIA CONTABIL L"
_RE_CONTINUACAO_HORA = re.compile(r'^(\d{2}/\d{2})\s+\d{2}:\d{2}\s+(.+)$')

# Moeda + indicador C/D do BB
_RE_CD = re.compile(r'(\d{1,3}(?:\.\d{3})*,\d{2})\s*([CD])\b', re.IGNORECASE)

# Alguns docs aparecem como "10.504" etc. (opcional)
_RE_DOC = re.compile(r'\b\d{1,3}(?:\.\d{3})+\b|\b\d{4,}\b')

def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _saldo_signed(valor_str: str, cd: str):
    """Saldo pode vir com C/D no PDF. Mantemos sinal: C positivo, D negativo."""
    v = parse_decimal_br(valor_str)
    cd = (cd or "").upper().strip()
    return v if cd == "C" else -v

def _valor_positive(valor_str: str):
    """VALOR do lançamento: sempre positivo (regra do seu app)."""
    v = parse_decimal_br(valor_str)
    try:
        return abs(v)
    except Exception:
        return v

def _tipo_from_cd(cd: str) -> str:
    cd = (cd or "").upper().strip()
    if cd == "C":
        return "entrada"
    if cd == "D":
        return "saida"
    return "saida"  # fallback

def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Remove vários trechos do texto (spans) e mantém o restante."""
    if not spans:
        return text
    spans = sorted(spans, key=lambda x: x[0])
    out = []
    last = 0
    for a, b in spans:
        if a > last:
            out.append(text[last:a])
        last = max(last, b)
    if last < len(text):
        out.append(text[last:])
    return "".join(out)

def parse_bb(linhas: list[str]) -> list[dict]:
    """
    Parser Banco do Brasil - Extrato conta corrente (PDF)

    Retorna lista de dicts com:
      - dt_movimento (date)
      - descricao (str)  -> completa, incluindo beneficiário/continuações
      - documento (str|None)
      - valor (Decimal, sempre positivo)
      - tipo ("entrada" | "saida") baseado em C/D do VALOR
      - saldo (Decimal|None) -> pode vir assinado (C/D)
    """
    transacoes: list[dict] = []

    # 1) normaliza linhas
    raw = []
    for ln in (linhas or []):
        s = _clean_spaces(ln)
        if s:
            raw.append(s)

    # 2) pega trecho entre "Lançamentos" e "Lançamentos futuros"
    started = False
    trecho = []
    for ln in raw:
        up = ln.upper()

        if not started:
            if up.startswith("LANÇAMENTOS"):
                started = True
            continue

        if up.startswith("LANÇAMENTOS FUTUROS"):
            break

        if "SALDO ANTERIOR" in up:
            continue

        # ignora linha grande "S A L D O"
        if up.strip() == "S A L D O":
            continue

        if up.replace(" ", "") == "SALDO":
            continue

        # ignora linha tipo: "0000 00000 999 S A L D O" (com prefixo numérico)
        if re.search(r"\bS\s*A\s*L\s*D\s*O\b", up):
            continue


        # ignora rodapés comuns
        if (
            up.startswith("OBSERVA")
            or up.startswith("SERVIÇO DE ATENDIMENTO")
            or up.startswith("SAC ")
            or up.startswith("OUVIDORIA")
            or up.startswith("PARA DEFICIENTES")
            or up.startswith("TRANSAÇÃO EFETUADA")
        ):
            continue

        trecho.append(ln)

    # fallback
    if not trecho:
        trecho = raw[:]

    # 3) junta continuações na linha anterior
    linhas_join: list[str] = []
    for ln in trecho:
        if _RE_DATA_INICIO.match(ln):
            linhas_join.append(ln)
            continue

        # continuação com hora (05/01 12:55 ...)
        if _RE_CONTINUACAO_HORA.match(ln) and linhas_join:
            linhas_join[-1] = _clean_spaces(linhas_join[-1] + " " + ln)
            continue

        # qualquer outra continuação cola na anterior
        if linhas_join:
            linhas_join[-1] = _clean_spaces(linhas_join[-1] + " " + ln)
        else:
            linhas_join.append(ln)

    # 4) parse
    for ln in linhas_join:
        m = _RE_DATA_INICIO.match(ln)
        if not m:
            continue

        dt_str, resto = m.group(1), m.group(2)
        try:
            dt_movimento = parse_data_br(dt_str)
        except Exception:
            continue

        cds = list(_RE_CD.finditer(resto))
        if not cds:
            continue

        # valor / saldo
        if len(cds) >= 2:
            valor_num, valor_cd = cds[-2].group(1), cds[-2].group(2)
            saldo_num, saldo_cd = cds[-1].group(1), cds[-1].group(2)
            saldo = _saldo_signed(saldo_num, saldo_cd)
            spans_to_remove = [(cds[-2].start(), cds[-2].end()), (cds[-1].start(), cds[-1].end())]
        else:
            valor_num, valor_cd = cds[-1].group(1), cds[-1].group(2)
            saldo = None
            spans_to_remove = [(cds[-1].start(), cds[-1].end())]

        valor = _valor_positive(valor_num)
        tipo = _tipo_from_cd(valor_cd)

        # ✅ descrição completa: remove apenas os tokens monetários e mantém o resto (inclusive partes após o valor)
        desc_raw = _remove_spans(resto, spans_to_remove)
        desc_raw = _clean_spaces(desc_raw)

        # documento (opcional): tenta pegar o "doc" mais característico (ex.: 10.504) sem destruir a descrição
        documento = None
        # aqui eu prefiro pegar o ÚLTIMO padrão de doc antes do valor (geralmente faz mais sentido)
        # se não achar, deixa None
        doc_candidates = list(_RE_DOC.finditer(desc_raw))
        if doc_candidates:
            documento = doc_candidates[-1].group(0)

        descricao = desc_raw

        if not descricao:
            descricao = "Lançamento"

        transacoes.append(
            {
                "dt_movimento": dt_movimento,
                "descricao": descricao,
                "documento": documento,
                "valor": valor,   # sempre positivo
                "tipo": tipo,     # C=entrada, D=saida
                "saldo": saldo,   # pode ser None
            }
        )

    return transacoes
