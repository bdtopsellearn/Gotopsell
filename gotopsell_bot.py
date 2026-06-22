#!/usr/bin/env python3
"""
Gotopsell Telegram Bot — Full System
- Channel join enforcement
- Instagram & Facebook: 2FA + Big Files
- bKash withdrawal
- Firebase backend
- Broadcast support via webhook
"""

import logging, random, datetime, asyncio
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler,
    ConversationHandler, ChatMemberHandler
)
from telegram.error import BadRequest, Forbidden
import firebase_admin
from firebase_admin import credentials, firestore
from aiohttp import web
import json, threading

# ══════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════
BOT_TOKEN  = "YOUR_BOT_TOKEN_HERE"
ADMIN_IDS  = [123456789]
FIREBASE_CREDENTIALS_PATH = "firebase_credentials.json"
BROADCAST_PORT = 8080   # local port for admin panel broadcast webhook

# ══════════════════════════════════════════════
#  FIREBASE
# ══════════════════════════════════════════════
cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
firebase_admin.initialize_app(cred)
db = firestore.client()

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  STATES
# ══════════════════════════════════════════════
AWAIT_2FA          = 1
AWAIT_BIGFILES     = 2
AWAIT_BKASH_NUMBER = 3

# ══════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════
def gen_username(platform: str) -> str:
    n = random.randint(10000, 999999)
    return f"gotopsell{n}" if platform == "instagram" else f"gtpsell{n}"

def get_settings() -> dict:
    doc = db.collection("settings").document("global").get()
    defaults = {
        "daily_password_instagram": "Pass@1234",
        "daily_password_facebook":  "Pass@5678",
        "instagram_2fa_rate":  2.90,
        "instagram_big_rate":  1.50,
        "facebook_2fa_rate":   2.50,
        "facebook_big_rate":   1.20,
        "min_withdraw":        10.0,
        "referral_enabled":    False,
        "top_enabled":         False,
        "bot_active":          True,
        "required_channels":   [],   # list of channel usernames e.g. ["@mychannel"]
    }
    if doc.exists:
        d = doc.to_dict()
        defaults.update(d)
    return defaults

def get_user(user_id: int, tg_user=None) -> dict:
    ref = db.collection("users").document(str(user_id))
    doc = ref.get()
    if not doc.exists:
        data = {
            "id": user_id,
            "username":  tg_user.username  if tg_user else "",
            "full_name": tg_user.full_name if tg_user else "",
            "balance":    0.0,
            "tasks_done": 0,
            "language":  "en",
            "banned":    False,
            "joined":    datetime.datetime.utcnow().isoformat(),
        }
        ref.set(data)
        return data
    return doc.to_dict()

def update_user(user_id: int, data: dict):
    db.collection("users").document(str(user_id)).update(data)

def save_submission(user_id, platform, task_type, username_acc, password, twofa):
    db.collection("submissions").add({
        "user_id": user_id, "platform": platform, "task_type": task_type,
        "username_acc": username_acc, "password": password, "twofa": twofa,
        "submitted_at": datetime.datetime.utcnow().isoformat(),
        "date": datetime.date.today().isoformat(), "status": "pending",
    })

# ══════════════════════════════════════════════
#  CHANNEL JOIN CHECK
# ══════════════════════════════════════════════
async def check_joined(bot, user_id: int) -> tuple[bool, list]:
    """Returns (all_joined, list_of_not_joined_channels)"""
    settings = get_settings()
    channels = settings.get("required_channels", [])
    if not channels:
        return True, []
    not_joined = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked", "banned"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return len(not_joined) == 0, not_joined

async def send_join_prompt(update_or_query, context, not_joined: list):
    """Send channel join message with buttons"""
    buttons = [[InlineKeyboardButton(f"📢 Join {ch}", url=f"https://t.me/{ch.lstrip('@')}")] for ch in not_joined]
    buttons.append([InlineKeyboardButton("✅ I've Joined — Check Again", callback_data="check_join")])
    text = (
        "⚠️ *Channel Join Required!*\n\n"
        "To use this bot you must join our channel(s):\n\n"
        + "\n".join(f"• {ch}" for ch in not_joined) +
        "\n\n👉 Join and then click *I've Joined* below."
    )
    if hasattr(update_or_query, 'message') and update_or_query.message:
        await update_or_query.message.reply_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update_or_query.edit_message_text(text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons))

