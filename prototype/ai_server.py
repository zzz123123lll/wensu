# -*- coding: utf-8 -*-
"""文序 · 本地 AI 代理（原型用）
监听 127.0.0.1:8766，转发 OpenAI 兼容 chat/completions 请求。
无 API Key 时降级为演示候选（不消耗任何调用）。
配置：同目录 api_config.json（参见 api_config.example.json）
"""
import json
import re
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import os
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'api_config.json')

FALLBACK_CANDIDATES = [
    {'tag': '方案一 · 更克制', 'text': '那些文字里，没有一句判断属于我——直到读者夸我"写得不错"，而我知道真正写下这些字的人不是我。'},
    {'tag': '方案二 · 更直接', 'text': '我复制、粘贴、改几个词，骗自己写完了一篇。读者说好，可我知道真正动笔的不是我。'},
    {'tag': '方案三 · 更文学', 'text': '那些文字读起来通顺、正确、无懈可击——只是没有一句话来自我。'},
]


def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def call_llm(messages, cfg, timeout=60):
    base = (cfg.get('base_url') or 'https://api.deepseek.com/v1').rstrip('/')
    key = cfg.get('api_key') or ''
    model = cfg.get('model') or 'deepseek-chat'
    body = json.dumps({
        'model': model,
        'messages': messages,
        'temperature': 0.8,
        'stream': False,
    }).encode('utf-8')
    req = urllib.request.Request(
        base + '/chat/completions', data=body, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data['choices'][0]['message']['content']


def parse_candidates(text):
    t = text.strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t, flags=re.S)
    try:
        arr = json.loads(t)
    except Exception:
        m = re.search(r'\[.*\]', t, re.S)
        if not m:
            return []
        try:
            arr = json.loads(m.group(0))
        except Exception:
            return []
    out = []
    for i, it in enumerate(arr[:3]):
        if isinstance(it, str):
            out.append({'tag': '方案%d' % (i + 1), 'text': it.strip()})
        elif isinstance(it, dict):
            txt = (it.get('text') or it.get('content') or '').strip()
            if txt:
                out.append({'tag': it.get('tag') or '方案%d' % (i + 1), 'text': txt})
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(200, {})

    def do_POST(self):
        if self.path != '/api/rewrite':
            return self._send(404, {'error': 'not found'})
        try:
            n = int(self.headers.get('Content-Length', 0))
            payload = json.loads(self.rfile.read(n).decode('utf-8'))
        except Exception:
            return self._send(400, {'error': 'bad json'})
        text = (payload.get('text') or '').strip()
        if not text:
            return self._send(400, {'error': 'empty text'})
        cfg = load_config()
        if not cfg.get('api_key'):
            return self._send(200, {'fallback': True, 'candidates': FALLBACK_CANDIDATES})
        style = payload.get('style') or '更自然、更像人写的'
        sys_prompt = (
            '你是中文写作助手。用户给你一段文字，请给出 3 个改写候选。'
            '要求：保留原意与事实，风格' + style + '，克制、不炫技、不添加原文没有的信息。'
            '只输出 JSON 数组，不要任何解释。每个元素形如 {"tag": "简短风格说明", "text": "改写后的文字"}。'
        )
        try:
            content = call_llm([
                {'role': 'system', 'content': sys_prompt},
                {'role': 'user', 'content': text},
            ], cfg)
            cands = parse_candidates(content)
            if not cands:
                raise ValueError('parse empty')
            return self._send(200, {'fallback': False, 'candidates': cands})
        except Exception as e:
            return self._send(200, {
                'fallback': True, 'error': str(e), 'candidates': FALLBACK_CANDIDATES})

    def log_message(self, *a):
        pass


if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8766'))
    print('文序 AI 代理已启动: http://127.0.0.1:%d （无 key 时为演示模式）' % port)
    HTTPServer(('127.0.0.1', port), Handler).serve_forever()
