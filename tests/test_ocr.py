"""ppllocr captcha recognition on real zimuku captchas (file name = answer)."""
from io import BytesIO
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
import captcha_ocr
from engine_adapter import build_engine

SAMPLES = sorted((ROOT / 'tests/fixtures/captcha').glob('*.bmp'))


class FakeModel:
    def __init__(self, details):
        self.details = details

    def classification_box(self, _image):
        return ''.join(d['char'] for d in self.details), self.details


class OcrTests(unittest.TestCase):
    def test_real_captchas(self):
        engine = build_engine(lambda _m: None, lambda _i: None, use_ocr=True)
        ocr = engine.zimuku_client.session.ocr
        self.assertIsInstance(ocr, captcha_ocr.PplOCR)
        self.assertIs(engine.zimuku_client.ocr, ocr)
        self.assertEqual(len(SAMPLES), 3)
        for sample in SAMPLES:
            self.assertEqual(ocr.recognize(sample.read_bytes()), sample.stem, sample.name)
        self.assertTrue(captcha_ocr.status()['loaded'])

    def test_untrusted_results_go_to_manual(self):
        blank = BytesIO()
        Image.new('RGB', (100, 27), 'white').save(blank, format='BMP')
        model = captcha_ocr._load_model()
        self.assertIsNone(captcha_ocr.PplOCR(model).recognize(blank.getvalue()))
        good = [{'char': c, 'conf': 0.9} for c in '12345']
        self.assertEqual(captcha_ocr.PplOCR(FakeModel(good)).recognize(b''), '12345')
        for details in (good[:4], good[:4] + [{'char': 'a', 'conf': 0.9}],
                        good[:4] + [{'char': '5', 'conf': 0.2}], good + [{'char': '6', 'conf': 0.9}]):
            self.assertIsNone(captcha_ocr.PplOCR(FakeModel(details)).recognize(b''))

    def test_disabled_and_glyph_fallback(self):
        engine = build_engine(lambda _m: None, lambda _i: None, use_ocr=False)
        self.assertIsNone(engine.zimuku_client.session.ocr)
        with patch.dict(os.environ, {'OCR_ENGINE': 'glyph'}):
            engine = build_engine(lambda _m: None, lambda _i: None, use_ocr=True)
            self.assertEqual(type(engine.zimuku_client.session.ocr).__name__, 'GlyphOCR')
        logs = []
        with patch.object(captcha_ocr, '_load_model', return_value=None):
            engine = build_engine(logs.append, lambda _i: None, use_ocr=True)
        self.assertEqual(type(engine.zimuku_client.session.ocr).__name__, 'GlyphOCR')
        self.assertTrue(any('ppllocr 加载失败' in m for m in logs))


if __name__ == '__main__':
    unittest.main()
