import re
import fitz
import pandas as pd
from datetime import datetime

def parse_sicoob_pdf(filepath: str) -> pd.DataFrame:
    """
    Lê um extrato PDF do SICOOB e retorna um DataFrame com:
    [data_movimento, documento, descricao, valor, tipo, origem_formato, texto_raw, pagina, linha_ordem]
    """
    doc = fitz.open(filepath)
    text = ""
    for page in doc:
        text += page.get_text() + "\n"

    lines = text.split('\n')
    
    # regex to match date like dd/mm/yyyy
    date_regex = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    money_regex = re.compile(r"^-?\d{1,3}(?:\.\d{3})*(?:,\d{2})?[DC]$")
    
    records = []
    i = 0
    linha_ordem = 1
    
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
            
        if date_regex.match(line):
            date_val = line
            
            if i + 1 < len(lines):
                next_line = lines[i+1].strip()
                
                # Check next line: Document number or Description
                if next_line.isdigit():
                    doc_num = next_line
                    i += 2
                else:
                    doc_num = ""
                    i += 1
                
                desc_lines = []
                val_str = None
                
                # Read description until money regex or break words
                while i < len(lines):
                    l2 = lines[i].strip()
                    
                    if date_regex.match(l2) or l2 in ["SALDO DO DIA", "RESUMO"] or "Sicoob" in l2 or "https://" in l2 or ("/" in l2 and len(l2) <= 5):
                        # Hit next block
                        break
                        
                    if money_regex.match(l2):
                        val_str = l2
                        i += 1
                        break
                    else:
                        if l2 and l2 != "Imprimir":
                            desc_lines.append(l2)
                        i += 1
                        
                if val_str:
                    desc_full = " - ".join(desc_lines)
                    val_clean = val_str[:-1].replace('.', '').replace(',', '.')
                    numeric_val = float(val_clean)
                    tipo = 'C' if val_str.endswith('C') else 'D'
                    if tipo == 'D':
                        numeric_val = -numeric_val
                        
                    dt_obj = datetime.strptime(date_val, "%d/%m/%Y").date()
                    
                    # Store as raw line for context (concatenated logic context)
                    raw_text = f"{date_val} {doc_num} {desc_full} {val_str}".replace("  ", " ").strip()
                    
                    records.append({
                        "data_movimento": dt_obj,
                        "documento": doc_num,
                        "descricao": desc_full,
                        "valor": numeric_val,
                        "tipo": tipo,
                        "origem_formato": "PDF",
                        "texto_raw": raw_text,
                        "pagina": 1,  # simplifying page track
                        "linha_ordem": linha_ordem
                    })
                    linha_ordem += 1
                    continue
        
        i += 1

    return pd.DataFrame(records)
