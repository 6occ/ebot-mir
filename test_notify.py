import sys
import types

# Stub requests module to avoid external dependency
requests_stub = types.ModuleType('requests')
requests_stub.post = lambda *a, **kw: None
sys.modules['requests'] = requests_stub

import notify


def test_send_error_handles_module(monkeypatch):
    sent = {}

    def fake_send_text(text, silent=None, parse_mode=None):
        sent['text'] = text
        return True, None

    monkeypatch.setattr(notify, '_send_text', fake_send_text)
    monkeypatch.setattr(notify, '_cooldown_passed', lambda sig: True)
    monkeypatch.setattr(notify, '_mark_sent', lambda sig: None)

    ok = notify.send_error('ctx', RuntimeError('boom'), '<module> info')
    assert ok
    assert '&lt;module&gt; info' in sent['text']
    assert '<module> info' not in sent['text']
