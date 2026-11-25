import unittest
from rolling_podcast import spoken_text,read_feed,BASE,ORIGIN,filename
from datetime import datetime,timezone
from email.utils import format_datetime
import xml.etree.ElementTree as ET

class PodcastTest(unittest.TestCase):
    def test_long_match_and_metadata(self):
        brief="El club ha anunciado el fichaje del jugador."
        dossier=brief+"\n\nInvestigación y contraste\n\nEl club confirma que el jugador ha firmado un contrato de tres temporadas después de superar el reconocimiento médico.\nMedio\n+2\n\nFuentes verificadas\n\nhttps://example.com/a\nConfianza editorial: alta"
        text,long=spoken_text(brief,dossier)
        self.assertTrue(long)
        self.assertIn("tres temporadas",text)
        self.assertNotIn("https",text)
        self.assertNotIn("Confianza",text)
        self.assertNotIn("+2",text)
    def test_mismatched_dossier(self):
        self.assertEqual(spoken_text("Un hecho distinto.", "Investigación y contraste\n\nTexto que no coincide."),("Un hecho distinto.",False))
    def test_missing_long(self):
        self.assertEqual(spoken_text("Explicación breve.","Explicación breve.\nFuentes verificadas\nTexto"),("Explicación breve.",False))
    def test_feed_prunes_old(self):
        now=datetime(2026,11,25,12,tzinfo=timezone.utc)
        root=ET.Element("rss")
        ch=ET.SubElement(root,"channel")
        for id,date in [("a"*24,now),("b"*24,datetime(2026,11,17,12,tzinfo=timezone.utc))]:
            i=ET.SubElement(ch,"item")
            for tag,value in {"guid":f"ttactualidad:tendencia:trv2-{id}:r1","link":ORIGIN+"/tendencias/trv2-"+id,"title":"Noticia","description":"Texto público","pubDate":format_datetime(date,usegmt=True)}.items():
                ET.SubElement(i,tag).text=value
        rows,count=read_feed(ET.tostring(root),now)
        self.assertEqual(count,2)
        self.assertEqual(len(rows),1)
    def test_filename_matches_existing_feed(self):
        self.assertRegex(filename("ttactualidad:tendencia:trv2-"+"a"*24+":r1"),r"^[a-f0-9]{24}\.mp3$")
if __name__=="__main__":
    unittest.main()
