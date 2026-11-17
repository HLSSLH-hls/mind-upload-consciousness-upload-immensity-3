# -*- coding: utf-8 -*-
"""
苍穹3号 · 访谈后端
- Flask Blueprint，挂载到主后端
- 字段级状态机：divergent_asking → divergent_captured → convergent_asking → filled/blank/skipped
- 每轮一次 LLM 调用，只做单次决策
- 没配 API key 时自动 mock 模式
"""

import os, json, uuid, threading, time
from pathlib import Path
from flask import Blueprint, request, jsonify
from dotenv import load_dotenv

# .env 显式从本文件所在目录加载（放 onboarding/.env）
ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# 相对导入（因为 onboarding/ 是包）
from .interview_schema import (DIMENSIONS, get_field, get_dimension, all_fields_in_order)

# =========================================================
# 配置
# =========================================================
API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
MOCK = not bool(API_KEY)

client = None
if not MOCK:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    except Exception as e:
        print(f"[interview] OpenAI 客户端初始化失败: {e}，降级为 mock 模式")
        MOCK = True

print(f"[interview] 模式: {'MOCK' if MOCK else 'DEEPSEEK'}，模型: {MODEL}")

# =========================================================
# Session 存储（内存字典，演示级）
# =========================================================
SESSIONS = {}
LOCK = threading.Lock()

# =========================================================
# 状态常量
# =========================================================
S_PENDING = "pending"
S_DIV_ASK = "divergent_asking"
S_DIV_CAP = "divergent_captured"
S_CONV_ASK = "convergent_asking"
S_FILLED = "filled"
S_BLANK = "blank"
S_SKIPPED = "skipped"


# =========================================================
# Session 初始化
# =========================================================
def _init_session(entry="interview"):
    sid = "s_" + uuid.uuid4().hex[:12]
    fields = {}
    for f in all_fields_in_order():
        fields[f["id"]] = {
            "field_id": f["id"],
            "dimension_id": f["dimension_id"],
            "dimension_label": f["dimension_label"],
            "label": f["label"],
            "description": f["description"],
            "divergent_question": f["divergent_question"],
            "max_attempts": f["max_attempts"],
            "depends_on": f["depends_on"],
            "value_type": f["value_type"],
            "status": S_PENDING,
            "attempts": 0,
            "divergent": {
                "rawNarrative": "",
                "candidateSignals": []
            },
            "convergent": {
                "attempts": [],
                "finalValue": None
            },
            "history": []
        }
    session = {
        "session_id": sid,
        "entry": entry,
        "created_at": time.time(),
        "fields": fields,
        "field_order": [f["id"] for f in all_fields_in_order()],
        "current_field_id": None,
        "transcript": []
    }
    return session


def _next_pending_field(session):
    """按顺序找下一个 pending 字段，跳过依赖未满足的"""
    for fid in session["field_order"]:
        f = session["fields"][fid]
        if f["status"] != S_PENDING:
            continue
        # 依赖检查
        dep = f.get("depends_on")
        if dep:
            dep_field = session["fields"].get(dep)
            if not dep_field or dep_field["status"] != S_FILLED:
                # 前置没填上，本字段直接 blank
                f["status"] = S_BLANK
                f["convergent"]["finalValue"] = None
                continue
        return f
    return None


def _advance(session):
    """当前字段结束后，推进到下一个。返回下一个问题或完成标记"""
    next_field = _next_pending_field(session)
    if not next_field:
        return {"type": "all_complete"}

    session["current_field_id"] = next_field["field_id"]
    next_field["status"] = S_DIV_ASK
    q = next_field["divergent_question"]
    next_field["history"].append({"role": "system", "text": q, "ts": time.time()})
    session["transcript"].append({"role": "system", "text": q, "field": next_field["field_id"], "ts": time.time()})
    return {
        "type": "next_field",
        "dimension": next_field["dimension_id"],
        "dimension_label": next_field["dimension_label"],
        "question": q,
    }


# =========================================================
# 护栏：证据校验
# =========================================================
def _evidence_valid(evidence, field):
    if not evidence or not isinstance(evidence, str):
        return False
    sources = [field["divergent"]["rawNarrative"]]
    for h in field["history"]:
        if h.get("role") == "user":
            sources.append(h.get("text", ""))
    return any(evidence in s for s in sources if s)


# =========================================================
# LLM 调用（带重试和降级）
# =========================================================
def _call_llm_json(messages, retry=1):
    if MOCK:
        raise RuntimeError("MOCK")
    last_err = None
    for i in range(retry + 1):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.25,
                timeout=20,
            )
            content = resp.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise last_err


