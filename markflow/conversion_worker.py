"""Process entry point: keep PDF parsing/OCR and their memory outside Qt's UI."""
import logging
import os


def convert_document(source, options, messages, cancel):
    # This module must remain independent of the Qt application. Windows spawn
    # imports it in a fresh interpreter (including the frozen executable).
    from .core import Cancelled, Converter
    if os.environ.get('MARKFLOW_OFFLINE_SELFTEST')=='1':
        import socket
        def offline_only(*args,**kwargs):
            raise RuntimeError('Self-check blocks all network connections')
        socket.socket.connect=offline_only
    logging.getLogger('pdfminer').setLevel(logging.ERROR)
    try:
        result = Converter().convert(source, options,
            lambda text: messages.put(('progress', text)), cancel)
        messages.put(('result', result))
    except Cancelled:
        messages.put(('cancelled', None))
    except Exception as exc:
        messages.put(('error', str(exc)))
