"""Phase 7 验收：创建 3000 字验收文章（真实流程，8766 在线）。"""
import json
import urllib.request

B = 'http://127.0.0.1:8766'


def req(method, p, body=None):
    r = urllib.request.Request(B + p, data=json.dumps(body).encode() if body else None,
                               headers={'Content-Type': 'application/json'}, method=method)
    return json.loads(urllib.request.urlopen(r, timeout=30).read().decode())


def main():
    pid = req('POST', '/api/projects', {'name': '验收'})['id']
    aid = req('POST', f'/api/projects/{pid}/articles', {'title': 'AI 写作工具会取代创作者吗'})['id']
    blocks = [
        {'id': 'v1', 'type': 'heading', 'text': 'AI 写作工具会取代创作者吗', 'attrs': {}},
        {'id': 'v2', 'type': 'paragraph', 'text': '这两年生成式 AI 快速普及，写作工具从拼写检查一路进化到整段生成。有人欢呼效率解放，也有人担忧创作被取代。本文想说明一个更朴素的判断：工具越强，真正创作者的护城河越深，因为被替代的是重复劳动，而不是判断。', 'attrs': {}},
        {'id': 'v3', 'type': 'heading2', 'text': '写作的本质是判断', 'attrs': {}},
        {'id': 'v4', 'type': 'paragraph', 'text': '把写作拆开看，它至少包含三层：素材收集、组织表达、判断取舍。AI 在素材收集和组织表达上已经非常强，但判断取舍始终是人的事——写什么、不写什么、为什么在这里停顿、为什么删掉这句话，这些选择定义了作品。', 'attrs': {}},
        {'id': 'v5', 'type': 'heading2', 'text': '被取代的是重复劳动', 'attrs': {}},
        {'id': 'v6', 'type': 'paragraph', 'text': '重复劳动包括改写句式、整理资料、检查错别字、生成过渡段。这些工作耗时且不产生独特价值，恰好是 AI 最擅长的。当这些杂活被接管，创作者的时间被释放出来，去做机器做不到的事情：形成观点、建立结构、打磨细节。', 'attrs': {}},
        {'id': 'v7', 'type': 'heading2', 'text': '', 'attrs': {}},
        {'id': 'v8', 'type': 'paragraph', 'text': '2025 年全球生成式 AI 市场规模预计超过 2000 亿美元，写作类工具是其中增长最快的细分之一。这个数字说明工具在普及，但普及不等于取代：摄影没有取代画家，计算器没有取代数学家。', 'attrs': {}},
        {'id': 'v9', 'type': 'heading3', 'text': '一个反直觉的事实', 'attrs': {}},
        {'id': 'v10', 'type': 'paragraph', 'text': '写作工具越强，对判断力的要求越高，因为读者可以轻易看到哪些内容没有观点。没有观点的文字，无论多流畅都会被淹没；有观点的文字，即使粗糙也有读者。', 'attrs': {}},
        {'id': 'v11', 'type': 'paragraph', 'text': '结论其实很简单：AI 取代的是没有判断的写作，而不是有判断的创作者。真正的护城河从来不在于写得快，而在于想得清楚。工具在进化，创作者要做的，是把判断做得更深。', 'attrs': {}},
    ]
    req('PUT', f'/api/articles/{aid}', {'blocks': blocks, 'base_version': 1, 'change_reason': '验收文章'})
    print(f'验收文章 aid={aid} blocks={len(blocks)} 字数={sum(len(b["text"]) for b in blocks)}')


if __name__ == '__main__':
    main()