# =========================================================
# Prompt A：泛化标注
# =========================================================
PROMPT_A_SYSTEM = """你是访谈记录员。用户对一个泛化问题做了回答。
你的任务不是评判、不是追问、不是结构化。只做一件事：从回答中标注出所有可能相关的候选信号。
规则：
- 不要遗漏用户提到的具体人、事、物、场景、感受；
- 不要归纳、不要替用户总结、不要补全用户没说的；
- 每个信号给 type（relationship / accomplishment / sensory / meaning / engagement / loss / frustration / loneliness / shame / anxiety / anger / helplessness / invalidation / other）和 confidence（0~1）；
- 如果用户回答完全没有可标注内容（如"不知道"），返回空数组。
输出 JSON 对象，格式：{"candidateSignals": [{"signal": "", "type": "", "confidence": 0.0}]}"""


def _annotate_divergent(field, user_answer):
    if MOCK:
        # mock：按空格和标点粗分
        import re
        parts = [p.strip() for p in re.split(r"[，。、；\s]+", user_answer) if p.strip()]
        return [{"signal": p, "type": "other", "confidence": 0.5} for p in parts[:8]]

    messages = [
        {"role": "system", "content": PROMPT_A_SYSTEM},
        {"role": "user", "content": json.dumps({
            "field_label": field["label"],
            "field_description": field["description"],
            "divergent_question": field["divergent_question"],
            "user_answer": user_answer,
        }, ensure_ascii=False)}
    ]
    try:
        data = _call_llm_json(messages)
        signals = data.get("candidateSignals", [])
        if not isinstance(signals, list):
            signals = []
        return signals
    except Exception as e:
        print(f"[interview] 泛化标注失败: {e}")
        return []


# =========================================================
# Prompt B：收敛提取
# =========================================================
PROMPT_B_SYSTEM = """你是访谈收敛器。当前字段已有泛化材料，用户刚刚对收敛问题做了回答。
你的任务是从用户回答和泛化材料中提取结构化值，或生成追问。

硬约束：
- accepted 时，value 必须是用户回答或泛化材料中真实出现的内容，evidence 填对应的原文片段；
- follow_up 时，追问必须基于 raw 中已出现的信号，不能引入用户没提过的新方向；
- 每次追问只聚焦一个点；
- 如果用户明确说"不知道 / 不想说 / 跳过 / 说不清"，返回 skip；
- 如果用户反复给不出可接受的值，返回 give_up。

输出 JSON 对象，格式：
{
  "decision": "accepted | follow_up | skip | give_up",
  "value": "结构化值，accepted 时填，字符串或数组",
  "evidence": "原文片段，accepted 时填",
  "follow_up_question": "追问问题，follow_up 时填",
  "reason": "简短理由"
}"""


def _converge(field, user_answer):
    if MOCK:
        # mock：第一次尝试直接接受；第二次之后给 up
        if len(field["convergent"]["attempts"]) == 0:
            return {
                "decision": "accepted",
                "value": user_answer,
                "evidence": user_answer,
                "reason": "mock: 首次收敛直接接受"
            }
        return {
            "decision": "give_up",
            "reason": "mock: 二次收敛放弃"
        }

    messages = [
        {"role": "system", "content": PROMPT_B_SYSTEM},
        {"role": "user", "content": json.dumps({
            "field_label": field["label"],
            "field_description": field["description"],
            "raw_narrative": field["divergent"]["rawNarrative"],
            "candidate_signals": field["divergent"]["candidateSignals"],
            "previous_attempts": field["convergent"]["attempts"],
            "latest_user_answer": user_answer,
        }, ensure_ascii=False)}
    ]
    try:
        return _call_llm_json(messages)
    except Exception as e:
        print(f"[interview] 收敛调用失败: {e}")
        return {"decision": "give_up", "reason": f"LLM 调用失败: {e}"}


# =========================================================
# 第一个收敛问题（模板拼接，不调 LLM，省一次调用）
# =========================================================
def _build_first_convergence(field):
    signals = field["divergent"]["candidateSignals"]
    if not signals:
        return f"关于「{field['label']}」，你能再具体说一点点吗？如果实在说不清，也没关系。"
    names = [s.get("signal", "") for s in signals if s.get("signal")][:4]
    if not names:
        return f"关于「{field['label']}」，你能再具体说一点点吗？"
    joined = "、".join(f"「{n}」" for n in names)
    return f"你提到了{joined}。如果要把「{field['label']}」整理清楚，你会保留哪些？可以补充，也可以说'说不清'。"


