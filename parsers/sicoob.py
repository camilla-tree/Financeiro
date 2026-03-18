import re
from decimal import Decimal
from .base import parse_data_br

# Ex: "03/02/2025 1532449 CRÉD.LIQUIDAÇÃO..."
_RE_DATA_INICIO = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.*)$')

# Captura o valor no final da linha com C, D (ou 0 por erro de leitura do PDF)
# Ex: "418.464,16C" | "449.329.27D" | "119.400,000"
_RE_VALOR_CD = re.compile(r'(-?[\d.,]+)\s*([CDcd0])\s*$', re.IGNORECASE)

def parse_sicoob(linhas: list[str]) -> list[dict]:
    """
    Parser SICOOB - Extrato conta corrente (PDF puramente textual)
    
    Retorna lista de dicts padronizada:
      - dt_movimento (date)
      - descricao (str)
      - documento (str|None)
      - valor (Decimal, entrada + / saída -)
      - saldo (Decimal|None) -> capturado do "SALDO DO DIA"
    """
    transacoes: list[dict] = []
    current_tx = None

    def _assign_saldo(dt_movimento, saldo):
        # Atribui o saldo ao último lançamento daquele dia
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

        # 1) Ignorar Cabeçalhos e Rodapés padronizados do Sicoob
        if (
            up.startswith("SICOOB") or
            up.startswith("SISTEMA DE COOP") or
            up.startswith("SISBR") or
            up.startswith("EXTRATO DE CONTA") or
            up.startswith("COOPERATIVA:") or
            up.startswith("CONTA:") or
            up.startswith("LANÇAMENTOS") or
            up.startswith("LANCAMENTOS") or
            up.startswith("DATA DOCUMENTO") or
            up.startswith("RESUMO") or
            up.startswith("SALDO EM CONTA") or
            up.startswith("LIMITE CHEQUE") or
            up.startswith("SALDO DISPON") or
            up.startswith("SALDO BLOQUEADO") or
            up.startswith("VENCIMENTO CHEQUE") or
            up.startswith("TAXA CHEQUE") or
            up.startswith("SAC:") or
            up.startswith("OUVIDORIA") or
            "HTTPS://" in up or
            "SALDO ANTERIOR" in up or
            re.match(r'^\d+/\d+$', up) # Páginas tipo "1/6"
        ):
            continue

        # 2) Capturar Saldo do Dia (vem em uma linha separada)
        if up.startswith("SALDO DO DIA"):
            m_val = _RE_VALOR_CD.search(up)
            if m_val:
                val_str = m_val.group(1)
                cd = m_val.group(2).upper()
                
                # Limpa tudo que não for número e divide por 100
                val_clean = re.sub(r'[^\d]', '', val_str)
                if val_clean:
                    saldo_num = Decimal(val_clean) / Decimal(100)
                    if cd == 'D':
                        saldo_num = -saldo_num
                    
                    if current_tx:
                        _assign_saldo(current_tx["dt_movimento"], saldo_num)
            continue

        # 3) Capturar Nova Transação
        m_data = _RE_DATA_INICIO.match(linha)
        if m_data:
            if current_tx:
                transacoes.append(current_tx)

            data_str, resto = m_data.groups()
            dt_movimento = parse_data_br(data_str)

            m_val = _RE_VALOR_CD.search(resto)
            if not m_val:
                continue

            val_str = m_val.group(1)
            cd = m_val.group(2).upper()

            # A descrição e documento é tudo o que sobra antes do valor
            desc_doc = resto[:m_val.start()].strip()

            # Separar documento da descrição
            parts = desc_doc.split(maxsplit=1)
            doc_num = None
            desc = desc_doc
            
            if len(parts) >= 2:
                fw = parts[0]
                # Se a primeira palavra for número ou "Pix", separamos
                if fw.upper() == "PIX" or fw.isdigit():
                    doc_num = fw if fw.upper() != "PIX" else None
                    desc = "Pix - " + parts[1] if fw.upper() == "PIX" else parts[1]

            # Conversão matemática imune a erros de pontuação (119.400,000 -> 119400.00)
            val_clean = re.sub(r'[^\d]', '', val_str)
            if val_clean:
                numeric_val = Decimal(val_clean) / Decimal(100)
                # O PDF às vezes lê a letra C como 0. Tudo que não for 'D' tratamos como crédito.
                if cd == 'D':
                    numeric_val = -numeric_val
            else:
                numeric_val = Decimal(0)

            current_tx = {
                "dt_movimento": dt_movimento,
                "descricao": desc,
                "documento": doc_num,
                "valor": numeric_val,
                "saldo": None
            }
        
        # 4) Continuação da Descrição (Linhas sem data)
        else:
            if current_tx:
                current_tx["descricao"] += f" - {linha}"

    # Adiciona a última transação processada
    if current_tx:
        transacoes.append(current_tx)

    return transacoes