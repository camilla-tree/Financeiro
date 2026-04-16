import os
import sys

# Adiciona o diretório atual no path para importar db.py
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from db import run_sql, fetch_df

def run():
    print("Iniciando migration...")
    
    # 1. Adiciona coluna tipo_relatorio (se não existir)
    try:
        run_sql("ALTER TABLE movimento_bancario ADD COLUMN tipo_relatorio VARCHAR(20) NOT NULL DEFAULT 'EMPRESA';")
        print("Coluna tipo_relatorio adicionada em movimento_bancario.")
    except Exception as e:
        print("Aviso na coluna 1 (pode já existir):", e)

    try:
        run_sql("ALTER TABLE movimento_processo ADD COLUMN tipo_relatorio VARCHAR(20) NOT NULL DEFAULT 'EMPRESA';")
        print("Coluna tipo_relatorio adicionada em movimento_processo.")
    except Exception as e:
        print("Aviso na coluna 2 (pode já existir):", e)

    # 2. Migra os dados baseados no is_cliente
    try:
        # Movimento Bancário
        run_sql("UPDATE movimento_bancario SET tipo_relatorio = 'CLIENTE' WHERE is_cliente = true;")
        print("Dados migrados em movimento_bancario.")
        # Movimento Processo
        run_sql("UPDATE movimento_processo SET tipo_relatorio = 'CLIENTE' WHERE is_cliente = true;")
        print("Dados migrados em movimento_processo.")
    except Exception as e:
        print("Erro na migração de dados:", e)

    # 3. Alterar a VIEW vw_movimento_bancario_conciliado se existir
    # A view talvez precise ser recriada se tiver select * ou depender de is_cliente
    try:
        df_view = fetch_df("SELECT definition FROM pg_views WHERE viewname = 'vw_movimento_bancario_conciliado';")
        if not df_view.empty:
            definition = df_view.iloc[0]['definition']
            print("\nDefinição da VIEW atual:")
            print(definition)
            # Não precisamos mexer na view se ela não explicitar is_cliente e der erro. Deixa para depois.
    except Exception as e:
         print("Erro na VIEW:", e)

    # 4. Remove a coluna is_cliente
    # Descomente isso se tiver certeza, mas vamos tentar dropar
    try:
        run_sql("ALTER TABLE movimento_bancario DROP COLUMN is_cliente;")
        print("Coluna is_cliente removida de movimento_bancario.")
        run_sql("ALTER TABLE movimento_processo DROP COLUMN is_cliente;")
        print("Coluna is_cliente removida de movimento_processo.")
    except Exception as e:
        print("Aviso ao dropar a coluna (pela VIEW ou dependencia):", e)

if __name__ == "__main__":
    run()
