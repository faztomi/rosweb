"""Small network layer with explicit data-provenance and persistent cache."""
from __future__ import annotations
import hashlib,json,re,sqlite3,time,unicodedata,os,tempfile
from pathlib import Path
from urllib.parse import unquote
import pandas as pd
import requests
from engine import norm

HERE=Path(__file__).resolve().parent
DATA=Path(tempfile.gettempdir())/'ros_public_cache'; DATA.mkdir(exist_ok=True)
STORE=DATA/'cache.sqlite3'
HEADERS={'User-Agent':'ROS-Simple-Web/1.0 (geographic scoring tool, public pilot)',
         'Accept':'application/json'}
GEOREF='https://apis.datos.gob.ar/georef/api/direcciones'
PHOTON='https://photon.komoot.io/api/'
NOMINATIM='https://nominatim.openstreetmap.org/search'
OVERPASS=['https://overpass.kumi.systems/api/interpreter',
          'https://overpass-api.de/api/interpreter']


def db():
    conn=sqlite3.connect(STORE,timeout=8)
    conn.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, timestamp REAL NOT NULL)')
    return conn


def cache_read(key, max_age=None):
    with db() as c:
        r=c.execute('SELECT value,timestamp FROM kv WHERE key=?',(key,)).fetchone()
    if not r or (max_age and time.time()-r[1]>max_age):return None
    try:return json.loads(r[0])
    except Exception:return None


def cache_write(key,value):
    with db() as c:c.execute('INSERT OR REPLACE INTO kv VALUES (?,?,?)',(key,json.dumps(value,ensure_ascii=False),time.time()))


def valid_latlon(lat,lon):
    try:
        a=float(lat);b=float(lon)
        return -56<a<-20 and -74<b<-53
    except (TypeError,ValueError):return False


def parse_embedded_coords(address):
    text=unquote(str(address).strip())
    for pattern in [r'@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)',
                    r'[?&](?:q|query)=(-?\d{1,2}\.\d+),\s*(-?\d{1,3}\.\d+)',
                    r'!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)',
                    r'^\s*(-?\d{1,2}\.\d+)\s*[,;]\s*(-?\d{1,3}\.\d+)\s*$']:
        m=re.search(pattern,text)
        if m and valid_latlon(m.group(1),m.group(2)):
            return {'lat':float(m.group(1)),'lon':float(m.group(2)), 'label':'Coordenadas compartidas','quality':'Coordenadas aportadas','provider':'Entrada'}
    return None


def _json_get(url,params,timeout=8):
    r=requests.get(url,params=params,headers=HEADERS,timeout=timeout)
    r.raise_for_status();return r.json()


def georef_candidates(q):
    q=q.strip()
    text=q
    variants=[{'direccion':q,'max':3},]
    # Normalize common qualifiers without changing the requested street name.
    parts=[x.strip() for x in q.split(',') if x.strip()]
    if len(parts)>=2:
        # Some addresses specify a town and the county separately, e.g. Temperley, Lomas de Zamora.
        base=parts[0]
        province=next((x for x in parts if norm(x) in {'buenos aires','provincia de buenos aires','ciudad autonoma de buenos aires','caba'}),None)
        if province:
            prov='Ciudad Autónoma de Buenos Aires' if 'ciudad' in norm(province) or norm(province)=='caba' else 'Buenos Aires'
            variants.append({'direccion':base,'provincia':prov,'max':3})
            variants.append({'direccion':base+', '+parts[1],'provincia':prov,'max':3})
        if len(parts)>=3 and norm(parts[1]) not in {'buenos aires','argentina'}:
            variants.append({'direccion':base,'departamento':parts[2],'provincia':'Buenos Aires','max':3})
    results=[]
    for v in variants[:4]:
        try:
            result=_json_get(GEOREF,v,timeout=6)
            for row in result.get('direcciones',[]):
                loc=row.get('ubicacion') or {}
                if valid_latlon(loc.get('lat'),loc.get('lon')):
                    name=row.get('nomenclatura') or row.get('nombre') or ''
                    results.append({'lat':float(loc['lat']),'lon':float(loc['lon']), 'label':name,'provider':'Georef Argentina'})
        except requests.RequestException:break
        if results:break
    return results


def _result_from_photon(q):
    out=[]
    try:
        js=_json_get(PHOTON,{'q':q,'limit':5,'lang':'es'},timeout=6)
        for feature in js.get('features',[]):
            lon,lat=feature['geometry']['coordinates']
            p=feature['properties']
            label=', '.join(str(p[k]) for k in ('name','housenumber','city','county','state') if p.get(k))
            if valid_latlon(lat,lon):out.append({'lat':lat,'lon':lon,'label':label,'provider':'Photon'})
    except (requests.RequestException,KeyError,ValueError,IndexError):pass
    return out


