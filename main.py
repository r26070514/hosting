import os
import sys
import json
import time
import shutil
import subprocess
import telebot
from telebot import types
from config import BOT_TOKEN, OWNER_ID, ALLOWED_EXTENSIONS, MAX_FILE_SIZE_MB, HOSTED_DIR, USERS_FILE

bot = telebot.TeleBot(BOT_TOKEN)

# ============ STATE ============
# running processes: {user_id: {bot_name: subprocess.Popen}}
running_processes = {}
# user states for multi-step actions
user_states = {}
# temp file buffers per user during upload: {user_id: {"bot_name": str, "files": {name: bytes}}}
upload_buffer = {}

# ============ USERS / ADMINS ============
def load_users():
    if not os.path.exists(USERS_FILE):
        data = {"admins": [OWNER_ID]}
        with open(USERS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        return data
    with open(USERS_FILE) as f:
        return json.load(f)

def save_users(data):
    with open(USERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def is_owner(uid):
    return uid == OWNER_ID

def is_admin(uid):
    if is_owner(uid):
        return True
    return uid in load_users().get("admins", [])

def add_admin(uid):
    data = load_users()
    if uid not in data["admins"]:
        data["admins"].append(uid)
        save_users(data)
        return True
    return False

def remove_admin(uid):
    data = load_users()
    if uid in data["admins"] and uid != OWNER_ID:
        data["admins"].remove(uid)
        save_users(data)
        return True
    return False

# ============ HELPERS ============
def ensure_dirs():
    os.makedirs(HOSTED_DIR, exist_ok=True)

def user_dir(uid):
    d = os.path.join(HOSTED_DIR, str(uid))
    os.makedirs(d, exist_ok=True)
    return d

def bot_dir(uid, bot_name):
    d = os.path.join(user_dir(uid), bot_name)
    os.makedirs(d, exist_ok=True)
    return d

def list_user_bots(uid):
    d = user_dir(uid)
    return [name for name in os.listdir(d) if os.path.isdir(os.path.join(d, name))]

def is_running(uid, bot_name):
    proc = running_processes.get(uid, {}).get(bot_name)
    return proc is not None and proc.poll() is None

def stop_bot(uid, bot_name):
    proc = running_processes.get(uid, {}).get(bot_name)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        del running_processes[uid][bot_name]
        return True
    return False

def start_bot(uid, bot_name, entry_file="main.py"):
    path = bot_dir(uid, bot_name)
    entry_path = os.path.join(path, entry_file)
    if not os.path.exists(entry_path):
        return False, f"Entry file `{entry_file}` not found in bot folder."

    # create logs
    log_path = os.path.join(path, "bot.log")
    log_file = open(log_path, "ab")

    try:
        proc = subprocess.Popen(
            [sys.executable, entry_path],
            cwd=path,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return False, f"Failed to start: {e}"

    running_processes.setdefault(uid, {})[bot_name] = proc
    return True, f"Started `{bot_name}` (PID: {proc.pid})"

def read_logs(uid, bot_name, lines=30):
    log_path = os.path.join(bot_dir(uid, bot_name), "bot.log")
    if not os.path.exists(log_path):
        return "No logs yet."
    with open(log_path, "rb") as f:
        data = f.read().decode(errors="ignore").splitlines()
    return "\n".join(data[-lines:]) or "No logs yet."

# ============ KEYBOARDS ============
def main_menu(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📂 My Bots", "➕ Add New Bot")
    kb.row("📊 Status", "📖 Help")
    if is_owner(uid):
        kb.row("👑 Admin Panel")
    return kb

def bots_menu(bots, uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for b in bots:
        status = "🟢" if is_running(uid, b) else "🔴"
        buttons.append(types.InlineKeyboardButton(f"{status} {b}", callback_data=f"open:{b}"))
    kb.add(*buttons)
    return kb

def bot_controls(uid, name):
    kb = types.InlineKeyboardMarkup(row_width=2)
    if is_running(uid, name):
        kb.row(types.InlineKeyboardButton("🛑 Stop", callback_data=f"stop:{name}"))
    else:
        kb.row(types.InlineKeyboardButton("▶️ Start", callback_data=f"start:{name}"))
    kb.row(
        types.InlineKeyboardButton("📜 Logs", callback_data=f"logs:{name}"),
        types.InlineKeyboardButton("📁 Files", callback_data=f"files:{name}"),
    )
    kb.row(types.InlineKeyboardButton("🗑 Delete", callback_data=f"del:{name}"))
    kb.row(types.InlineKeyboardButton("🔙 Back", callback_data="back_bots"))
    return kb

def admin_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(
        types.InlineKeyboardButton("➕ Add Admin", callback_data="adm_add"),
        types.InlineKeyboardButton("➖ Remove Admin", callback_data="adm_remove"),
    )
    kb.row(types.InlineKeyboardButton("📋 List Admins", callback_data="adm_list"))
    return kb

# ============ COMMANDS ============
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    uid = msg.from_user.id
    if not is_admin(uid):
        bot.send_message(uid, "⛔ You are not authorized to use this bot.")
        return
    bot.send_message(
        uid,
        f"👋 Welcome {msg.from_user.first_name}!\n\n"
        "This is a **Telegram Bot Hosting Bot**.\n"
        "You can upload bot code files and run them here.",
        reply_markup=main_menu(uid),
        parse_mode="Markdown",
    )

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if not is_admin(msg.from_user.id):
        return
    bot.send_message(msg.chat.id, HELP_TEXT, parse_mode="Markdown")

HELP_TEXT = """
📖 *How to use*

1️⃣ Tap **➕ Add New Bot**
2️⃣ Send a name for your bot (e.g. `mybot`)
3️⃣ Upload the bot's files (one by one).
4️⃣ When done, tap **✅ Finish Upload**.
5️⃣ Go to **📂 My Bots** → select your bot → **▶️ Start**.

📌 *Notes:*
• Entry file must be named `main.py`.
• If your bot has a token, put it inside your `main.py` or a `.env` file you upload.
• Logs are saved in `bot.log` per bot.

👑 Owner commands: `/addadmin <id>`, `/removeadmin <id>`, `/admins`
"""

@bot.message_handler(commands=["addadmin"])
def cmd_addadmin(msg):
    if not is_owner(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(msg, "Usage: /addadmin <user_id>")
        return
    ok = add_admin(int(parts[1]))
    bot.reply_to(msg, "✅ Admin added." if ok else "ℹ️ Already an admin.")

@bot.message_handler(commands=["removeadmin"])
def cmd_removeadmin(msg):
    if not is_owner(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(msg, "Usage: /removeadmin <user_id>")
        return
    ok = remove_admin(int(parts[1]))
    bot.reply_to(msg, "✅ Admin removed." if ok else "ℹ️ Not found or cannot remove owner.")

@bot.message_handler(commands=["admins"])
def cmd_admins(msg):
    if not is_admin(msg.from_user.id):
        return
    data = load_users()
    text = "👑 *Owner:*\n• `{}`\n\n🛡 *Admins:*\n".format(OWNER_ID)
    text += "\n".join(f"• `{a}`" for a in data["admins"] if a != OWNER_ID) or "_(none)_"
    bot.send_message(msg.chat.id, text, parse_mode="Markdown")

# ============ TEXT BUTTON HANDLERS ============
@bot.message_handler(func=lambda m: m.text == "📂 My Bots")
def btn_my_bots(msg):
    if not is_admin(msg.from_user.id): return
    bots = list_user_bots(msg.from_user.id)
    if not bots:
        bot.send_message(msg.chat.id, "📭 You have no bots yet. Use ➕ Add New Bot.")
        return
    bot.send_message(msg.chat.id, "📂 *Your Bots:*", reply_markup=bots_menu(bots, msg.from_user.id), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "➕ Add New Bot")
def btn_add_bot(msg):
    uid = msg.from_user.id
    if not is_admin(uid): return
    user_states[uid] = "awaiting_bot_name"
    bot.send_message(uid, "✏️ Send a name for your new bot (letters, numbers, `_` only):", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📊 Status")
def btn_status(msg):
    uid = msg.from_user.id
    if not is_admin(uid): return
    bots = list_user_bots(uid)
    if not bots:
        bot.send_message(uid, "No bots.")
        return
    lines = ["📊 *Bot Status*\n"]
    for b in bots:
        status = "🟢 Running" if is_running(uid, b) else "🔴 Stopped"
        lines.append(f"• `{b}` — {status}")
    bot.send_message(uid, "\n".join(lines), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "📖 Help")
def btn_help(msg):
    if not is_admin(msg.from_user.id): return
    bot.send_message(msg.chat.id, HELP_TEXT, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👑 Admin Panel")
def btn_admin(msg):
    uid = msg.from_user.id
    if not is_owner(uid): return
    bot.send_message(uid, "👑 *Admin Panel*", reply_markup=admin_menu(), parse_mode="Markdown")

# ============ FILE UPLOAD FLOW ============
@bot.message_handler(content_types=["document"])
def on_document(msg):
    uid = msg.from_user.id
    if not is_admin(uid): return

    state = user_states.get(uid)
    if state not in ("uploading",):
        bot.reply_to(msg, "⚠️ Start with ➕ Add New Bot first.")
        return

    doc = msg.document
    fname = doc.file_name
    ext = os.path.splitext(fname)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        bot.reply_to(msg, f"❌ Extension `{ext}` not allowed.", parse_mode="Markdown")
        return
    if doc.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        bot.reply_to(msg, f"❌ File too large. Max {MAX_FILE_SIZE_MB} MB.")
        return

    file_info = bot.get_file(doc.file_id)
    data = bot.download_file(file_info.file_path)

    buf = upload_buffer.setdefault(uid, {"bot_name": user_states.get(f"{uid}_botname"), "files": {}})
    buf["files"][fname] = data

    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✅ Finish Upload", callback_data="finish_upload"))
    bot.reply_to(
        msg,
        f"📥 Received `{fname}` ({len(data)} bytes)\n"
        f"📦 Total files: {len(buf['files'])}\n\nUpload more or tap Finish.",
        reply_markup=kb,
        parse_mode="Markdown",
    )

# Handle bot-name state before generic text
@bot.message_handler(func=lambda m: user_states.get(m.from_user.id) == "awaiting_bot_name", content_types=["text"])
def on_bot_name(msg):
    uid = msg.from_user.id
    name = msg.text.strip()
    if not name.replace("_", "").replace("-", "").isalnum() or len(name) > 40:
        bot.reply_to(msg, "❌ Invalid name. Use letters, numbers, `_`, `-` (max 40 chars).", parse_mode="Markdown")
        return
    user_states[uid] = "uploading"
    user_states[f"{uid}_botname"] = name
    upload_buffer[uid] = {"bot_name": name, "files": {}}
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton("✅ Finish Upload", callback_data="finish_upload"))
    bot.reply_to(
        msg,
        f"✅ Bot name set: `{name}`\n\n"
        "📤 Now send the bot's files one by one (must include `main.py`).\n"
        "When done, tap **Finish Upload**.",
        reply_markup=kb,
        parse_mode="Markdown",
    )

# ============ CALLBACKS ============
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    uid = call.from_user.id
    if not is_admin(uid):
        bot.answer_callback_query(call.id, "⛔ Not authorized.")
        return
    data = call.data

    # ---- Open bot menu ----
    if data.startswith("open:"):
        name = data.split(":", 1)[1]
        bot.edit_message_text(
            f"🤖 *{name}*\nStatus: {'🟢 Running' if is_running(uid, name) else '🔴 Stopped'}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=bot_controls(uid, name),
            parse_mode="Markdown",
        )
        return

    if data == "back_bots":
        bots = list_user_bots(uid)
        bot.edit_message_text(
            "📂 *Your Bots:*",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=bots_menu(bots, uid),
            parse_mode="Markdown",
        )
        return

    # ---- Start / Stop ----
    if data.startswith("start:"):
        name = data.split(":", 1)[1]
        if is_running(uid, name):
            bot.answer_callback_query(call.id, "Already running.")
            return
        ok, msg_ = start_bot(uid, name)
        bot.answer_callback_query(call.id, msg_)
        bot.edit_message_text(
            f"🤖 *{name}*\nStatus: {'🟢 Running' if is_running(uid, name) else '🔴 Stopped'}",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            reply_markup=bot_controls(uid, name), parse_mode="Markdown",
        )
        return

    if data.startswith("stop:"):
        name = data.split(":", 1)[1]
        stop_bot(uid, name)
        bot.answer_callback_query(call.id, "Stopped.")
        bot.edit_message_text(
            f"🤖 *{name}*\nStatus: 🔴 Stopped",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            reply_markup=bot_controls(uid, name), parse_mode="Markdown",
        )
        return

    # ---- Logs ----
    if data.startswith("logs:"):
        name = data.split(":", 1)[1]
        logs = read_logs(uid, name, 30)
        if len(logs) > 3500:
            logs = logs[-3500:]
        bot.send_message(uid, f"📜 *Logs for {name}*:\n```\n{logs}\n```", parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    # ---- Files ----
    if data.startswith("files:"):
        name = data.split(":", 1)[1]
        path = bot_dir(uid, name)
        files = os.listdir(path)
        text = f"📁 *Files in {name}*:\n" + "\n".join(f"• `{f}`" for f in files)
        bot.send_message(uid, text, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    # ---- Delete ----
    if data.startswith("del:"):
        name = data.split(":", 1)[1]
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("⚠️ Yes, Delete", callback_data=f"delconfirm:{name}"),
            types.InlineKeyboardButton("Cancel", callback_data=f"open:{name}"),
        )
        bot.edit_message_text(
            f"⚠️ Delete bot `{name}` and all its files?",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            reply_markup=kb, parse_mode="Markdown",
        )
        return

    if data.startswith("delconfirm:"):
        name = data.split(":", 1)[1]
        stop_bot(uid, name)
        shutil.rmtree(bot_dir(uid, name), ignore_errors=True)
        bot.answer_callback_query(call.id, "Deleted.")
        bots = list_user_bots(uid)
        bot.edit_message_text(
            "📂 *Your Bots:*",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            reply_markup=bots_menu(bots, uid), parse_mode="Markdown",
        )
        return

    # ---- Finish upload ----
    if data == "finish_upload":
        buf = upload_buffer.get(uid)
        if not buf or not buf["files"]:
            bot.answer_callback_query(call.id, "No files uploaded.")
            return
        if "main.py" not in buf["files"]:
            bot.answer_callback_query(call.id, "Missing main.py!", show_alert=True)
            return
        bname = buf["bot_name"]
        target = bot_dir(uid, bname)
        for fname, content in buf["files"].items():
            with open(os.path.join(target, fname), "wb") as f:
                f.write(content)
        upload_buffer.pop(uid, None)
        user_states.pop(uid, None)
        user_states.pop(f"{uid}_botname", None)
        bot.edit_message_text(
            f"✅ Bot `{bname}` saved with {len(buf['files'])} file(s).\n"
            f"Go to 📂 My Bots to start it.",
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            parse_mode="Markdown",
        )
        return

    # ---- Admin panel ----
    if data == "adm_add":
        bot.send_message(uid, "Send the user ID to add as admin (or use `/addadmin <id>`).", parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "adm_remove":
        bot.send_message(uid, "Use `/removeadmin <id>` to remove an admin.", parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

    if data == "adm_list":
        d = load_users()
        text = "👑 *Admins:*\n" + "\n".join(f"• `{a}`" for a in d["admins"])
        bot.send_message(uid, text, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        return

# ============ ENTRY ============
if __name__ == "__main__":
    ensure_dirs()
    load_users()
    print("🚀 Hosting bot is running...")
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
