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
HEADERS={'User-Agent':'ROS-Web/4.1 (https://rosweb.streamlit.app; geographic scoring, address lookup on click)',
         'Accept':'application/json'}
GEOREF='https://apis.datos.gob.ar/georef/api/direcciones'
GEOREF_V2='https://apis.datos.gob.ar/georef/api/v2.0/direcciones'
PHOTON='https://photon.komoot.io/api/'
NOMINATIM='https://nominatim.openstreetmap.org/search'
OVERPASS=['https://overpass.kumi.systems/api/interpreter',
          'https://overpass-api.de/api/interpreter',
          'https://overpass.private.coffee/api/interpreter']


def db():
    conn=sqlite3.connect(STORE,timeout=3)
    conn.execute('CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, timestamp REAL NOT NULL)')
    return conn


def cache_read(key, max_age=None):
    # Cache outages must never become visitor-facing exceptions.
    conn=None
    try:
        conn=db()
        row=conn.execute('SELECT value,timestamp FROM kv WHERE key=?',(key,)).fetchone()
        if not row or (max_age and time.time()-row[1]>max_age):return None
        return json.loads(row[0])
    except (sqlite3.Error,ValueError,TypeError):
        return None
    finally:
        if conn is not None:conn.close()


def cache_write(key,value):
    conn=None
    try:
        conn=db()
        conn.execute('INSERT OR REPLACE INTO kv VALUES (?,?,?)',
                     (key,json.dumps(value,ensure_ascii=False),time.time()))
        conn.commit()
    except (sqlite3.Error,ValueError,TypeError):
        pass
    finally:
        if conn is not None:conn.close()


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
    """Georef's structured address service, including provincial/county fallbacks.

    In particular "Ioria 358, Lomas de Zamora" needs a county-constrained
    alternative, rather than failing after one unqualified string query.
    Results remain *candidates* until street and province are checked.
    """
    parts=[x.strip() for x in q.split(',') if x.strip()]
    street=parts[0] if parts else q.strip()
    province=next((x for x in parts[1:] if norm(x) in {
        'buenos aires','provincia de buenos aires','caba','capital federal',
        'ciudad autonoma de buenos aires'}),None)
    is_caba=bool(province and norm(province) in {'caba','capital federal','ciudad autonoma de buenos aires'})
    is_ba=bool(province and not is_caba)
    town=next((x for x in parts[1:] if norm(x) not in {
        'buenos aires','provincia de buenos aires','caba','capital federal',
        'ciudad autonoma de buenos aires','argentina'}),None)
    known_counties={
        'temperley':'Lomas de Zamora','banfield':'Lomas de Zamora',
        'llavallol':'Lomas de Zamora','lomas de zamora':'Lomas de Zamora',
        'adrogue':'Almirante Brown', 'longchamps':'Almirante Brown',
        'quilmes':'Quilmes','bernal':'Quilmes', 'avellaneda':'Avellaneda',
    }
    county=known_counties.get(norm(town or ''),town)
    variants=[{'direccion':q,'max':4}]
    if street!=q and (town or province):
        vals={'direccion':street,'max':4}
        if province: vals['provincia']='Ciudad Autónoma de Buenos Aires' if is_caba else 'Buenos Aires'
        elif county: vals['provincia']='Buenos Aires'
        if county and not is_caba: vals['departamento']=county
        variants.append(vals)
        if town:
            vals2={'direccion':street,'localidad':town,'max':4}
            if not is_caba: vals2['provincia']='Buenos Aires'
            variants.append(vals2)
    results=[]
    # Start with V2 and fall back to the stable V0.x endpoint.
    for endpoint in (GEOREF_V2,GEOREF):
        for values in variants[:3]:
            try:
                reply=_json_get(endpoint,values,timeout=(3,5))
            except (requests.RequestException,ValueError,TypeError):
                continue
            for row in reply.get('direcciones',[]):
                loc=row.get('ubicacion') or {}
                if not valid_latlon(loc.get('lat'),loc.get('lon')):
                    continue
                street_name=row.get('calle') or {}
                height=row.get('altura') or {}
                prov=row.get('provincia') or {}
                depart=row.get('departamento') or {}
                locality=row.get('localidad_censal') or row.get('localidad') or {}
                def val(x):return x.get('nombre','') if isinstance(x,dict) else str(x or '')
                num=height.get('valor') if isinstance(height,dict) else height
                results.append({'lat':float(loc['lat']),'lon':float(loc['lon']),
                    'label':row.get('nomenclatura') or '', 'provider':'Georef Argentina',
                    'street':val(street_name),'house_number':str(int(float(num))) if num is not None and str(num).replace('.','',1).isdigit() else str(num or ''),
                    'region':val(prov),'locality':val(locality), 'county':val(depart)})
            if any(_quality(q,r)[0]>=5 and _district_match(q,r) for r in results):
                return results
        if results: break
    return results


