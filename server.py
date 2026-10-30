"""
Antigravity Web — Full-Featured Desktop Parity Edition
Supports:
- PC + Ubuntu Hybrid Conversation & Artifact Sync
- Media & File Uploads (Images, PDFs, Code)
- Slash Commands (/plan, /boost, /goal, etc.) & @ Mentions (Files, Conversations, Skills)
- Interactive Question Cards (ask_question) & Plan Proceed Buttons
- Stream Cancellation (Abort) & Battle Mode (Side-by-Side Dual Model Comparison)
- 5-Tab Auxiliary Pane: Artifacts (Mermaid/KaTeX/Carousel/Comments), Subagents Tree, Tasks & Live Terminal, Files Changed (Git & Tool Diffs), Generative UI Widgets
- System Hub: Projects/Workspaces, Skills/Rules/Plugins/MCP Inspector, Scheduled Automations & Services
- Turkish Voice Bridge (Faster-Whisper STT + Antalia-2 Mini TTS)
"""
import asyncio
import base64
import difflib
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

# =====================================================================
# CONFIGURATION (all secrets & host-specific values come from env / .env)
# =====================================================================
def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


BASE_DIR = Path(__file__).parent.resolve()
_load_dotenv(BASE_DIR / ".env")

APP_TITLE = "Antigravity Web Pro"
AGY_BIN = os.path.expanduser(os.getenv("AGY_BIN", "~/.local/bin/agy"))
HOME_DIR = os.path.expanduser("~")

DEFAULT_DATA_DIR = (
    os.path.expanduser("~/.gemini/antigravity-cli")
    if os.path.exists(os.path.expanduser("~/.gemini/antigravity-cli"))
    else os.path.expanduser("~/.gemini/antigravity")
)
ANTIGRAVITY_DATA_DIR = Path(os.getenv("ANTIGRAVITY_DATA_DIR", DEFAULT_DATA_DIR))
UBUNTU_CONFIG_DIR = Path(os.path.expanduser("~/.gemini/config"))

# Runtime data (sync, uploads, settings) lives next to the app unless overridden
APP_DATA_DIR = Path(os.getenv("AGY_WEB_DATA_DIR", str(BASE_DIR)))

PC_SYNC_DIR = Path(os.getenv("PC_SYNC_DIR", str(APP_DATA_DIR / "pc_sync")))
PC_SYNC_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = APP_DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

SETTINGS_FILE = APP_DATA_DIR / "settings.json"
COMMENTS_FILE = APP_DATA_DIR / "artifact_comments.json"

# --- Auth: PIN is mandatory, signing secret is generated & persisted if absent ---
AUTH_PIN = os.getenv("AGY_WEB_PIN", "").strip()
if len(AUTH_PIN) < 6:
    raise RuntimeError(
        "AGY_WEB_PIN must be set (min. 6 characters) in the environment or .env file. "
        "This app can run shell commands on your server — use a strong PIN."
    )


def _load_or_create_secret() -> str:
    env_secret = os.getenv("AGY_WEB_SECRET", "").strip()
    if env_secret:
        return env_secret
    secret_file = APP_DATA_DIR / ".agy_web_secret"
    if secret_file.exists():
        return secret_file.read_text(encoding="utf-8").strip()
    new_secret = secrets.token_hex(32)
    secret_file.write_text(new_secret, encoding="utf-8")
    try:
        os.chmod(secret_file, 0o600)
    except Exception:
        pass
    return new_secret


SECRET_KEY = _load_or_create_secret()
SESSION_DAYS = int(os.getenv("AGY_WEB_SESSION_DAYS", "30"))

# Optional features
VOICE_SERVER_URL = os.getenv("VOICE_SERVER_URL", "").strip()  # e.g. http://127.0.0.1:8000
ENABLE_TERMINAL = os.getenv("AGY_WEB_ENABLE_TERMINAL", "false").lower() == "true"
MONITORED_SERVICES = [s.strip() for s in os.getenv("AGY_WEB_SERVICES", "agy-web,cloudflared").split(",") if s.strip()]

# Brute-force protection for the PIN login
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
_login_failures: dict[str, list[float]] = {}

STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Track running subprocesses for instant cancellation
ACTIVE_PROCESSES: dict[str, asyncio.subprocess.Process] = {}

AVAILABLE_MODELS = [
    {"id": "gemini-4-argon-high", "name": "Gemini 4 Argon (High)", "badge": "Pro / Deep"},
    {"id": "gemini-4-argon-medium", "name": "Gemini 4 Argon (Medium)", "badge": "Balanced"},
    {"id": "gemini-4-argon-low", "name": "Gemini 4 Argon (Low)", "badge": "Fast"},
    {"id": "gemini-3.8-flash-high", "name": "Gemini 3.8 Flash (High)", "badge": "Fast + Smart"},
    {"id": "gemini-3.8-flash-medium", "name": "Gemini 3.8 Flash (Medium)", "badge": "Standard"},
    {"id": "gemini-3.8-flash-low", "name": "Gemini 3.8 Flash (Low)", "badge": "Ultra Fast"},
    {"id": "claude-opus-5-5-high", "name": "Claude Opus 5.5 (High)", "badge": "Anthropic"},
    {"id": "claude-opus-5-5-medium", "name": "Claude Opus 5.5 (Medium)", "badge": "Anthropic"},
    {"id": "claude-sonnet-5-5-high", "name": "Claude Sonnet 5.5 (High)", "badge": "Anthropic"},
    {"id": "claude-sonnet-5-5-medium", "name": "Claude Sonnet 5.5 (Medium)", "badge": "Anthropic"},
    {"id": "gpt-oss-120b-medium", "name": "GPT-OSS 120B (Medium)", "badge": "Open Source"},
]

