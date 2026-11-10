"""Carga optativa de Censo 2022 y ventas mensuales, con validación de trazabilidad.
No rellena datos geográficos ni series temporales inexistentes.
"""
from __future__ import annotations
import csv
from datetime import date
from io import StringIO
import math
import re
import unicodedata


def canon(value):
    s=unicodedata.normalize('NFKD',str(value or '').lower())
    s=''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+',' ',re.sub(r'[^a-z0-9 ]',' ',s)).strip()


def _records(data):
    if isinstance(data,bytes): data=data.decode('utf-8-sig')
    if not isinstance(data,str):
        raise ValueError('Se requiere CSV UTF-8.')
    return list(csv.DictReader(StringIO(data)))


def _number(v):
    try:
        n=float(str(v).strip().replace(',','.'))
        if math.isfinite(n):return n
    except (TypeError,ValueError):pass
    raise ValueError('Número inválido: '+str(v))


def leer_censo_csv(text):
    """CSV localidad,tipo,poblacion_2022,superficie_km2,fuente_url.
    Sin fuente identificable o escala territorial explícita: rechazar.
    """
    out={}
    for i,row in enumerate(_records(text),2):
        loc=row.get('localidad','').strip()
        tipo=row.get('tipo','').strip()
        url=row.get('fuente_url','').strip()
        if not loc or tipo not in ('Barrio CABA','Municipio','Localidad PBA'):
            raise ValueError(f'Fila {i}: localidad/tipo inválido. Tipo esperado: Barrio CABA, Municipio o Localidad PBA.')
        if not url.startswith(('https://','http://')):
            raise ValueError(f'Fila {i}: falta URL de la fuente oficial.')
        population=int(_number(row.get('poblacion_2022')))
        if population<=0:raise ValueError(f'Fila {i}: población no positiva.')
        area_raw=row.get('superficie_km2','').strip()
        area=_number(area_raw) if area_raw else None
        if area is not None and area<=0:raise ValueError(f'Fila {i}: superficie inválida.')
        key=(canon(loc),tipo)
        if key in out:raise ValueError(f'Fila {i}: localidad duplicada.')
        out[key]={'Población':population,'Densidad poblacional':round(population/area,2) if area else None,
                  'Fuente población':'Censo 2022 · '+url,'Censo 2022 validado':True,
                  'superficie_km2':area}
    return out


def aplicar_censo(registro,dataset):
    """No cambia atributos poblacionales cuando el nivel espacial no coincide."""
    rec=dict(registro)
    if not dataset:return rec
    loc=canon(rec.get('Ubicación'))
    tipo=str(rec.get('Tipo') or '')
    key=(loc,tipo)
    # No asigna datos de partido a cada localidad del partido.
    x=dataset.get(key)
    if not x:return rec
    rec['Población']=x['Población']
    if x['Densidad poblacional'] is not None:
        rec['Densidad poblacional']=x['Densidad poblacional']
        rec['Fuente densidad']='Densidad calculada con Censo 2022 y superficie aportada'
    else:
        rec['Fuente densidad']='Densidad del modelo territorial previo (año no documentado)'
    rec['Fuente población']=x['Fuente población']
    rec['Censo 2022 validado']=True
    # Recalcular nivel de población por percentiles de unidades del MISMO tipo.
    # Las categorías territoriales restantes pertenecen a la fuente histórica.
    rec['Población nivel V37']=None
    return rec


def normalizar_ventas_mensuales(ventas_csv,ipc_csv):
    """Normaliza al último IPC declarado, MISMO conjunto de 12 meses para todas
    las tiendas admitidas. Montos en miles de pesos. Devuelve ventas anuales en
    moneda homogénea y reporta exclusiones.

    columnas ventas: id_sucursal,periodo,ventas_k
    columnas ipc: periodo,indice_ipc
    """
    ventas=_records(ventas_csv)
    inflacion=_records(ipc_csv)
    indexes={}
    for i,r in enumerate(inflacion,2):
        month=str(r.get('periodo','')).strip()
        if not re.fullmatch(r'20\d\d-(0[1-9]|1[0-2])',month):
            raise ValueError(f'IPC fila {i}: período inválido.')
        indexes[month]=_number(r.get('indice_ipc'))
        if indexes[month]<=0:raise ValueError('El IPC debe ser positivo.')
    by_store={}
    for i,r in enumerate(ventas,2):
        sid=str(r.get('id_sucursal','')).strip().lstrip('0')
        month=str(r.get('periodo','')).strip()
        if not sid or not re.fullmatch(r'20\d\d-(0[1-9]|1[0-2])',month):
            raise ValueError(f'Ventas fila {i}: ID o período inválido.')
        amount=_number(r.get('ventas_k'))
        if amount<0:raise ValueError(f'Ventas fila {i}: valor negativo.')
        if month in by_store.setdefault(sid,{}):
            raise ValueError(f'Duplicado ID/período: {sid}/{month}')
        by_store[sid][month]=amount
    periods=sorted(indexes)
    if len(periods)<12:raise ValueError('Se necesitan al menos 12 meses de IPC.')
    end=periods[-1]
    y,m=map(int,end.split('-'))
    required=[f'{(y*12+(m-1)-k)//12:04d}-{(y*12+(m-1)-k)%12+1:02d}' for k in range(11,-1,-1)]
    if not all(p in indexes for p in required):
        raise ValueError('La serie de IPC tiene meses faltantes dentro del último año.')
    values={}
    for sid,months in by_store.items():
        if not all(p in months for p in required):continue
        annual=sum(months[p]*indexes[end]/indexes[p] for p in required)
        values[sid]={'annual_k':float(annual),'monthly_k':float(months[end]),
                     'months':12,'price_base':end}
    if len(values)<4:
        raise ValueError('Hay menos de 4 sucursales con 12 meses completos y IPC coincidente.')
    return {'stores':values,'period':f'{required[0]} a {end}',
            'base_ipc':end,'excluded_stores':len(by_store)-len(values),
            'stores_count':len(values),'method':'12 meses de ventas en pesos del mes base mediante IPC suministrado'}
