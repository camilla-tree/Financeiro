# services/conciliacao_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from db import fresh_conn


@dataclass(frozen=True)
class ConciliacaoMaps:
    cat_id_by_label: Dict[str, Optional[int]]
    proc_id_by_label: Dict[str, Optional[int]]
    cliente_id_by_label: Dict[str, Optional[int]]


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        if pd.isna(v):
            return None
        return int(v)
    except Exception:
        return None


def calcular_changes(
    df_mov: pd.DataFrame,
    edited: pd.DataFrame,
    maps: ConciliacaoMaps,
) -> List[dict]:
    current_is_conc = {
        int(r["movimento_id"]): bool(pd.notna(r["conciliacao_id"]))
        for _, r in df_mov.iterrows()
    }
    
    current_is_cliente = {
        int(r["movimento_id"]): bool(r.get("is_cliente", False))
        for _, r in df_mov.iterrows()
    }

    changes: List[dict] = []

    # Para lookup rápido
    df_mov_by_id = df_mov.set_index("movimento_id", drop=False)

    for i in range(len(edited)):
        mid = int(edited.loc[i, "ID"])

        new_cat_label = edited.loc[i, "Categoria"]
        new_proc_label = edited.loc[i, "Processo"]
        new_cliente_label = edited.loc[i, "Cliente"]

        new_cat_id = maps.cat_id_by_label.get(new_cat_label)
        new_proc_id = maps.proc_id_by_label.get(new_proc_label) if new_proc_label and new_proc_label != "-" else None
        new_cliente_id = maps.cliente_id_by_label.get(new_cliente_label) if new_cliente_label and new_cliente_label != "-" else None

        want_conc = bool(edited.loc[i, "Conciliado"])
        already_conc = current_is_conc.get(mid, False)

        want_cliente = bool(edited.loc[i, "Pgto_Cliente"])
        already_cliente = current_is_cliente.get(mid, False)

        # Regra: se já conciliado, não permite desmarcar por aqui (fase 1)
        if already_conc and (not want_conc):
            want_conc = True

        try:
            old_row = df_mov_by_id.loc[mid]
            if isinstance(old_row, pd.DataFrame):
                old_row = old_row.iloc[0]
        except KeyError:
            continue
            
        old_cat = _safe_int(old_row.get("categoria_id"))
        
        old_obs_val = old_row.get("observacao")
        if isinstance(old_obs_val, pd.Series):
            old_obs_val = old_obs_val.iloc[0]
        old_obs = (old_obs_val or "")

        new_obs = edited.loc[i, "Observação"]
        if new_obs is None:
            new_obs = ""
        new_obs_str = str(new_obs)
        
        old_proc_ref = old_row.get("processo_ref")
        if isinstance(old_proc_ref, pd.Series): old_proc_ref = old_proc_ref.iloc[0]
        old_cliente_nome = old_row.get("cliente_nome")
        if isinstance(old_cliente_nome, pd.Series): old_cliente_nome = old_cliente_nome.iloc[0]
        
        old_proc_ref_str = "" if pd.isna(old_proc_ref) else str(old_proc_ref).strip()
        new_proc_label_str = "" if str(new_proc_label).strip() in ("-", "None") or pd.isna(new_proc_label) else str(new_proc_label).strip()
        
        old_cliente_nome_str = "" if pd.isna(old_cliente_nome) or str(old_cliente_nome).strip() == "-" else str(old_cliente_nome).strip()
        new_cliente_label_str = "" if str(new_cliente_label).strip() in ("-", "None") or pd.isna(new_cliente_label) else str(new_cliente_label).strip()

        if (
            new_cat_id != old_cat
            or new_obs_str != old_obs
            or (want_conc != already_conc)
            or (want_cliente != already_cliente)
            or (new_proc_label_str != old_proc_ref_str)
            or (new_cliente_label_str != old_cliente_nome_str)
        ):
            obs_to_save = (new_obs_str.strip() or None)
            
            if new_cliente_id is not None and not want_conc:
                want_conc = True

            changes.append({
                "mid": mid,
                "new_cat_id": new_cat_id,
                "proc_changed": new_proc_label_str != old_proc_ref_str,
                "new_proc_id": new_proc_id,
                "want_conc": want_conc,
                "new_obs": obs_to_save,
                "want_cliente": want_cliente,
                "cliente_changed": new_cliente_label_str != old_cliente_nome_str,
                "new_cliente_id": new_cliente_id
            })

    return changes


def aplicar_changes_no_banco(
    changes: List[dict],
    *,
    usuario_id: Optional[int],
    status_confirmada_id: int,
) -> None:
    if not changes:
        return

    with fresh_conn() as conn:
        try:
            with conn.cursor() as cur:
                for ch in changes:
                    mid = ch["mid"]
                    
                    # 1) Categoria e Is_cliente
                    cur.execute(
                        "UPDATE movimento_bancario SET categoria_id = %s, is_cliente = %s WHERE id = %s",
                        (ch["new_cat_id"], ch["want_cliente"], int(mid)),
                    )
                    
                    # 1.5) Processo (tabela N:N convertida em 1 único vínculo na base)
                    if ch["proc_changed"]:
                        cur.execute("DELETE FROM movimento_processo WHERE movimento_bancario_id = %s", (int(mid),))
                        if ch["new_proc_id"] is not None:
                            cur.execute(
                                "INSERT INTO movimento_processo (movimento_bancario_id, processo_id, valor_atribuido) VALUES (%s, %s, (SELECT valor FROM movimento_bancario WHERE id = %s))", 
                                (int(mid), ch["new_proc_id"], int(mid))
                            )

                    # 2) Conciliação
                    if ch["want_conc"] or ch["cliente_changed"]:
                        cur.execute(
                            """
                            INSERT INTO conciliacao (
                                movimento_bancario_id, status_id, regra_aplicada,
                                probabilidade, usuario_confirmacao_id, dt_confirmacao, observacao, cliente_id
                            )
                            VALUES (%s, %s, 'MANUAL', 1.0, %s, NOW(), %s, %s)
                            ON CONFLICT (movimento_bancario_id)
                            DO UPDATE SET
                                status_id = EXCLUDED.status_id,
                                usuario_confirmacao_id = EXCLUDED.usuario_confirmacao_id,
                                dt_confirmacao = NOW(),
                                observacao = EXCLUDED.observacao,
                                cliente_id = EXCLUDED.cliente_id
                            """,
                            (int(mid), int(status_confirmada_id), usuario_id, ch["new_obs"], ch["new_cliente_id"]),
                        )
                    elif not ch["want_conc"]:
                        cur.execute("DELETE FROM conciliacao WHERE movimento_bancario_id = %s", (int(mid),))
            # Só faz o commit se tudo deu certo
            conn.commit()
        except Exception as e:
            # Se deu qualquer erro no meio do caminho, desfaz e não trava o banco
            conn.rollback()
            raise e