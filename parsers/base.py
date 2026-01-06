
import re
from decimal import Decimal
from datetime import datetime

def parse_decimal_br(valor_str: str) -> Decimal:
    s = (valor_str or "").strip()

    # normaliza espaços
    s = re.sub(r"\s+", " ", s)

    # sinal no fim: "1.234,56-" -> "-1.234,56"
    if s.endswith("-"):
        s = "-" + s[:-1].strip()

    # normaliza "- R$" -> "-R$"
    s = s.replace("- R$", "-R$")

    # remove moeda e espaços residuais
    s = s.replace("R$", "").strip()

    # remove separador milhar e troca decimal
    s = s.replace(".", "").replace(",", ".")

    # remove espaços entre sinal e número (caso "- 1234.56")
    s = s.replace("- ", "-")

    return Decimal(s)


def parse_data_br(data_str: str) -> datetime.date:
    """
    Converte '31/10/2025' ou '27 OUT 2025'
    """
    data_str = data_str.strip().upper()
    try:
        return datetime.strptime(data_str, '%d/%m/%Y').date()
    except ValueError:
        meses = {
            'JAN': '01','FEV': '02','MAR': '03','ABR': '04','MAI': '05','JUN': '06',
            'JUL': '07','AGO': '08','SET': '09','OUT': '10','NOV': '11','DEZ': '12'
        }
        for mes, num in meses.items():
            if mes in data_str:
                data_str = data_str.replace(mes, num)
        return datetime.strptime(data_str, '%d %m %Y').date()

def extrair_valores_monetarios(linha: str):
    return re.findall(r'-?R?\$?\s?\d{1,3}(?:\.\d{3})*,\d{2}', linha)

