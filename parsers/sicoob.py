import re
import io
import pandas as pd
from decimal import Decimal
from .base import parse_data_br

# Ex: "03/02/2025 1532449 CRÉD.LIQUIDAÇÃO..."
_RE_DATA_INICIO = re.compile(r'^(\d{2}/\d{2}/\d{4})\s+(.*)$')

# Captura o valor no final da linha com C, D (ou 0 por erro de leitura do PDF)
# Ex: "418.464,16C" | "449.329.27D" | "119.400,000"
_RE_VALOR_CD = re.compile(r'(-?[\d.,]+)\s*([CDcd0])\s*$', re.IGNORECASE)

def parse_sicoob_excel(file_bytes: bytes) -> list[dict]:
    """
    Parser SICOOB - Extrato conta corrente em Excel (.xlsx)
    """
    df = pd.read_excel(io.BytesIO(file_bytes), header=None)
    
    # Achar linha de cabeçalho
    header_idx = -1
    for i, row in df.iterrows():
        row_str = [str(x).strip().upper() for x in row.values]
        if 'DATA' in row_str and 'HISTÓRICO' in row_str:
            header_idx = i
            break
            
    if header_idx == -1:
        raise ValueError("Não foi possível encontrar a linha de cabeçalho (DATA, HISTÓRICO) no arquivo .xlsx.")
        
    df.columns = [str(c).strip().upper() for c in df.iloc[header_idx].values]
    df = df.iloc[header_idx + 1:].reset_index(drop=True)
    
    transacoes = []
    last_date = None
    last_historico = None
    
    for _, row in df.iterrows():
        data_val = row.get("DATA")
        historico_raw = row.get("HISTÓRICO")
        
        hist_str = ""
        if not pd.isna(historico_raw):
            hist_str = str(historico_raw).strip()
            
        if hist_str.upper() in ["SALDO DO DIA", "SALDO ANTERIOR"]:
            continue
            
        dt_mov = None
        # Parse data se houver
        if not pd.isna(data_val) and str(data_val).strip() != "":
            if isinstance(data_val, pd.Timestamp):
                dt_mov = data_val.date()
            else:
                try:
                    dt_mov = parse_data_br(str(data_val).strip()[:10])
                except Exception:
                    pass

        # Atualização do contexto (memória para as linhas que vêm sem data/histórico)
        if dt_mov:
            last_date = dt_mov
            last_historico = hist_str.upper()
        else:
            dt_mov = last_date
            
        if not dt_mov:
            continue
            
        # Para o parent row de "CRÉD.LIQUIDAÇÃO COBRANÇA" guardamos o estado
        if hist_str.upper() == "CRÉD.LIQUIDAÇÃO COBRANÇA" and (not pd.isna(data_val) and str(data_val).strip() != ""):
            last_historico = "CRÉD.LIQUIDAÇÃO COBRANÇA"
            
        is_sub_transacao = False
        
        # Identificando sub-transações (linhas sem DATA preenchida)
        if pd.isna(data_val) or str(data_val).strip() == "":
            if last_historico == "CRÉD.LIQUIDAÇÃO COBRANÇA":
                is_sub_transacao = True
            else:
                continue

        # Parse descricao e detalhamento
        desc = hist_str
        detalhe = row.get("DETALHAMENTO")
        
        if is_sub_transacao:
            desc = "CRÉD.LIQUIDAÇÃO COBRANÇA"
            if not pd.isna(detalhe) and str(detalhe).strip():
                desc += f" - {str(detalhe).strip()}"
        else:
            if not pd.isna(detalhe) and str(detalhe).strip():
                if desc:
                    desc = f"{desc} - {str(detalhe).strip()}"
                else:
                    desc = str(detalhe).strip()
                
        # Documento
        doc = None
        if is_sub_transacao:
            doc_raw = row.get("DOC")
            if not pd.isna(doc_raw) and str(doc_raw).strip() != "":
                doc = str(doc_raw).strip()
        else:
            doc_raw = row.get("DOCUMENTO")
            if not pd.isna(doc_raw) and str(doc_raw).strip() != "":
                doc = str(doc_raw).strip()

        # Valor
        valor_val = None
        if is_sub_transacao or hist_str.upper() == "CRÉD.LIQUIDAÇÃO COBRANÇA":
            valor_val = row.get("COMPROV")
        else:
            valor_col = None
            for col in df.columns:
                if "VALOR" in col:
                    valor_col = col
                    break
            if valor_col:
                valor_val = row.get(valor_col)
                
        if pd.isna(valor_val) or str(valor_val).strip() == "":
            continue
            
        try:
            if isinstance(valor_val, str):
                v_str = str(valor_val).upper().replace('R$', '').strip()
                if v_str.endswith('C') or v_str.endswith('D'):
                    is_d = v_str.endswith('D')
                    v_clean = re.sub(r'[^\d]', '', v_str)
                    numeric_val = Decimal(v_clean) / Decimal(100)
                    if is_d:
                        numeric_val = -numeric_val
                else:
                    v_clean = v_str.replace('.', '').replace(',', '.')
                    numeric_val = Decimal(re.sub(r'[^\d\.-]', '', v_clean))
            else:
                numeric_val = Decimal(f"{float(valor_val):.2f}")
        except Exception:
            numeric_val = Decimal(0)
            
        if numeric_val == 0:
            pass
            
        transacoes.append({
            "dt_movimento": dt_mov,
            "descricao": desc,
            "documento": doc,
            "valor": numeric_val,
            "saldo": None
        })
        
    return transacoes

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