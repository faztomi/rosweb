"""On-demand, bounded OpenFreeMap vector-tile queries (OpenMapTiles schema).
This is a *cartographic sample*, not a definitive POI census. Never silently
interpret a missing tile as zero amenities.
"""
from __future__ import annotations
import math
from concurrent.futures import ThreadPoolExecutor
import requests
from engine import geo_dist, norm

TILES_URL='https://tiles.openfreemap.org/planet/latest/{z}/{x}/{y}.pbf'
Z=14
UA='ROS-Simple-AR/1.0 (local user application; bounded OSM tiles)'


def xy(lat,lon,z=Z):
    lat=max(-85.05,min(85.05,lat))
    n=2**z
    return (lon+180)/360*n,(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n


def feature_coords(geom):
    t=geom.get('type')
    co=geom.get('coordinates')
    if t=='Point':return co
    if t=='LineString':return co[len(co)//2] if co else None
    if t=='Polygon':return co[0][len(co[0])//2] if co and co[0] else None
    if t=='MultiPoint':return co[0] if co else None
    if t=='MultiLineString':return co[0][len(co[0])//2] if co and co[0] else None
    if t=='MultiPolygon':return co[0][0][0] if co and co[0] and co[0][0] else None
    return None


def tile_latlon(x,y,zoom,relative_x,relative_y,extent):
    xx=(x+relative_x/extent)/(2**zoom)
    yy=(y+relative_y/extent)/(2**zoom)
    return (math.degrees(math.atan(math.sinh(math.pi*(1-2*yy)))),xx*360-180)


def _fetch(tile):
    x,y=tile
    url=TILES_URL.format(z=Z,x=x,y=y)
    r=requests.get(url,headers={'User-Agent':UA},timeout=8)
    r.raise_for_status()
    if not r.content or len(r.content)>4_000_000:raise ValueError('Tile unavailable or unexpectedly large')
    return tile,r.content


def read_tiles(lat,lon):
    try:
        import mapbox_vector_tile
    except ImportError:return None
    # 650-700 m around each address, maximum 9 tiles at z14 in Argentina.
    r_m=760
    dlat=r_m/111_320
    dlon=r_m/(111_320*max(.35,math.cos(math.radians(lat))))
    x1,y1=xy(lat+dlat,lon-dlon);x2,y2=xy(lat-dlat,lon+dlon)
    left,right=int(min(x1,x2)),int(max(x1,x2))
    top,bottom=int(min(y1,y2)),int(max(y1,y2))
    tiles=[(i,j) for i in range(left,right+1) for j in range(top,bottom+1)]
    if len(tiles)>9:return None
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:raw=list(pool.map(_fetch,tiles))
        result=[];seen=set()
        for (tx,ty),blob in raw:
            layers=mapbox_vector_tile.decode(blob,default_options={'geojson':True,'y_coord_down':True})
            for layername in ['poi','transportation']:
                layer=layers.get(layername)
                if not layer:continue
                extent=layer.get('extent') or 4096
                for f in layer.get('features',[]):
                    p=f.get('properties') or {}
                    coord=feature_coords(f.get('geometry') or {})
                    if not coord:continue
                    lat0,lon0=tile_latlon(tx,ty,Z,coord[0],coord[1],extent)
                    if geo_dist(lat,lon,lat0,lon0)>720:continue
                    cl=norm(p.get('class',''));sub=norm(p.get('subclass',''))
                    if layername=='poi':
                        tags={'name':p.get('name',''),'brand':p.get('brand','')}
                        if sub in {'supermarket','convenience','bakery','butcher','greengrocer','chemist','clothes','shoes','mall','kiosk','department_store','hardware','florist','beverages','books','electronics'}:
                            tags['shop']=sub
                        elif cl in {'grocery','shop','clothing_store','alcohol_shop'}:
                            tags['shop']='convenience' if cl=='grocery' else 'variety_store'
                        if sub in {'school','kindergarten','hospital','clinic','doctors','pharmacy','bank','atm','university','post_office','marketplace'}:
                            tags['amenity']=sub
                        elif cl in {'school','hospital','atm','college','post','library'}:
                            tags['amenity']='school' if cl=='college' else 'post_office' if cl=='post' else 'hospital' if cl=='hospital' else cl
                        if cl=='bus' or sub in {'bus_stop','platform','station','tram_stop'}:tags['highway']='bus_stop'
                        if cl=='railway':tags['railway']='station'
                        if not any(k in tags for k in ['shop','amenity','highway','railway']):continue
                        typ='node'
                    else:
                        if cl not in {'primary','secondary','tertiary','minor','path'}:continue
                        tags={'highway': 'residential' if cl=='minor' else 'footway' if cl=='path' else cl}
                        typ='way'
                    osm_id=p.get('osm_id') or f.get('id')
                    # Avoid counting the same POI more than once when clipped across neighboring tiles.
                    marker=(layername,str(osm_id)) if p.get('osm_id') else (layername,cl,sub,round(lat0,4),round(lon0,4))
                    if marker in seen:continue
                    seen.add(marker)
                    result.append({'type':typ,'id':str(osm_id),'tags':tags})
        return result
    except (requests.RequestException,ValueError,KeyError,TypeError,IndexError,OverflowError):return None