SLASH_COMMANDS = [
    {"cmd": "/plan", "title": "Adım Adım Planlama", "desc": "Karmaşık görevleri kodlamadan önce detaylı uygulama planına dönüştürür."},
    {"cmd": "/boost", "title": "Derin Düşünme & Doğrulama", "desc": "Çoklu bakış açısı, stratejik planlama ve titiz doğrulama ile derin analiz yapar."},
    {"cmd": "/goal", "title": "Otonom Hedef Modu", "desc": "Hedef tamamen gerçekleşene kadar durmadan uzun süreli otonom çalışır."},
    {"cmd": "/teamwork", "title": "Çoklu Ajan Takımı", "desc": "Büyük projeler için otonom alt ajanlardan oluşan bir ekip kurar."},
    {"cmd": "/grill-me", "title": "Tasarım Mülakatı", "desc": "Tasarım kararlarını netleştirmek için sana adım adım sorular sorar."},
    {"cmd": "/browser", "title": "Web Tarayıcı Otomasyonu", "desc": "Web sitelerinde gezinme, araştırma ve etkileşim görevlerini başlatır."},
    {"cmd": "/schedule", "title": "Zamanlayıcı / Cron Görevi", "desc": "Tek seferlik zamanlayıcı veya periyodik tekrarlayan görev kurar."},
    {"cmd": "/compact", "title": "Bağlamı Sıkıştır", "desc": "Uzayan konuşmalarda geçmişi özetleyerek bağlam penceresini rahatlatır."},
    {"cmd": "/learn", "title": "Kalıcı Yetenek / Kural Öğren", "desc": "Çözülen karmaşık bir kurulumu gelecekteki görevler için kalıcı hale getirir."},
]

# Same-origin app: no CORS middleware on purpose (prevents cross-site API calls).
app = FastAPI(title=APP_TITLE, docs_url=None, redoc_url=None, openapi_url=None)


# =====================================================================
# SETTINGS & AUTHENTICATION
# =====================================================================
def load_settings() -> dict[str, Any]:
    defaults = {
        "permission_mode": "always-proceed",  # always-proceed | sandbox
        "default_model": "gemini-4-argon-high",
        "default_effort": "high",
        "auto_tts": False,
        "default_workspace": HOME_DIR,
    }
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            defaults.update(saved)
        except Exception:
            pass
    return defaults


def save_settings(data: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    current.update(data)
    SETTINGS_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def create_auth_token() -> str:
    exp = int(time.time()) + (SESSION_DAYS * 86400)
    payload = f"owner:{exp}"
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_token_str(token: Optional[str]) -> bool:
    if not token:
        return False
    parts = token.split(":")
    if len(parts) != 3:
        return False
    user, exp_str, sig = parts
    try:
        if int(exp_str) < int(time.time()):
            return False
    except ValueError:
        return False
    payload = f"{user}:{exp_str}"
    expected_sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


async def require_auth(
    request: Request,
    agy_token: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
):
    token = agy_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        token = request.query_params.get("token")
    if not verify_token_str(token):
        raise HTTPException(status_code=401, detail="Oturum süresi doldu veya giriş yapılmadı.")
    return True


# =====================================================================
# TRANSCRIPT, DIFF, SUBAGENT & METADATA PARSERS
# =====================================================================
TITLE_RE = re.compile(r'title\s*:\s*"((?:[^"\\]|\\.)*)"')
TIME_RE = re.compile(r"seconds\s*:\s*(\d+)")
USER_REQ_RE = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.DOTALL)
MODEL_CHANGE_RE = re.compile(r"changed setting `Model Selection` from .*? to ([^\n.]+)")
EMBED_RE = re.compile(r'<agent-embed\s+src=["\']([^"\']+)["\']\s*>\s*</agent-embed>', re.IGNORECASE)


def unescape_pbtxt_str(raw: str) -> str:
    try:
        return raw.replace('\\"', '"').replace("\\\\", "\\")
    except Exception:
        return raw


def parse_annotation_pbtxt(pbtxt_path: Path) -> dict[str, Any]:
    if not pbtxt_path.exists():
        return {}
    try:
        text = pbtxt_path.read_text(encoding="utf-8", errors="replace")
        title_m = TITLE_RE.search(text)
        time_m = TIME_RE.search(text)
        title = unescape_pbtxt_str(title_m.group(1)) if title_m else ""
        ts = int(time_m.group(1)) if time_m else 0
        return {"title": title, "last_view_ts": ts}
    except Exception:
        return {}


