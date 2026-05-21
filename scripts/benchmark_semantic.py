"""纯语义对比（去掉子串匹配加分，只看 FAISS 能做到多少）"""
import tempfile, os, time, numpy as np
from chainmem import ChainMemory
from chainmem.pipeline.ingester import set_model, _get_model

test_texts = [
    ("记忆系统", "其实我的想法是把每一次的记忆包括一次对话全部变成一个链条这样只要想起开头几个字就能顺着把后面的内容推导出来。"),
    ("股决", "关于股决项目我觉得应该先做好最薄弱的一环然后让朋友内测反馈再扩从不用登录墙开始。"),
    ("博客", "AI风向标博客已经完成全栈升级用Express加SQLite做后端管理面板用React前端用Astro加TailwindCSS。"),
    ("股票", "每日股票分析系统支持A股港股美股自动生成分析报告并推送到企业微信飞书Telegram等渠道。"),
    ("部署", "部署铁律所有前端后端改动现在本地生产服务器上改好让用户确认用户说上了才能推云端。"),
]

queries = [
    ("其实我的想法", "记忆系统"),
    ("我的想法", "记忆系统"),
    ("推导出来", "记忆系统"),
    ("记忆", "记忆系统"),
    ("把每一次的记忆", "记忆系统"),
    ("关于股决", "股决"),
    ("股决项目", "股决"),
    ("从不用登录墙开始", "股决"),
    ("内测反馈", "股决"),
    ("AI风向标", "博客"),
    ("博客", "博客"),
    ("Astro", "博客"),
    ("A股", "股票"),
    ("自动生成分析报告", "股票"),
    ("企业微信", "股票"),
    ("部署铁律", "部署"),
    ("本地生产服务器", "部署"),
    ("推云端", "部署"),
    ("1234567890", None),
    ("xyz xyz", None),
]


def semantic_only(model_name: str) -> dict:
    """纯 FAISS 语义检索（不包含任何子串加分/兜底）"""
    set_model(model_name)
    embedder = _get_model()
    
    # 1. 嵌入所有节点
    all_texts = []
    all_chain_ids = []
    for topic, text in test_texts:
        from chainmem.pipeline.ingester import chunk_text, merge_short_chunks
        chunks = merge_short_chunks(chunk_text(text))
        for chunk in chunks:
            all_texts.append((topic, chunk))
    
    node_texts = [t for _, t in all_texts]
    chain_labels = [l for l, _ in all_texts]
    
    t0 = time.time()
    embeddings = embedder.encode(node_texts, normalize_embeddings=True)
    t_emb = time.time() - t0
    
    import faiss
    dim = embeddings.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embeddings.astype(np.float32))
    
    # 3. 查询
    correct = 0
    total = len(queries)
    wrongs = []
    
    for query, expected in queries:
        q_vec = embedder.encode([query], normalize_embeddings=True).astype(np.float32)
        scores, idx = index.search(q_vec, 1)
        best_score = float(scores[0][0])
        
        if best_score < 0.4:
            matched = None
        else:
            matched = chain_labels[int(idx[0][0])]
        
        ok = (matched == expected) or (expected is None and matched is None)
        if ok:
            correct += 1
        else:
            wrongs.append((query, expected, matched, best_score))
    
    return {
        "model": model_name,
        "accuracy": correct / total * 100,
        "correct": correct,
        "total": total,
        "embed_time": round(t_emb, 2),
        "dim": dim,
        "wrongs": wrongs,
    }


# 跑对比
models = [
    "all-MiniLM-L6-v2",
    "intfloat/multilingual-e5-small",  
]

results = []
for m in models:
    print(f"⏳ {m}...")
    try:
        r = semantic_only(m)
        results.append(r)
        print(f"  ✅ 准确率: {r['accuracy']:.1f}% ({r['correct']}/{r['total']})  维度: {r['dim']}")
    except Exception as e:
        print(f"  ❌ {e}")

# 输出
print(f"\n{'='*60}")
print(f"  纯语义准确率对比（无子串兜底）")
print(f"{'='*60}")
for r in results:
    print(f"\n  🔍 [{r['model']}]")
    print(f"     准确率: {r['accuracy']:.1f}% ({r['correct']}/{r['total']})")
    print(f"     维度: {r['dim']}  嵌入耗时: {r['embed_time']}s")
    if r['wrongs']:
        print(f"     失败:")
        for q, exp, act, s in r['wrongs']:
            print(f"       ❌ \"{q}\"  score={s:.4f}  期望={exp}  实际={act}")

if len(results) >= 2:
    print(f"\n{'─'*60}")
    for r in results:
        print(f"  {r['model']:<42} {r['accuracy']:5.1f}%  ({r['dim']}d)")
    print(f"{'─'*60}")
