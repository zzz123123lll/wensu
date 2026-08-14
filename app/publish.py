"""通用发布适配器（三件套：webhook / 本地目录 / 剪贴板由前端处理）。

- 目标配置（webhook 地址/自定义头、本地目录）用 settings._encrypt（DPAPI）加密落盘
- 发布是显式动作：选目标 + 格式，点"发布"才发送；结果与失败原因写 publish_logs（诚实）
- webhook 2xx 即 ok，目标平台后续状态由对方负责——不假装"发布成功 = 已上线"
"""

import json
import os
import urllib.parse
from importlib import metadata as importlib_metadata

import httpx

from app import db, settings
from app.domains import exports as export_service

ALLOWED_FORMATS = {"markdown": "md", "html": "wechat", "plain": "txt"}


class PublishError(Exception):
    pass


def _app_version() -> str:
    try:
        return importlib_metadata.version("wensu")
    except Exception:
        return "dev"


def _encrypt_config(config: dict) -> bytes:
    return settings._encrypt(json.dumps(config, ensure_ascii=False).encode("utf-8"))


def _decrypt_config(enc) -> dict:
    try:
        return json.loads(settings._decrypt(bytes(enc)).decode("utf-8"))
    except Exception:
        return {}


def validate_target(kind: str, config: dict) -> dict:
    """校验并规范化目标配置；非法抛 ValueError（精确原因）。"""
    if kind == "webhook":
        url = str(config.get("url") or "").strip()
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise ValueError("Webhook 地址必须是 http/https 完整网址")
        headers = {}
        for k, v in (config.get("headers") or {}).items():
            if len(headers) >= 10:
                break
            k2, v2 = str(k).strip()[:100], str(v).strip()[:500]
            if k2:
                headers[k2] = v2
        return {"url": url, "headers": headers}
    if kind == "local":
        d = str(config.get("dir") or "").strip()
        if not d:
            raise ValueError("本地目录不能为空")
        return {"dir": d}
    raise ValueError(f"未知目标类型：{kind}")


def list_targets_public(conn) -> list[dict]:
    """目标列表（凭据脱敏：不返回完整 URL/头，只给摘要）。"""
    out = []
    for t in db.list_publish_targets(conn):
        cfg = _decrypt_config(t["config_enc"])
        if t["kind"] == "webhook":
            host = urllib.parse.urlparse(cfg.get("url", "")).hostname or "（未知）"
            summary = f"Webhook → {host}（{len(cfg.get('headers', {}))} 个自定义头）"
        else:
            summary = "本地目录 → " + str(cfg.get("dir", ""))
        out.append({"id": t["id"], "name": t["name"], "kind": t["kind"],
                    "enabled": t["enabled"], "summary": summary, "created_at": t["created_at"]})
    return out


def _post_webhook(url: str, headers: dict, json_payload: dict, timeout: float = 10.0):
    try:
        r = httpx.post(url, headers=headers, json=json_payload, timeout=timeout)
        if 200 <= r.status_code < 300:
            return True, f"HTTP {r.status_code}"
        return False, f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        return False, f"网络错误：{e}"


def publish_article(conn, article_id: int, target_id: int, fmt: str) -> dict:
    """发布单篇到目标：渲染内容 → 按类型发送/写入 → 记录日志。"""
    if fmt not in ALLOWED_FORMATS:
        raise PublishError(f"不支持的发布格式：{fmt}")
    art = db.get_article(conn, article_id)
    if art is None:
        raise PublishError(f"草稿 {article_id} 不存在")
    t = db.get_publish_target(conn, target_id)
    if t is None:
        raise PublishError(f"发布目标 {target_id} 不存在")
    cfg = _decrypt_config(t["config_enc"])
    export_fmt = ALLOWED_FORMATS[fmt]
    data = export_service.build_export_data(conn, article_id)
    content = export_service.render(data, export_fmt).decode("utf-8")
    payload = {"title": art["title"], "format": fmt, "content": content,
               "app": "wensu", "version": _app_version()}

    ok, msg = False, ""
    try:
        if t["kind"] == "webhook":
            ok, msg = _post_webhook(cfg.get("url", ""), cfg.get("headers", {}), payload)
        elif t["kind"] == "local":
            os.makedirs(cfg["dir"], exist_ok=True)
            fname = export_service.safe_filename(art["title"], export_fmt)
            dest = os.path.join(cfg["dir"], fname)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)
            ok, msg = True, f"已写入 {dest}"
        else:
            msg = "未知目标类型"
    except OSError as e:
        ok, msg = False, f"写入失败：{e}"
    db.record_publish_log(conn, article_id, target_id, fmt, "ok" if ok else "failed", msg)
    return {"status": "ok" if ok else "failed", "message": msg, "target": t["name"]}
