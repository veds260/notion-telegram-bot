import os
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, BotCommand
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters
)
from datetime import datetime, timedelta, time as dtime
import pytz
import asyncio

# Load environment
load_dotenv()
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
TASKS_DB_ID = os.getenv("TASKS_DB_ID")
TEAM_DB_ID = os.getenv("TEAM_DB_ID")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
NOTION_VERSION = os.getenv("NOTION_VERSION")
HEADERS = {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION}
UA_TZ = pytz.timezone('Europe/Kiev')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_chat.username or "No username"
    await update.message.reply_text(f"Hello {username}! Your chat ID is {chat_id}.")
    print(f"New user: {username}, chat ID: {chat_id}")


user_chat_ids = set()
(ASK_NAME, ASK_DESC, ASK_DATE, ASK_PRIORITY, ASK_CATEGORY, ASK_MEMBER) = range(6)



def get_team_member_id(username):
    url = f"https://api.notion.com/v1/databases/{TEAM_DB_ID}/query"
    res = requests.post(url, headers=HEADERS)
    for r in res.json().get('results', []):
        uname_field = r['properties'].get('Telegram Username', {})
        uname_list = uname_field.get('rich_text') or uname_field.get('title') or uname_field.get('text') or []
        if uname_list:
            plain = uname_list[0].get('plain_text', '').lstrip('@').lower()
            if plain == username.lower():
                print("DEBUG usernames in Notion DB:", [item['properties']['Telegram Username'] for item in res.json()['results']])
                return r['id']
    return None


def fetch_tasks(start=None, end=None, pending=True):
    url = f"https://api.notion.com/v1/databases/{TASKS_DB_ID}/query"
    filters = []
    if start and end:
        filters.append({"property": "Due date", "date": {"on_or_after": start, "on_or_before": end}})
    if pending:
        filters.append({"property": "Status", "status": {"does_not_equal": "Done"}})
    payload = {"filter": {"and": filters}} if filters else {}
    res = requests.post(url, headers=HEADERS, json=payload)
    return res.json().get('results', [])

def mark_task_done(page_id):
    requests.patch(f"https://api.notion.com/v1/pages/{page_id}",
                   headers=HEADERS, json={"properties": {"Status": {"status": {"name": "Done"}}}})

def format_task(task):
    props = task['properties']
    return (
        f"👋 Hello!\n\n"
        f"📌 *Task:* {props['Task name']['title'][0]['plain_text']}\n"
        f"📝 *Description:* {props.get('Description', {}).get('rich_text', [{}])[0].get('plain_text', 'N/A')}\n"
        f"🗓️ *Deadline:* {props.get('Due date', {}).get('date', {}).get('start', 'N/A')}\n"
        f"⚡ *Priority:* {props.get('Priority', {}).get('select', {}).get('name', 'N/A')}\n"
        f"📂 *Category:* {', '.join([cat['name'] for cat in props.get('Task Category', {}).get('multi_select', [])]) or 'N/A'}\n\n"
        f"🚀 Let's get this done! You've got this! 💪"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    chat_id = update.effective_chat.id
    user_chat_ids.add(chat_id)
    member_id = get_team_member_id(username)
    if not member_id:
        await update.message.reply_text("❌ You are not linked in the Team DB.")
        return
    tasks = fetch_tasks()
    assigned = [t for t in tasks if any(r['id'] == member_id for r in t['properties'].get('Assigned To ', {}).get('relation', []))]
    if assigned:
        for t in assigned:
            btn = InlineKeyboardButton("✅ Mark Done", callback_data=f"done:{t['id']}")
            await update.message.reply_text(format_task(t), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[btn]]))
    else:
        await update.message.reply_text("🎉 No tasks assigned.")

async def weektasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    member_id = get_team_member_id(username)
    if not member_id:
        await update.message.reply_text("❌ Not linked in the Team DB.")
        return
    today = datetime.now(UA_TZ).date()
    end_week = today + timedelta(days=6 - today.weekday())
    tasks = fetch_tasks(start=today.isoformat(), end=end_week.isoformat())
    assigned = [t for t in tasks if any(r['id'] == member_id for r in t['properties'].get('Assigned To ', {}).get('relation', []))]
    msg = "\n".join([f"📌 {t['properties']['Task name']['title'][0]['plain_text']}" for t in assigned])
    await update.message.reply_text(f"📅 This week's tasks:\n{msg or '✅ None'}")