def clean_user_content(raw_content: str) -> tuple[str, Optional[str]]:
    if not raw_content:
        return "", None
    model_used = None
    m_model = MODEL_CHANGE_RE.search(raw_content)
    if m_model:
        model_used = m_model.group(1).strip()

    m_req = USER_REQ_RE.search(raw_content)
    if m_req:
        cleaned = m_req.group(1).strip()
        return cleaned, model_used

    cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw_content, flags=re.DOTALL)
    cleaned = re.sub(r"<USER_SETTINGS_CHANGE>.*?</USER_SETTINGS_CHANGE>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip(), model_used


def list_artifacts_for_brain(brain_dir: Path) -> list[dict[str, Any]]:
    if not brain_dir.exists():
        return []
    artifacts = []
    for item in sorted(brain_dir.iterdir()):
        if item.name.startswith(".") or item.is_dir():
            continue
        if item.suffix.lower() in {".md", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".html", ".pdf", ".json", ".txt", ".py"}:
            if item.name.endswith(".metadata.json") or item.name.endswith(".resolved"):
                continue
            stat = item.stat()
            ext = item.suffix.lower()
            atype = "markdown" if ext == ".md" else (
                "image" if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else (
                    "html" if ext == ".html" else "file"
                )
            )
            artifacts.append({
                "name": item.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "type": atype,
            })
    return artifacts


def clean_arg_val(v: Any) -> Any:
    if isinstance(v, str) and len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        try:
            return json.loads(v)
        except Exception:
            return v.strip('"')
    return v


def parse_transcript_jsonl(jsonl_path: Path) -> dict[str, Any]:
    """Parses transcript.jsonl into turns, interactive question cards, file diffs, terminal tasks, and subagent calls."""
    turns: list[dict[str, Any]] = []
    file_changes: list[dict[str, Any]] = []
    terminal_tasks: list[dict[str, Any]] = []
    subagent_invocations: list[dict[str, Any]] = []

    first_user_text = ""
    last_message_preview = ""
    active_model = None
    step_count = 0
    created_at_str = None
    updated_at_str = None

    if not jsonl_path.exists():
        return {
            "turns": [],
            "file_changes": [],
            "terminal_tasks": [],
            "subagent_invocations": [],
            "first_user_text": "",
            "preview": "",
            "model": None,
            "step_count": 0,
            "created_at": None,
            "updated_at": None,
        }

    current_assistant_turn: Optional[dict[str, Any]] = None
    pending_tool_Index: Optional[dict[str, Any]] = None

    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                step = json.loads(line)
            except Exception:
                continue

            step_count += 1
            step_type = step.get("type", "")
            step_time = step.get("created_at")
            if step_time:
                if not created_at_str:
                    created_at_str = step_time
                updated_at_str = step_time

            if step_type == "USER_INPUT":
                if current_assistant_turn:
                    turns.append(current_assistant_turn)
                    current_assistant_turn = None

                raw_content = step.get("content", "")
                clean_text, model_change = clean_user_content(raw_content)
                if model_change:
                    active_model = model_change

                media_list = []
                for m in step.get("media", []) or []:
                    uri = m.get("uri", "")
                    media_list.append({
                        "mime_type": m.get("mime_type", "image/png"),
                        "filename": Path(uri).name if uri else "image.png",
                        "uri": uri,
                    })

                if clean_text or media_list:
                    if not first_user_text and clean_text:
                        first_user_text = clean_text[:120]
                    last_message_preview = clean_text[:160] if clean_text else "[Görsel/Medya]"
                    turns.append({
                        "role": "user",
                        "content": clean_text,
                        "media": media_list,
                        "created_at": step_time,
                        "step_index": step.get("step_index", 0),
                    })

            elif step_type == "PLANNER_RESPONSE":
                if not current_assistant_turn:
                    current_assistant_turn = {
                        "role": "assistant",
                        "content": "",
                        "thoughts": [],
                        "tool_calls": [],
                        "questions": [],
                        "embeds": [],
                        "has_plan_approval": False,
                        "created_at": step_time,
                        "model": active_model,
                        "usage": {},
                    }

                thinking = (step.get("thinking") or "").strip()
                if thinking:
                    current_assistant_turn["thoughts"].append(thinking)

                for tc in step.get("tool_calls", []) or []:
                    tc_name = tc.get("name", "tool")
                    raw_args = tc.get("args", {}) or {}
                    tc_args = {k: clean_arg_val(v) for k, v in raw_args.items()} if isinstance(raw_args, dict) else {}

                    # 1. Interactive ask_question cards
                    if tc_name == "ask_question" and isinstance(tc_args, dict):
                        qs = tc_args.get("questions", [])
                        if isinstance(qs, str):
                            try:
                                qs = json.loads(qs)
                            except Exception:
                                qs = []
                        if isinstance(qs, list):
                            for q in qs:
                                if isinstance(q, dict):
                                    current_assistant_turn["questions"].append({
                                        "question": q.get("question", ""),
                                        "options": q.get("options", []),
                                        "is_multi_select": bool(q.get("is_multi_select", False)),
                                    })

                    # 2. File Changes & Diffs tracking
                    if tc_name in ("write_file", "write_to_file", "edit_file", "replace_file_content", "multi_replace_file_content"):
                        fpath = str(tc_args.get("Path") or tc_args.get("TargetFile") or "")
                        if "implementation_plan.md" in fpath.lower():
                            current_assistant_turn["has_plan_approval"] = True

                        if tc_name in ("write_file", "write_to_file"):
                            new_content = str(tc_args.get("Content") or tc_args.get("CodeContent") or "")
                            preview_lines = new_content.splitlines()[:35]
                            diff_text = "\n".join(f"+ {ln}" for ln in preview_lines)
                            if len(new_content.splitlines()) > 35:
                                diff_text += f"\n... (+{len(new_content.splitlines()) - 35} satır daha)"
                            file_changes.append({
                                "action": "create",
                                "path": fpath,
                                "basename": Path(fpath.replace("\\", "/")).name if fpath else "file",
                                "diff": diff_text,
                                "timestamp": step_time,
                            })
                        elif tc_name in ("edit_file", "replace_file_content"):
                            target_s = str(tc_args.get("Target") or tc_args.get("TargetContent") or "")
                            repl_s = str(tc_args.get("Replacement") or tc_args.get("ReplacementContent") or "")
                            udiff = "\n".join(
                                difflib.unified_diff(
                                    target_s.splitlines(),
                                    repl_s.splitlines(),
                                    fromfile="Önceki",
                                    tofile="Yeni",
                                    lineterm="",
                                )
                            )
                            file_changes.append({
                                "action": "edit",
                                "path": fpath,
                                "basename": Path(fpath.replace("\\", "/")).name if fpath else "file",
                                "diff": udiff or f"- {target_s[:200]}\n+ {repl_s[:200]}",
                                "timestamp": step_time,
                            })

                    # 3. Terminal Commands tracking
                    if tc_name == "run_command":
                        cmd_line = str(tc_args.get("CommandLine") or "")
                        summary = str(tc_args.get("toolSummary") or cmd_line[:60])
                        task_entry = {
                            "command": cmd_line,
                            "summary": summary,
                            "timestamp": step_time,
                            "output": "",
                        }
                        terminal_tasks.append(task_entry)
                        pending_tool_Index = task_entry

                    # 4. Subagent Invocations tracking
                    if tc_name == "invoke_subagent":
                        subagent_invocations.append({
                            "type": str(tc_args.get("TypeName") or "subagent"),
                            "role": str(tc_args.get("Role") or "Subagent"),
                            "prompt": str(tc_args.get("Prompt") or ""),
                            "timestamp": step_time,
                        })

                    current_assistant_turn["tool_calls"].append({
                        "name": tc_name,
                        "args": tc_args,
                    })

                content = (step.get("content") or "").strip()
                if content:
                    # Extract <agent-embed src="..."> if present
                    for em in EMBED_RE.finditer(content):
                        src_uri = em.group(1)
                        current_assistant_turn["embeds"].append(Path(src_uri).name)

                    if current_assistant_turn["content"]:
                        current_assistant_turn["content"] += "\n\n" + content
                    else:
                        current_assistant_turn["content"] = content
                    last_message_preview = content[:160]

                if step.get("input_tokens") or step.get("output_tokens"):
                    current_assistant_turn["usage"] = {
                        "input_tokens": step.get("input_tokens", 0),
                        "output_tokens": step.get("output_tokens", 0),
                    }

            elif step_type == "GENERIC" and pending_tool_Index is not None:
                out_txt = (step.get("content") or "").strip()
                if out_txt:
                    pending_tool_Index["output"] = out_txt[:1500]
                pending_tool_Index = None

    if current_assistant_turn:
        turns.append(current_assistant_turn)

    return {
        "turns": turns,
        "file_changes": file_changes,
        "terminal_tasks": terminal_tasks,
        "subagent_invocations": subagent_invocations,
        "first_user_text": first_user_text,
        "preview": last_message_preview,
        "model": active_model,
        "step_count": step_count,
        "created_at": created_at_str,
        "updated_at": updated_at_str,
    }


def load_pc_metadata_index() -> dict[str, dict[str, Any]]:
    idx_file = PC_SYNC_DIR / "pc_conversations_index.json"
    if not idx_file.exists():
        return {}
    try:
        return json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_pc_customizations_index() -> dict[str, Any]:
    idx_file = PC_SYNC_DIR / "pc_customizations_index.json"
    if not idx_file.exists():
        return {"skills": [], "rules": [], "plugins": [], "mcp_servers": {}}
    try:
        return json.loads(idx_file.read_text(encoding="utf-8"))
    except Exception:
        return {"skills": [], "rules": [], "plugins": [], "mcp_servers": {}}


def discover_all_conversations(include_subagents: bool = False) -> list[dict[str, Any]]:
    pc_index = load_pc_metadata_index()
    conv_map: dict[str, dict[str, Any]] = {}

    for source_label, root_dir in [("pc", PC_SYNC_DIR), ("ubuntu", ANTIGRAVITY_DATA_DIR)]:
        if not root_dir.exists():
            continue

        brain_root = root_dir / "brain"
        ann_root = root_dir / "annotations"

        uuids = set()
        if brain_root.exists():
            for d in brain_root.iterdir():
                if d.is_dir() and not d.name.startswith("."):
                    uuids.add(d.name)
        if ann_root.exists():
            for f in ann_root.glob("*.pbtxt"):
                uuids.add(f.stem)

        for cid in uuids:
            brain_dir = brain_root / cid
            jsonl_path = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
            pbtxt_path = ann_root / f"{cid}.pbtxt"

            ann = parse_annotation_pbtxt(pbtxt_path)
            pc_meta = pc_index.get(cid, {})

            nesting_depth = pc_meta.get("nesting_depth", 0)
            parent_cid = pc_meta.get("parent_conversation_id", "")
            if not include_subagents and nesting_depth > 0:
                continue

            mtime = 0.0
            if jsonl_path.exists():
                mtime = max(mtime, jsonl_path.stat().st_mtime)
            if pbtxt_path.exists():
                mtime = max(mtime, pbtxt_path.stat().st_mtime)
            if ann.get("last_view_ts"):
                mtime = max(mtime, float(ann["last_view_ts"]))
            if pc_meta.get("last_modified_ts"):
                mtime = max(mtime, float(pc_meta["last_modified_ts"]))

            title = ann.get("title") or pc_meta.get("title") or ""
            preview = pc_meta.get("preview") or ""
            step_count = pc_meta.get("step_count") or 0
            workspace = pc_meta.get("workspace") or ""

            if (not title or not preview) and jsonl_path.exists():
                try:
                    parsed = parse_transcript_jsonl(jsonl_path)
                    if not title:
                        title = parsed["first_user_text"] or f"Sohbet {cid[:8]}"
                    if not preview:
                        preview = parsed["preview"]
                    step_count = max(step_count, parsed["step_count"])
                except Exception:
                    pass

            if not title:
                title = f"Sohbet {cid[:8]}"

            artifacts = list_artifacts_for_brain(brain_dir)
            has_transcript = jsonl_path.exists()

            if not has_transcript and not preview and not artifacts:
                continue

            existing = conv_map.get(cid)
            origin = "pc" if (cid in pc_index or source_label == "pc") else "ubuntu"
            if existing:
                if has_transcript and (not existing["has_transcript"] or mtime >= existing["updated_ts"]):
                    existing["root_dir"] = str(root_dir)
                    existing["has_transcript"] = True
                existing["updated_ts"] = max(existing["updated_ts"], mtime)
                if title and existing["title"].startswith("Sohbet "):
                    existing["title"] = title
                if preview and not existing["preview"]:
                    existing["preview"] = preview
                if artifacts and not existing["artifacts"]:
                    existing["artifacts"] = artifacts
                if workspace and not existing["workspace"]:
                    existing["workspace"] = workspace
            else:
                conv_map[cid] = {
                    "id": cid,
                    "title": title,
                    "preview": preview,
                    "origin": origin,
                    "workspace": workspace,
                    "step_count": step_count,
                    "nesting_depth": nesting_depth,
                    "parent_conversation_id": parent_cid,
                    "agent_name": pc_meta.get("agent_name", ""),
                    "updated_ts": mtime if mtime > 0 else time.time(),
                    "artifacts": artifacts,
                    "has_transcript": has_transcript,
                    "root_dir": str(root_dir),
                }

    results = list(conv_map.values())
    results.sort(key=lambda x: x["updated_ts"], reverse=True)
    return results


def find_conversation_paths(cid: str) -> tuple[Path, Path]:
    candidates = []
    for root in [ANTIGRAVITY_DATA_DIR, PC_SYNC_DIR]:
        bdir = root / "brain" / cid
        jpath = bdir / ".system_generated" / "logs" / "transcript.jsonl"
        if jpath.exists():
            candidates.append((jpath.stat().st_mtime, root, bdir))
        elif bdir.exists():
            candidates.append((0.0, root, bdir))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1], candidates[0][2]
    return ANTIGRAVITY_DATA_DIR, ANTIGRAVITY_DATA_DIR / "brain" / cid


