#!/usr/bin/env python3
"""P1 行李延误批量重跑 — 使用 review.py --forceid（含推送+数据库同步）"""
import json, sys, subprocess, time
from pathlib import Path
from datetime import datetime
from collections import Counter

FORCEID_FILE = "_p1_baggage_forceids.json"
REVIEW_DIR = Path("review_results/baggage_delay")
BATCH_LOG = Path("docs/p1_batch_log.md")

def load_forceids():
    with open(FORCEID_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def classify(remark, is_additional):
    if is_additional == "Y" or "需补齐资料" in remark:
        return "SUPPLEMENT"
    if "拒赔" in remark or "拒绝" in remark:
        return "REJECT"
    if "审核通过" in remark or "赔付" in remark:
        return "PASS"
    return "OTHER"

def run_one(fid):
    start = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "scripts/review.py", "--forceid", fid],
            capture_output=True, text=True, timeout=600, cwd="."
        )
        elapsed = time.time() - start
        rf = REVIEW_DIR / f"{fid}_ai_review.json"
        if rf.exists():
            with open(rf, "r", encoding="utf-8") as f:
                d = json.load(f)
            return {"forceid": fid, "ok": True, "elapsed": elapsed,
                    "remark": d.get("Remark",""), "is_additional": d.get("IsAdditional",""),
                    "conclusions": d.get("KeyConclusions",[]), "debug": d.get("DebugInfo",{})}
        return {"forceid": fid, "ok": False, "elapsed": elapsed, "error": r.stderr[-300:]}
    except Exception as e:
        return {"forceid": fid, "ok": False, "elapsed": time.time()-start, "error": str(e)[:300]}

def main():
    batch_num = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    batch_size = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    forceids = load_forceids()
    si = (batch_num - 1) * batch_size
    ei = si + batch_size
    batch = forceids[si:ei]
    if not batch:
        print(f"批次{batch_num}: 无更多案件 (共{len(forceids)}件)")
        return
    print(f"=== 批次{batch_num}: 第{si+1}-{min(ei,len(forceids))}件 / 共{len(forceids)}件 ===")
    results = []
    for i, fid in enumerate(batch):
        print(f"[{i+1}/{len(batch)}] {fid} ...", end=" ", flush=True)
        r = run_one(fid)
        cat = classify(r.get("remark",""), r.get("is_additional",""))
        print(f"{cat} | {r.get('remark', r.get('error',''))[:100]}")
        results.append(r)
    # Stats
    cats = Counter(classify(r.get("remark",""), r.get("is_additional","")) for r in results)
    print(f"\n统计: PASS={cats.get('PASS',0)} REJECT={cats.get('REJECT',0)} SUPPLEMENT={cats.get('SUPPLEMENT',0)} ERROR={cats.get('ERROR',0)}")
    # Save
    rf = f"_p1_batch_{batch_num:03d}_results.json"
    with open(rf, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    # Log
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    log = f"\n## 批次{batch_num} — {ts}\n\n| 分类 | 数量 |\n|------|------|\n"
    for k in ["PASS","REJECT","SUPPLEMENT","ERROR"]:
        log += f"| {k} | {cats.get(k,0)} |\n"
    log += "\n"
    for cat_name, label in [("PASS","通过"), ("REJECT","拒赔"), ("SUPPLEMENT","仍需补件"), ("ERROR","错误")]:
        items = [r for r in results if classify(r.get("remark",""), r.get("is_additional","")) == cat_name]
        if items:
            log += f"### {label}案件\n\n| forceid | 结论 |\n|---------|------|\n"
            for r in items:
                log += f"| {r['forceid']} | {r.get('remark', r.get('error',''))[:120]} |\n"
            log += "\n"
    log += "---\n"
    with open(BATCH_LOG, "a", encoding="utf-8") as f:
        f.write(log)
    print(f"日志已追加到 {BATCH_LOG}\n结果已保存到 {rf}")

if __name__ == "__main__":
    main()