def _result_from_photon(q):
    out=[]
    try:
        js=_json_get(PHOTON,{'q':q,'limit':5,'lang':'es'},timeout=6)
        for feature in js.get('features',[]):
            lon,lat=feature['geometry']['coordinates']
            p=feature['properties']
            label=', '.join(str(p[k]) for k in ('street','name','housenumber','city','county','state') if p.get(k))
            if valid_latlon(lat,lon):out.append({'lat':lat,'lon':lon,'label':label,'provider':'Photon',
                 'street':p.get('street') or p.get('name') or '', 'house_number':str(p.get('housenumber') or ''),
                 'locality':p.get('city') or '', 'county':p.get('county') or '', 'region':p.get('state') or ''})
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
                 'address':r.get('address',{}),'type':r.get('type',''),
                 'street':r.get('address',{}).get('road') or r.get('address',{}).get('pedestrian') or '',
                 'house_number':r.get('address',{}).get('house_number') or '',
                 'locality':r.get('address',{}).get('town') or r.get('address',{}).get('city') or r.get('address',{}).get('suburb') or '',
                 'region':r.get('address',{}).get('state') or ''} for r in js if valid_latlon(r.get('lat'),r.get('lon'))]
    except (requests.RequestException,ValueError,KeyError):return []


def _requested_house(q):
    # First chunk only: protects names such as "20 de Septiembre 341".
    head=q.split(',')[0].strip()
    m=re.search(r'(?:^|\s)(\d{1,5})(?:\s*(?:bis|b))?\s*$',head,re.I)
    return m.group(1) if m else None


def _quality(query,candidate):
    """Prefer structured street/number evidence over incidental numbers in labels."""
    head=query.split(',')[0].strip()
    street_head=norm(re.sub(r'\b\d{1,5}\s*(?:bis|b)?\s*$','',head))
    if not street_head:return 0,'No verificable'
    label=norm(candidate.get('label',''))
    from difflib import SequenceMatcher
    street=norm(candidate.get('street',''))
    stop={'de','del','la','el','av','avenida','calle','general','dr','doctor','presidente','julio','san'}
    tokens=[t for t in street_head.split() if t not in stop and len(t)>=3]
    check=street or label
    token_match=sum(token in check.split() for token in tokens)
    # Permit one small name variation (e.g. missing accent / shortening).
    similarity=SequenceMatcher(None,street_head,street).ratio() if street else 0
    if tokens and token_match < max(1,len(tokens)-1) and similarity<.76:
        return -1,'Calle distinta'
    house=_requested_house(query)
    actual=str(candidate.get('house_number') or '').strip()
    if house:
        if actual and actual != house:
            return -1,'Altura distinta'
        if actual==house:
            return 6,'Altura exacta'
        # Only the first comma-delimited portion of unstructured text can
        # contain a defensible matched height. Not postcodes in other fields.
        first=norm(str(candidate.get('label','')).split(',')[0])
        if re.search(r'\b'+re.escape(house)+r'\b',first):
            return 4,'Altura indicada en resultado (verificar)'
        return 1,'Calle localizada; altura sin verificar'
    return 2,'Calle o intersección (verificar)'


