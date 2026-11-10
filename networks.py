"""Optional network upload. Never influences the 4-variable territorial ROS."""
from __future__ import annotations
import csv,io,math
from engine import normal


def read_network(upload):
    if upload is None:return []
    data=upload.getvalue()
    if len(data)>12*1024*1024:raise ValueError("El archivo supera el límite de 12 MB")
    if upload.name.lower().endswith('.csv'):
        text=data.decode('utf-8-sig',errors='replace')
        dialect=csv.Sniffer().sniff(text[:2048],delimiters=',;\t')
        return list(csv.DictReader(io.StringIO(text),dialect=dialect))[:10000]
    if upload.name.lower().endswith('.xlsx'):
        import pandas as pd
        return pd.read_excel(io.BytesIO(data),dtype=str).fillna('').head(10000).to_dict('records')
    raise ValueError('Subí un CSV o Excel (.xlsx)')


def own_nearby(rows,coords):
    if not coords or not coords.get('verified'):return None
    distances=[]
    for row in rows:
        cleaned={normal(k):v for k,v in row.items()}
        lat=next((cleaned[k] for k in ('latitud','lat','latitude') if k in cleaned),None)
        lon=next((cleaned[k] for k in ('longitud','lon','longitude','lng') if k in cleaned),None)
        try:
            a=float(str(lat).replace(',','.'));b=float(str(lon).replace(',','.'))
            if not(-90<=a<=90 and -180<=b<=180):continue
        except (ValueError,TypeError):continue
        la1,lo1=math.radians(coords['lat']),math.radians(coords['lon'])
        la2,lo2=math.radians(a),math.radians(b)
        d=6371008.8*2*math.asin(min(1,math.sqrt(math.sin((la2-la1)/2)**2+math.cos(la1)*math.cos(la2)*math.sin((lo2-lo1)/2)**2)))
        label=str(next((cleaned[k] for k in ('nombre','sucursal','direccion') if k in cleaned),'Sucursal'))
        distances.append((d,label))
    return sorted(distances)[:5] if distances else []