_last_nominatim=[0.]
def _result_from_nominatim(q):
    # User-triggered query only, never bulk. OSMF usage: <= 1 req/sec.
    wait=1.05-(time.monotonic()-_last_nominatim[0])
    if wait>0:time.sleep(wait)
    _last_nominatim[0]=time.monotonic()
    try:
        js=_json_get(NOMINATIM,{'q':q,'format':'jsonv2','limit':5,'countrycodes':'ar','addressdetails':1},timeout=6)
        return [{'lat':float(r['lat']),'lon':float(r['lon']),'label':r['display_name'],'provider':'Nominatim',
                 'address':r.get('address',{}),'type':r.get('type','')} for r in js if valid_latlon(r.get('lat'),r.get('lon'))]
    except (requests.RequestException,ValueError,KeyError):return []


def _requested_house(q):
    # First chunk only: protects names such as "20 de Septiembre 341".
    head=q.split(',')[0].strip()
    m=re.search(r'(?:^|\s)(\d{1,5})(?:\s*(?:bis|b))?\s*$',head,re.I)
    return m.group(1) if m else None


def _quality(query,candidate):
    label=norm(candidate.get('label',''))
    requested=norm(query)
    head=query.split(',')[0].strip()
    street_head=norm(re.sub(r'\b\d{1,5}\s*(?:bis|b)?\s*$','',head))
    if not street_head:return 0,'No verificable'
    # tolerate abbreviated first names and prefixes; require last two significant tokens when possible
    common={'de','del','la','el','av','avenida','calle','general','dr','doctor','presidente','julio'}
    tokens=[x for x in street_head.split() if len(x)>2 and x not in common]
    matches=sum(tok in label.split() for tok in tokens)
    if tokens and matches < max(1,len(tokens)-1):return -1,'Calle distinta'
    house=_requested_house(query)
    if house:
        normed=norm(candidate.get('label',''))
        # Do not claim number exact just because it appears somewhere else in the result.
        found=re.search(r'\b'+re.escape(house)+r'\b', normed)
        if found:return 4,'Altura indicada en resultado (verificar)'
        if candidate.get('address',{}).get('house_number')==house:return 4,'Altura indicada en resultado (verificar)'
        if re.search(r'\b\d{2,5}\b',normed):
            # A different house number in a result is not acceptable.
            head0=normed.split(',')[0] if ',' in normed else normed
            if re.search(r'\b\d{2,5}\b',head0):return -1,'Altura distinta'
        return 1,'Calle localizada; altura sin verificar'
    return 2,'Calle o intersección (verificar)'


def _district_match(query,candidate):
    # When the user typed a town, avoid accidental matches from other provinces.
    normq=norm(query)
    label=norm(candidate.get('label',''))
    if any(w in normq for w in ['buenos aires','temperley','banfield','lomas de zamora']):
        if 'cordoba' in label or 'santa fe' in label and 'santa fe' not in normq:return False
    # Require explicitly named province if returned, unless it is a federal capital synonym.
    for p in ['cordoba','mendoza','santa fe','entre rios','tucuman','salta','chubut']:
        if p in label and p not in normq:return False
    return True


def geocode(q):
    coords=parse_embedded_coords(q)
    if coords:return coords
    key='geo:'+norm(q)
    cached=None if os.environ.get('ROS_PUBLIC')=='1' else cache_read(key,max_age=180*86400)
    if cached:return cached
    items=georef_candidates(q)
    # Only request the next provider when the previous one has no good candidate.
    valid=[]
    for source in [items]:
        for cand in source:
            if not _district_match(q,cand):continue
            rank,quality=_quality(q,cand)
            if rank>=1:valid.append((rank,cand,quality))
        if any(rank>=4 for rank,_,_ in valid):break
    if not any(rank>=4 for rank,_,_ in valid):
        for source in [_result_from_photon(q)]:
            for cand in source:
                if not _district_match(q,cand):continue
                rank,quality=_quality(q,cand)
                if rank>=1:valid.append((rank,cand,quality))
    if not valid:
        for cand in _result_from_nominatim(q):
            if not _district_match(q,cand):continue
            rank,quality=_quality(q,cand)
            if rank>=1:valid.append((rank,cand,quality))
    if not valid:return None
    rank,c,quality=max(valid,key=lambda x:x[0])
    answer={**c,'quality':quality}
    if os.environ.get('ROS_PUBLIC')!='1':cache_write(key,answer)
    return answer


ALIASES={'Nombre':['nombre','sucursal','local','tienda','name'],
         'Dirección':['direccion','domicilio','ubicacion','address','calle'],
         'Latitud':['latitud','lat','latitude'],
         'Longitud':['longitud','lon','lng','longitude'],
         'Cadena':['cadena','marca','brand']}