# ══════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════
MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("💰 Balance"),  KeyboardButton("📋 Tasks")],
        [KeyboardButton("📤 Withdraw"), KeyboardButton("👤 Profile")],
        [KeyboardButton("🌐 Language")],
    ],
    resize_keyboard=True,
)

# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    db_user = get_user(user.id, user)
    if db_user.get("banned"):
        await update.message.reply_text("❌ You are banned from this bot.")
        return
    # Channel check
    joined, not_joined = await check_joined(context.bot, user.id)
    if not joined:
        await send_join_prompt(update, context, not_joined)
        return
    await update.message.reply_text(
        "⭐ *Welcome to Gotopsell Bot!* ⭐\n\n"
        "🔥 *What can this bot do?*\n"
        "• 📋 Complete tasks & earn money\n"
        "• 💸 Fast bKash withdrawals\n"
        "• ⚡ Easy to use interface\n"
        "• 📊 Track your earnings\n\n"
        "👉 *Tap below to get started!*",
        parse_mode="Markdown", reply_markup=MAIN_KB,
    )

# ══════════════════════════════════════════════
#  CHANNEL CHECK CALLBACK
# ══════════════════════════════════════════════
async def cb_check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    joined, not_joined = await check_joined(context.bot, user.id)
    if joined:
        await query.edit_message_text(
            "✅ *You have joined all channels!*\n\nUse /start to begin.",
            parse_mode="Markdown"
        )
    else:
        await send_join_prompt(query, context, not_joined)

# ══════════════════════════════════════════════
#  GUARD: channel check before any action
# ══════════════════════════════════════════════
async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    db_user = get_user(user.id)
    if db_user.get("banned"):
        await update.message.reply_text("❌ You are banned.")
        return False
    joined, not_joined = await check_joined(context.bot, user.id)
    if not joined:
        await send_join_prompt(update, context, not_joined)
        return False
    return True

# ══════════════════════════════════════════════
#  💰 BALANCE
# ══════════════════════════════════════════════
async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context): return
    db_user  = get_user(update.effective_user.id)
    settings = get_settings()
    await update.message.reply_text(
        "💰 *Your Balance*\n\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"💵 Available: *${db_user['balance']:.2f}*\n"
        f"✅ Tasks Done: *{db_user['tasks_done']}*\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Minimum withdrawal: *${settings['min_withdraw']:.2f}*",
        parse_mode="Markdown",
    )

# ══════════════════════════════════════════════
#  📋 TASKS
# ══════════════════════════════════════════════
async def handle_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context): return
    settings = get_settings()
    if not settings.get("bot_active", True):
        await update.message.reply_text("⚠️ Bot is currently under maintenance. Please try later.")
        return
    await update.message.reply_text(
        "📋 *Select Platform*\n\nChoose a platform to see available tasks:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📸 Instagram", callback_data="platform_instagram"),
             InlineKeyboardButton("👍 Facebook",  callback_data="platform_facebook")],
        ]),
    )

async def cb_platform(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    platform = query.data.split("_")[1]
    context.user_data["platform"] = platform
    settings = get_settings()
    if platform == "instagram":
        r2fa = settings.get("instagram_2fa_rate", 2.90)
        rbig = settings.get("instagram_big_rate", 1.50)
        icon = "📸"
    else:
        r2fa = settings.get("facebook_2fa_rate", 2.50)
        rbig = settings.get("facebook_big_rate", 1.20)
        icon = "👍"
    name = platform.capitalize()
    await query.edit_message_text(
        f"{icon} *{name} Tasks*\n\nSelect a task type:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔐 {name} 2FA — ${r2fa:.2f}", callback_data=f"tasktype_{platform}_2fa")],
            [InlineKeyboardButton(f"📦 Big Files — ${rbig:.2f}",  callback_data=f"tasktype_{platform}_big_files")],
        ]),
    )