# =========================================================
# 核心：处理用户回答
# =========================================================
def _handle_answer(session, text):
    fid = session["current_field_id"]
    if not fid:
        return {"type": "error", "message": "没有进行中的字段"}
    field = session["fields"][fid]

    # 记录用户回答
    field["history"].append({"role": "user", "text": text, "ts": time.time()})
    session["transcript"].append({"role": "user", "text": text, "field": fid, "ts": time.time()})

    # 阶段 1：泛化回答
    if field["status"] == S_DIV_ASK:
        field["divergent"]["rawNarrative"] = text
        signals = _annotate_divergent(field, text)
        field["divergent"]["candidateSignals"] = signals
        field["status"] = S_DIV_CAP

        # 立即生成第一个收敛问题
        q = _build_first_convergence(field)
        field["status"] = S_CONV_ASK
        field["history"].append({"role": "system", "text": q, "ts": time.time()})
        session["transcript"].append({"role": "system", "text": q, "field": fid, "ts": time.time()})
        return {"type": "follow_up", "question": q}

    # 阶段 2：收敛回答
    if field["status"] == S_CONV_ASK:
        last_q = ""
        for h in reversed(field["history"]):
            if h["role"] == "system":
                last_q = h["text"]
                break

        result = _converge(field, text)
        decision = result.get("decision", "give_up")
        attempts = len(field["convergent"]["attempts"])

        # 记录本次尝试
        field["convergent"]["attempts"].append({
            "question": last_q,
            "answer": text,
            "decision": decision,
            "value": result.get("value"),
            "reason": result.get("reason", ""),
            "ts": time.time(),
        })

        # accepted：校验证据
        if decision == "accepted":
            value = result.get("value")
            evidence = result.get("evidence", "")
            if value and _evidence_valid(evidence, field):
                field["convergent"]["finalValue"] = value
                field["status"] = S_FILLED
                return _advance(session)
            # 证据不合法，降级为追问
            decision = "follow_up"

        # skip
        if decision == "skip":
            field["status"] = S_SKIPPED
            field["convergent"]["finalValue"] = None
            return _advance(session)

        # give_up 或超过最大追问次数
        if decision == "give_up" or attempts >= field["max_attempts"] - 1:
            field["status"] = S_BLANK
            field["convergent"]["finalValue"] = None
            return _advance(session)

        # follow_up
        q = result.get("follow_up_question") or f"关于「{field['label']}」，能再具体一点吗？"
        field["attempts"] += 1
        field["history"].append({"role": "system", "text": q, "ts": time.time()})
        session["transcript"].append({"role": "system", "text": q, "field": fid, "ts": time.time()})
        return {"type": "follow_up", "question": q}

    # 字段已终止，推进
    return _advance(session)


# =========================================================
# Finalize：生成 raw + derived
# =========================================================
def _finalize(session):
    raw_fields = []
    derived = {
        "behaviorSpace": [],
        "happinessHabits": [],
        "sadnessHabits": [],
        "behaviorHabits": [],
        "autobiographicalMemory": [],
        "dictionary": [],
        "stateTransition": [],
        "wordTransition": [],
        "blankFields": [],
    }

    for fid in session["field_order"]:
        f = session["fields"][fid]
        raw_fields.append({
            "field": fid,
            "dimension": f["dimension_id"],
            "label": f["label"],
            "status": f["status"],
            "divergent": f["divergent"],
            "convergent": f["convergent"],
        })

        if f["status"] != S_FILLED:
            derived["blankFields"].append({
                "field": fid,
                "label": f["label"],
                "reason": "用户多轮未给出可接受的值" if f["status"] == S_BLANK else "用户跳过",
                "rawAvailable": bool(f["divergent"]["rawNarrative"]),
            })
            continue

        v = f["convergent"]["finalValue"]
        # 按字段映射到 derived
        if fid == "behavior_space":
            items = v if isinstance(v, list) else [v]
            derived["behaviorSpace"] = [{"action": str(x)} for x in items]
        elif fid == "happiness_source":
            items = v if isinstance(v, list) else [v]
            derived["happinessHabits"] = [{"perceptions": [str(x)], "intensity": None} for x in items]
        elif fid == "sadness_source":
            items = v if isinstance(v, list) else [v]
            derived["sadnessHabits"] = [{"perceptions": [str(x)], "intensity": None} for x in items]
        elif fid == "behavior_habit":
            items = v if isinstance(v, list) else [v]
            derived["behaviorHabits"] = [{"perceptions": [str(x)], "actions": [], "value": None} for x in items]
        elif fid == "memory_fragment":
            items = v if isinstance(v, list) else [v]
            derived["autobiographicalMemory"] = [{"perceptions": [str(x)], "actions": [], "value": None} for x in items]
        elif fid == "dictionary":
            items = v if isinstance(v, list) else [v]
            derived["dictionary"] = [{"word": str(x), "meaning": "", "evidence": ""} for x in items]

    raw = {
        "schemaVersion": "1.0.0",
        "versionTag": "u_" + session["session_id"][2:],
        "entry": session["entry"],
        "fields": raw_fields,
        "transcript": session["transcript"],
    }
    derived["schemaVersion"] = "1.0.0"
    derived["sourceRawVersion"] = "1.0.0"
    derived["versionTag"] = raw["versionTag"]
    return raw, derived


