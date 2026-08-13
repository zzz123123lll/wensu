"""Phase 7 验收：扩充验收文章 + 完整检查流程（8766 在线，真实模型）。"""
import json
import time
import urllib.request

B = 'http://127.0.0.1:8766'
AID = 7


def req(method, p, body=None):
    r = urllib.request.Request(B + p, data=json.dumps(body).encode() if body else None,
                               headers={'Content-Type': 'application/json'}, method=method)
    return json.loads(urllib.request.urlopen(r, timeout=30).read().decode())


def main():
    art = req('GET', f'/api/articles/{AID}')
    blocks = art['blocks']
    extra = [
        {'id': 'v12', 'type': 'paragraph', 'text': '有人会反驳：如果 AI 生成的文字质量已经和人类差不多，判断还有意义吗？这个问题本身混淆了两个概念：生成质量与判断责任。生成质量解决的是"像不像人写的"，判断责任解决的是"这段话该不该出现"。前者是技术问题，后者是作者问题。', 'attrs': {}},
        {'id': 'v13', 'type': 'heading2', 'text': '工具进化史给我们的答案', 'attrs': {}},
        {'id': 'v14', 'type': 'paragraph', 'text': '回看文字工具的历史：打字机没有取代作家，反而让更多人开始写作；文字处理器没有取代编辑，反而让修改变得便宜；搜索引擎没有取代记忆，反而让记忆从事实清单变成索引能力。每一轮工具升级，淘汰的都是旧工具的使用方式，而不是使用工具的人。', 'attrs': {}},
        {'id': 'v15', 'type': 'paragraph', 'text': '生成式 AI 的独特之处在于它第一次触及了"产出内容"本身。但这恰恰是它最容易被高估的地方：产出内容不等于产出判断，就像印刷术让书变多，但没有让观点变多。观点仍然是稀缺的，而稀缺的东西才值得被珍视。', 'attrs': {}},
        {'id': 'v16', 'type': 'paragraph', 'text': '所以真正的分水岭不在工具，而在使用工具的人如何定义自己的劳动：如果写作只是把想法翻译成文字，那么 AI 确实会取代你；如果写作是在无数可能的表达中做出选择，那么 AI 只是让你的选择更快地暴露出来。', 'attrs': {}},
    ]
    blocks.extend(extra)
    req('PUT', f'/api/articles/{AID}', {'blocks': blocks, 'base_version': art['version'], 'change_reason': '验收扩充'})
    total = sum(len(b['text']) for b in blocks)
    print(f'扩充后：blocks={len(blocks)} 字数={total}')

    # 完整检查：通用 + 观点长文（AI 语义）+ 公众号渠道（经验建议）
    r = req('POST', '/api/reviews', {'article_id': AID, 'profile_selection': {
        'common': ['common-markdown'], 'type': ['opinion-essay'], 'channel': ['wechat-mini'], 'personal': []}})
    rid = r['review_id']
    print(f'检查 session={rid} 确定性 issues={len(r["issues"])}')

    # 流式（AI + 证据阶段，真实模型，最长 180s）
    req2 = urllib.request.Request(B + f'/api/reviews/{rid}/stream')
    t0 = time.time()
    raw = urllib.request.urlopen(req2, timeout=180).read().decode()
    lines = [json.loads(l) for l in raw.strip().split('\n') if l.strip()]
    stages = [(e.get('stage'), e.get('status'), e.get('count')) for e in lines if e.get('type') == 'stage']
    issues = [e['issue'] for e in lines if e.get('type') == 'issue']
    warns = [e.get('message') for e in lines if e.get('type') == 'warning']
    print(f'流式耗时 {time.time()-t0:.1f}s | stages={stages}')
    print(f'总 issues={len(issues)}（确定性 {sum(1 for i in issues if i["source_type"]=="system")} + AI {sum(1 for i in issues if i["source_type"]=="ai")} + 证据 {sum(1 for i in issues if i["source_type"]=="evidence")}）')
    for i in issues[:8]:
        print(f'  [{i["severity"]}/{i["source_type"]}] {i["rule_id"]}: {i["reason"][:48]}')
    if warns:
        print('warnings:', warns[:2])

    # 导出双版本（公众号渠道补丁）
    e = req('POST', f'/api/reviews/{rid}/exports', {'target': 'wechat-mini'})
    print(f'导出：general={e["general_file"]} channel={e["channel_file"]} stale={len(e["stale"])}')
    g = urllib.request.urlopen(B + f"/api/review-exports/{e['export_id']}/general", timeout=10).read().decode()
    print(f'通用版 {len(g)} 字符，开头：{g[:50]!r}')
    rep = json.loads(urllib.request.urlopen(B + f"/api/review-exports/{e['export_id']}/report", timeout=10).read().decode())
    print(f'摘要：version={rep["article_version"]} issues={rep["issues"]} files={ {k: v["sha1"][:8] for k, v in rep["files"].items() if v} }')


if __name__ == '__main__':
    main()
