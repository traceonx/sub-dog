"""Captcha OCR backed by ppllocr (https://github.com/gitpetyr/ppllocr, MIT).

The engine calls ``session.ocr.recognize(image_bytes)`` and submits whatever
non-empty answer comes back; an empty answer falls through to manual input
(web jobs) or failure (external jobs). So this adapter only answers when the
result looks exactly like the site's captcha: 5 digits, each confident.

OCR_ENGINE=glyph switches back to the engine's built-in glyph matcher.
"""
from __future__ import annotations

import os
import re
import threading

CAPTCHA_LEN = 5
MIN_CONF = float(os.environ.get('OCR_MIN_CONF', '0.5'))

_lock = threading.Lock()
_model = None
_load_error: str | None = None


def _load_model():
    """Load the ONNX model once per process (about 1 s, shared by all jobs)."""
    global _model, _load_error
    with _lock:
        if _model is None and _load_error is None:
            try:
                from ppllocr import OCR
                _model = OCR()
            except Exception as exc:  # missing package, broken model, unsupported CPU
                _load_error = f'{type(exc).__name__}: {exc}'
        return _model


class PplOCR:
    name = 'ppllocr'

    def __init__(self, model, logger=None):
        self.model = model
        self.log = logger or (lambda _message: None)
        self.ready = True

    def recognize(self, image: bytes) -> str | None:
        text, details = self.model.classification_box(image)
        digits = [d for d in details if d['char'].isdigit()]
        answer = ''.join(d['char'] for d in digits)
        lowest = min((d['conf'] for d in digits), default=0.0)
        if len(digits) != len(details) or not re.fullmatch(rf'\d{{{CAPTCHA_LEN}}}', answer) or lowest < MIN_CONF:
            self.log(f'[验证码] ppllocr 结果不可信（{text or "空"}，最低置信度 {lowest:.2f}），改为人工处理')
            return None
        self.log(f'[验证码] ppllocr 识别：{answer}（最低置信度 {lowest:.2f}）')
        return answer


def attach(client, logger) -> str:
    """Install the configured OCR on a ZimukuClient; return the engine name in use."""
    if client.session.ocr is None:  # OCR disabled for this job
        return 'off'
    if os.environ.get('OCR_ENGINE', 'ppllocr').lower() == 'glyph':
        return 'glyph'
    model = _load_model()
    if model is None:
        logger(f'[验证码] ppllocr 加载失败，改用内置字形识别：{_load_error}')
        return 'glyph'
    client.ocr = client.session.ocr = PplOCR(model, logger)
    return 'ppllocr'


def preload():
    """Warm the model in the background so the first captcha is not slowed down."""
    if os.environ.get('OCR_ENGINE', 'ppllocr').lower() != 'glyph':
        threading.Thread(target=_load_model, name='ocr-preload', daemon=True).start()


def status() -> dict:
    engine = 'glyph' if os.environ.get('OCR_ENGINE', 'ppllocr').lower() == 'glyph' else 'ppllocr'
    return {'engine': engine, 'loaded': _model is not None, 'error': _load_error}
