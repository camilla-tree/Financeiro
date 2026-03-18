import re
import fitz
from datetime import datetime

def parse_sicoob_pdf(lines: list[str]) -> list[dict]:
    """
    Lê um extrato PDF do SICOOB (lista de linhas) e retorna uma lista de dicionários com:
    [data_movimento, documento, descricao, valor, tipo, origem_formato, texto_raw, pagina, linha_ordem]
    """

    row_regex = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.*?)\s+(-?\d{1,3}(?:\.\d{3})*(?:,\d{2})?[DC])$")
    records = []
    current_record = None
    linha_ordem = 1
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        m = row_regex.match(line)
        if m:
            if current_record:
                records.append(current_record)
                
            date_val = m.group(1)
            raw_desc = m.group(2)
            val_str = m.group(3)
            
            doc_num = ""
            desc = raw_desc
            parts = raw_desc.split(maxsplit=1)
            if len(parts) >= 2:
                fw = parts[0]
                if fw.lower() == "pix" or fw.isdigit():
                    doc_num = fw if fw.lower() != "pix" else ""
                    desc = "Pix - " + parts[1] if fw.lower() == "pix" else parts[1]
            
            val_clean = val_str[:-1].replace('.', '').replace(',', '.')
            numeric_val = float(val_clean)
            tipo = 'C' if val_str.endswith('C') else 'D'
            if tipo == 'D':
                numeric_val = -numeric_val
                
            dt_obj = datetime.strptime(date_val, "%d/%m/%Y").date()
            
            current_record = {
                "data_movimento": dt_obj,
                "documento": doc_num,
                "descricao": desc,
                "valor": numeric_val,
                "tipo": tipo,
                "origem_formato": "PDF",
                "texto_raw": line,
                "pagina": 1,
                "linha_ordem": linha_ordem
            }
            linha_ordem += 1
        else:
            if current_record and "SALDO DO DIA" not in line and "SALDO ANTERIOR" not in line and "SALDO BLOQUEADO" not in line and "Sicoob" not in line and "https://" not in line and "EXTRATO" not in line and len(line) > 3:
                current_record["descricao"] += f" - {line}"
                current_record["texto_raw"] += f" {line}"
            
    if current_record:
        records.append(current_record)
        
    final_records = [r for r in records if "SALDO ANTERIOR" not in r["descricao"]]
    return final_records
