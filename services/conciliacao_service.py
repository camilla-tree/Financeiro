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
) -> List[Tuple[int, Optional[int], Optional[int], bool, Optional[str]]]:
    """
    Retorna lista de changes no formato:
      (movimento_id, new_cat_id, new_proc_id, want_conc, new_obs)

    Mantém as mesmas regras da fase 1:
    - Se já estava conciliado no DB, não permite "desconciliar" (força want_conc=True).
    - Detecta mudanças por: categoria, processo, observacao ou flag conciliado.
    """
    current_is_conc = {
        int(r["movimento_id"]): bool(pd.notna(r["conciliacao_id"]))
        for _, r in df_mov.iterrows()
    }

    changes: List[Tuple[int, Optional[int], Optional[int], bool, Optional[str]]] = []

    # Para lookup rápido (evita df_mov[df_mov[...] == mid] em loop)
    df_mov_by_id = df_mov.set_index("movimento_id", drop=False)

    for i in range(len(edited)):
        mid = int(edited.loc[i, "ID"])

        new_cat_label = edited.loc[i, "Categoria"]
        new_proc_label = edited.loc[i, "Processo"]

        new_cat_id = maps.cat_id_by_label.get(new_cat_label)
        new_proc_id = maps.proc_id_by_label.get(new_proc_label)

        want_conc = bool(edited.loc[i, "Conciliado"])
        already_conc = current_is_conc.get(mid, False)

        # fase 1: se já conciliado no banco, não pode voltar pra não conciliado
        if already_conc and (not want_conc):
            want_conc = True

        old_row = df_mov_by_id.loc[mid]
        old_cat = _safe_int(old_row.get("categoria_id"))
        old_proc = _safe_int(old_row.get("processo_id"))
        old_obs = (old_row.get("observacao") or "")

        new_obs = edited.loc[i, "Observação"]
        if new_obs is None:
            new_obs = ""
        new_obs_str = str(new_obs)

        if (
            new_cat_id != old_cat
            or new_proc_id != old_proc
            or new_obs_str != old_obs
            or (want_conc != already_conc)
        ):
            obs_to_save = (new_obs_str.strip() or None)
            changes.append((mid, new_cat_id, new_proc_id, want_conc, obs_to_save))

    return changes


def aplicar_changes_no_banco(
    changes: List[Tuple[int, Optional[int], Optional[int], bool, Optional[str]]],
    *,
    usuario_id: Optional[int],
    status_confirmada_id: int,
) -> None:
    """
    Aplica changes em transação única.
    Mantém a mesma lógica do seu código atual:
    - sempre atualiza categoria_id em movimento_bancario
    - se want_conc=True: UPSERT em conciliacao
    - se want_conc=False: DELETE conciliacao

    Observação: a regra atual usa (SELECT cliente_id FROM processo WHERE id = %s)
    baseado no processo_id.
    """
    if not changes:
        return

    with fresh_conn() as conn:
        try:
            # evita estado "aborted transaction"
            try:
                conn.rollback()
            except Exception:
                pass

            with conn.cursor() as cur:
                for mid, new_cat_id, new_proc_id, want_conc, new_obs in changes:
                    # 1) Categoria sempre atualiza no movimento
                    cur.execute(
                        """
                        UPDATE movimento_bancario
                        SET categoria_id = %s
                        WHERE id = %s
                        """,
                        (new_cat_id, int(mid)),
                    )

                    # 2) Conciliação (fase 1)
                    if want_conc:
                        cur.execute(
                            """
                            INSERT INTO conciliacao (
                                movimento_bancario_id,
                                processo_id,
                                cliente_id,
                                status_id,
                                regra_aplicada,
                                probabilidade,
                                usuario_confirmacao_id,
                                dt_confirmacao,
                                observacao
                            )
                            VALUES (
                                %s,
                                %s,
                                (SELECT cliente_id FROM processo WHERE id = %s),
                                %s,
                                'MANUAL',
                                1.0,
                                %s,
                                NOW(),
                                %s
                            )
                            ON CONFLICT (movimento_bancario_id)
                            DO UPDATE SET
                                processo_id = EXCLUDED.processo_id,
                                cliente_id = EXCLUDED.cliente_id,
                                status_id = EXCLUDED.status_id,
                                regra_aplicada = 'MANUAL',
                                probabilidade = 1.0,
                                usuario_confirmacao_id = EXCLUDED.usuario_confirmacao_id,
                                dt_confirmacao = NOW(),
                                observacao = EXCLUDED.observacao
                            """,
                            (
                                int(mid),
                                new_proc_id,
                                new_proc_id,
                                int(status_confirmada_id),
                                usuario_id,
                                new_obs,
                            ),
                        )
                    else:
                        cur.execute(
                            "DELETE FROM conciliacao WHERE movimento_bancario_id = %s",
                            (int(mid),),
                        )

            conn.commit()

        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
