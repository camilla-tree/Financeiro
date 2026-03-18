import pandas as pd
from db import fetch_df

df = fetch_df("SELECT * FROM vw_movimento_bancario_conciliado LIMIT 1;")
print("Columns in view:", df.columns.tolist())
