#!/usr/bin/env python3
"""
SSFR - Simulador do Fluxo Rodoviário (corrigido)

Funcionalidades adicionadas / corrigidas:
- Semáforos dinâmicos: decremento de remainingTime e troca de cores (green -> yellow -> red -> green)
- Uso de fixedYellowTime, greenLightTime e redLightTime (quando presentes na MIB)
- Acumulador para trafficRate fraccionário (evita perder veículos quando step < 60s)
- Cálculo de mostCongestedRoad e averageWaitTime (estimativa por veículo-segundo)
- Backup atómico ao gravar (gera .bak e escreve para temp antes de substituir)
- Verbose por defeito (agora imprime mesmo sem passar --verbose). Usa --quiet para silenciar.
- Parsing de destinationRoadId/destinationId robusto
- Validações básicas de valores
"""

import argparse
import math
import copy
import shutil
import tempfile
from pathlib import Path

# -------------------------
# Utilitários para ficheiro MIB
# -------------------------
def parse_mib_text(path: Path):
    data = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            data[key] = val
    return data


def load_mib_raw(path: Path):
    return path.read_text(encoding="utf-8").splitlines()


def atomic_write_mib(path: Path, raw_lines, data_map):
    # backup original
    bak = path.with_suffix(path.suffix + ".bak")
    try:
        shutil.copy2(path, bak)
    except Exception:
        # se copiar falhar, continuamos (p.ex. ficheiro pode não existir)
        pass

    # escrever para temp e depois substituir
    dirp = path.parent
    fd, tmp_path = tempfile.mkstemp(prefix="tmp_traffic_mib_", dir=str(dirp))
    tmp_path = Path(tmp_path)
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            for line in raw_lines:
                if "=" not in line or line.strip().startswith("#"):
                    f.write(line.rstrip("\n") + "\n")
                    continue
                key = line.split("=", 1)[0].strip()
                if key in data_map:
                    f.write(f"{key} = {data_map[key]}\n")
                else:
                    f.write(line.rstrip("\n") + "\n")
            # acrescentar chaves novas, se existirem
            existing_keys = {ln.split("=",1)[0].strip() for ln in raw_lines if "=" in ln}
            for k, v in data_map.items():
                if k not in existing_keys:
                    f.write(f"{k} = {v}\n")
        # substituir atomicamente
        tmp_path.replace(path)
    finally:
        try:
            # remover o fd se ainda aberto
            import os
            os.close(fd)
        except Exception:
            pass


# -------------------------
# Modelo da via
# -------------------------
class Road:
    def __init__(self, idx):
        self.idx = idx
        self.roadId = idx
        self.vehicleCount = 0                     # veículos actualmente na via
        self.trafficRate = 0                      # veículos/min que entram (RGT)
        self.trafficLightColor = "red"            # "green"/"yellow"/"red"
        self.remainingTime = 0                    # segundos restantes para mudar o estado actual
        self.maxCapacity = 1000
        self.outflowRate = 0                      # veículos/min que podem atravessar quando permitido
        self.greenLightTime = 30
        self.redLightTime = 30

        # campos internos (runtime)
        self._in_accumulator = 0.0                # acumulador para trafficRate fraccionário
        self._wait_accum = 0.0                    # veículo-segundos acumulados (para averageWaitTime)

    def __repr__(self):
        return (f"Road {self.idx}: vehicles={self.vehicleCount}, in={self.trafficRate}/m, "
                f"out={self.outflowRate}/m, light={self.trafficLightColor}({self.remainingTime}s), cap={self.maxCapacity}")


def safe_int(val, default=0):
    try:
        return int(float(val))
    except Exception:
        return default


def build_roads(data, n):
    roads = {}
    for i in range(1, n + 1):
        r = Road(i)

        def get(key, default=None):
            # procura key.i e depois key
            v = data.get(f"{key}.{i}")
            if v is None:
                v = data.get(key)
            return v if v is not None else default

        r.vehicleCount = safe_int(get("vehicleCount", r.vehicleCount))
        r.trafficRate = safe_int(get("trafficRate", r.trafficRate))
        r.trafficLightColor = str(get("trafficLightColor", r.trafficLightColor)).lower()
        r.remainingTime = safe_int(get("remainingTime", r.remainingTime))
        r.maxCapacity = safe_int(get("maxCapacity", r.maxCapacity))
        r.outflowRate = safe_int(get("outflowRate", r.outflowRate))
        r.greenLightTime = safe_int(get("greenLightTime", r.greenLightTime))
        r.redLightTime = safe_int(get("redLightTime", r.redLightTime))

        # normalizar valores
        if r.vehicleCount < 0: r.vehicleCount = 0
        if r.trafficRate < 0: r.trafficRate = 0
        if r.outflowRate < 0: r.outflowRate = 0
        if r.maxCapacity <= 0: r.maxCapacity = 1000
        if r.remainingTime < 0: r.remainingTime = 0

        roads[i] = r
    return roads


