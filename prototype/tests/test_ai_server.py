# -*- coding: utf-8 -*-
"""文序原型 · ai_server 纯函数测试（不依赖网络 / API Key）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_server import FALLBACK_CANDIDATES, load_config, parse_candidates


def test_fallback_structure():
    assert len(FALLBACK_CANDIDATES) == 3
    for c in FALLBACK_CANDIDATES:
        assert c['tag'] and c['text']


def test_parse_plain_array():
    out = parse_candidates('[{"tag": "A", "text": "x"}, {"tag": "B", "text": "y"}]')
    assert len(out) == 2
    assert out[0]['text'] == 'x'
    assert out[1]['tag'] == 'B'


def test_parse_fenced_json():
    out = parse_candidates('```json\n[{"tag": "A", "text": "你好"}]\n```')
    assert len(out) == 1
    assert out[0]['text'] == '你好'


def test_parse_string_array_caps_at_three():
    out = parse_candidates('["a", "b", "c", "d"]')
    assert len(out) == 3
    assert out[2]['tag'].startswith('方案')


def test_parse_garbage_returns_empty():
    assert parse_candidates('抱歉，我无法完成') == []


def test_parse_with_surrounding_text():
    out = parse_candidates('好的，候选如下：[{"tag": "A", "text": "x"}] 希望有帮助')
    assert len(out) == 1


def test_parse_missing_fields_skipped():
    out = parse_candidates('[{"tag": "A"}, {"text": "只有文本"}]')
    assert len(out) == 1
    assert out[0]['text'] == '只有文本'


def test_load_config_returns_dict():
    c = load_config()
    assert isinstance(c, dict)
