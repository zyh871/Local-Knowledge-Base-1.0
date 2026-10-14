"""
回答不完整（截断）诊断脚本。

用于区分四种成因，每一种都有明确的判据：

    ① 模型能力       —— 模型自己写不下去 / 不擅长，finish_reason=stop 且长度正常
    ② max_tokens     —— finish_reason="length"，usage.completion_tokens 顶到上限
    ③ 流式被截断     —— 流式总字数与同步调用差异明显，或流中 finish_reason 缺失/异常
    ④ 前端渲染       —— 后端 final.answer 完整，但页面显示不全（见 --frontend 提示）

用法：
    cd backend
    python scripts/diagnose_truncation.py                # 用当前 .env 的模型
    python scripts/diagnose_truncation.py --max-tokens 4096
    python scripts/diagnose_truncation.py --prompt "写一篇2000字的技术介绍"

输出的关键字段是 `finish_reason` 与 `usage.completion_tokens`：
    finish_reason == "length"  → 确定是 ② max_tokens 截断
    finish_reason == "stop"    → 模型正常写完 → 若仍显短，去看是不是 ③/④
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_PROMPT = (
    "请详细介绍 Clash Verge 的特点，至少写 8 个方面，每个方面 2-3 句话，"
    "尽量展开，不要省略。"
)


def _finish_reason_of(chunk) -> str | None:
    """从流式 chunk 中提取 finish_reason（不同 provider 放的位置不一致）。"""
    meta = getattr(chunk, "response_metadata", None) or {}
    if isinstance(meta, dict):
        if meta.get("finish_reason"):
            return meta["finish_reason"]
        # 有些 provider 嵌在 choices 里
        choices = meta.get("choices")
        if isinstance(choices, list) and choices:
            fr = choices[0].get("finish_reason")
            if fr:
                return fr
    gen = getattr(chunk, "generation_info", None) or {}
    if isinstance(gen, dict) and gen.get("finish_reason"):
        return gen["finish_reason"]
    return None


def run_sync(llm, prompt: str) -> None:
    print("=" * 66)
    print("【同步调用】—— 判断是否被 max_tokens 截断")
    print("=" * 66)
    started = time.time()
    message = llm.invoke(prompt)
    text = message.content if hasattr(message, "content") else str(message)

    meta = getattr(message, "response_metadata", None) or {}
    usage = getattr(message, "usage_metadata", None) or meta.get("token_usage") or {}

    print(f"  finish_reason    : {meta.get('finish_reason')}")
    print(f"  usage            : {usage}")
    print(f"  正文字数         : {len(text)}")
    print(f"  结尾 30 字       : {text[-30:]!r}")
    print(f"  耗时             : {time.time() - started:.1f}s")
    print()

    if meta.get("finish_reason") == "length":
        print("  >>> 判定：命中 max_tokens 上限（②）。模型还没写完就被强制停止。")
    elif meta.get("finish_reason") == "stop":
        print("  >>> 判定：模型正常收尾（①）。若内容仍显短，属模型自身能力/风格问题。")
    else:
        print(f"  >>> 判定：finish_reason 异常（{meta.get('finish_reason')}），需看服务端返回体。")
    print()


def run_stream(llm, prompt: str) -> dict:
    print("=" * 66)
    print("【流式调用】—— 判断是否在传输层被截断")
    print("=" * 66)
    started = time.time()
    parts: list[str] = []
    finish_reason = None
    chunk_count = 0
    last_chunk_at = started

    try:
        for chunk in llm.stream(prompt):
            chunk_count += 1
            text = chunk.content if hasattr(chunk, "content") else ""
            if text:
                parts.append(text)
            fr = _finish_reason_of(chunk)
            if fr:
                finish_reason = fr
            last_chunk_at = time.time()
    except Exception as exc:  # noqa: BLE001
        print(f"  !! 流式抛异常（③ 的直接证据）：{type(exc).__name__}: {exc}")

    full = "".join(parts)
    print(f"  chunk 数         : {chunk_count}")
    print(f"  finish_reason    : {finish_reason}")
    print(f"  正文字数         : {len(full)}")
    print(f"  结尾 30 字       : {full[-30:]!r}")
    print(f"  总耗时           : {last_chunk_at - started:.1f}s")
    print()

    if finish_reason == "length":
        print("  >>> 判定：流在 max_tokens 处被服务端正常终止（②）。")
    elif finish_reason is None and chunk_count > 0:
        print("  >>> 判定：流没有给出 finish_reason —— 连接可能在正常收尾前就断了（③）。")
        print("      建议对比上面的同步调用字数：明显偏少即可确认。")
    elif finish_reason == "stop":
        print("  >>> 判定：流正常收尾（①/④）。backend 侧无问题。")
    print()
    return {"chars": len(full), "finish_reason": finish_reason, "chunks": chunk_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="回答截断诊断")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="显式指定 max_tokens；不传则用 provider 默认（当前线上即此情况）")
    parser.add_argument("--skip-stream", action="store_true")
    args = parser.parse_args()

    from app.core.config import get_settings

    settings = get_settings()
    print()
    print(f"后端        : {settings.model.backend}")
    print(f"LLM 模型    : {settings.model.llm_model}")
    print(f"max_tokens  : {args.max_tokens if args.max_tokens else '（未设置 → 用 provider 默认）'}")
    print()

    from app.rag.providers import get_llm

    llm = get_llm()
    if args.max_tokens:
        # 用 bind() 生成新的 Runnable 配置，不要试图给返回值赋属性
        # （`_ChatModelBinding` 是 frozen 的，赋值会抛 ValueError）
        llm = llm.bind(max_tokens=args.max_tokens)

    run_sync(llm, args.prompt)

    stream_result = None
    if not args.skip_stream:
        stream_result = run_stream(llm, args.prompt)

    print("=" * 66)
    print("【如何区分前端渲染问题（④）】")
    print("=" * 66)
    print("  后端已在 `final` 事件里给出完整 answer，前端会用**它覆盖**流式拼接结果")
    print("  （Chat.vue: `messages[assistantIdx].content = finalAnswer`）。")
    print("  → 页面显示的最终文本 == 后端 final.answer。")
    print()
    print("  因此：把浏览器 Network 面板里 /ask/stream 的最后一帧 final.answer")
    print("  与页面上看到的文本逐字对比 ——")
    print("    * 两者一致且都短  → 问题在后端/模型（①②③）")
    print("    * 帧里完整、页面短 → 才是 ④ 前端渲染（Markdown 渲染丢内容 / CSS 截断 / 虚拟滚动）")
    print()
    print("  另外可用 curl 直接抓流，绕开浏览器：")
    print("    curl -N -X POST http://127.0.0.1:8001/ask/stream \\")
    print("      -H 'Authorization: Bearer <TOKEN>' -H 'Content-Type: application/json' \\")
    print("      -d '{\"question\":\"...\",\"conversation_id\":\"diag\"}'")
    print()

    if stream_result is not None and stream_result["finish_reason"] is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