# =====================================================================
# AUTH & BASIC SETTINGS ENDPOINTS
# =====================================================================
@app.post("/api/auth/login")
async def login(request: Request):
    client_ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown")
    now = time.time()
    recent = [t for t in _login_failures.get(client_ip, []) if now - t < LOGIN_WINDOW_SECONDS]
    _login_failures[client_ip] = recent
    if len(recent) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Çok fazla hatalı deneme. 15 dakika sonra tekrar deneyin.")

    data = await request.json()
    pin = str(data.get("pin", "")).strip()
    if not hmac.compare_digest(pin.encode(), AUTH_PIN.encode()):
        recent.append(now)
        _login_failures[client_ip] = recent
        raise HTTPException(status_code=401, detail="Hatalı PIN / Şifre!")
    _login_failures.pop(client_ip, None)
    token = create_auth_token()
    resp = JSONResponse({"ok": True, "token": token})
    resp.set_cookie(
        key="agy_token",
        value=token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="strict",
    )
    return resp


@app.get("/api/auth/verify")
async def verify_auth(_: bool = Depends(require_auth)):
    return {"ok": True, "settings": load_settings()}


@app.get("/api/settings")
async def get_settings_api(_: bool = Depends(require_auth)):
    return load_settings()


@app.post("/api/settings")
async def update_settings_api(request: Request, _: bool = Depends(require_auth)):
    data = await request.json()
    return save_settings(data)


@app.get("/api/models")
async def get_models(_: bool = Depends(require_auth)):
    return {"models": AVAILABLE_MODELS, "slash_commands": SLASH_COMMANDS}


