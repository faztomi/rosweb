import unittest
import osm_resiliente as g

class GeoTest(unittest.TestCase):
 def make_georef(self,street='Ioria',district='Lomas de Zamora',locality='Lomas de Zamora',number=355):
  return dict(ubicacion={'lat':-34.76,'lon':-58.43},calle={'nombre':street},altura={'valor':number},departamento={'nombre':district},localidad_censal={'nombre':locality},provincia={'nombre':'Buenos Aires'},nomenclatura=f'{street} {number}, {locality}, {district}')
 def test_rechaza_glew(self):
  req=g.extract_address('Ioria 355, Lomas de Zamora, Buenos Aires')
  bad=self.make_georef('Ioria','Almirante Brown','Glew')
  self.assertIsNone(g._from_georef(bad,req,None))
 def test_acepta_lomas(self):
  req=g.extract_address('Ioria 355, Lomas de Zamora, Buenos Aires')
  good=self.make_georef('Ioria','Lomas de Zamora','Lomas de Zamora')
  self.assertIsNotNone(g._from_georef(good,req,None))
 def test_rechaza_calle_diferente(self):
  req=g.extract_address('Ioria 355, Lomas de Zamora, Buenos Aires')
  bad=self.make_georef('Lavalle','Lomas de Zamora','Lomas de Zamora')
  self.assertIsNone(g._from_georef(bad,req,None))
 def test_rechaza_altura_distinta(self):
  req=g.extract_address('Ioria 355, Lomas de Zamora, Buenos Aires')
  bad=self.make_georef(number=359)
  self.assertIsNone(g._from_georef(bad,req,None))
 def test_rechaza_nominatim_glew(self):
  req=g.extract_address('Ioria 355, Lomas de Zamora, Buenos Aires')
  bad={'lat':-34.76,'lon':-58.43,'address':{'road':'Ioria','house_number':'355','town':'Glew','county':'Almirante Brown','state':'Buenos Aires'}}
  self.assertIsNone(g._from_nominatim(bad,req,None))
 def test_acepta_nominatim_lomas(self):
  req=g.extract_address('Ioria 355, Lomas de Zamora, Buenos Aires')
  good={'lat':-34.76,'lon':-58.43,'address':{'road':'Ioria','house_number':'355','city':'Lomas de Zamora','county':'Lomas de Zamora','state':'Buenos Aires'}}
  self.assertIsNotNone(g._from_nominatim(good,req,None))
 def test_rechaza_photon_glew(self):
  req=g.extract_address('Ioria 355, Lomas de Zamora, Buenos Aires')
  bad={'geometry':{'coordinates':[-58.43,-34.76]},'properties':{'street':'Ioria','housenumber':'355','city':'Glew','county':'Almirante Brown','state':'Buenos Aires'}}
  self.assertIsNone(g._from_photon(bad,req,None))
 def test_no_malinterpretar_numeros_calle(self):
  self.assertEqual(g.extract_address('9 de Julio 500, Lomas de Zamora')['number'],500)
  self.assertEqual(g.extract_address('20 de Septiembre 341, Lomas de Zamora')['street'],'20 de Septiembre')
if __name__ == '__main__':unittest.main()