# -------------------------
# Funções de simulação
# -------------------------
def vehicles_fractional_add(rate_per_min: int, step_sec: int, accumulator: float):
    """
    Usa acumulador para evitar perder veículos quando step < 60s.
    Retorna (to_add:int, new_accumulator:float)
    """
    add_float = rate_per_min * (step_sec / 60.0)
    accum = accumulator + add_float
    to_add = int(math.floor(accum))
    accum -= to_add
    return to_add, accum


def vehicles_per_step(rate_per_min: int, step_sec: int):
    return max(0, math.floor(rate_per_min * (step_sec / 60.0)))


def update_traffic_lights(roads: dict, fixed_yellow: int, step_seconds: int):
    """
    Decrementa remainingTime e troca o estado do semáforo quando necessário.
    Ordem: green -> yellow -> red -> green
    """
    for r in roads.values():
        if r.remainingTime > 0:
            r.remainingTime -= step_seconds
        # se chegou a zero ou ficou negativo, trocamos
        if r.remainingTime <= 0:
            color = r.trafficLightColor
            if color == "green":
                r.trafficLightColor = "yellow"
                r.remainingTime = fixed_yellow
            elif color == "yellow":
                r.trafficLightColor = "red"
                r.remainingTime = r.redLightTime
            elif color == "red":
                r.trafficLightColor = "green"
                r.remainingTime = r.greenLightTime
            else:
                # estado desconhecido -> colocar em red por segurança
                r.trafficLightColor = "red"
                r.remainingTime = r.redLightTime


def step(roads, dest_map, step_seconds, fixed_yellow, verbose=False):
    moved = 0
    injected = 0
    blocked = 0

    # 0) atualizar semáforos primeiro (faz com que tempo em yellow/red/green seja aplicado)
    update_traffic_lights(roads, fixed_yellow, step_seconds)

    # 1) Injecção (RGT) usando acumulador fraccionário
    for rid, r in roads.items():
        add, new_acc = vehicles_fractional_add(r.trafficRate, step_seconds, r._in_accumulator)
        r._in_accumulator = new_acc
        if add > 0:
            space = max(0, r.maxCapacity - r.vehicleCount)
            add_ok = min(add, space)
            r.vehicleCount += add_ok
            injected += add_ok
            blocked += (add - add_ok)
            if verbose:
                print(f"[INJ] Via {rid}: tentativa={add}, adicionados={add_ok}, bloqueados={add-add_ok}")

    # 2) Movimento (só vias com semáforo green/yellow)
    snapshot = copy.deepcopy(roads)
    for rid, r_snap in snapshot.items():
        if r_snap.trafficLightColor not in ("green", "yellow"):
            if verbose:
                print(f"[STOP] Via {rid}: semáforo {r_snap.trafficLightColor} (não atravessa)")
            continue

        can_cross = min(r_snap.vehicleCount, vehicles_per_step(r_snap.outflowRate, step_seconds))
        if can_cross <= 0:
            if verbose:
                print(f"[NO_MOVE] Via {rid}: semáforo {r_snap.trafficLightColor} mas nada para atravessar")
            continue

        dests = dest_map.get(rid, [])
        if not dests:
            # escoamento externo: simplesmente remover até can_cross
            roads[rid].vehicleCount -= can_cross
            moved += can_cross
            if verbose:
                print(f"[OUT] Via {rid}: escoamento externo removidos={can_cross}")
            continue

        per_dest = can_cross // len(dests)
        rest = can_cross % len(dests)

        moved_this_origin = 0
        for i, dest in enumerate(dests):
            alloc = per_dest + (1 if i < rest else 0)
            if alloc <= 0:
                continue
            # valida existência do destino
            if dest not in roads:
                # destino fora da rede: remoção
                roads[rid].vehicleCount -= alloc
                moved += alloc
                moved_this_origin += alloc
                if verbose:
                    print(f"[OUT-DEST] Via {rid} -> externo(dest={dest}): removidos {alloc}")
                continue
            dest_space = max(0, roads[dest].maxCapacity - roads[dest].vehicleCount)
            actually_moved = min(alloc, dest_space)
            if actually_moved <= 0:
                blocked += alloc
                if verbose:
                    print(f"[BLOCKED] Via {rid} -> Via {dest}: tentativa {alloc}, sem espaço (dest_space=0)")
                continue
            roads[dest].vehicleCount += actually_moved
            roads[rid].vehicleCount -= actually_moved
            moved += actually_moved
            moved_this_origin += actually_moved
            if verbose:
                print(f"[MOVE] Via {rid} -> Via {dest}: tentativa={alloc}, movidos={actually_moved}, espaço_dest={dest_space}")

        if moved_this_origin == 0 and verbose:
            print(f"[NO_MOVE_ALLOWED] Via {rid} não moveu veículos (destinos sem espaço)")

    # 3) Atualizar métricas internas de espera (vehicle-seconds)
    for r in roads.values():
        r._wait_accum += r.vehicleCount * step_seconds

    return {"moved": moved, "injected": injected, "blocked": blocked}