# =====================================================================
# WORKSPACES & @ MENTION CONTEXT ENDPOINTS
# =====================================================================
@app.get("/api/workspaces")
async def get_workspaces(_: bool = Depends(require_auth)):
    home = Path(os.path.expanduser("~"))
    workspaces = [
        {"name": "Ubuntu Ana Dizin (~)", "path": str(home), "origin": "ubuntu"},
    ]
    for item in sorted(home.iterdir()):
        if item.is_dir() and not item.name.startswith(".") and item.name not in ("snap", "venv"):
            workspaces.append({
                "name": item.name,
                "path": str(item),
                "origin": "ubuntu",
            })
    return {"workspaces": workspaces}


@app.get("/api/mentions")
async def get_mentions(
    workspace: str = Query(default=HOME_DIR),
    q: str = Query(default=""),
    _: bool = Depends(require_auth),
):
    """Returns files in the selected workspace, recent conversations, and skills for the @ mention menu."""
    items: list[dict[str, Any]] = []
    ws_path = Path(workspace) if workspace else Path(os.path.expanduser("~"))
    if ws_path.exists() and ws_path.is_dir():
        try:
            count = 0
            for root, dirs, files in os.walk(ws_path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("venv", "node_modules", "__pycache__", "pc_sync")]
                rel_root = Path(root).relative_to(ws_path)
                if len(rel_root.parts) > 2:
                    continue
                for fn in files:
                    if fn.startswith(".") or fn.endswith((".pyc", ".db-shm", ".db-wal")):
                        continue
                    rel_file = str(rel_root / fn) if str(rel_root) != "." else fn
                    full_file = str(Path(root) / fn)
                    items.append({
                        "type": "file",
                        "label": f"📄 {rel_file}",
                        "insert": f"@{full_file} ",
                        "desc": workspace,
                    })
                    count += 1
                    if count >= 40:
                        break
                if count >= 40:
                    break
        except Exception:
            pass

    # Add recent conversations
    convs = discover_all_conversations()[:15]
    for c in convs:
        items.append({
            "type": "conversation",
            "label": f"💬 {c['title']}",
            "insert": f"@[Konuşma: {c['title']} ({c['id']})] ",
            "desc": c["origin"].upper(),
        })

    # Filter by query if provided
    q_low = q.strip().lower()
    if q_low:
        items = [it for it in items if q_low in it["label"].lower() or q_low in it["insert"].lower()]

    return {"items": items[:30]}


# =====================================================================
# MEDIA & FILE UPLOAD ENDPOINT
# =====================================================================
@app.post("/api/upload")
async def upload_file(request: Request, _: bool = Depends(require_auth)):
    """Accepts JSON { filename, content_base64, mime_type, conversation_id } and saves to .user_uploaded."""
    data = await request.json()
    filename = Path(data.get("filename") or "upload.bin").name
    b64 = data.get("content_base64") or ""
    mime_type = data.get("mime_type") or "application/octet-stream"
    cid = (data.get("conversation_id") or "").strip()

    if "," in b64 and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]

    raw_bytes = base64.b64decode(b64)
    ts_name = f"media_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}_{filename}"

    if cid:
        _, brain_dir = find_conversation_paths(cid)
        up_dir = brain_dir / ".user_uploaded"
    else:
        up_dir = UPLOADS_DIR

    up_dir.mkdir(parents=True, exist_ok=True)
    dest = up_dir / ts_name
    dest.write_bytes(raw_bytes)

    return {
        "ok": True,
        "filename": ts_name,
        "original_name": filename,
        "path": str(dest),
        "mime_type": mime_type,
        "size": len(raw_bytes),
    }


# =====================================================================
# CONVERSATIONS, SUBAGENTS, DIFFS & ARTIFACT COMMENTS
# =====================================================================
@app.get("/api/conversations")
async def list_conversations(
    q: str = Query(default=""),
    source: str = Query(default="all"),
    _: bool = Depends(require_auth),
):
    convs = discover_all_conversations(include_subagents=False)
    if source in ("pc", "ubuntu"):
        convs = [c for c in convs if c["origin"] == source]

    q_clean = q.strip().lower()
    if q_clean:
        filtered = []
        for c in convs:
            if (
                q_clean in c["title"].lower()
                or q_clean in (c["preview"] or "").lower()
                or q_clean in (c["workspace"] or "").lower()
                or q_clean in c["id"].lower()
            ):
                filtered.append(c)
            else:
                _, bdir = find_conversation_paths(c["id"])
                jpath = bdir / ".system_generated" / "logs" / "transcript.jsonl"
                if jpath.exists():
                    try:
                        if q_clean in jpath.read_text(encoding="utf-8", errors="ignore").lower():
                            filtered.append(c)
                    except Exception:
                        pass
        convs = filtered

    out = []
    for c in convs:
        item = dict(c)
        item.pop("root_dir", None)
        out.append(item)

    return {"conversations": out, "total": len(out)}


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str, _: bool = Depends(require_auth)):
    pc_index = load_pc_metadata_index()
    root_dir, brain_dir = find_conversation_paths(cid)
    jsonl_path = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
    full_jsonl_path = brain_dir / ".system_generated" / "logs" / "transcript_full.jsonl"

    target_jsonl = full_jsonl_path if full_jsonl_path.exists() else jsonl_path
    parsed = parse_transcript_jsonl(target_jsonl)

    ann = parse_annotation_pbtxt(root_dir / "annotations" / f"{cid}.pbtxt")
    if not ann.get("title"):
        ann = parse_annotation_pbtxt(PC_SYNC_DIR / "annotations" / f"{cid}.pbtxt")

    pc_meta = pc_index.get(cid, {})
    title = ann.get("title") or pc_meta.get("title") or parsed["first_user_text"] or f"Sohbet {cid[:8]}"
    artifacts = list_artifacts_for_brain(brain_dir)

    # Discover child subagent conversations from pc_index or transcript invocations
    subagents = []
    for other_cid, meta in pc_index.items():
        if meta.get("parent_conversation_id") == cid:
            subagents.append({
                "id": other_cid,
                "title": meta.get("title") or meta.get("agent_name") or f"Subagent {other_cid[:8]}",
                "agent_name": meta.get("agent_name") or "subagent",
                "step_count": meta.get("step_count", 0),
                "status": meta.get("status", "DONE"),
                "preview": meta.get("preview", ""),
            })

    for inv in parsed["subagent_invocations"]:
        subagents.append({
            "id": "",
            "title": f"{inv['role']} ({inv['type']})",
            "agent_name": inv["type"],
            "step_count": 0,
            "status": "INVOKED",
            "preview": inv["prompt"][:200],
        })

    return {
        "id": cid,
        "title": title,
        "origin": "pc" if cid in pc_index else ("pc" if root_dir == PC_SYNC_DIR else "ubuntu"),
        "workspace": pc_meta.get("workspace", ""),
        "model": parsed["model"] or "Gemini 4 Argon (High)",
        "step_count": parsed["step_count"] or pc_meta.get("step_count", 0),
        "turns": parsed["turns"],
        "artifacts": artifacts,
        "file_changes": parsed["file_changes"],
        "terminal_tasks": parsed["terminal_tasks"],
        "subagents": subagents,
    }


