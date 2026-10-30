"""
Antigravity PC -> Ubuntu Otomatik Konuşma, Artifact, Alt Ajan (Subagent) ve Yetenek (Skill/Rule/MCP) Senkronizasyon Aracı
"""
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PC_AGY_DIR = Path(os.path.expanduser(r"~\.gemini\antigravity"))
PC_CONFIG_DIR = Path(os.path.expanduser(r"~\.gemini\config"))
# Required: your server's SSH target, e.g. set AGY_REMOTE_HOST=ubuntu@203.0.113.10
REMOTE_HOST = os.getenv("AGY_REMOTE_HOST", "").strip()
# Must match PC_SYNC_DIR on the server (default: <agy-web dir>/pc_sync)
REMOTE_SYNC_DIR = os.getenv("AGY_REMOTE_SYNC_DIR", "~/agy-web/pc_sync")
STATE_FILE = Path(__file__).parent / ".sync_state.json"

ALLOWED_ARTIFACT_EXTS = {".md", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".html", ".pdf", ".json", ".txt", ".py"}


def extract_workspace_name(workspace_uris_raw: str) -> str:
    if not workspace_uris_raw:
        return ""
    try:
        uris = json.loads(workspace_uris_raw)
        if isinstance(uris, list) and uris:
            u = unquote(str(uris[0]))
            return Path(u.replace("file:///", "").replace("file://", "")).name
    except Exception:
        pass
    if "/" in workspace_uris_raw or "\\" in workspace_uris_raw:
        return Path(unquote(workspace_uris_raw).rstrip("/\\")).name
    return ""


def parse_iso_to_ts(dt_str: str) -> float:
    if not dt_str:
        return 0.0
    try:
        clean = dt_str.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(clean).timestamp()
    except Exception:
        return 0.0


def build_pc_conversations_index() -> dict:
    """Reads conversation_summaries.db on Windows to extract clean UTF-8 titles, previews, workspaces, and subagent links."""
    db_path = PC_AGY_DIR / "conversation_summaries.db"
    index = {}
    if not db_path.exists():
        return index

    try:
        con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        cur = con.cursor()
        rows = cur.execute(
            """
            SELECT conversation_id, title, preview, step_count, last_modified_time,
                   workspace_uris, nesting_depth, parent_conversation_id, agent_name, status, battle_id, winning_conversation_id
            FROM conversation_summaries
            """
        ).fetchall()
        for cid, title, preview, step_count, last_mod, ws_uris, depth, parent_id, agent_name, status, battle_id, win_cid in rows:
            index[cid] = {
                "title": title or "",
                "preview": (preview or "")[:200],
                "step_count": step_count or 0,
                "last_modified_ts": parse_iso_to_ts(str(last_mod or "")),
                "workspace": extract_workspace_name(str(ws_uris or "")),
                "workspace_uris": str(ws_uris or ""),
                "nesting_depth": depth or 0,
                "parent_conversation_id": parent_id or "",
                "agent_name": agent_name or "",
                "status": status or "",
                "battle_id": battle_id or "",
                "winning_conversation_id": win_cid or "",
                "origin": "pc",
            }
        con.close()
    except Exception as e:
        print(f"[Uyarı] conversation_summaries.db okunamadı: {e}")

    return index


def parse_skill_frontmatter(skill_md: Path) -> dict:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        name_m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        desc_m = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        return {
            "name": (name_m.group(1).strip().strip('"\'') if name_m else skill_md.parent.name),
            "description": (desc_m.group(1).strip().strip('"\'') if desc_m else ""),
            "content": text[:4000],
        }
    except Exception:
        return {"name": skill_md.parent.name, "description": "", "content": ""}