async def cb_tasktype(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    parts    = query.data.split("_", 2)
    platform = parts[1]
    task     = parts[2]
    context.user_data["platform"] = platform
    context.user_data["task"]     = task
    settings = get_settings()
    name     = platform.capitalize()
    icon     = "📸" if platform == "instagram" else "👍"
    if task == "2fa":
        rate = settings.get(f"{platform}_2fa_rate", 2.90)
        text = (
            f"🔐 *{name} 2FA*  ⏳ Review time: any time\n\n"
            f"📋 Task: 📱 Submit {name} (2FA) *${rate:.2f}*\n\n"
            f"📄 *Description:*\n"
            f"Create a new {name} account using a real mobile device.\n\n"
            f"🔐 *REQUIRED!*\n"
            f"Use the credentials the bot provides.\n\n"
            f"❗ Account must stay active for 24 hours.\n\n"
            f"After registration:\n"
            f"✅ Click *Account Created* → submit 2FA code"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Start Task", callback_data=f"start_{platform}_2fa")]])
    else:
        rate = settings.get(f"{platform}_big_rate", 1.50)
        text = (
            f"📦 *{name} Big Files*  ⏳ Review time: any time\n\n"
            f"📋 Task: Submit multiple accounts *${rate:.2f}* per account\n\n"
            f"📄 *Format* (one per line):\n"
            f"`username:password:2facode`\n\n"
            f"✅ All valid accounts counted automatically."
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Start Task", callback_data=f"start_{platform}_big_files")]])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

async def cb_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    parts    = query.data.split("_", 2)
    platform = parts[1]
    task     = parts[2]
    context.user_data["platform"] = platform
    context.user_data["task"]     = task
    settings = get_settings()
    if task == "2fa":
        pw       = settings.get(f"daily_password_{platform}", "Pass@1234")
        username = gen_username(platform)
        context.user_data["acc_username"] = username
        context.user_data["acc_password"] = pw
        name = platform.capitalize()
        await query.edit_message_text(
            f"✅ *Your Account Credentials*\n\n"
            f"Create a *{name}* account with this info:\n\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"👤 Username: `{username}`\n"
            f"🔑 Password: `{pw}`\n"
            f"━━━━━━━━━━━━━━━━━\n\n"
            f"1️⃣ Create the account\n"
            f"2️⃣ Click *Account Created* below",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Account Created", callback_data="step_account_created")]]),
        )
    else:
        await query.edit_message_text(
            "📦 *Big Files Submission*\n\n"
            "Paste accounts below:\n\n"
            "`username:password:2facode`\n\n"
            "One per line 👇", parse_mode="Markdown",
        )
        return AWAIT_BIGFILES

async def cb_account_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✅ *Account created!*\n\nNow enable 2FA, then click below:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔐 Submit 2FA Code", callback_data="step_ask_2fa")]]),
    )

async def cb_ask_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔐 *Enter your 2FA Code*\n\nType and send your 2FA/backup code 👇",
        parse_mode="Markdown",
    )
    return AWAIT_2FA

async def receive_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    context.user_data["acc_2fa"] = code
    await update.message.reply_text(
        f"✅ 2FA received: `{code}`\n\nClick *Submit Account* to complete:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 Submit Account", callback_data="step_submit")]]),
    )
    return ConversationHandler.END

async def cb_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    user     = query.from_user
    platform = context.user_data.get("platform", "instagram")
    username = context.user_data.get("acc_username", "N/A")
    password = context.user_data.get("acc_password", "N/A")
    twofa    = context.user_data.get("acc_2fa", "N/A")
    save_submission(user.id, platform, "2fa", username, password, twofa)
    name = platform.capitalize()
    for aid in ADMIN_IDS:
        try:
            await query.get_bot().send_message(chat_id=aid,
                text=(f"📥 *New {name} 2FA Submission*\n\n"
                      f"👤 User: `{user.id}` @{user.username or 'N/A'}\n"
                      f"━━━━━━━━━━━━━━━━━\n"
                      f"📱 Username: `{username}`\n"
                      f"🔑 Password: `{password}`\n"
                      f"🔐 2FA: `{twofa}`\n"
                      f"━━━━━━━━━━━━━━━━━\n"
                      f"⏳ Status: *Pending Review*"),
                parse_mode="Markdown")
        except Exception:
            pass
    await query.edit_message_text(
        f"🎉 *Task Submitted!*\n\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📱 Username: `{username}`\n"
        f"🔑 Password: `{password}`\n"
        f"🔐 2FA: `{twofa}`\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"⏳ Under review — balance added after approval.",
        parse_mode="Markdown",
    )