async def reminders(context: ContextTypes.DEFAULT_TYPE):
    for chat_id in user_chat_ids:
        username = (await context.bot.get_chat(chat_id)).username
        member_id = get_team_member_id(username)
        if not member_id:
            continue
        tasks = fetch_tasks()
        assigned = [t for t in tasks if any(r['id'] == member_id for r in t['properties'].get('Assigned To ', {}).get('relation', []))]
        for t in assigned:
            btn = InlineKeyboardButton("✅ Mark Done", callback_data=f"done:{t['id']}")
            await context.bot.send_message(chat_id, format_task(t), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[btn]]))

async def mark_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data.startswith("done:"):
        mark_task_done(q.data.split(":")[1])
        await q.edit_message_text("✅ Task marked complete!")

# --- Add Task Flow ---
async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 Enter task name:")
    return ASK_NAME

async def ask_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['task_name'] = update.message.text
    await update.message.reply_text("📄 Enter task description:")
    return ASK_DESC

async def ask_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['description'] = update.message.text
    await update.message.reply_text("📅 Enter due date (YYYY-MM-DD):")
    return ASK_DATE

async def ask_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['due_date'] = update.message.text
    keyboard = [['High', 'Medium', 'Low']]
    await update.message.reply_text("⚡ Select priority:", reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
    return ASK_PRIORITY

async def ask_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['priority'] = update.message.text
    res = requests.get(f"https://api.notion.com/v1/databases/{TASKS_DB_ID}", headers=HEADERS)
    categories = res.json()['properties']['Task Category']['multi_select']['options']
    buttons = [[InlineKeyboardButton(c['name'], callback_data=f"cat:{c['name']}")] for c in categories]
    await update.message.reply_text("📂 Select task category:", reply_markup=InlineKeyboardMarkup(buttons))
    return ASK_CATEGORY

async def assign_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category_name = query.data.split(":")[1]
    context.user_data['category'] = category_name
    res = requests.post(f"https://api.notion.com/v1/databases/{TEAM_DB_ID}/query", headers=HEADERS)
    members = res.json().get('results', [])
    buttons = [[InlineKeyboardButton(m['properties']['Telegram Username']['rich_text'][0]['plain_text'], callback_data=f"assign:{m['id']}")] for m in members]
    await query.message.reply_text("👥 Select assignee:", reply_markup=InlineKeyboardMarkup(buttons))
    return ASK_MEMBER

async def assign_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    member_id = query.data.split(":")[1]
    data = {
        "parent": {"database_id": TASKS_DB_ID},
        "properties": {
            "Task name": {"title": [{"text": {"content": context.user_data['task_name']}}]},
            "Description": {"rich_text": [{"text": {"content": context.user_data['description']}}]},
            "Due date": {"date": {"start": context.user_data['due_date']}},
            "Priority": {"select": {"name": context.user_data['priority']}},
            "Task Category": {"multi_select": [{"name": context.user_data['category']}]},
            "Assigned To ": {"relation": [{"id": member_id}]}
        }
    }
    requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=data)
    await query.edit_message_text("✅ Task added successfully!")
    return ConversationHandler.END

async def set_commands(app):
    commands = [
        BotCommand("start", "Show your tasks"),
        BotCommand("addtask", "Add a new task"),
        BotCommand("weektasks", "Show tasks for this week")
    ]
    await app.bot.set_my_commands(commands)

async def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(lambda app: app.job_queue.start()).build()
    app.add_handler(CommandHandler("start", start))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("weektasks", weektasks))
    app.add_handler(CallbackQueryHandler(mark_complete, pattern="^done:"))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('addtask', add_task)],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_desc)],
            ASK_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_date)],
            ASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_priority)],
            ASK_PRIORITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_category)],
            ASK_CATEGORY: [CallbackQueryHandler(assign_category, pattern="^cat:")],
            ASK_MEMBER: [CallbackQueryHandler(assign_member, pattern="^assign:")]
        },
        fallbacks=[]
    ))

    for hr in range(7, 23, 2):
        app.job_queue.run_daily(reminders, time=dtime(hour=hr, tzinfo=UA_TZ))

    await set_commands(app)
    print("🤖 Bot running...")
    await app.run_polling()



if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.get_event_loop().run_until_complete(main())
