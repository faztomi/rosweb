import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import load_data,find_by_name,score,match_address
from georef import verify_address,street_and_number
from networks import own_nearby
class FakeResp:
    def raise_for_status(self):pass
    def json(self):return {'direcciones':[{'altura':{'valor':355},'calle':{'nombre':'LORIA'},'departamento':{'nombre':'Almirante Brown'},'provincia':{'nombre':'Buenos Aires'},'ubicacion':{'lat':-34.8,'lon':-58.38},'nomenclatura':'Loria 355, Glew, Almirante Brown'}]}
class FakeSession:
    def get(self,*args,**kwargs):return FakeResp()
class TestROS(unittest.TestCase):
    def test_dataset_81(self):
        data=load_data();self.assertEqual(len(data['zonas']),81)
        self.assertEqual(sum(z['ambito']=='CABA' for z in data['zonas']),48)
        self.assertEqual(sum(z['ambito']=='PBA' for z in data['zonas']),33)
    def test_all_original_formulas(self):
        for z in load_data()['zonas']:self.assertAlmostEqual(score(z)['raw'],z['ros_original'],places=6)
    def test_lomas(self):
        z,_=match_address('Ioria 355, Lomas de Zamora, Buenos Aires')
        self.assertEqual(z['nombre'],'Lomas De Zamora')
        self.assertEqual(score(z)['score100'],90.6)
    def test_balvanera(self):
        z,_=match_address('Combate de los Pozos 100, Balvanera, CABA')
        self.assertEqual(z['nombre'],'Balvanera')
        self.assertEqual(score(z)['score100'],71.9)
    def test_temperley_alias(self):
        z,_=match_address('Julio Sanchez Gardel 28, Temperley, Buenos Aires')
        self.assertEqual(z['nombre'],'Lomas De Zamora')
    def test_wrong_street_only_not_jurisdiction(self):
        z,err=match_address('General San Martín 200')
        self.assertIsNone(z)
    def test_unknown_locality(self):
        z,_=match_address('Calle 999, Rosario, Santa Fe')
        self.assertIsNone(z)
    def test_georef_rejects_wrong_district(self):
        z=find_by_name('PBA','Lomas de Zamora')
        self.assertFalse(verify_address('Loria 355, Lomas de Zamora',z,FakeSession())['verified'])
    def test_network_optional(self):
        self.assertIsNone(own_nearby([],None))
    def test_coordinates(self):
        geo={'verified':True,'lat':-34.75,'lon':-58.4}
        self.assertEqual(len(own_nearby([{'Latitud':-34.75,'Longitud':-58.4,'Nombre':'Tienda'}],geo)),1)
if __name__=='__main__':unittest.main()

class Upload:
    def __init__(self,name,data):self.name=name;self._data=data
    def getvalue(self):return self._data

class NetworkRealCSV(unittest.TestCase):
    def test_actual_dia_sample(self):
        from networks import read_network
        csv_file=Path('/mnt/data/RED_DIA_REFERENCIAS_PUBLICAS_PARCIAL.csv')
        if csv_file.exists():
            rows=read_network(Upload('RED_DIA.csv',csv_file.read_bytes()))
            self.assertEqual(len(rows),139)
            self.assertEqual(rows[0]['Nombre'],'DIA referencia 001')
            self.assertEqual(score(find_by_name('PBA','Lomas de Zamora'))['raw'],3.625)
