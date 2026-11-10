"""Verifica la base PBA incorporada y un cálculo espacial real.
Esta prueba no depende de redes ni APIs.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import CENSUS, census_index, population_at
assert CENSUS.is_file(), f"FALTA CENSO PBA: {CENSUS}"
assert CENSUS.stat().st_size > 5_000_000, "El archivo censal está truncado"
index=census_index()
assert index is not None and len(index[0]) >= 23_000, "Error de lectura de radios PBA"
for (lat,lon) in [(-34.766,-58.401),(-34.920,-57.956)]:
    pop=population_at(lat,lon)
    assert pop and pop['coverage_800']>=90 and pop[800]>500, (lat,lon,pop)
    print(f'PBA Censo OK: {lat},{lon}: {pop[800]:,} personas 800m')
print('CENSO PBA LISTO')