async def receive_bigfiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user     = update.effective_user
    platform = context.user_data.get("platform", "instagram")
    settings = get_settings()
    rate     = settings.get(f"{platform}_big_rate", 1.50)
    lines    = update.message.text.strip().split("\n")
    count, errors = 0, []
    for i, line in enumerate(lines, 1):
        parts = line.strip().split(":")
        if len(parts) >= 2:
            save_submission(user.id, platform, "big_files",
                parts[0].strip(), parts[1].strip(),
                parts[2].strip() if len(parts) > 2 else "")
            count += 1
        else:
            errors.append(i)
    name = platform.capitalize()
    await update.message.reply_text(
        f"📦 *{name} Big Files Submitted*\n\n"
        f"✅ Valid: *{count}*\n"
        f"❌ Invalid: *{len(errors)}*"
        + (f" (line {','.join(map(str,errors))})" if errors else "") +
        f"\n\n💰 Estimated: *${count*rate:.2f}*\n"
        f"⏳ Under review — balance added after approval.",
        parse_mode="Markdown", reply_markup=MAIN_KB,
    )
    return ConversationHandler.END

# ══════════════════════════════════════════════
#  📤 WITHDRAW — bKash only
# ══════════════════════════════════════════════
async def handle_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context): return
    user     = update.effective_user
    db_user  = get_user(user.id)
    settings = get_settings()
    min_w    = settings.get("min_withdraw", 10.0)
    if db_user["balance"] < min_w:
        needed = min_w - db_user["balance"]
        await update.message.reply_text(
            f"📤 *Withdraw*\n\n❌ *Insufficient balance!*\n\n"
            f"💵 Your balance: *${db_user['balance']:.2f}*\n"
            f"📌 Minimum required: *${min_w:.2f}*\n"
            f"⬆️ Need *${needed:.2f}* more.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    await update.message.reply_text(
        f"📤 *Withdraw via bKash*\n\n"
        f"💵 Your balance: *${db_user['balance']:.2f}*\n\n"
        f"📲 Send your *bKash number*:\n"
        f"Example: `01XXXXXXXXX`",
        parse_mode="Markdown",
    )
    return AWAIT_BKASH_NUMBER

async def receive_bkash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user   = update.effective_user
    number = update.message.text.strip().replace("+880","0").replace(" ","")
    if not (number.startswith("01") and len(number)==11 and number.isdigit()):
        await update.message.reply_text(
            "❌ *Invalid bKash number!*\n\nPlease enter valid 11-digit number.\nExample: `01XXXXXXXXX`",
            parse_mode="Markdown",
        )
        return AWAIT_BKASH_NUMBER
    db_user = get_user(user.id)
    db.collection("withdrawals").add({
        "user_id": user.id, "username": user.username or "",
        "full_name": user.full_name, "amount": db_user["balance"],
        "method": "bKash", "bkash_number": number, "status": "pending",
        "requested_at": datetime.datetime.utcnow().isoformat(),
        "date": datetime.date.today().isoformat(),
    })
    for aid in ADMIN_IDS:
        try:
            await update.get_bot().send_message(chat_id=aid,
                text=(f"💸 *bKash Withdrawal Request*\n\n"
                      f"👤 User: `{user.id}` @{user.username or 'N/A'}\n"
                      f"💵 Amount: *${db_user['balance']:.2f}*\n"
                      f"📲 bKash: `{number}`\n"
                      f"⏳ Status: *Pending*"),
                parse_mode="Markdown")
        except Exception:
            pass
    await update.message.reply_text(
        f"✅ *Withdrawal Submitted!*\n\n"
        f"💳 Method: *bKash*\n📲 Number: `{number}`\n"
        f"💵 Amount: *${db_user['balance']:.2f}*\n\n"
        f"⏳ Admin will process soon.",
        parse_mode="Markdown", reply_markup=MAIN_KB,
    )
    return ConversationHandler.END

# ══════════════════════════════════════════════
#  👤 PROFILE & 🌐 LANGUAGE
# ══════════════════════════════════════════════
async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context): return
    user = update.effective_user
    db_user = get_user(user.id)
    await update.message.reply_text(
        "👤 *My Profile*\n\n━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: `{user.id}`\n👤 Name: *{user.full_name}*\n"
        f"📛 Username: @{user.username or 'N/A'}\n"
        f"💰 Balance: *${db_user['balance']:.2f}*\n"
        f"✅ Tasks Done: *{db_user['tasks_done']}*\n"
        f"🌐 Language: *{db_user['language'].upper()}*\n━━━━━━━━━━━━━━━━━",
        parse_mode="Markdown",
    )