@app.get("/api/conversations/{cid}/artifacts/{filename}")
async def get_artifact(cid: str, filename: str, raw: bool = Query(default=False), _: bool = Depends(require_auth)):
    safe_name = Path(filename).name
    for root in [ANTIGRAVITY_DATA_DIR, PC_SYNC_DIR]:
        fpath = root / "brain" / cid / safe_name
        if fpath.exists() and fpath.is_file():
            if raw or fpath.suffix.lower() in {".html", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}:
                mime, _ = mimetypes.guess_type(str(fpath))
                headers = {}
                if fpath.suffix.lower() == ".html":
                    # Agent-generated HTML runs in an opaque-origin sandbox: it cannot read
                    # the app's cookies/localStorage or call the API as the logged-in user.
                    headers["Content-Security-Policy"] = "sandbox allow-scripts allow-forms allow-popups"
                return FileResponse(fpath, media_type=mime or "text/html", headers=headers)
            if fpath.suffix.lower() in {".md", ".txt", ".py", ".json"}:
                content = fpath.read_text(encoding="utf-8", errors="replace")
                return {"name": safe_name, "type": "text", "content": content}
            mime, _ = mimetypes.guess_type(str(fpath))
            return FileResponse(fpath, media_type=mime or "application/octet-stream")
    raise HTTPException(status_code=404, detail="Artifact dosyası bulunamadı.")


@app.get("/api/conversations/{cid}/media/{filename}")
async def get_media(cid: str, filename: str, _: bool = Depends(require_auth)):
    safe_name = Path(filename).name
    for root in [ANTIGRAVITY_DATA_DIR, PC_SYNC_DIR]:
        for sub in [".user_uploaded", "", "scratch"]:
            fpath = (root / "brain" / cid / sub / safe_name) if sub else (root / "brain" / cid / safe_name)
            if fpath.exists() and fpath.is_file():
                mime, _ = mimetypes.guess_type(str(fpath))
                return FileResponse(fpath, media_type=mime or "image/png")
    if (UPLOADS_DIR / safe_name).exists():
        mime, _ = mimetypes.guess_type(str(UPLOADS_DIR / safe_name))
        return FileResponse(UPLOADS_DIR / safe_name, media_type=mime or "image/png")
    raise HTTPException(status_code=404, detail="Medya bulunamadı.")


@app.get("/api/conversations/{cid}/comments")
async def get_artifact_comments(cid: str, _: bool = Depends(require_auth)):
    all_c = {}
    if COMMENTS_FILE.exists():
        try:
            all_c = json.loads(COMMENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"comments": all_c.get(cid, [])}


