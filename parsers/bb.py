# parsers/bb.py
import re
from .base import parse_decimal_br, parse_data_br

# Linha principal do BB começa com data completa:
# "05/01/2026 0000 13105 144 Pix - Enviado 10.501 2.277,00 D"
_RE_DATA_INICIO = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.*)$')

# Continuação comum (beneficiário) vem como:
# "05/01 12:55 D C ASSESSORIA CONTABIL L"
_RE_CONTINUACAO_HORA = re.compile(r'^(\d{2}/\d{2})\s+\d{2}:\d{2}\s+(.+)$')

# A regex captura [C], [D], [(+)] ou [(-)]
_RE_CD = re.compile(r'(\d{1,3}(?:\.\d{3})*,\d{2})\s*([CD]|\(\+\)|\(\-\))', re.IGNORECASE)

# Alguns docs aparecem como "10.504" etc. (opcional)
_RE_DOC = re.compile(r'\b\d{1,3}(?:\.\d{3})+\b|\b\d{4,}\b')

def _clean_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _saldo_signed(valor_str: str, cd: str):
    """Saldo pode vir com C/D ou (+)/(-) no PDF. Mantemos sinal."""
    v = parse_decimal_br(valor_str)
    cd = (cd or "").upper().strip()
    return v if cd in ["C", "(+)"] else -v

def _valor_signed(valor_str: str, cd: str):
    """VALOR do lançamento: assinado conforme C/D."""
    v = parse_decimal_br(valor_str)
    cd = (cd or "").upper().strip()
    try:
        if cd in ["C", "(+)"]:
            return abs(v)
        elif cd in ["D", "(-)"]:
            return -abs(v)
        return -abs(v)
    except Exception:
        return -v if cd in ["D", "(-)"] else v

def _tipo_from_cd(cd: str) -> str:
    cd = (cd or "").upper().strip()
    if cd in ["C", "(+)"]:
        return "entrada"
    if cd in ["D", "(-)"]:
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

        # ignora linha grande "S A L D O" (quebra o laço e ignora o resto abaixo)
        if "S A L D O" in up:
            break

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

    # 3) Nova Lógica Baseada em Âncoras de Valor (Sugerida e Aprimorada)
    # Junta tudo em uma única string e fatia considerando os valores como âncoras.
    texto_completo = _clean_spaces(" ".join(trecho))
    
    data_atual = None
    inicio_fatia = 0
    
    iter_valores = list(_RE_CD.finditer(texto_completo))

    for match in iter_valores:
        valor_str = match.group(1)
        cd_str = match.group(2)

        fatia_texto = texto_completo[inicio_fatia:match.start()].strip()
        inicio_fatia = match.end()

        # Ignora saldos isolados que sobram na string
        up_fatia = fatia_texto.upper()
        if "S A L D O" in up_fatia:
            break
        if re.search(r"\bS\s*A\s*L\s*D\s*O\b", up_fatia):
            continue

        # Verifica se fatia é um indicativo de salto e saldo isolado de transação
        # Se a fatia tem pouquíssimos ou nenhum caractere (sem letras), 
        # é indicativo de que este valor seja apenas o saldo referente ao lançamento anterior.
        tem_letras = bool(re.search(r'[a-zA-Z]', fatia_texto))
        if transacoes and not tem_letras and len(fatia_texto) < 15:
            # É provável que seja o saldo listado em linha com a transação
            transacoes[-1]['saldo'] = _saldo_signed(valor_str, cd_str)
            continue

        # Verifica se há novas datas dentro desta fatia de texto (TODO o bloco original)
        datas = re.findall(r'\b\d{2}/\d{2}/\d{4}\b', fatia_texto)
        if datas:
            datas_validas = [d for d in datas if d != "00/00/0000"]
            if datas_validas:
                try:
                    data_atual = parse_data_br(datas_validas[-1])
                except Exception:
                    pass

        if not data_atual:
            continue

        # Regra Específica: Texto "órfão" antes do BB Rende Fácil pertence ao lançamento anterior
        if transacoes:
            up_f = fatia_texto.upper()
            idx_rende = up_f.find("BB RENDE FÁCIL")
            if idx_rende == -1:
                idx_rende = up_f.find("BB RENDE FACIL")
            
            if idx_rende > 0:
                texto_antes = fatia_texto[:idx_rende].strip()
                # Removemos as datas deste pedaço antes de apensar ao lançamento anterior
                texto_antes = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '', texto_antes)
                texto_antes = _clean_spaces(texto_antes)
                if texto_antes:
                    transacoes[-1]["descricao"] = (transacoes[-1]["descricao"] + " " + texto_antes).strip()
                # O lançamento atual passa a considerar do "BB Rende Fácil" pra frente
                fatia_texto = fatia_texto[idx_rende:].strip()

        # Limpa as datas de dentro da descrição atual para não poluir
        desc_limpa = re.sub(r'\b\d{2}/\d{2}/\d{4}\b', '', fatia_texto)
        desc_limpa = _clean_spaces(desc_limpa)

        # Regras de limpeza de BB
        idx_pix = desc_limpa.upper().find("PIX -")
        if idx_pix != -1:
            desc_limpa = desc_limpa[idx_pix:]

        desc_limpa = re.sub(r'(?i)^RENDE F[AÁ]CIL\s*', '', desc_limpa).strip()

        # Processa valores e documentos
        valor = _valor_signed(valor_str, cd_str)
        tipo = _tipo_from_cd(cd_str)

        doc_candidates = list(_RE_DOC.finditer(desc_limpa))
        documento = doc_candidates[-1].group(0) if doc_candidates else None

        if not desc_limpa:
            desc_limpa = "Lançamento"

        transacoes.append({
            "dt_movimento": data_atual,
            "descricao": desc_limpa,
            "documento": documento,
            "valor": valor,
            "tipo": tipo,
            "saldo": None  # Saldos serão preenchidos no próximo ciclo, se caírem no critério acima
        })

    return transacoes