def _district_match(query,candidate):
    q=norm(query)
    label=norm(candidate.get('label',''))
    province=norm(candidate.get('region',''))
    full=label+' '+province
    if 'cordoba' in full and 'cordoba' not in q:return False
    if 'santa fe' in full and 'santa fe' not in q:return False
    for name in ('mendoza','entre rios','tucuman','salta','chubut'):
        if name in full and name not in q:return False
    if 'buenos aires' in q and province and not ('buenos aires' in province or 'capital federal' in province):
        return False
    # If county/locality is declared, constrain the search unless the candidate
    # has no structured locality (Nominatim place records may omit it).
    loc=norm(candidate.get('locality','')+' '+candidate.get('county',''))
    by_county={'temperley':'lomas de zamora','banfield':'lomas de zamora','llavallol':'lomas de zamora'}
    for term in ('temperley','banfield','lomas de zamora','quilmes','lanus','la plata','adrogue','avellaneda'):
        if term in q and loc:
            expected=by_county.get(term,term)
            if term not in loc and expected not in loc:
                return False
    return True


def geocode(q):
    coords=parse_embedded_coords(q)
    if coords:return coords
    key='geo:v4:'+norm(q)
    cached=cache_read(key,max_age=30*86400)
    if cached:return cached
    candidates=[]
    for candidate in georef_candidates(q):
        if not _district_match(q,candidate):continue
        rating,quality=_quality(q,candidate)
        if rating>=1:candidates.append((rating,candidate,quality))
    if not any(x[0]>=4 for x in candidates):
        for candidate in _result_from_photon(q):
            if not _district_match(q,candidate):continue
            rating,quality=_quality(q,candidate)
            if rating>=1:candidates.append((rating,candidate,quality))
    if not any(x[0]>=4 for x in candidates):
        for candidate in _result_from_nominatim(q):
            if not _district_match(q,candidate):continue
            rating,quality=_quality(q,candidate)
            if rating>=1:candidates.append((rating,candidate,quality))
    if not candidates:return None
    rating,candidate,quality=max(candidates,key=lambda x:x[0])
    answer={**candidate,'quality':quality}
    cache_write(key,answer)
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
                if rating < 4 or not _district_match(str(out.loc[i]['Dirección']),cand):continue
                out.at[i,'Latitud']=str(loc['lat']);out.at[i,'Longitud']=str(loc['lon']);found+=1
        # No aggressive client-side retry loops; failed rows are recorded as pending.
    return out,found


def overpass_pois(lat, lon):
    """Full OSM features from an actual Overpass response. NEVER use map tiles
    as a proxy for exhaustive business coverage; don't cache failures or partial
    cartographic samples as complete census data.
    """
    key = f'osm_complete:v7:{round(lat,4)}:{round(lon,4)}'
    cached = cache_read(key, max_age=7*86400)
    if isinstance(cached, list) and cached:
        return cached, 'OpenStreetMap (consulta almacenada, hasta 7 días)'
    query = f'''[out:json][timeout:20];(
      nwr(around:700,{lat:.6f},{lon:.6f})["shop"];
      nwr(around:700,{lat:.6f},{lon:.6f})["amenity"~"^(school|kindergarten|hospital|clinic|doctors|dentist|pharmacy|bank|atm|university|college|post_office|marketplace|library|community_centre|restaurant|cafe|fast_food|bar|pub|food_court)$"];
      nwr(around:700,{lat:.6f},{lon:.6f})["highway"="bus_stop"];
      nwr(around:700,{lat:.6f},{lon:.6f})["public_transport"];
      nwr(around:700,{lat:.6f},{lon:.6f})["railway"~"^(station|halt|tram_stop)$"];
      way(around:450,{lat:.6f},{lon:.6f})["highway"~"^(primary|secondary|tertiary|residential|pedestrian|footway)$"];
    );out tags center qt;'''
    for endpoint in OVERPASS:
        try:
            response = requests.post(endpoint, data={'data':query}, headers=HEADERS,
                                     timeout=(4,12))
            response.raise_for_status()
            payload=response.json()
            if not isinstance(payload.get('elements'),list) or payload.get('remark'):
                continue
            # A zero-element dataset isn't a proven complete commercial census.
            data=[{'id':i.get('id'),'type':i.get('type'),
                   'tags':i.get('tags') or {},
                   'lat':i.get('lat', (i.get('center') or {}).get('lat')),
                   'lon':i.get('lon', (i.get('center') or {}).get('lon'))}
                  for i in payload['elements']]
            if not data:
                continue
            cache_write(key, data)
            return data, 'OpenStreetMap (consulta geográfica completa)'
        except (requests.RequestException,ValueError,KeyError,TypeError):
            continue
    return None, 'Servicio geográfico no disponible'
