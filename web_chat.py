#!/usr/bin/env python3
"""
Kimi-DeepSeek 多模型代码流水线 —— Web 对话框版（类似 Kimi 网页界面）

用法:
  python web_chat.py
  kweb

然后浏览器打开 http://127.0.0.1:5000
"""
import sys
import os
import json
import time
import queue
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, request, jsonify, Response, stream_with_context

import pipeline

app = Flask(__name__)

# 简单内存历史
CHATS = {}
CHATS_LOCK = threading.Lock()


def generate_chat_id():
    return f"chat_{int(time.time() * 1000)}"


def event_stream(chat_id, question):
    """SSE 流，返回流水线每一步的状态"""
    q = queue.Queue()

    def callback(step, status, data):
        q.put({
            "event": "step",
            "step": step,
            "status": status,
            "data": data,
            "time": time.time(),
        })

    def worker():
        try:
            t0 = time.time()
            result = pipeline.run(question, callback=callback, verbose=False)
            elapsed = time.time() - t0
            if result is None:
                q.put({"event": "error", "error": "流水线返回空结果"})
            elif "error" in result:
                q.put({"event": "error", "error": result["error"], "step": result.get("step")})
            else:
                q.put({
                    "event": "done",
                    "code": result.get("code", ""),
                    "paths": result.get("paths", []),
                    "elapsed": elapsed,
                })
        except Exception as e:
            import traceback
            q.put({"event": "error", "error": f"{e}\n\n{traceback.format_exc()}"})
        finally:
            q.put({"event": "close"})

    # 保存用户消息
    with CHATS_LOCK:
        if chat_id not in CHATS:
            CHATS[chat_id] = []
        CHATS[chat_id].append({"role": "user", "content": question, "time": time.time()})

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    assistant_content = ""
    while True:
        try:
            msg = q.get(timeout=300)
        except queue.Empty:
            yield f"data: {json.dumps({'event': 'error', 'error': '超时'}, ensure_ascii=False)}\n\n"
            break

        if msg["event"] == "done":
            assistant_content = msg["code"]
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            break
        elif msg["event"] == "close":
            break
        elif msg["event"] == "error":
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            break
        else:
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"

    # 保存助手消息
    if assistant_content:
        with CHATS_LOCK:
            CHATS[chat_id].append({"role": "assistant", "content": assistant_content, "time": time.time()})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json() or {}
    question = data.get("question", "").strip()
    chat_id = data.get("chat_id") or generate_chat_id()
    if not question:
        return jsonify({"error": "问题为空"}), 400
    return Response(
        stream_with_context(event_stream(chat_id, question)),
        mimetype="text/event-stream",
    )


@app.route("/api/history", methods=["GET"])
def history():
    chat_id = request.args.get("chat_id", "default")
    with CHATS_LOCK:
        return jsonify(CHATS.get(chat_id, []))


@app.route("/api/chats", methods=["GET"])
def chats():
    with CHATS_LOCK:
        return jsonify(list(CHATS.keys()))


if __name__ == "__main__":
    missing = []
    for k, svc in [("KIMI_KEY", "kimi"), ("KIMI_CODE_KEY", "kimi_code"), ("DEEPSEEK_KEY", "deepseek")]:
        if not pipeline.CFG[svc]["key"]:
            missing.append(k)
    if missing:
        print(f"缺少环境变量: {', '.join(missing)}")
        print("请检查 .env 文件或环境变量。")
        sys.exit(2)

    print("\n" + "=" * 50)
    print("Kimi-DeepSeek Web Chat")
    print("打开浏览器访问: http://127.0.0.1:5000")
    print("=" * 50 + "\n")
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
