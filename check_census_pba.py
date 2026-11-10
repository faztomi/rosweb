"""Prevent publishing a broken PBA census file."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import census_index,population_at,CENSUS
assert CENSUS.is_file(), f'Falta archivo censal: {CENSUS}'
assert CENSUS.stat().st_size > 5_000_000, 'El Censo está truncado'
assert len(census_index()[0]) >= 23800, 'Radios censales incompletos'
for p in [(-34.77,-58.40),(-34.92,-57.95)]:
    value=population_at(*p)
    assert value and value[800]>1000 and value['coverage_800']>=90,(p,value)
print('Censo PBA 2022 OK (radios y población accesible)')
