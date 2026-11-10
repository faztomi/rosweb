"""ROS territorial: reproduce la lógica original del Excel suministrado.

No realiza solicitudes a OpenStreetMap ni presupone que los datos históricos
representen el entorno de una puerta específica.
"""
from __future__ import annotations
import json
import base64
import zlib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "ros_territorios.json"
WEIGHTS = (17.5, 17.5, 27.5, 37.5)
NAMES = ("Población", "Densidad poblacional", "Densidad comercial", "Cupo disponible")


def normal(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


# Base territorial extraída del Excel ROS original. Se incluye en este archivo para
# que no dependa de que GitHub conserve o suba la carpeta data/.
# El archivo externo es opcional; ante una ruta inexistente, usamos esta copia.
_SNAPSHOT_B85 = (
    "c-"
    "rM%%Wfmb72W$Q8d@cQAXnXbXD7+y#DO$sq{%EC7=t2NlG<)|Pnx9Skug9%B0n%oFS2mF^D_LCd`YTFNv0B!x2jmuh?WKzG}#YyA6}k&?yLCyCq"
    "=PSH=};*c79jvoP2p&OkCTRlcp_gRs84g|0w!)><a(#ysT}tv)i3;Hg)yn_Jq=JpSO0{6oaNN&g@_^ZSA0({Z#K3<8m~u{I0fmSG2bNwye+hzN"
    "^Ng<?Fl|Usw>byWANaP^Hdv-wn_D)Cu7n8E_!dSSB19!q7S82F&U+IMWUk5nK?<jWzwgkY|g_+EL#>z}R2#d;ikEakzi5?_Y7fyLjE3zhun6dO"
    "ttots5^N-"
    ")s8S;tBkFWREIwed1@X9+yKqEZ#1TXgWw%95QG|t{s%u2f*zChts!BF)YV#o4V{*ZoZc(aeFV*x;y>N@cKG$mansOS=w*h_|tDk|Nhsf!`VM=a"
    "WVVQFRS7=Lw8oz<@}zDp{t4)-"
    "wpi#Hy_(?7r<Qk`|Td{FY`NDJnWs%fB(E~{40Oqo!y(g3pBgEa~N;mzlFf>qRdEkyXW@S@1i87;d|u&k6L~}Eu>yLX5g`Ua~G?-"
    "`<nhYZc<)$x5t0k(~SHHooW5@u-"
    "zZs#Wt_EzP`Ow+l=4b+P;6z<$J5_xI8Nd^9Ni$Xy@2g&CdL=_q!{Ru13~_9SJjN|6RavX4HQ#FrbB`lBOa}P^kGXvWY?sd-"
    "~=pvli?F&mkz<2e!JjwQHlzS_z;5cnj39C6SD!;?1@X@7B8j&oxnSE5?+<&^A&C3~Va6R%^kgtI-"
    "A=ZFVnvz!LB{+8M!im3O~&G?z$_DB4s3L*ZsLolBUBvF#;@S(I%<2-+v^+>4bK!~4h<2hAXoO1PjD^^lYjCQ;*-"
    "{#&vZZMG6^iaSZbY?}q3oe%7T#tkDan=6JpNzjIB3~H{3GKq#A2X!)O3&_SJhqiC4s)IL<NR%WB$6HaHNCY8FCE_i_wmHee3-2O*s1?a1pp-"
    ";431yO?a075965-Y{Y8&p|3{cx#WRly5&G|)T>qyJil5iYp9l+UIaFS}++lF-"
    "I&izxnNb%>1OwcTH1e6xA7Hn9}+A@Z<@0TM14pQbLk)|F`%E@$*t%&oJoDy6B0@tXY1jZmWY^N*{@i_aIkjub7ubMX2xT)lf5)^Ag3`NB@S6r^"
    "eTCb)O8sj6`y_;xg!}dfF)YErGKg7Zpc*lB4AGt4i>ebv$&`b$XFDTasldFxJZf@Lw(+SBK+>~|c+Ho|KFhId3oa2sCY0V#nN$5xSl*e{rht6L"
    "=4s6LelX<|F6jH+*Ae(1N9+kF_y`yw(YbTLBVhrVZoFpeIHMpU$WlqwgJ#odM9ZzC&+>A+JS%{k&DtM|6B%1>spaYr8n3sAvb(gm2`P+5xVzW~"
    "qjmVUp0xq5jfxn#nJp0jIMaDVQaFZv_(Gtl{(Fcul0)z?VwC|n|D%(*cod8I|a02(?IYaR_f<mHcE4p=Q?w)P`I(5bVrK>7iyI~}y2nJyC$P})"
    "TG?mU|=l55I1By8;JM8o1V1GJ@G<P9E(=2f&7~sirMa7`bQmvgR@esJj#`cP9lO-QZwGgS5oeWvR$1Ut!<K459w)EGCjW~(XnFNax(maYR|M-"
    "H%gYDXzF0OC`H;Sq}25uBdO@qtrN`v>NuAMZ+@jSwh4Dc1mJQ1hIJ2}4JF1SzJps8GJl8_2z@Wd4;Lr|;*XtG`$%GLcnX7kb|DpGh@>sn}5_Qc"
    "<Ov|$S#&;t>cOOZ+@hCXqbtK^(4%_&Y!t-W-^*!DF+z=$mMOQ6z-"
    ")SeTueW{dFTNj6IIiAdZ>c_fEt|blV8KexA;6hNDoD3J+)_&4q{<8EmKKl?U27Rcaa`B2mcx5%>X1hFQ2lp#iMdwop7n!i8GFhE$nHYRM`=N4I"
    "#i?z_XQiu#kr{D80%H20jJP)1q-IhwlrkT+E%vo*$F_>@L-9ZY<Uv~*#A(tz!wiWUh3@H``NG#_Rax&foqxbM$Tc-"
    "0PX+~;(y1w05S#1508;TrvHYiVe*oR^&my-HDHcVD6Xk%*7NA^`N68#MfkNj3^v~R76Wcc{Wx#AnAmEa%@4x2{8Vv=|y2+;#7l{Afb>lo$aa>N"
    "?NK0oN0@^x-xRd@~m0&?S4cV`%!n}a~XnGz$ZQ>=2!W-a#P!r%*^?G_)j(z@j+=M6A;UKC5j@%`Anb-"
    "D@rqN9mAV?lj<3Ay;Q;W`I+q&Tvcz@YWW<Ou~qvIkassj47!qVm<c`RbvBBeRty{UWGwr&{R(#(t$nL73jQ{=gHYb{VeanY3_rUT$60`IM$Aoc"
    "SY{JzGzw1bPOt76?KCmIFZumJO>g1|Gv`_$Hx4)y3Za>hVp5$V(bG6Dp@Lalh(R6{!{V_BTzbKqPUe31hU>mfJ!OZy+KacBYfpMB(CUPW5DR$S"
    "r<7-1No@=$7%y37Hn#8C7jb`9-Uqq4PKuk%6M{Ow+~(p#Bv(f%LccFyrak!Fxu4P~2;1O;evoht%b-"
    ";THvYbK4sn^>d*RE%q_*~3lWGpr`Z@ZghY9lttot-"
    "Z1n|8*!M5htF0QBsisoI>gPDk!CyAb$$!twTF{YwI}crv5J=)}#tA5UFP&3J>k<N3Xu`Z}nUWvmz2#-"
    "vUiw8U7eOSsxwa7vn+GzdvcR5@8V1D+ml?&GC4W(c~!<vbpjLfSD>DTL%h5ed7AIE?x{S+-"
    "0|tU!<R8hA@IJo`U;H21u!yZ88VMGK4!K8)v_`aw|0VYRWcJ;;W`;hFZEZ$ulY2Z0GZ-"
    "@Y<hRytb`p@+k1RQJSJO0}}9bI3NojtkWiD+qB6|(e0!e`ctR(kG`!oc@B!8VJ0(Z@=CJQu}-"
    "nM2MuVe2+N~{Sosa$U9;A)Z6{@1gvkYA3{78Y!U3PbqL3QbQ@wfE6A;&{Tvq;vX<L=``Gp<DnYR{_qXm5dxOkCD(8u8Pr!jc-"
    "#bkPAJ>E?u+DZc|#<{^MTnefan<p}$9iUTmJ_7$^GoJlW9PSl88^_%Yq%mkT@1evOLS@KMzRIKv`O{dw=ExS^LLYUkKGdFZIhwH&Qs8vLke+6?"
    "TB9hog)0Vm$a|HY!~w1pNL&+Yrg2vZNZ03TeZrzvR!*)u<Xe91$kx-iBnp=_WZhYEcd?WeYfTsl&qS0QH6uGNK6AxacGR?SrY#NO8V%}|(kOXT"
    "BE|ZOn%Jbg!!u;R1G^$Zxx}mVOMlY1*l*AM4R@Pyj#C+duV*6(F}Ny3R>tC+3<IH#GXQ#;uX+F2wN(>YO#`m+wedNi;Ux!VQe%49Mt^zSrBWi%"
    "R-7qRelwy`v>8GA30Kd}0wjVR*UTBVh)oRIEBe~jWMw&38YdA-"
    "@aiR{54Sqglk%9ovWl!eFe8Y<OO_PybO}vg1S@q6B`z&1vNaF>X<Akz7v~ivW6V&z9FMDQWsp8LzRAE3&)^kVR`%#JfuDDac+|tlgo&b(;x*zB"
    "!iERyMSj8shYt8JJQ{5r>|hk&Z4i<gyrfAuN#<_4jnm-AU5%lN%~Jstc#VOfa+`BuQrk77JY{lEmCTFw>vAyvRks+pjTQlogA$(w7pa`WwyG!M"
    "m8b18&Z4OxR06KF(H=qi99jESdEUCnni-"
    "{p2CQ&^CrcQYkCL5w0wVBL>&C^<d1>O`h;)%m`sfC4g&+YgvimKXhx@&rCl%gY({*vwyp62GgJuH%F#^r-"
    "B|JPgC3l}}pCtKe_Mx?r4w5o}fGKYpu$Y8=jFlnp-(&&H8pKzG{r_Ko^8UXTGV!7"
)


@lru_cache(maxsize=1)
def load_data():
    # Los datos ya viajan dentro de engine.py, por lo que NO hacen falta JSON,
    # carpetas adicionales ni una consulta a internet.
    payload = zlib.decompress(base64.b85decode(_SNAPSHOT_B85.encode("ascii")))
    data = json.loads(payload.decode("utf-8"))
    if len(data.get("zonas", [])) != 81:
        raise ValueError("La base territorial incluida en el motor no es válida")
    return data


ALIASES = {
    # Solo localidades de asignación municipal clara. No se infiere barrio a partir de un comercio.
    "temperley": "Lomas De Zamora", "banfield": "Lomas De Zamora",
    "llavallol": "Lomas De Zamora", "turdera": "Lomas De Zamora",
    "villa centenario": "Lomas De Zamora", "ingeniero budge": "Lomas De Zamora",
    "adrogue": "Almirante Brown", "burzaco": "Almirante Brown",
    "longchamps": "Almirante Brown", "glew": "Almirante Brown",
    "claypole": "Almirante Brown", "rafael calzada": "Almirante Brown",
    "malvinas argentinas almirante brown": "Almirante Brown",
    "aldó bonzi": "La Matanza", "aldo bonzi": "La Matanza",
    "isidro casanova": "La Matanza", "san justo": "La Matanza",
    "villa luzuriaga": "La Matanza", "ramos mejia": "La Matanza",
    "tapiales": "La Matanza", "gregorio de laferrere": "La Matanza",
    "del viso": "Pilar", "presidente derqui": "Pilar",
    "pablo podesta": "Tres De Febrero", "caseros": "Tres De Febrero",
    "ciudadela": "Tres De Febrero", "santos lugares": "Tres De Febrero",
    "jose leon suarez": "General San Martin", "villa ballester": "General San Martin",
    "martinez": "San Isidro", "beccar": "San Isidro", "acassuso": "San Isidro",
    "wilde": "Avellaneda", "sarandi": "Avellaneda", "dock sud": "Avellaneda",
    "bernal": "Quilmes", "ezpeleta": "Quilmes", "don bosco": "Quilmes",
    "solano": "Quilmes", "san francisco solano": "Quilmes",
    "garin": "Escobar", "ingeniero maschwitz": "Escobar",
    "monte grande": "Esteban Echeverria", "el jaguel": "Esteban Echeverria",
    "canning": "Esteban Echeverria", "ezeiza": "Ezeiza",
}


def zone_indexes():
    zones = load_data()["zonas"]
    return {(z["ambito"], normal(z["nombre"])): z for z in zones}


def match_address(address: str):
    """Match a named barrio or municipio without relying on a live map provider.

    Only search after the first comma. Street names alone are NEVER interpreted
    as jurisdictions. Returns (row, confidence_label) or (None, reason).
    """
    segments = [normal(s) for s in str(address).split(",")]
    jurisdiction = ", ".join(segments[1:]).strip()
    if not jurisdiction:
        return None, "Escribí también la localidad o el barrio, después de una coma."
    explicit_caba = any(x in jurisdiction for x in ("caba", "capital federal", "ciudad autonoma", "ciudad de buenos aires"))
    idx = zone_indexes()
    matches = []
    for (scope,name),z in idx.items():
        # Exact segment or a longer jurisdiction segment with word boundaries.
        if any(re.search(r"(?<!\w)"+re.escape(name)+r"(?!\w)", s) for s in segments[1:]):
            matches.append((scope,z))
    if matches:
        if explicit_caba:
            matches = [(s,z) for s,z in matches if s=="CABA"]
        if matches:
            # Prefer the most specific CABA barrio when its jurisdiction was explicitly entered.
            matches.sort(key=lambda x:(x[0]=="CABA",len(x[1]["nombre"])),reverse=True)
            return matches[0][1], "Nombre territorial indicado en la dirección"
    for alias,mname in sorted(ALIASES.items(),key=lambda a:len(a[0]),reverse=True):
        alias=normal(alias)
        if any(re.search(r"(?<!\w)"+re.escape(alias)+r"(?!\w)", s) for s in segments[1:]):
            row=idx.get(("PBA",normal(mname)))
            if row:return row, "Localidad asociada a su municipio"
    return None, "No se identificó el barrio o municipio de la base ROS."


def find_by_name(scope, name):
    return zone_indexes().get((scope, normal(name)))


def score(row):
    """Same ordinal rating as the Excel: components in 1..4, weights 17.5/17.5/27.5/37.5."""
    values = row["componentes"]
    weighted = sum(float(x)*p/100.0 for x,p in zip(values, WEIGHTS))
    if abs(weighted-float(row["ros_original"]))>1e-6:
        raise ValueError("Los indicadores no coinciden con el ROS original")
    return {
        "raw":round(weighted,4),
        "score100":round(weighted/4*100,1),
        "clasificacion":row["clasificacion_original"],
        "parts":[{"name":n,"level":int(v),"weight":w,"points":round(w*float(v)/4,2)} for n,v,w in zip(NAMES,values,WEIGHTS)],
    }
