"""RAG 流式事件生成器

从 api/routes.py 抽取，被 /chat/stream 和 /agent/stream 共用。
字符级 JSON 状态机实现真正的流式输出——在 LLM 生成过程中逐字推送，
而非等完整响应返回后再一次性发送。
"""

import os
import json
import time
import uuid
import asyncio
from typing import Optional, Dict, Any, AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Empty

from .topic_detection import detect_topic_switch
from shared.stream_utils import (
    _save_session_message,
    _parse_stream_structured,
    _enrich_recommendations,
)

# 线程池，用于将阻塞操作（检索、TTS）卸载到后台线程
_stream_executor = ThreadPoolExecutor(max_workers=2)


async def rag_stream_events(
    rag_service,
    question: str,
    session_id: str,
    memory,
    chat_history: list,
    filter: Optional[Dict[str, Any]] = None,
    tts_service=None,
    ecommerce_graph=None,
    enriched_query: str = "",
    skip_query_expansion: bool = False,
    enable_tts: bool = False,
) -> AsyncGenerator[str, None]:
    """核心 RAG 流式事件生成器，被 /chat/stream 和 /agent/stream 共用。

    enriched_query: 如果外部已做查询增强（含话题切换检测/槽位累积/实体桥接），
                    传入此参数可跳过内部 enrichment 步骤。
    skip_query_expansion: 简单查询跳过 LLM 扩展（3 个并行调用），直接检索。
    enable_tts: 是否启用语音合成（默认关闭，仅语音输入场景开启）
    """
    t0 = time.time()
    search_q = question if question and len(question.strip()) >= 2 else (enriched_query or question)

    # 短问题自动跳过查询扩展，省 LLM 调用
    if not skip_query_expansion and len(search_q.strip()) <= 15:
        skip_query_expansion = True
        print(f"[RAG] 短问题自动跳过查询扩展: '{search_q[:30]}'", flush=True)

    # 话题切换检测
    topic_hint = detect_topic_switch(question, chat_history)
    if topic_hint:
        print(f"[RAG] {topic_hint}", flush=True)

    loop = asyncio.get_running_loop()

    # ── 流水线并行：后台启动检索，同时向前端推 waiting 逐字动画 ──
    retrieval_future = loop.run_in_executor(
        _stream_executor,
        lambda: rag_service.query_stream(
            search_q,
            filter=filter,
            chat_history=chat_history[-6:],
            skip_query_expansion=skip_query_expansion,
            topic_switch_hint=topic_hint,
        ),
    )

    # 逐字推送 waiting 消息，直到检索完成
    waiting_text = "正在为您查找相关信息..."
    for i, ch in enumerate(waiting_text):
        yield f"data: {json.dumps({'type': 'waiting', 'content': ch, 'index': i}, ensure_ascii=False)}\n\n"
        if retrieval_future.done():
            break
        await asyncio.sleep(0.03)  # ~30ms/字，整句话约0.3s推完

    try:
        stream_result = await retrieval_future

        stream_gen = stream_result.get("stream")
        if stream_gen is None:
            yield f"data: {json.dumps({'type': 'error', 'content': '流式生成器为空'}, ensure_ascii=False)}\n\n"
            return

        # 清除 waiting，准备输出答案
        yield f"data: {json.dumps({'type': 'clear_waiting'}, ensure_ascii=False)}\n\n"

        # ── P0: 真正流式 ──
        # 字符级状态机：扫描 "answer_text":"  → 逐字推 chunk → 遇到闭合 " 停
        chunk_queue: Queue = Queue()
        _MARKER = 'answer_text'
        t_llm_start = time.time()

        def _json_depth_closed(text: str) -> bool:
            """检查 text 中第一个 JSON 对象的 {} 是否完整闭合"""
            start = text.find('{')
            if start == -1:
                return False
            depth = 0
            for ch in text[start:]:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return True
            return False

        def _stream_worker():
            full_text = ""
            state = 0          # 0=找key,1=收集key,2=等冒号,3=等值引号,4=在值中
            key_buf = ""
            val_buf = ""
            esc = False
            try:
                for token in stream_gen:
                    full_text += token
                    for ch in token:
                        if state == 4:  # 在 answer_text 值内部
                            if esc:
                                val_buf += {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}.get(ch, ch)
                                esc = False
                            elif ch == '\\':
                                esc = True
                            elif ch == '"':
                                # answer_text 值结束
                                state = 0
                                if val_buf:
                                    chunk_queue.put(("chunk", val_buf))
                            else:
                                val_buf += ch
                                chunk_queue.put(("chunk", val_buf))
                            continue

                        if state == 0:  # 找下一个 '"'
                            if ch == '"':
                                state = 1
                                key_buf = ""
                            continue

                        if state == 1:  # 收集 key
                            if ch == '"':
                                if key_buf == _MARKER:
                                    state = 2
                                else:
                                    state = 0
                            else:
                                key_buf += ch
                            continue

                        if state == 2:  # 等冒号
                            if ch == ':':
                                state = 3
                            elif ch not in (' ', '\n', '\r', '\t'):
                                state = 0
                            continue

                        if state == 3:  # 等值的起始引号
                            if ch == '"':
                                state = 4
                                val_buf = ""
                                esc = False
                            elif ch not in (' ', '\n', '\r', '\t'):
                                state = 0
                            continue

                    # JSON 完整闭合 → done
                    if _json_depth_closed(full_text):
                        chunk_queue.put(("done", full_text))
                        return

                # 流结束
                chunk_queue.put(("done", full_text))
            except Exception as e:
                chunk_queue.put(("error", str(e)))

        loop.run_in_executor(_stream_executor, _stream_worker)

        # 异步读队列，yield chunk 事件
        full_text = ""
        answer_text = ""
        while True:
            def _dequeue():
                try:
                    return chunk_queue.get(timeout=0.05)
                except Empty:
                    return None
            item = await loop.run_in_executor(_stream_executor, _dequeue)
            if item is None:
                await asyncio.sleep(0.01)
                continue
            kind, payload = item

            if kind == "chunk":
                answer_text = payload
                yield f"data: {json.dumps({'type': 'chunk', 'content': payload}, ensure_ascii=False)}\n\n"
            elif kind == "done":
                full_text = payload
                break
            elif kind == "error":
                yield f"data: {json.dumps({'type': 'error', 'content': f'流式解析失败: {payload}'}, ensure_ascii=False)}\n\n"
                return

        llm_time = time.time() - t_llm_start
        rag_time = time.time() - t0
        # 提取 RAG 内部分段耗时
        rag_breakdown = stream_result.get("_timing", {}) if stream_result else {}
        if rag_breakdown:
            _b = rag_breakdown
            print(f"[RAG ⏱] 分段耗时: intent={_b.get('intent_filter_ms','?')}ms expand={_b.get('query_expansion_ms','?')}ms"
                  f" retrieval={_b.get('retrieval_ms','?')}ms({_b.get('retrieved_docs_n','?')}docs)"
                  f" rerank={_b.get('rerank_ms','?')}ms build={_b.get('build_context_ms','?')}ms"
                  f" prompt={_b.get('prompt_ms','?')}ms({_b.get('prompt_chars','?')}字)"
                  f" pre_llm={_b.get('pre_llm_total_ms','?')}ms", flush=True)
        print(f"[RAG] 检索+生成耗时: {rag_time:.2f}s | LLM流耗时: {llm_time:.2f}s | answer_text: {len(answer_text)}字 | full_text: {len(full_text)}字", flush=True)

        # 解析 JSON 获取 recommendations + voice_friendly
        parsed = _parse_stream_structured(full_text)
        voice_friendly = parsed.get("voice_friendly", "")
        recommendations = parsed.get("recommendations", [])
        if not isinstance(recommendations, list):
            recommendations = []

        # 如果流式没提取到 answer_text，用 JSON 解析结果
        if not answer_text:
            answer_text = parsed.get("answer_text", full_text)

        if not answer_text or answer_text.strip().startswith("{") and len(answer_text.strip()) < 100:
            answer_text = "抱歉，当前知识库中没有找到与您问题匹配的商品信息，请尝试更换关键词或扩大搜索范围。"

        chat_history.append({"role": "user", "content": question})
        chat_history.append({"role": "assistant", "content": answer_text[:500]})
        if len(chat_history) > 12:
            chat_history[:] = chat_history[-12:]

        memory.remember(session_id, question, full_text, recommendations)
        stored_pids = [r.get("product_id", "") for r in (recommendations or []) if r.get("product_id")]
        if stored_pids:
            print(f"[Memory] RAG 记住推荐: session={session_id[:16]} pids={stored_pids}")
        else:
            # LLM 产出的 recommendations 中 product_id 经常为空，
            # 尝试用图谱补全再记一次，避免后续 cart 操作找不到商品
            enriched_for_memory = _enrich_recommendations(recommendations, ecommerce_graph) if ecommerce_graph else recommendations
            enriched_pids = [r.get("product_id", "") for r in enriched_for_memory if r.get("product_id")]
            if enriched_pids:
                memory.remember(session_id, question, full_text, enriched_for_memory)
                print(f"[Memory] RAG 记住推荐(图谱补全): session={session_id[:16]} pids={enriched_pids}")

        yield f"data: {json.dumps({'type': 'session', 'sid': session_id}, ensure_ascii=False)}\n\n"

        enriched_recs: list = []
        if recommendations:
            enriched_recs = _enrich_recommendations(recommendations, ecommerce_graph)
            print(f"[RAG] 推送 cards: {len(enriched_recs)} 条", flush=True)
        else:
            print(f"[RAG] 无 recommendations: full_text 长度={len(full_text)} 首200字={full_text[:200]}", flush=True)
        yield f"data: {json.dumps({'type': 'cards', 'cards': enriched_recs}, ensure_ascii=False)}\n\n"

        tts_url = ""
        if enable_tts and voice_friendly and tts_service is not None:
            try:
                def _do_tts():
                    return tts_service.synthesize(
                        voice_friendly, output_filename=f"tts_{uuid.uuid4().hex}.mp3"
                    )
                audio_path = await loop.run_in_executor(_stream_executor, _do_tts)
                if audio_path:
                    tts_url = f"/voice/playback/{os.path.basename(audio_path)}"
                else:
                    print("[TTS] 合成返回空路径", flush=True)
            except Exception as e:
                print(f"[TTS] 合成失败: {e}", flush=True)

        yield f"data: {json.dumps({'type': 'voice', 'url': tts_url, 'text': voice_friendly}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'timing': {'rag_time': round(rag_time, 3), 'total': round(time.time() - t0, 3), 'breakdown': rag_breakdown}}, ensure_ascii=False)}\n\n"

        # ── 回写 Go 网关 MySQL，持久化会话消息 ──
        if session_id and session_id != "default":
            cards_json = json.dumps(enriched_recs, ensure_ascii=False) if enriched_recs else ""
            _save_session_message(session_id, "user", question)
            _save_session_message(session_id, "assistant", answer_text, cards=cards_json, voice_url=tts_url)

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield f"data: {json.dumps({'type': 'error', 'content': f'流式查询失败: {str(e)}'}, ensure_ascii=False)}\n\n"