async def handle_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, context): return
    await update.message.reply_text(
        "🌐 *Select Language*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🇺🇸 English", callback_data="lang_en"),
             InlineKeyboardButton("🇧🇩 বাংলা",  callback_data="lang_bn")],
            [InlineKeyboardButton("🇸🇦 عربي",   callback_data="lang_ar"),
             InlineKeyboardButton("🇮🇳 हिंदी",  callback_data="lang_hi")],
        ]),
    )

async def cb_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    lang  = query.data.split("_")[1]
    update_user(query.from_user.id, {"language": lang})
    names = {"en":"English 🇺🇸","bn":"বাংলা 🇧🇩","ar":"عربي 🇸🇦","hi":"हिंदी 🇮🇳"}
    await query.edit_message_text(f"✅ Language changed to *{names.get(lang,lang)}*!", parse_mode="Markdown")

# ══════════════════════════════════════════════
#  CALLBACK ROUTER
# ══════════════════════════════════════════════
async def cb_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "check_join":           return await cb_check_join(update, context)
    if data.startswith("platform_"):   return await cb_platform(update, context)
    if data.startswith("tasktype_"):   return await cb_tasktype(update, context)
    if data.startswith("start_"):      return await cb_task_start(update, context)
    if data == "step_account_created": return await cb_account_created(update, context)
    if data == "step_ask_2fa":        return await cb_ask_2fa(update, context)
    if data == "step_submit":         return await cb_submit(update, context)
    if data.startswith("lang_"):       return await cb_lang(update, context)

# ══════════════════════════════════════════════
#  MESSAGE ROUTER
# ══════════════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    routes = {
        "💰 Balance":  handle_balance,
        "📋 Tasks":    handle_tasks,
        "📤 Withdraw": handle_withdraw,
        "👤 Profile":  handle_profile,
        "🌐 Language": handle_language,
    }
    fn = routes.get(text)
    if fn:
        await fn(update, context)
    else:
        await update.message.reply_text("Please use the buttons below.", reply_markup=MAIN_KB)

# ══════════════════════════════════════════════
#  BROADCAST WEBHOOK SERVER
#  Admin panel POSTs to http://localhost:8080/broadcast
# ══════════════════════════════════════════════
_app_ref = None   # set in main()

async def broadcast_handler(request):
    try:
        data   = await request.json()
        secret = data.get("secret", "")
        msg    = data.get("message", "")
        photo  = data.get("photo", "")
        if secret != "GOTOPSELL_BROADCAST_SECRET":
            return web.Response(status=403, text="Forbidden")
        users_snap = db.collection("users").get()
        sent = failed = 0
        bot = _app_ref.bot
        for doc in users_snap:
            uid = doc.to_dict().get("id")
            if not uid: continue
            try:
                if photo:
                    await bot.send_photo(chat_id=uid, photo=photo, caption=msg, parse_mode="Markdown")
                else:
                    await bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                sent += 1
            except (Forbidden, BadRequest):
                failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)   # rate limit safety
        return web.json_response({"sent": sent, "failed": failed})
    except Exception as e:
        return web.Response(status=500, text=str(e))

async def run_webhook_server():
    web_app = web.Application()
    web_app.router.add_post("/broadcast", broadcast_handler)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", BROADCAST_PORT)
    await site.start()
    logger.info(f"📡 Broadcast server running on port {BROADCAST_PORT}")

# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════
def main():
    global _app_ref
    application = Application.builder().token(BOT_TOKEN).build()
    _app_ref = application

    conv_2fa = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_ask_2fa, pattern="^step_ask_2fa$")],
        states={AWAIT_2FA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_2fa)]},
        fallbacks=[], per_message=False,
    )
    conv_big = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_task_start, pattern="^start_.+_big_files$")],
        states={AWAIT_BIGFILES: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bigfiles)]},
        fallbacks=[], per_message=False,
    )
    conv_wd = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📤 Withdraw$"), handle_withdraw)],
        states={AWAIT_BKASH_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bkash)]},
        fallbacks=[], per_message=False,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_2fa)
    application.add_handler(conv_big)
    application.add_handler(conv_wd)
    application.add_handler(CallbackQueryHandler(cb_router))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def post_init(app):
        await run_webhook_server()

    application.post_init = post_init
    logger.info("🚀 Gotopsell Bot running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