def build_pc_customizations_index() -> dict:
    """Collects Skills, Rules, Plugins, and MCP servers from PC (~/.gemini/config and builtin)."""
    skills = []
    rules = []
    plugins = []
    mcp_servers = {}

    # 1. Skills & Plugins under ~/.gemini/config
    if PC_CONFIG_DIR.exists():
        for skill_md in PC_CONFIG_DIR.rglob("SKILL.md"):
            info = parse_skill_frontmatter(skill_md)
            is_plugin = "plugins" in skill_md.parts
            plugin_name = ""
            if is_plugin:
                try:
                    p_idx = skill_md.parts.index("plugins")
                    plugin_name = skill_md.parts[p_idx + 1]
                except Exception:
                    pass
            skills.append({
                "name": info["name"],
                "description": info["description"],
                "plugin": plugin_name,
                "origin": "pc",
                "content": info["content"],
            })

        # Rules
        rules_dir = PC_CONFIG_DIR / "rules"
        if rules_dir.exists():
            for rfile in rules_dir.glob("*.md"):
                rules.append({
                    "name": rfile.name,
                    "origin": "pc",
                    "content": rfile.read_text(encoding="utf-8", errors="replace")[:4000],
                })
        for rfile in PC_CONFIG_DIR.rglob("AGENTS.md"):
            rules.append({
                "name": f"{rfile.parent.parent.name}/{rfile.name}",
                "origin": "pc",
                "content": rfile.read_text(encoding="utf-8", errors="replace")[:4000],
            })

        # Plugins
        plugins_dir = PC_CONFIG_DIR / "plugins"
        if plugins_dir.exists():
            for pdir in plugins_dir.iterdir():
                if pdir.is_dir():
                    plugins.append({
                        "name": pdir.name,
                        "origin": "pc",
                    })

        # MCP Config
        mcp_file = PC_CONFIG_DIR / "mcp_config.json"
        if mcp_file.exists():
            try:
                mcp_servers = json.loads(mcp_file.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass

    # Builtin skills on PC
    builtin_dir = PC_AGY_DIR / "builtin" / "skills"
    if builtin_dir.exists():
        for skill_md in builtin_dir.rglob("SKILL.md"):
            info = parse_skill_frontmatter(skill_md)
            skills.append({
                "name": info["name"],
                "description": info["description"],
                "plugin": "builtin",
                "origin": "pc",
                "content": info["content"],
            })

    return {
        "skills": skills,
        "rules": rules,
        "plugins": plugins,
        "mcp_servers": mcp_servers,
    }


def load_sync_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_sync_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def sync_once(force_full: bool = False) -> int:
    prev_state = {} if force_full else load_sync_state()
    new_state = dict(prev_state)
    files_to_pack: list[tuple[Path, str]] = []

    ann_dir = PC_AGY_DIR / "annotations"
    if ann_dir.exists():
        for f in ann_dir.glob("*.pbtxt"):
            rel = f"annotations/{f.name}"
            st = f.stat()
            sig = f"{int(st.st_mtime)}:{st.st_size}"
            if prev_state.get(rel) != sig:
                files_to_pack.append((f, rel))
                new_state[rel] = sig

    brain_dir = PC_AGY_DIR / "brain"
    if brain_dir.exists():
        for conv_dir in brain_dir.iterdir():
            if not conv_dir.is_dir() or conv_dir.name.startswith("."):
                continue
            cid = conv_dir.name

            jsonl = conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
            if jsonl.exists():
                rel = f"brain/{cid}/.system_generated/logs/transcript.jsonl"
                st = jsonl.stat()
                sig = f"{int(st.st_mtime)}:{st.st_size}"
                if prev_state.get(rel) != sig:
                    files_to_pack.append((jsonl, rel))
                    new_state[rel] = sig

            for item in conv_dir.iterdir():
                if item.is_file() and not item.name.startswith("."):
                    if item.name.endswith(".metadata.json") or item.name.endswith(".resolved"):
                        continue
                    if item.suffix.lower() in ALLOWED_ARTIFACT_EXTS and item.stat().st_size < 10 * 1024 * 1024:
                        rel = f"brain/{cid}/{item.name}"
                        st = item.stat()
                        sig = f"{int(st.st_mtime)}:{st.st_size}"
                        if prev_state.get(rel) != sig:
                            files_to_pack.append((item, rel))
                            new_state[rel] = sig

            up_dir = conv_dir / ".user_uploaded"
            if up_dir.exists() and up_dir.is_dir():
                for up_file in up_dir.iterdir():
                    if up_file.is_file() and up_file.stat().st_size < 10 * 1024 * 1024:
                        rel = f"brain/{cid}/.user_uploaded/{up_file.name}"
                        st = up_file.stat()
                        sig = f"{int(st.st_mtime)}:{st.st_size}"
                        if prev_state.get(rel) != sig:
                            files_to_pack.append((up_file, rel))
                            new_state[rel] = sig

    if not files_to_pack and not force_full:
        return 0

    pc_index = build_pc_conversations_index()
    index_bytes = json.dumps(pc_index, ensure_ascii=False, indent=2).encode("utf-8")

    cust_index = build_pc_customizations_index()
    cust_bytes = json.dumps(cust_index, ensure_ascii=False, indent=2).encode("utf-8")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        ti = tarfile.TarInfo(name="pc_conversations_index.json")
        ti.size = len(index_bytes)
        ti.mtime = int(time.time())
        tar.addfile(ti, io.BytesIO(index_bytes))

        ti2 = tarfile.TarInfo(name="pc_customizations_index.json")
        ti2.size = len(cust_bytes)
        ti2.mtime = int(time.time())
        tar.addfile(ti2, io.BytesIO(cust_bytes))

        for local_path, arcname in files_to_pack:
            try:
                tar.add(str(local_path), arcname=arcname)
            except Exception as e:
                print(f"[Atlandı] {local_path}: {e}")

    archive_bytes = buf.getvalue()
    kb = len(archive_bytes) / 1024.0
    print(f"[Senkronizasyon] {len(files_to_pack)} dosya + indeksler paketlendi ({kb:.1f} KB). Ubuntu'ya aktarılıyor...")

    cmd = [
        "ssh",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=no",
        REMOTE_HOST,
        f"mkdir -p {REMOTE_SYNC_DIR} && tar -xzf - -C {REMOTE_SYNC_DIR}",
    ]
    res = subprocess.run(cmd, input=archive_bytes, capture_output=True)
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", errors="replace")
        print(f"[HATA] SSH aktarımı başarısız: {err}")
        return -1

    save_sync_state(new_state)
    print(f"[OK] Başarıyla senkronize edildi!")
    return len(files_to_pack)


def main():
    if not REMOTE_HOST:
        print("[HATA] AGY_REMOTE_HOST ortam değişkenini ayarlayın (örn: setx AGY_REMOTE_HOST ubuntu@203.0.113.10)")
        sys.exit(1)
    loop_mode = "--loop" in sys.argv
    force_full = "--full" in sys.argv
    interval = 30

    if not loop_mode:
        sync_once(force_full=force_full)
        return

    print(f"=== Antigravity PC -> Ubuntu Canlı Senkronizasyon Başladı (Her {interval} sn) ===")
    sync_once(force_full=force_full)
    while True:
        time.sleep(interval)
        try:
            sync_once(force_full=False)
        except Exception as e:
            print(f"[Hata] {e}")


if __name__ == "__main__":
    main()