# =========================================================
# Blueprint 路由
# =========================================================
interview_bp = Blueprint("interview", __name__, url_prefix="/api/interview")


@interview_bp.route("/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    entry = data.get("entry", "interview")
    session = _init_session(entry)
    with LOCK:
        SESSIONS[session["session_id"]] = session

    nxt = _advance(session)
    if nxt.get("type") == "all_complete":
        return jsonify({"sessionId": session["session_id"], "type": "all_complete"})

    return jsonify({
        "sessionId": session["session_id"],
        "type": nxt["type"],
        "dimension": nxt.get("dimension"),
        "dimensionLabel": nxt.get("dimension_label"),
        "question": nxt.get("question"),
        "progress": _progress(session),
    })


@interview_bp.route("/answer", methods=["POST"])
def api_answer():
    data = request.get_json(silent=True) or {}
    sid = data.get("sessionId")
    text = (data.get("text") or "").strip()
    if not sid or not text:
        return jsonify({"error": "sessionId 和 text 必填"}), 400
    with LOCK:
        session = SESSIONS.get(sid)
    if not session:
        return jsonify({"error": "session 不存在或已过期"}), 404

    result = _handle_answer(session, text)
    result["progress"] = _progress(session)
    return jsonify(result)


@interview_bp.route("/skip", methods=["POST"])
def api_skip():
    data = request.get_json(silent=True) or {}
    sid = data.get("sessionId")
    if not sid:
        return jsonify({"error": "sessionId 必填"}), 400
    with LOCK:
        session = SESSIONS.get(sid)
    if not session:
        return jsonify({"error": "session 不存在"}), 404
    fid = session["current_field_id"]
    if fid:
        session["fields"][fid]["status"] = S_SKIPPED
        session["fields"][fid]["convergent"]["finalValue"] = None
    result = _advance(session)
    result["progress"] = _progress(session)
    return jsonify(result)


@interview_bp.route("/finalize", methods=["POST"])
def api_finalize():
    data = request.get_json(silent=True) or {}
    sid = data.get("sessionId")
    if not sid:
        return jsonify({"error": "sessionId 必填"}), 400
    with LOCK:
        session = SESSIONS.get(sid)
    if not session:
        return jsonify({"error": "session 不存在"}), 404

    raw, derived = _finalize(session)
    return jsonify({"raw": raw, "derived": derived})


@interview_bp.route("/state", methods=["GET"])
def api_state():
    """调试用：返回当前 session 的内部状态（前端不用）"""
    sid = request.args.get("sessionId")
    with LOCK:
        session = SESSIONS.get(sid)
    if not session:
        return jsonify({"error": "session 不存在"}), 404
    return jsonify({
        "sessionId": sid,
        "currentFieldId": session["current_field_id"],
        "fields": {
            fid: {
                "status": f["status"],
                "attempts": f["attempts"],
                "hasRaw": bool(f["divergent"]["rawNarrative"]),
            }
            for fid, f in session["fields"].items()
        }
    })

@interview_bp.route("/profile/save", methods=["POST"])
def api_profile_save():
    """
    把 raw + derived 保存到项目根目录 versionData/ 下。
    文件名：profile_{versionTag}.json
    """
    data = request.get_json(silent=True) or {}
    version_tag = (data.get("versionTag") or "").strip()
    raw = data.get("raw")
    derived = data.get("derived")
    if not version_tag or not raw or not derived:
        return jsonify({"ok": False, "error": "versionTag / raw / derived 必填"}), 400

    # interview_backend.py 在 onboarding/ 里，parent.parent = 项目根目录
    save_dir = Path(__file__).resolve().parent.parent / "versionData"
    save_dir.mkdir(exist_ok=True)
    file_path = save_dir / f"profile_{version_tag}.json"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump({
                "versionTag": version_tag,
                "savedAt": time.time(),
                "raw": raw,
                "derived": derived,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True, "versionTag": version_tag, "path": str(file_path)}) 

def _progress(session):
    total = len(session["field_order"])
    done = sum(
        1 for fid in session["field_order"]
        if session["fields"][fid]["status"] in (S_FILLED, S_BLANK, S_SKIPPED)
    )
    dims = []
    for dim in DIMENSIONS:
        dim_fields = [f["id"] for f in dim["fields"]]
        dim_done = all(
            session["fields"][fid]["status"] in (S_FILLED, S_BLANK, S_SKIPPED)
            for fid in dim_fields
        )
        dims.append({"id": dim["id"], "label": dim["label"], "done": dim_done})
    return {"total": total, "done": done, "dimensions": dims}