# -------------------------
# Loop principal
# -------------------------
def run(mib_path: Path, steps=30, write=False, verbose=True):
    if not mib_path.exists():
        raise FileNotFoundError(f"Ficheiro MIB não encontrado: {mib_path}")

    raw = load_mib_raw(mib_path)
    data = parse_mib_text(mib_path)

    num_roads = max(1, safe_int(data.get("numberOfRoads", 2)))
    step_sec = max(1, safe_int(data.get("simulationStep", 5)))
    current_time = safe_int(data.get("currentSimulationTime", 0))
    fixed_yellow = max(1, safe_int(data.get("fixedYellowTime", 4)))

    roads = build_roads(data, num_roads)

    # construir dest_map a partir de destinationRoadId.i -> destinationId.i
    dest_map = {}
    for key, val in data.items():
        if key.startswith("destinationRoadId."):
            idx = key.split(".", 1)[1]
            try:
                origin = safe_int(val)
            except Exception:
                continue
            dest_key = f"destinationId.{idx}"
            dest_val = data.get(dest_key)
            if dest_val is None:
                continue
            try:
                dest = safe_int(dest_val)
            except Exception:
                continue
            dest_map.setdefault(origin, []).append(dest)

    # fallback 2-vias: 1 -> 2
    if not dest_map and num_roads >= 2:
        dest_map = {1: [2]}

    if verbose:
        print(f"Mapa: {data.get('mapName','(unknown)')}, vias={num_roads}, step={step_sec}s, tempo_actual={current_time}s")
        print("Dest_map:", dest_map)
        print("Estado inicial das vias:")
        for r in roads.values():
            print("  ", r)
        print("-" * 60)

    # simulação principal
    cumulative_moved = 0
    cumulative_injected = 0
    cumulative_blocked = 0

    for s in range(1, steps + 1):
        summary = step(roads, dest_map, step_sec, fixed_yellow, verbose=verbose)
        cumulative_moved += summary["moved"]
        cumulative_injected += summary["injected"]
        cumulative_blocked += summary["blocked"]
        current_time += step_sec

        if verbose:
            print(f"[STEP {s}] moved={summary['moved']}, injected={summary['injected']}, blocked={summary['blocked']}, sim_time={current_time}s")
            for r in roads.values():
                print("   ->", r)
            print("-" * 40)

    # recalcular métricas finais
    total_vehicles = sum(r.vehicleCount for r in roads.values())
    # averageWaitTime: estimativa = total vehicle-seconds acumulados / max(1, total_vehicles)
    total_vehicle_seconds = sum(r._wait_accum for r in roads.values())
    average_wait_time = int(total_vehicle_seconds / max(1, total_vehicles)) if total_vehicles > 0 else 0

    # most congested: via com maior vehicleCount
    most_congested = max(roads.values(), key=lambda R: R.vehicleCount).idx

    # atualizar dados para escrita
    for i, r in roads.items():
        data[f"vehicleCount.{i}"] = str(r.vehicleCount)
        data[f"remainingTime.{i}"] = str(max(0, r.remainingTime))
        data[f"trafficLightColor.{i}"] = str(r.trafficLightColor)
    data["currentSimulationTime"] = str(current_time)
    data["totalVehicles"] = str(total_vehicles)
    data["averageWaitTime"] = str(average_wait_time)
    data["mostCongestedRoad"] = str(most_congested)

    if write:
        atomic_write_mib(mib_path, raw, data)
        if verbose:
            print(f"Valores actualizados gravados em: {mib_path} (backup .bak criado)")

    # resumo final
    if verbose:
        print("=" * 60)
        print("Resumo final da simulação:")
        print(f"  total_moved = {cumulative_moved}")
        print(f"  total_injected = {cumulative_injected}")
        print(f"  total_blocked = {cumulative_blocked}")
        print(f"  totalVehicles (final) = {total_vehicles}")
        print(f"  averageWaitTime (est) = {average_wait_time} s")
        print(f"  mostCongestedRoad = {most_congested}")
        print("=" * 60)

    return roads, data


# -------------------------
# CLI
# -------------------------
def cli():
    parser = argparse.ArgumentParser(description="SSFR - Simulador do Fluxo Rodoviário (corrigido)")
    parser.add_argument(
        "--mib",
        default=r"C:\Users\pedro\PycharmProjects\GR_Trabalho\TRAFFIC_MIB.txt",
        help="Caminho para o ficheiro MIB (default project path)"
    )
    parser.add_argument("--steps", type=int, default=60, help="Número de passos da simulação")
    parser.add_argument("--write-back", dest="write_back", action="store_true", help="Gravar valores actualizados de volta no ficheiro MIB")
    parser.add_argument("--quiet", action="store_true", help="Silenciar output verbose")
    args = parser.parse_args()

    verbose = not args.quiet  # verbose por defeito
    try:
        run(Path(args.mib), steps=args.steps, write=args.write_back, verbose=verbose)
    except Exception as e:
        print("Erro durante a execução:", e)


if __name__ == "__main__":
    cli()