@app.post("/api/conversations/{cid}/comments")
async def add_artifact_comment(cid: str, request: Request, _: bool = Depends(require_auth)):
    body = await request.json()
    all_c = {}
    if COMMENTS_FILE.exists():
        try:
            all_c = json.loads(COMMENTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    clist = all_c.get(cid, [])
    clist.append({
        "id": uuid.uuid4().hex[:8],
        "artifact": body.get("artifact", ""),
        "quote": body.get("quote", ""),
        "comment": body.get("comment", ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    all_c[cid] = clist
    COMMENTS_FILE.write_text(json.dumps(all_c, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "comments": clist}


# =====================================================================
# LIVE CHAT STREAMING, ABORT & BATTLE MODE
# =====================================================================
def build_context_bridge_prompt(cid: str, user_prompt: str, attachments: list[dict]) -> str:
    parts = []
    if cid:
        _, brain_dir = find_conversation_paths(cid)
        jsonl_path = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
        if jsonl_path.exists():
            parsed = parse_transcript_jsonl(jsonl_path)
            turns = parsed.get("turns", [])
            if turns:
                recent_turns = turns[-8:]
                parts.append("[ÖNCEKİ KONUŞMA GEÇMİŞİ]:")
                for t in recent_turns:
                    role_label = "Kullanıcı" if t["role"] == "user" else "Antigravity"
                    text = (t.get("content") or "").strip()
                    if len(text) > 1200:
                        text = text[:1200] + "..."
                    if text:
                        parts.append(f"{role_label}: {text}")

    if attachments:
        att_lines = ["[KULLANICININ YÜKLEDİĞİ DOSYALAR / GÖRSELLER (view_file veya ilgili araçla inceleyebilirsin)]:"]
        for att in attachments:
            att_lines.append(f"- {att.get('original_name', att.get('filename'))}: {att.get('path')}")
        parts.append("\n".join(att_lines))

    if parts:
        parts.append("[KULLANICININ YENİ MESAJI]:\n" + user_prompt)
        return "\n\n".join(parts)
    return user_prompt


def append_turn_to_transcript(
    cid: str,
    user_prompt: str,
    assistant_reply: str,
    thoughts: list[str],
    tool_calls: list[dict],
    model: str,
    attachments: list[dict] = None,
):
    _, brain_dir = find_conversation_paths(cid)
    logs_dir = brain_dir / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = logs_dir / "transcript.jsonl"

    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    media_objs = []
    for att in attachments or []:
        if str(att.get("mime_type", "")).startswith("image/"):
            media_objs.append({"mime_type": att.get("mime_type"), "uri": att.get("path")})

    user_step = {
        "step_index": int(time.time()),
        "source": "USER_EXPLICIT",
        "type": "USER_INPUT",
        "status": "DONE",
        "created_at": now_iso,
        "content": f"<USER_REQUEST>\n{user_prompt}\n</USER_REQUEST>\n<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` from None to {model}.\n</USER_SETTINGS_CHANGE>",
        "media": media_objs,
    }
    model_step = {
        "step_index": int(time.time()) + 1,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": now_iso,
        "content": assistant_reply,
        "thinking": "\n".join(thoughts) if thoughts else "",
        "tool_calls": tool_calls,
    }
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(user_step, ensure_ascii=False) + "\n")
        f.write(json.dumps(model_step, ensure_ascii=False) + "\n")


@app.post("/api/chat/abort")
async def abort_chat(request: Request, _: bool = Depends(require_auth)):
    body = await request.json()
    stream_id = body.get("stream_id") or ""
    killed = []
    if stream_id and stream_id in ACTIVE_PROCESSES:
        proc = ACTIVE_PROCESSES.pop(stream_id, None)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                killed.append(stream_id)
            except Exception:
                pass
    else:
        for sid, proc in list(ACTIVE_PROCESSES.items()):
            if proc.returncode is None:
                try:
                    proc.terminate()
                    killed.append(sid)
                except Exception:
                    pass
            ACTIVE_PROCESSES.pop(sid, None)
    return {"ok": True, "aborted": killed}


@app.post("/api/chat/stream")
async def chat_stream(request: Request, _: bool = Depends(require_auth)):
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    conversation_id = (body.get("conversation_id") or "").strip() or None
    model = (body.get("model") or "gemini-4-argon-high").strip()
    effort = (body.get("effort") or "high").strip()
    workspace = (body.get("workspace") or HOME_DIR).strip()
    attachments = body.get("attachments") or []
    stream_id = body.get("stream_id") or uuid.uuid4().hex[:10]

    if not prompt and not attachments:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")

    settings = load_settings()
    use_sandbox = settings.get("permission_mode") == "sandbox"

    async def event_generator() -> AsyncGenerator[str, None]:
        ubuntu_db = ANTIGRAVITY_DATA_DIR / "conversations" / f"{conversation_id}.db" if conversation_id else None
        use_native_resume = bool(conversation_id and ubuntu_db and ubuntu_db.exists())

        effective_prompt = prompt
        if (conversation_id and not use_native_resume) or attachments:
            effective_prompt = build_context_bridge_prompt(conversation_id if not use_native_resume else "", prompt, attachments)

        cmd = [
            AGY_BIN,
            "-p",
            effective_prompt,
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--effort",
            effort,
        ]
        if use_sandbox:
            cmd.append("--sandbox")
        else:
            cmd.append("--dangerously-skip-permissions")

        if workspace and os.path.isdir(workspace):
            cmd.extend(["--add-dir", workspace])

        if use_native_resume and conversation_id:
            cmd.extend(["--conversation", conversation_id])

        env = os.environ.copy()
        env["SSH_CONNECTION"] = "1"
        cwd_dir = workspace if (workspace and os.path.isdir(workspace)) else os.path.expanduser("~")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd_dir,
            env=env,
        )
        ACTIVE_PROCESSES[stream_id] = proc

        active_cid = conversation_id
        full_reply_chunks: list[str] = []
        collected_thoughts: list[str] = []
        collected_tools: list[dict] = []

        try:
            assert proc.stdout is not None
            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line_str = line_bytes.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    evt = json.loads(line_str)
                except Exception:
                    continue

                etype = evt.get("event")
                if etype == "init":
                    new_cid = evt.get("conversation_id")
                    if not active_cid and new_cid:
                        active_cid = new_cid
                    yield f"data: {json.dumps({'type': 'init', 'stream_id': stream_id, 'conversation_id': active_cid or new_cid, 'model': model})}\n\n"

                elif etype == "step_update":
                    su = evt.get("step_update", {})
                    stype = su.get("step_type", "")
                    state = su.get("state", "")
                    text_delta = su.get("text_delta", "")
                    thinking_delta = su.get("thinking_delta", "")

                    if thinking_delta:
                        collected_thoughts.append(thinking_delta)
                        yield f"data: {json.dumps({'type': 'thinking', 'delta': thinking_delta})}\n\n"

                    if stype == "agent_response" and text_delta:
                        full_reply_chunks.append(text_delta)
                        yield f"data: {json.dumps({'type': 'delta', 'delta': text_delta})}\n\n"
                    elif stype not in ("user_input", "system_message", "agent_response") and state == "ACTIVE":
                        tool_info = {"name": stype, "state": state}
                        collected_tools.append(tool_info)
                        yield f"data: {json.dumps({'type': 'tool', 'tool': stype, 'state': state})}\n\n"

                elif etype == "result":
                    res = evt.get("result", {})
                    final_text = res.get("response") or "".join(full_reply_chunks)
                    usage = res.get("usage", {})
                    if conversation_id and not use_native_resume:
                        try:
                            append_turn_to_transcript(
                                conversation_id,
                                prompt,
                                final_text,
                                collected_thoughts,
                                collected_tools,
                                model,
                                attachments,
                            )
                        except Exception:
                            pass

                    yield f"data: {json.dumps({'type': 'done', 'conversation_id': active_cid or res.get('conversation_id'), 'response': final_text, 'usage': usage})}\n\n"

            await proc.wait()
        finally:
            ACTIVE_PROCESSES.pop(stream_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/battle")
async def chat_battle(request: Request, _: bool = Depends(require_auth)):
    """Runs two models side-by-side in parallel for Battle Mode comparison."""
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    model_a = (body.get("model_a") or "gemini-4-argon-high").strip()
    model_b = (body.get("model_b") or "claude-opus-5-5-high").strip()
    effort = (body.get("effort") or "high").strip()
    workspace = (body.get("workspace") or HOME_DIR).strip()

    if not prompt:
        raise HTTPException(status_code=400, detail="Mesaj boş olamaz.")

    async def run_one_model(mdl: str) -> dict[str, Any]:
        t0 = time.time()
        cmd = [
            AGY_BIN,
            "-p",
            prompt,
            "--dangerously-skip-permissions",
            "--model",
            mdl,
            "--effort",
            effort,
        ]
        env = os.environ.copy()
        env["SSH_CONNECTION"] = "1"
        cwd_dir = workspace if (workspace and os.path.isdir(workspace)) else os.path.expanduser("~")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd_dir,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        elapsed = round(time.time() - t0, 2)
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text and stderr:
            text = "⚠️ " + stderr.decode("utf-8", errors="replace").strip()
        return {"model": mdl, "response": text, "duration_seconds": elapsed}

    res_a, res_b = await asyncio.gather(run_one_model(model_a), run_one_model(model_b))
    return {"ok": True, "prompt": prompt, "left": res_a, "right": res_b}


# =====================================================================
# SYSTEM HUB: CUSTOMIZATIONS, AUTOMATIONS & MINI TERMINAL
# =====================================================================
@app.get("/api/system/customizations")
async def get_system_customizations(_: bool = Depends(require_auth)):
    pc_cust = load_pc_customizations_index()
    skills = list(pc_cust.get("skills", []))
    rules = list(pc_cust.get("rules", []))
    plugins = list(pc_cust.get("plugins", []))
    mcp_servers = dict(pc_cust.get("mcp_servers", {}))

    # Also scan Ubuntu's ~/.gemini/config
    if UBUNTU_CONFIG_DIR.exists():
        for smd in UBUNTU_CONFIG_DIR.rglob("SKILL.md"):
            try:
                txt = smd.read_text(encoding="utf-8", errors="replace")
                nm = re.search(r"^name:\s*(.+)$", txt, re.MULTILINE)
                dm = re.search(r"^description:\s*(.+)$", txt, re.MULTILINE)
                sname = nm.group(1).strip() if nm else smd.parent.name
                if not any(s["name"] == sname for s in skills):
                    skills.append({
                        "name": sname,
                        "description": dm.group(1).strip() if dm else "",
                        "plugin": "ubuntu",
                        "origin": "ubuntu",
                        "content": txt[:4000],
                    })
            except Exception:
                pass

        for rmd in (UBUNTU_CONFIG_DIR / "rules").glob("*.md") if (UBUNTU_CONFIG_DIR / "rules").exists() else []:
            rules.append({
                "name": rmd.name,
                "origin": "ubuntu",
                "content": rmd.read_text(encoding="utf-8", errors="replace")[:4000],
            })

    return {
        "skills": skills,
        "rules": rules,
        "plugins": plugins,
        "mcp_servers": mcp_servers,
    }


@app.get("/api/system/automations")
async def get_system_automations(_: bool = Depends(require_auth)):
    services = []
    for svc in MONITORED_SERVICES:
        try:
            res = subprocess.run(["systemctl", "is-active", svc], capture_output=True, text=True, timeout=3)
            state = res.stdout.strip() or "inactive"
        except Exception:
            state = "unknown"
        services.append({"name": svc, "status": state})

    sidecars = []
    sidecars_dir = UBUNTU_CONFIG_DIR / "sidecars"
    if sidecars_dir.exists():
        for sfile in sidecars_dir.rglob("sidecar.json"):
            try:
                sdata = json.loads(sfile.read_text(encoding="utf-8"))
                sidecars.append({
                    "id": sfile.parent.name,
                    "display_name": sdata.get("display_name", sfile.parent.name),
                    "description": sdata.get("description", ""),
                    "args": sdata.get("args", []),
                })
            except Exception:
                pass

    return {"services": services, "automations": sidecars}


@app.post("/api/system/automations")
async def create_system_automation(request: Request, _: bool = Depends(require_auth)):
    body = await request.json()
    name = (body.get("display_name") or "Yeni Otomasyon").strip()
    cron_expr = (body.get("cron") or "CRON_TZ=Europe/Istanbul 0 9 * * *").strip()
    if not cron_expr.startswith("CRON_TZ="):
        cron_expr = f"CRON_TZ=Europe/Istanbul {cron_expr}"
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Görev komutu (prompt) boş olamaz.")

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or f"auto-{int(time.time())}"
    sdir = UBUNTU_CONFIG_DIR / "sidecars" / slug
    sdir.mkdir(parents=True, exist_ok=True)
    sjson = {
        "builtin": "schedule",
        "args": [cron_expr, "agentapi", "new-conversation", "--", prompt],
        "restart_policy": "always",
        "display_name": name,
        "description": f"{cron_expr} -> {prompt[:100]}",
    }
    (sdir / "sidecar.json").write_text(json.dumps(sjson, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "id": slug, "automation": sjson}


@app.post("/api/terminal/exec")
async def exec_terminal_command(request: Request, _: bool = Depends(require_auth)):
    if not ENABLE_TERMINAL:
        raise HTTPException(
            status_code=403,
            detail="Mini terminal kapalı. Açmak için .env içinde AGY_WEB_ENABLE_TERMINAL=true ayarlayın.",
        )
    body = await request.json()
    command = (body.get("command") or "").strip()
    cwd = (body.get("cwd") or HOME_DIR).strip()
    if not command:
        raise HTTPException(status_code=400, detail="Komut boş.")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=25.0)
        output = stdout.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        output = "⚠️ Komut 25 saniye zaman aşımına uğradı."

    return {"ok": True, "command": command, "cwd": cwd, "output": output, "returncode": proc.returncode}


# =====================================================================
# TURKISH VOICE BRIDGE (FASTER-WHISPER STT & ANTALIA-2 MINI TTS)
# Optional: requires a voice server exposing /tts-only and /stt-only
# (see extras/voice_endpoints.py). Disabled when VOICE_SERVER_URL is empty.
# =====================================================================
def _require_voice():
    if not VOICE_SERVER_URL:
        raise HTTPException(status_code=503, detail="Ses özelliği kapalı (VOICE_SERVER_URL ayarlanmamış).")


@app.post("/api/voice/tts")
async def voice_tts(request: Request, _: bool = Depends(require_auth)):
    _require_voice()
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Metin boş.")
    # Clean markdown symbols for smooth Turkish TTS reading
    clean_text = re.sub(r"```.*?```", " kod bloğu. ", text, flags=re.DOTALL)
    clean_text = re.sub(r"[#*`_~>\[\]\(\)]", " ", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()[:1200]

    def call_tts() -> bytes:
        req = urllib.request.Request(
            f"{VOICE_SERVER_URL}/tts-only",
            data=json.dumps({"text": clean_text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()

    try:
        wav_bytes = await asyncio.to_thread(call_tts)
        return Response(content=wav_bytes, media_type="audio/wav")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"TTS servisi hatası: {e}")


@app.post("/api/voice/stt")
async def voice_stt(request: Request, _: bool = Depends(require_auth)):
    _require_voice()
    raw_pcm = await request.body()
    if len(raw_pcm) < 800:
        raise HTTPException(status_code=400, detail="Ses kaydı çok kısa.")

    def call_stt() -> dict:
        req = urllib.request.Request(
            f"{VOICE_SERVER_URL}/stt-only",
            data=raw_pcm,
            headers={"Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        res = await asyncio.to_thread(call_stt)
        return res
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"STT servisi hatası: {e}")


# =====================================================================
# STATIC & PWA FRONTEND ROUTES
# =====================================================================
@app.get("/", response_class=HTMLResponse)
async def index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Antigravity Web Hazırlanıyor...</h1>")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
