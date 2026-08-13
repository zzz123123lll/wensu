"""E2E 假 LLM 服务：OpenAI 兼容 /v1/chat/completions，固定可控回复。

不访问公网；供真实后端 E2E 把模型调用指向本地。
- GET /health → ok
- POST /v1/chat/completions → {"choices":[{"message":{"content": <回复>}}]}
  回复内容由请求体 x-e2e-reply 指定（默认"这是本地假模型的回答"）；
  可用 x-e2e-error 触发 401/500 模拟上游失败。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

app = FastAPI(title="fake-llm")

DEFAULT_REPLY = "这是本地假模型的回答，用于真实后端 E2E。"


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    reply = body.get("x-e2e-reply") or DEFAULT_REPLY
    err = body.get("x-e2e-error")
    if err == "401":
        return JSONResponse({"error": {"message": "invalid api key"}}, status_code=401)
    if err == "500":
        return JSONResponse({"error": {"message": "upstream boom"}}, status_code=500)
    return {
        "id": "chatcmpl-e2e",
        "object": "chat.completion",
        "model": body.get("model", "e2e-model"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FAKE_LLM_PORT", "8899")), log_level="warning")