def read_network(file_name,content):
    name=file_name.lower()
    from io import BytesIO
    if name.endswith(('.xlsx','.xlsm')):df=pd.read_excel(BytesIO(content),dtype=str)
    elif name.endswith('.csv'):
        try:df=pd.read_csv(BytesIO(content),sep=None,engine='python',dtype=str,encoding='utf-8-sig')
        except UnicodeDecodeError:df=pd.read_csv(BytesIO(content),sep=None,engine='python',dtype=str,encoding='latin1')
    else:raise ValueError('Elegí un CSV o XLSX.')
    df.columns=[norm(c) for c in df.columns]
    matched={}
    for new, choices in ALIASES.items():
        for opt in choices:
            if norm(opt) in df.columns:matched[new]=norm(opt);break
    if not matched.get('Dirección') and not (matched.get('Latitud') and matched.get('Longitud')):
        raise ValueError('La red debe tener una columna Dirección o columnas Latitud y Longitud.')
    result=pd.DataFrame()
    for col in ALIASES:result[col]=df[matched[col]] if col in matched else ''
    result=result.fillna('').astype(str)
    result['Nombre']=result['Nombre'].where(result['Nombre'].str.strip()!='',pd.Series([f'Sucursal {i+1}' for i in range(len(result))]))
    result=result.loc[(result['Dirección'].str.strip()!='')|((result['Latitud'].str.strip()!='') & (result['Longitud'].str.strip()!=''))].copy()
    if len(result)>5000:raise ValueError('Límite: 5.000 sucursales por archivo.')
    return result.reset_index(drop=True)


def _prepare_bulk(row):
    address=str(row['Dirección'])
    text=address.split(',')[0].strip()
    if not text:return None
    p=norm(address)
    province='Ciudad Autónoma de Buenos Aires' if any(s in p for s in ['caba','capital federal','ciudad autonoma']) else 'Buenos Aires' if 'buenos aires' in p else None
    entry={'direccion':text,'max':1}
    if province:entry['provincia']=province
    return entry


def geocode_network_georef(df):
    """Official Georef bulk POST. Does not use public Nominatim for mass geocoding."""
    out=df.copy()
    missing=[]
    for i,row in out.iterrows():
        if not valid_latlon(row.get('Latitud'),row.get('Longitud')) and _prepare_bulk(row):missing.append(i)
    if not missing:return out,0
    found=0
    for start in range(0,len(missing),25):
        idxs=missing[start:start+25]
        body={'direcciones':[_prepare_bulk(out.loc[i]) for i in idxs]}
        try:
            response=requests.post(GEOREF,json=body,headers=HEADERS,timeout=18)
            response.raise_for_status()
            blocks=response.json().get('resultados',[])
        except (requests.RequestException,ValueError):break
        for i,record in zip(idxs,blocks):
            opts=record.get('direcciones') or []
            if not opts:continue
            loc=(opts[0].get('ubicacion') or {})
            if valid_latlon(loc.get('lat'),loc.get('lon')):
                cand={'label':opts[0].get('nomenclatura') or '', 'lat':loc['lat'], 'lon':loc['lon']}
                rating,_=_quality(str(out.loc[i]['Dirección']),cand)
                if rating < 1 or not _district_match(str(out.loc[i]['Dirección']),cand):continue
                out.at[i,'Latitud']=str(loc['lat']);out.at[i,'Longitud']=str(loc['lon']);found+=1
        # No aggressive client-side retry loops; failed rows are recorded as pending.
    return out,found


def overpass_pois(lat,lon):
    key=f'osm:v5:{round(lat,4)}:{round(lon,4)}'
    result=cache_read(key,max_age=14*86400)
    if result is not None:return result,'Datos de OpenStreetMap en caché (hasta 14 días)'
    # Fast, keyless vector tiles first. This is a cartographic sample, whose
    # POI completeness may be lower than a full OSM query.
    try:
        from tiles import read_tiles
        elements=read_tiles(lat,lon)
        if elements:
            cache_write(key,elements)
            return elements,'OpenFreeMap / OpenMapTiles (muestra cartográfica OSM)'
    except Exception:
        pass
    query=f'''[out:json][timeout:12];(\n nwr(around:650,{lat:.6f},{lon:.6f})["shop"~"^(supermarket|convenience|bakery|butcher|greengrocer|chemist|clothes|shoes|variety_store|mall|kiosk|department_store|hardware|florist|beverages|books|electronics)$"];\n nwr(around:650,{lat:.6f},{lon:.6f})["amenity"~"^(school|kindergarten|hospital|clinic|doctors|pharmacy|bank|atm|university|post_office|marketplace)$"];\n nwr(around:700,{lat:.6f},{lon:.6f})["highway"="bus_stop"];\n nwr(around:700,{lat:.6f},{lon:.6f})["public_transport"];\n nwr(around:700,{lat:.6f},{lon:.6f})["railway"~"^(station|halt|tram_stop)$"];\n way(around:450,{lat:.6f},{lon:.6f})["highway"~"^(primary|secondary|tertiary|residential|pedestrian|footway)$"];\n);out center qt;'''
    # A short, bounded query is attempted once at each of two public services.
    for endpoint in OVERPASS:
        try:
            r=requests.post(endpoint,data={'data':query},headers=HEADERS,timeout=9)
            r.raise_for_status()
            data=r.json()
            if isinstance(data.get('elements'),list):
                # Avoid storing geometries and huge data in cache.
                elements=[{'id':e.get('id'),'type':e.get('type'),'tags':e.get('tags',{})} for e in data['elements'][:8000]]
                cache_write(key,elements)
                return elements,'OpenStreetMap / Overpass'
        except (requests.RequestException,KeyError,ValueError):continue
    return None,'Overpass no respondió; no se atribuyen valores a sus indicadores'
