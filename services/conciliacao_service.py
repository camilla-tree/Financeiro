# services/conciliacao_service.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from db import fresh_conn


@dataclass(frozen=True)
class ConciliacaoMaps:
    cat_id_by_label: Dict[str, Optional[int]]
    # proc_id_by_label removido pois a gestão agora é via N:N na outra seção


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
) -> List[Tuple[int, Optional[int], Optional[int], bool, Optional[str], bool]]:
    """
    Retorna lista de changes no formato:
      (movimento_id, new_cat_id, new_proc_id, want_conc, new_obs, want_cliente)
      Nota: new_proc_id será sempre None aqui, pois o vínculo é feito na seção dedicada.
    """
    current_is_conc = {
        int(r["movimento_id"]): bool(pd.notna(r["conciliacao_id"]))
        for _, r in df_mov.iterrows()
    }
    
    current_is_cliente = {
        int(r["movimento_id"]): bool(r.get("is_cliente", False))
        for _, r in df_mov.iterrows()
    }

    changes: List[Tuple[int, Optional[int], Optional[int], bool, Optional[str], bool]] = []

    # Para lookup rápido
    df_mov_by_id = df_mov.set_index("movimento_id", drop=False)

    for i in range(len(edited)):
        mid = int(edited.loc[i, "ID"])

        new_cat_label = edited.loc[i, "Categoria"]
        # Processo na tabela é apenas visualização agora, ignoramos na edição em massa

        new_cat_id = maps.cat_id_by_label.get(new_cat_label)
        new_proc_id = None # Vínculo gerido separadamente

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
        
        # Safe extraction for potentially duplicate or nan values
        old_obs_val = old_row.get("observacao")
        if isinstance(old_obs_val, pd.Series):
            old_obs_val = old_obs_val.iloc[0]
        old_obs = (old_obs_val or "")

        new_obs = edited.loc[i, "Observação"]
        if new_obs is None:
            new_obs = ""
        new_obs_str = str(new_obs)

        # Detecta mudanças (Categoria, Observação ou Status Conciliado ou Cliente)
        if (
            new_cat_id != old_cat
            or new_obs_str != old_obs
            or (want_conc != already_conc)
            or (want_cliente != already_cliente)
        ):
            obs_to_save = (new_obs_str.strip() or None)
            changes.append((mid, new_cat_id, new_proc_id, want_conc, obs_to_save, want_cliente))

    return changes


def aplicar_changes_no_banco(
    changes: List[Tuple[int, Optional[int], Optional[int], bool, Optional[str], bool]],
    *,
    usuario_id: Optional[int],
    status_confirmada_id: int,
) -> None:
    if not changes:
        return

    with fresh_conn() as conn:
        try:
            with conn.cursor() as cur:
                for mid, new_cat_id, _, want_conc, new_obs, want_cliente in changes:
                    # 1) Categoria e Is_cliente atualiza no movimento_bancario
                    cur.execute(
                        "UPDATE movimento_bancario SET categoria_id = %s, is_cliente = %s WHERE id = %s",
                        (new_cat_id, want_cliente, int(mid)),
                    )

                    # 2) Conciliação (Tabela Mestre)
                    if want_conc:
                        cur.execute(
                            """
                            INSERT INTO conciliacao (
                                movimento_bancario_id, status_id, regra_aplicada,
                                probabilidade, usuario_confirmacao_id, dt_confirmacao, observacao
                            )
                            VALUES (%s, %s, 'MANUAL', 1.0, %s, NOW(), %s)
                            ON CONFLICT (movimento_bancario_id)
                            DO UPDATE SET
                                status_id = EXCLUDED.status_id,
                                usuario_confirmacao_id = EXCLUDED.usuario_confirmacao_id,
                                dt_confirmacao = NOW(),
                                observacao = EXCLUDED.observacao
                            """,
                            (int(mid), int(status_confirmada_id), usuario_id, new_obs),
                        )
                    else:
                        cur.execute("DELETE FROM conciliacao WHERE movimento_bancario_id = %s", (int(mid),))
            # Só faz o commit se tudo deu certo
            conn.commit()
        except Exception as e:
            # Se deu qualquer erro no meio do caminho, desfaz e não trava o banco
            conn.rollback()
            raise e