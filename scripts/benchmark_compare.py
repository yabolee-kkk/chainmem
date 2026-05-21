"""ChainMem 嵌入模型对比基准测试"""
import tempfile, os, sys, time
from chainmem import ChainMemory

# ── 测试数据 ──
test_texts = [
    ("记忆系统", "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条这样只要想起开头几个字就能顺着把后面的内容推导出来。"),
    ("股决", "关于股决项目我觉得应该先做好最薄弱的一环然后让朋友内测反馈再扩从不用登录墙开始。"),
    ("博客", "AI风向标博客已经完成全栈升级用Express加SQLite做后端管理面板用React前端用Astro加TailwindCSS。"),
    ("股票", "每日股票分析系统支持A股港股美股自动生成分析报告并推送到企业微信飞书Telegram等渠道。"),
    ("部署", "部署铁律所有前端后端改动现在本地生产服务器上改好让用户确认用户说上了才能推云端。"),
]

queries = [
    ("其实我的想法", "记忆系统", "精确前缀"),
    ("我的想法", "记忆系统", "短语级模糊"),
    ("推导出来", "记忆系统", "尾部内容"),
    ("记忆", "记忆系统", "单关键词"),
    ("把每一次的记忆", "记忆系统", "中间短语"),
    ("关于股决", "股决", "精确前缀"),
    ("股决项目", "股决", "完整短语"),
    ("从不用登录墙开始", "股决", "尾部内容"),
    ("内测反馈", "股决", "内部短语"),
    ("AI风向标", "博客", "精确前缀"),
    ("博客", "博客", "单关键词"),
    ("Astro", "博客", "技术栈关键词"),
    ("A股", "股票", "关键词"),
    ("自动生成分析报告", "股票", "功能短语"),
    ("企业微信", "股票", "渠道名"),
    ("部署铁律", "部署", "精确前缀"),
    ("本地生产服务器", "部署", "内部短语"),
    ("推云端", "部署", "尾部短语"),
    ("1234567890", None, "完全不匹配"),
    ("xyz xyz", None, "噪音查询"),
]


def run_benchmark(model_name: str) -> dict:
    """用指定模型跑一遍基准测试，返回统计"""
    db = tempfile.mktemp(suffix=".db")
    cm = ChainMemory(db_path=db).open()
    
    # 设置为指定模型 (先设模型再结链，确保所有嵌入用同一模型)
    cm.set_model(model_name)
    
    t_start = time.time()
    for topic, text in test_texts:
        cm.ingest(text, source=topic, tags=[topic])
    
    cm.retriever.rebuild_index()
    t_ingest = time.time() - t_start
    
    # 追溯测试
    correct = 0
    details = []
    t_query_start = time.time()
    for query, expected_topic, scenario in queries:
        retrieved_texts = cm.retrieve(query)
        
        if not retrieved_texts:
            matched_topic = None
            match_ok = expected_topic is None
        else:
            reconstructed = "".join(retrieved_texts)
            matched_topic = None
            for topic, original_text in test_texts:
                if original_text in reconstructed or reconstructed in original_text:
                    matched_topic = topic
                    break
                overlap = len(set(reconstructed) & set(original_text)) / max(len(set(reconstructed)), 1)
                if overlap > 0.5 and len(reconstructed) > 5:
                    matched_topic = topic
                    break
            if matched_topic is None and retrieved_texts:
                for topic, original_text in test_texts:
                    if any(t in original_text for t in retrieved_texts[:1]):
                        matched_topic = topic
                        break
            match_ok = matched_topic == expected_topic
        
        if match_ok:
            correct += 1
        details.append((query, expected_topic, matched_topic, match_ok, scenario))
    
    t_query = time.time() - t_query_start
    total = len(queries)
    
    # 纯语义（不包含子串匹配）的准确率
    correct_semantic = 0
    for query, expected_topic, scenario in queries:
        # 用纯FAISS搜索（不走子串加分路径）
        q_vec = cm.retriever.embedder.encode([query], normalize_embeddings=True).astype('float32')
        scores, indices = cm.retriever.index.search(q_vec, 1)
        best_score = float(scores[0][0])
        if best_score < 0.4:
            matched = None
        else:
            node_id = cm.retriever.id_list[int(indices[0][0])]
            text = cm.retriever.id_to_text[node_id]
            matched = None
            for topic, original_text in test_texts:
                if text in original_text or original_text in text:
                    matched = topic
                    break
        if matched == expected_topic or (expected_topic is None and matched is None):
            correct_semantic += 1
        elif expected_topic is None and matched is not None and best_score < 0.5:
            # 噪声查询如果不是子串命中则算对（子串兜底不算语义）
            raw_query = query
            is_substring_hit = any(raw_query in txt for txt in cm.retriever.id_to_text.values())
            if not is_substring_hit:
                correct_semantic += 1
    
    cm.close()
    os.unlink(db)
    
    return {
        "model": model_name,
        "total": total,
        "correct": correct,
        "accuracy": correct / total * 100,
        "ingest_time": round(t_ingest, 2),
        "query_time": round(t_query / len(queries), 4),
        "details": details,
    }


# ── 跑多个模型 ──
models = [
    "all-MiniLM-L6-v2",          # 当前（小、快、一般中文）
    "intfloat/multilingual-e5-small",  # 微软多语言小模型（470MB）
]

results = []
for model_name in models:
    print(f"⏳ 加载模型 [{model_name}]...")
    try:
        r = run_benchmark(model_name)
        results.append(r)
        print(f"  完成: {r['accuracy']:.1f}% ({r['correct']}/{r['total']})")
    except Exception as e:
        print(f"  ❌ 失败: {e}")

# ── 输出对比 ──
print(f"\n{'='*65}")
print(f"  嵌入模型对比基准测试")
print(f"{'='*65}")

for r in results:
    print(f"\n  📊 [{r['model']}]")
    print(f"     准确率: {r['accuracy']:.1f}% ({r['correct']}/{r['total']})")
    print(f"     结链耗时: {r['ingest_time']}s")
    print(f"     平均查询: {r['query_time']}s/次")
    
    # 展示失败
    failures = [d for d in r['details'] if not d[3]]
    if failures:
        print(f"     失败 ({len(failures)}):")
        for q, exp, act, ok, sc in failures:
            print(f"       ❌ {q:<20} 期望={exp or '无':<6} 实际={act or '无':<6} ({sc})")

if len(results) >= 2:
    print(f"\n{'='*65}")
    old = results[0]
    new = results[1]
    diff = new['accuracy'] - old['accuracy']
    sign = "+" if diff >= 0 else ""
    print(f"  📈 提升: {old['accuracy']:.1f}% → {new['accuracy']:.1f}% ({sign}{diff:.1f}%)")
    print(f"{'='*65}")

# 详细结果
for r in results:
    print(f"\n── [{r['model']}] 逐项 ──")
    for q, exp, act, ok, sc in r['details']:
        status = "✅" if ok else "❌"
        exp_s = exp or "无"
        act_s = act or "无"
        print(f"  {status} {q:<24} {exp_s:<8} {act_s:<8} ({sc})")
