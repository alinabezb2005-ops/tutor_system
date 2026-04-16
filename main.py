"""
Сервер кабинета репетитора Ильи Котельникова
FastAPI + JSON-база + Telegram Bot + Финансы + Уведомления
"""

import json, os, hashlib, asyncio, threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from telegram import Update
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
    TG_AVAILABLE = True
except ImportError:
    TG_AVAILABLE = False

# ════════════════════════════════════════════════════
#  КОНФИГ
# ════════════════════════════════════════════════════
BOT_TOKEN      = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")
ADMIN_ID       = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ilya2024")
NOTIFY_HOUR    = int(os.getenv("NOTIFY_HOUR", "9"))
TZ_OFFSET      = int(os.getenv("TZ_OFFSET", "3"))

DATA_DIR   = Path("data")
UPLOAD_DIR = Path("static/uploads")
DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

STUDENTS_FILE  = DATA_DIR / "students.json"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
PROGRAMS_FILE  = DATA_DIR / "programs.json"
FINANCE_FILE   = DATA_DIR / "finance.json"
TG_CHATS_FILE  = DATA_DIR / "tg_chats.json"

def load_json(p, d):
    return json.load(open(p, encoding="utf-8")) if p.exists() else d

def save_json(p, d):
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def get_students():   return load_json(STUDENTS_FILE, {})
def save_students(d): save_json(STUDENTS_FILE, d)
def get_knowledge():  return load_json(KNOWLEDGE_FILE, [])
def save_knowledge(d):save_json(KNOWLEDGE_FILE, d)
def get_finance():    return load_json(FINANCE_FILE, {"monthly": {}})
def save_finance(d):  save_json(FINANCE_FILE, d)
def get_tg_chats():   return load_json(TG_CHATS_FILE, {})
def save_tg_chats(d): save_json(TG_CHATS_FILE, d)
def get_programs():   return load_json(PROGRAMS_FILE, {
    "Математика ЕГЭ профиль": ["Алгебра","Геометрия","Тригонометрия","Производные","Интегралы","Задачи ЕГЭ"],
    "Химия ЕГЭ":              ["Неорганическая химия","Органическая химия","Расчётные задачи","Электрохимия"],
    "Математика базовый":     ["Алгебра","Геометрия","Статистика"],
    "Химия 8-9 класс":        ["Введение в химию","Неорганическая химия","Реакции"],
    "Индивидуальная":         []
})

def hash_pw(pw):   return hashlib.sha256(pw.encode()).hexdigest()
def today_str():   return date.today().strftime("%d.%m")
def now_time():    return datetime.now().strftime("%H:%M")
def month_key():   return date.today().strftime("%Y-%m")

def add_monthly_revenue(amount: float):
    f = get_finance(); mk = month_key()
    f["monthly"].setdefault(mk, {"revenue": 0, "lessons": 0})
    f["monthly"][mk]["revenue"] += amount
    save_finance(f)

def add_monthly_lesson():
    f = get_finance(); mk = month_key()
    f["monthly"].setdefault(mk, {"revenue": 0, "lessons": 0})
    f["monthly"][mk]["lessons"] += 1
    save_finance(f)

# ════════════════════════════════════════════════════
#  FASTAPI APP
# ════════════════════════════════════════════════════
app = FastAPI(title="Кабинет репетитора")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

def require_admin(request: Request):
    if request.headers.get("X-Admin-Token", "") != hash_pw(ADMIN_PASSWORD):
        raise HTTPException(401, "Нет доступа")

@app.get("/",      response_class=HTMLResponse)
async def student_page(): return FileResponse("static/cabinet.html")

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():   return FileResponse("static/admin.html")

# ── Auth ─────────────────────────────────────────────
class LoginReq(BaseModel):    name: str; password: str
class AdminLoginReq(BaseModel): password: str

@app.post("/api/login")
async def login(req: LoginReq):
    s = get_students()
    if req.name not in s: raise HTTPException(404, "Не найден")
    if s[req.name].get("password","1234") != req.password: raise HTTPException(403, "Неверный пароль")
    st = dict(s[req.name]); st.pop("password", None)
    return {"ok": True, "student": st, "name": req.name}

@app.post("/api/admin/login")
async def admin_login(req: AdminLoginReq):
    if req.password != ADMIN_PASSWORD: raise HTTPException(403, "Неверный пароль")
    return {"ok": True, "token": hash_pw(ADMIN_PASSWORD)}

# ── Ученики ──────────────────────────────────────────
@app.get("/api/student/{name}")
async def get_student(name: str):
    s = get_students()
    if name not in s: raise HTTPException(404)
    st = dict(s[name]); st.pop("password", None)
    return st

@app.get("/api/students")
async def list_students(request: Request):
    require_admin(request)
    s = get_students()
    return [{"name":k, **{kk:vv for kk,vv in v.items() if kk != "password"}} for k,v in s.items()]

def empty_student(name, pw, color, subject, program, topics, lesson_price, sub_price, sub_lessons):
    return {
        "password": pw, "color": color, "subject": subject,
        "program": program, "program_topics": topics,
        "streak":0, "lessonsTotal":0, "avgGrade":0, "doneTasks":0,
        "progress": [{"t":t,"p":0} for t in topics[:6]],
        "achievements":[], "tasks":[], "grades":[], "schedule":[],
        "materials":[],
        "messages":[{"f":"tutor","text":"Привет! Добро пожаловать в кабинет 👋","time":now_time()}],
        "next": None, "created": today_str(),
        "lesson_price": lesson_price,
        "subscription_price": sub_price,
        "subscription_lessons": sub_lessons,
        "current_sub": None,
        "balance": 0,
        "payments": [],
        "tg_id": None
    }

class NewStudent(BaseModel):
    name: str; password: str="1234"; subject: str=""
    program: str="Индивидуальная"; color: str="#4a7c59"
    lesson_price: float=0; subscription_price: float=0; subscription_lessons: int=8

@app.post("/api/admin/students")
async def create_student(req: NewStudent, request: Request):
    require_admin(request)
    s = get_students()
    if req.name in s: raise HTTPException(400, "Уже существует")
    topics = get_programs().get(req.program, [])
    s[req.name] = empty_student(req.name, req.password, req.color,
        req.subject or req.program, req.program, topics,
        req.lesson_price, req.subscription_price, req.subscription_lessons)
    save_students(s)
    return {"ok": True, "name": req.name}

@app.put("/api/admin/students/{name}")
async def update_student(name: str, data: dict, request: Request):
    require_admin(request)
    s = get_students()
    if name not in s: raise HTTPException(404)
    allowed = {"color","subject","program","streak","lessonsTotal","avgGrade","doneTasks",
               "next","program_topics","progress","lesson_price","subscription_price","subscription_lessons","tg_id"}
    for k,v in data.items():
        if k in allowed: s[name][k] = v
    save_students(s); return {"ok": True}

@app.delete("/api/admin/students/{name}")
async def delete_student(name: str, request: Request):
    require_admin(request)
    s = get_students()
    if name not in s: raise HTTPException(404)
    del s[name]; save_students(s); return {"ok": True}

# ── Абонементы ───────────────────────────────────────
class SubReq(BaseModel):
    student: str; lessons: int=8; price: float=0; paid: bool=True; note: str=""

@app.post("/api/admin/subscription")
async def create_subscription(req: SubReq, request: Request):
    require_admin(request)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]
    st["current_sub"] = {
        "type":"subscription", "lessons_total": req.lessons,
        "lessons_left": req.lessons, "price": req.price,
        "paid": req.paid, "date": today_str(), "note": req.note
    }
    if req.paid and req.price > 0:
        st.setdefault("payments",[])
        st["payments"].insert(0,{"date":today_str(),"type":"Абонемент",
            "amount":req.price,"lessons":req.lessons,"paid":True,"note":req.note})
        add_monthly_revenue(req.price)
    save_students(s); return {"ok": True}

class PaymentReq(BaseModel):
    student: str; amount: float; payment_type: str="Оплата"; note: str=""

@app.post("/api/admin/payment")
async def add_payment(req: PaymentReq, request: Request):
    require_admin(request)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]
    st.setdefault("payments",[])
    st["payments"].insert(0,{"date":today_str(),"type":req.payment_type,
        "amount":req.amount,"paid":True,"note":req.note})
    st["balance"] = st.get("balance",0) + req.amount
    add_monthly_revenue(req.amount)
    save_students(s); return {"ok": True}

@app.post("/api/admin/conduct-lesson")
async def conduct_lesson(data: dict, request: Request):
    require_admin(request)
    name = data.get("student","")
    s = get_students()
    if name not in s: raise HTTPException(404)
    st = s[name]
    sub = st.get("current_sub")
    msg = ""
    if sub and sub.get("lessons_left",0) > 0:
        sub["lessons_left"] -= 1
        msg = f"Списан урок из абонемента. Осталось: {sub['lessons_left']}/{sub['lessons_total']}"
        if sub["lessons_left"] == 0:
            st["current_sub"] = None
            msg += " — абонемент закончился!"
    else:
        price = st.get("lesson_price",0)
        st["balance"] = st.get("balance",0) - price
        msg = f"Разовый урок. {'Долг: '+str(price)+' ₽' if price else 'Цена не указана'}"
    st["lessonsTotal"] = st.get("lessonsTotal",0) + 1
    add_monthly_lesson()
    save_students(s)
    return {"ok": True, "message": msg}

# ── Финансовая сводка ────────────────────────────────
@app.get("/api/admin/finance-summary")
async def finance_summary(request: Request):
    require_admin(request)
    s = get_students(); f = get_finance(); mk = month_key()
    md = f["monthly"].get(mk, {"revenue":0,"lessons":0})
    debtors, active_subs = [], []
    total_debt = total_prepay = 0
    for name, st in s.items():
        bal = st.get("balance",0)
        if bal < 0:  debtors.append({"name":name,"debt":-bal}); total_debt += -bal
        elif bal > 0: total_prepay += bal
        sub = st.get("current_sub")
        if sub:
            active_subs.append({"name":name,"lessons_left":sub.get("lessons_left",0),
                "lessons_total":sub.get("lessons_total",0),"paid":sub.get("paid",True)})
    return {
        "month_revenue": md["revenue"], "month_lessons": md["lessons"],
        "total_debt": total_debt, "total_prepay": total_prepay,
        "debtors": debtors, "active_subscriptions": active_subs,
        "monthly_history": f["monthly"]
    }

# ── Задания ──────────────────────────────────────────
class TaskReq(BaseModel):
    student:str; title:str; subj:str; due:str=""; priority:str="normal"

@app.post("/api/admin/task")
async def add_task(req: TaskReq, request: Request):
    require_admin(request)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]; st.setdefault("tasks",[])
    tid = max((t["id"] for t in st["tasks"]),default=0)+1
    st["tasks"].append({"id":tid,"title":req.title,"subj":req.subj,
        "due":req.due or str(date.today()),"done":False,"pri":req.priority})
    save_students(s); return {"ok":True,"id":tid}

@app.patch("/api/student/{name}/task/{task_id}")
async def toggle_task(name:str, task_id:int):
    s = get_students()
    if name not in s: raise HTTPException(404)
    for t in s[name].get("tasks",[]):
        if t["id"]==task_id:
            t["done"]=not t["done"]
            if t["done"]: s[name]["doneTasks"]=s[name].get("doneTasks",0)+1
            break
    save_students(s); return {"ok":True}

# ── Оценки ───────────────────────────────────────────
class GradeReq(BaseModel):
    student:str; subject:str; topic:str; grade_type:str="Оценка"; grade:int; comment:str=""

@app.post("/api/admin/grade")
async def add_grade(req: GradeReq, request: Request):
    require_admin(request)
    if req.grade not in (2,3,4,5): raise HTTPException(400)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]; st.setdefault("grades",[])
    st["grades"].insert(0,{"d":today_str(),"s":req.subject,"t":req.topic,
        "tp":req.grade_type,"g":req.grade,"c":req.comment})
    g = st["grades"]
    st["avgGrade"]=round(sum(x["g"] for x in g[:10])/min(len(g),10),1)
    save_students(s); return {"ok":True}

# ── Расписание ───────────────────────────────────────
class LessonReq(BaseModel):
    student:str; day:str; time:str; subject:str; topic:str=""; color:str="#4a7c59"

@app.post("/api/admin/lesson")
async def add_lesson(req: LessonReq, request: Request):
    require_admin(request)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]; st.setdefault("schedule",[])
    st["schedule"].append({"day":req.day,"time":req.time,"name":req.subject,
        "topic":req.topic,"color":req.color})
    st["next"]={"day":req.day,"time":req.time.split("–")[0].strip(),
                "name":req.subject,"topic":req.topic}
    save_students(s); return {"ok":True}

# ── Сообщения ────────────────────────────────────────
class MsgReq(BaseModel): student:str; text:str

@app.post("/api/admin/message")
async def send_message(req: MsgReq, request: Request):
    require_admin(request)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    s[req.student].setdefault("messages",[])
    s[req.student]["messages"].append({"f":"tutor","text":req.text,"time":now_time()})
    save_students(s); return {"ok":True}

@app.post("/api/student/{name}/message")
async def student_message(name:str, req:dict):
    s = get_students()
    if name not in s: raise HTTPException(404)
    s[name].setdefault("messages",[])
    s[name]["messages"].append({"f":"student","text":req.get("text",""),"time":now_time()})
    save_students(s); return {"ok":True}

# ── Прогресс ─────────────────────────────────────────
class ProgressReq(BaseModel): student:str; topic:str; value:int

@app.post("/api/admin/progress")
async def update_progress(req: ProgressReq, request: Request):
    require_admin(request)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]; st.setdefault("progress",[])
    found=False
    for p in st["progress"]:
        if p["t"]==req.topic: p["p"]=max(0,min(100,req.value)); found=True; break
    if not found: st["progress"].append({"t":req.topic,"p":max(0,min(100,req.value))})
    save_students(s); return {"ok":True}

# ── Программы ────────────────────────────────────────
@app.get("/api/programs")
async def get_programs_api(): return get_programs()

@app.post("/api/admin/programs")
async def add_program(data:dict, request:Request):
    require_admin(request)
    progs = get_programs(); name=data.get("name","").strip()
    if not name: raise HTTPException(400)
    progs[name]=data.get("topics",[]); save_json(PROGRAMS_FILE,progs); return {"ok":True}

# ── База знаний ──────────────────────────────────────
@app.get("/api/knowledge")
async def knowledge_list(subject:str="", student:str=""):
    items=get_knowledge()
    if subject: items=[i for i in items if i.get("subject","").lower()==subject.lower()]
    if student: items=[i for i in items if not i.get("for_student") or i.get("for_student")==student]
    return items

@app.post("/api/admin/knowledge")
async def add_knowledge(request:Request,
    title:str=Form(...), subject:str=Form(...), kind:str=Form(...),
    content:str=Form(""), for_student:str=Form(""),
    file:Optional[UploadFile]=File(None)):
    require_admin(request)
    items=get_knowledge()
    item={"id":len(items)+1,"title":title,"subject":subject,"kind":kind,
          "content":content,"for_student":for_student,"date":today_str(),"file_url":""}
    if file and file.filename:
        safe=f"{len(items)+1}_{file.filename}"
        with open(UPLOAD_DIR/safe,"wb") as f: f.write(await file.read())
        item["file_url"]=f"/static/uploads/{safe}"; item["file_name"]=file.filename
    items.insert(0,item); save_knowledge(items); return {"ok":True}

@app.delete("/api/admin/knowledge/{item_id}")
async def delete_knowledge(item_id:int, request:Request):
    require_admin(request)
    items=[i for i in get_knowledge() if i["id"]!=item_id]
    save_knowledge(items); return {"ok":True}

class AchReq(BaseModel): student:str; text:str

@app.post("/api/admin/achievement")
async def add_achievement(req:AchReq, request:Request):
    require_admin(request)
    s=get_students()
    if req.student not in s: raise HTTPException(404)
    s[req.student].setdefault("achievements",[])
    s[req.student]["achievements"].insert(0,req.text)
    save_students(s); return {"ok":True}

# ════════════════════════════════════════════════════
#  УТРЕННИЕ УВЕДОМЛЕНИЯ
# ════════════════════════════════════════════════════
def get_today_label():
    days=["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]
    return days[datetime.now().weekday()]

async def send_morning_notifications(bot):
    today = get_today_label()
    s = get_students()
    lessons_today = []
    for name, st in s.items():
        for lesson in st.get("schedule",[]):
            if today in lesson.get("day","").lower():
                lessons_today.append((name, st, lesson))

    # Уведомление репетитору
    if ADMIN_ID:
        if not lessons_today:
            try: await bot.send_message(ADMIN_ID,"📅 Сегодня занятий нет.")
            except: pass
        else:
            lines=[f"☀️ *Занятия сегодня ({today_str()}):*\n"]
            for name,st,lesson in lessons_today:
                sub=st.get("current_sub")
                bal=st.get("balance",0)
                sub_str=f"📦 {sub['lessons_left']} ур. осталось" if sub else "⚠️ нет абонемента"
                fin_str="✅ оплачено" if bal>=0 else f"⚠️ долг {-bal:.0f} ₽"
                lines.append(f"• *{name}*\n  {lesson['name']} · {lesson['time']}\n  {sub_str} · {fin_str}")
            try: await bot.send_message(ADMIN_ID,"\n".join(lines),parse_mode="Markdown")
            except Exception as e: print(f"Ошибка уведомления репетитору: {e}")

    # Уведомления ученикам
    for name, st, lesson in lessons_today:
        tg_id = st.get("tg_id")
        if not tg_id: continue
        sub = st.get("current_sub")
        sub_str=""
        if sub:
            sub_str=f"\n📦 Абонемент: {sub['lessons_left']} из {sub['lessons_total']} уроков"
            if sub["lessons_left"]<=2: sub_str+="\n⚠️ Скоро закончится — напомни репетитору!"
        text=(f"☀️ Привет, {name.split()[0]}!\n\n"
              f"Сегодня занятие:\n*{lesson['name']}*\n"
              f"🕐 {lesson['time']}{sub_str}\n\nУдачи! 💪")
        try: await bot.send_message(tg_id, text, parse_mode="Markdown")
        except Exception as e: print(f"Ошибка уведомления {name}: {e}")

async def notification_scheduler(bot):
    print(f"⏰ Планировщик: уведомления каждый день в {NOTIFY_HOUR}:00 (UTC+{TZ_OFFSET})")
    sent_today = None
    while True:
        now = datetime.utcnow()
        local_hour = (now.hour + TZ_OFFSET) % 24
        today = now.date()
        if local_hour == NOTIFY_HOUR and sent_today != today:
            sent_today = today
            print(f"📤 Утренние уведомления {today}")
            await send_morning_notifications(bot)
        await asyncio.sleep(60)

# ════════════════════════════════════════════════════
#  TELEGRAM BOT
# ════════════════════════════════════════════════════
def is_admin(u): return u.effective_user.id == ADMIN_ID

async def tg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == ADMIN_ID:
        await update.message.reply_text(
            "👋 Привет, Илья!\n\n"
            "Команды:\n"
            "/ученики · /добавить Имя; пароль; Программа\n"
            "/задание Имя; Предмет; Текст; Срок\n"
            "/оценка Имя; Предмет; Тема; Оценка; Комм.\n"
            "/занятие Имя; День; Время; Предмет; Тема\n\n"
            "💰 Финансы:\n"
            "/провести Имя — провести урок\n"
            "/абонемент Имя; Уроков; Цена\n"
            "/оплата Имя; Сумма; Комментарий\n"
            "/финансы · /долги\n\n"
            "/сообщение Имя; Текст\n"
            "/прогресс Имя; Тема; %\n"
            "/кабинет Имя"
        )
    else:
        chats = get_tg_chats(); name = chats.get(str(uid))
        if name:
            await update.message.reply_text(f"👋 Привет, {name.split()[0]}! Буду присылать напоминания о занятиях.")
        else:
            await update.message.reply_text(
                "👋 Привет!\nНапиши своё имя *точно как у репетитора*, чтобы получать уведомления о занятиях.\nНапример: `Аня Иванова`",
                parse_mode="Markdown")

async def tg_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid == ADMIN_ID: return
    name = update.message.text.strip()
    s = get_students()
    if name in s:
        chats = get_tg_chats(); chats[str(uid)] = name; save_tg_chats(chats)
        s[name]["tg_id"] = uid; save_students(s)
        await update.message.reply_text(f"✅ {name.split()[0]}, готово! Буду присылать напоминания о занятиях каждое утро 🎓")
    else:
        await update.message.reply_text("❌ Имя не найдено. Уточни написание у репетитора.")

async def tg_students(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    s = get_students()
    if not s: await update.message.reply_text("Учеников нет."); return
    lines = ["📋 *Ученики:*\n"]
    for name,st in s.items():
        active = sum(1 for t in st.get("tasks",[]) if not t.get("done"))
        sub = st.get("current_sub")
        sub_str = f"📦 {sub['lessons_left']}/{sub['lessons_total']}" if sub else "нет абон."
        bal = st.get("balance",0)
        bal_str = "✅" if bal>=0 else f"⚠️ долг {-bal:.0f}₽"
        lines.append(f"• *{name}* | {sub_str} | {bal_str} | задания: {active}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def tg_add(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts = [p.strip() for p in " ".join(ctx.args).split(";")]
    name=parts[0] if parts else ""; pw=parts[1] if len(parts)>1 else "1234"; prog=parts[2] if len(parts)>2 else "Индивидуальная"
    if not name: await update.message.reply_text("⚠️ `/добавить Имя; пароль; Программа`",parse_mode="Markdown"); return
    s = get_students()
    if name in s: await update.message.reply_text(f"❌ «{name}» уже есть"); return
    topics = get_programs().get(prog,[])
    s[name] = empty_student(name,pw,"#4a7c59",prog,prog,topics,0,0,8)
    save_students(s)
    await update.message.reply_text(f"✅ *{name}* добавлен!\nПрограмма: {prog} · Пароль: `{pw}`", parse_mode="Markdown")

async def tg_task(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if len(parts)<3: await update.message.reply_text("⚠️ `/задание Имя; Предмет; Текст; Срок`",parse_mode="Markdown"); return
    name,subj,title=parts[0],parts[1],parts[2]; due=parts[3] if len(parts)>3 else str(date.today())
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; st.setdefault("tasks",[])
    tid=max((t["id"] for t in st["tasks"]),default=0)+1
    st["tasks"].append({"id":tid,"title":title,"subj":subj,"due":due,"done":False,"pri":"normal"})
    save_students(s)
    await update.message.reply_text(f"✅ Задание выдано *{name}*\n{subj}: {title}\nдо {due}", parse_mode="Markdown")

async def tg_grade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if len(parts)<4: await update.message.reply_text("⚠️ `/оценка Имя; Предмет; Тема; Оценка; Комм.`",parse_mode="Markdown"); return
    name,subj,topic,g_str=parts[0],parts[1],parts[2],parts[3]; comment=parts[4] if len(parts)>4 else ""
    try: g=int(g_str); assert g in(2,3,4,5)
    except: await update.message.reply_text("❌ Оценка: 2–5"); return
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; st.setdefault("grades",[])
    st["grades"].insert(0,{"d":today_str(),"s":subj,"t":topic,"tp":"Оценка","g":g,"c":comment})
    st["avgGrade"]=round(sum(x["g"] for x in st["grades"][:10])/min(len(st["grades"]),10),1)
    save_students(s)
    await update.message.reply_text(f"{['🔴','🟠','🟡','🟢'][g-2]} *{g}* — *{name}*\n{subj}: {topic}", parse_mode="Markdown")

async def tg_lesson(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if len(parts)<4: await update.message.reply_text("⚠️ `/занятие Имя; День; Время; Предмет; Тема`",parse_mode="Markdown"); return
    name,day,time_,subj=parts[0],parts[1],parts[2],parts[3]; topic=parts[4] if len(parts)>4 else ""
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; st.setdefault("schedule",[])
    st["schedule"].append({"day":day,"time":time_,"name":subj,"topic":topic,"color":"#4a7c59"})
    st["next"]={"day":day,"time":time_.split("–")[0].strip(),"name":subj,"topic":topic}
    save_students(s)
    await update.message.reply_text(f"📅 *{name}*\n{day} · {time_}\n{subj}: {topic}", parse_mode="Markdown")

async def tg_conduct(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    name = " ".join(ctx.args); s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; sub=st.get("current_sub")
    if sub and sub.get("lessons_left",0)>0:
        sub["lessons_left"]-=1
        msg=f"✅ Урок проведён!\n*{name}*\nАбонемент: {sub['lessons_left']}/{sub['lessons_total']} уроков осталось"
        if sub["lessons_left"]==0:
            st["current_sub"]=None
            msg+="\n\n⚠️ *Абонемент закончился!*"
    else:
        price=st.get("lesson_price",0); st["balance"]=st.get("balance",0)-price
        bal=st["balance"]
        msg=f"✅ Разовый урок — *{name}*\n"
        msg+=f"Долг: {-bal:.0f} ₽" if bal<0 else f"Оплачено ✅"
    st["lessonsTotal"]=st.get("lessonsTotal",0)+1
    add_monthly_lesson(); save_students(s)
    await update.message.reply_text(msg, parse_mode="Markdown")

async def tg_subscription(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if not parts[0]: await update.message.reply_text("⚠️ `/абонемент Имя; Уроков; Цена`",parse_mode="Markdown"); return
    name=parts[0]
    try: lessons=int(parts[1]) if len(parts)>1 else 8
    except: lessons=8
    try: price=float(parts[2]) if len(parts)>2 else 0
    except: price=0
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]
    st["current_sub"]={"type":"subscription","lessons_total":lessons,"lessons_left":lessons,
                        "price":price,"paid":price>0,"date":today_str(),"note":""}
    if price>0:
        st.setdefault("payments",[])
        st["payments"].insert(0,{"date":today_str(),"type":"Абонемент","amount":price,"lessons":lessons,"paid":True,"note":""})
        add_monthly_revenue(price)
    save_students(s)
    await update.message.reply_text(f"📦 Абонемент создан!\n*{name}*: {lessons} уроков · {price:.0f} ₽", parse_mode="Markdown")

async def tg_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if len(parts)<2: await update.message.reply_text("⚠️ `/оплата Имя; Сумма; Комментарий`",parse_mode="Markdown"); return
    name=parts[0]
    try: amount=float(parts[1])
    except: await update.message.reply_text("❌ Сумма — число"); return
    note=parts[2] if len(parts)>2 else ""
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; st.setdefault("payments",[])
    st["payments"].insert(0,{"date":today_str(),"type":"Оплата","amount":amount,"paid":True,"note":note})
    st["balance"]=st.get("balance",0)+amount; add_monthly_revenue(amount); save_students(s)
    bal=st["balance"]
    await update.message.reply_text(f"💰 *{name}*: +{amount:.0f} ₽\n{'Долг: '+str(-bal)+'₽' if bal<0 else 'Баланс: +'+str(bal)+'₽'}", parse_mode="Markdown")

async def tg_finance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    s=get_students(); f=get_finance(); mk=month_key()
    md=f["monthly"].get(mk,{"revenue":0,"lessons":0})
    total_debt=sum(-st.get("balance",0) for st in s.values() if st.get("balance",0)<0)
    month_name=datetime.now().strftime("%B")
    lines=[f"💰 *Финансы — {month_name}*\n",
           f"Выручка: *{md['revenue']:.0f} ₽*",
           f"Уроков: *{md['lessons']}*",
           f"Долги учеников: *{total_debt:.0f} ₽*\n"]
    subs=[n for n,st in s.items() if st.get("current_sub")]
    if subs:
        lines.append("📦 *Активные абонементы:*")
        for n in subs:
            sub=s[n]["current_sub"]
            lines.append(f"  • {n}: {sub['lessons_left']}/{sub['lessons_total']} ур.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def tg_debts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    s=get_students()
    debtors=[(n,st) for n,st in s.items() if st.get("balance",0)<0]
    if not debtors: await update.message.reply_text("✅ Долгов нет!"); return
    lines=["⚠️ *Должники:*\n"]+[f"• *{n}*: {-st['balance']:.0f} ₽" for n,st in debtors]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def tg_message_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";",1)]
    if len(parts)<2: await update.message.reply_text("⚠️ `/сообщение Имя; Текст`",parse_mode="Markdown"); return
    name,text=parts[0],parts[1]; s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    s[name].setdefault("messages",[])
    s[name]["messages"].append({"f":"tutor","text":text,"time":now_time()})
    save_students(s)
    await update.message.reply_text(f"💬 Отправлено *{name}*", parse_mode="Markdown")

async def tg_progress_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if len(parts)<3: await update.message.reply_text("⚠️ `/прогресс Имя; Тема; %`",parse_mode="Markdown"); return
    name,topic=parts[0],parts[1]
    try: pct=max(0,min(100,int(parts[2])))
    except: await update.message.reply_text("❌ Процент — число"); return
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; st.setdefault("progress",[])
    found=False
    for p in st["progress"]:
        if p["t"]==topic: p["p"]=pct; found=True; break
    if not found: st["progress"].append({"t":topic,"p":pct})
    save_students(s)
    await update.message.reply_text(f"📈 *{name}* — {topic}: {pct}%", parse_mode="Markdown")

async def tg_cabinet_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    name=" ".join(ctx.args); s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; active=[t for t in st.get("tasks",[]) if not t.get("done")]
    sub=st.get("current_sub"); bal=st.get("balance",0)
    lines=[f"📊 *{name}*\n",f"Программа: {st.get('program','')}",
           f"Занятий: {st.get('lessonsTotal',0)} | Ср.оценка: {st.get('avgGrade',0)}",
           f"Задания: {len(active)} активных"]
    if sub: lines.append(f"📦 Абонемент: {sub['lessons_left']}/{sub['lessons_total']}")
    lines.append(f"Баланс: {'долг '+str(-bal)+'₽' if bal<0 else str(bal)+'₽'}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def tg_notify_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await send_morning_notifications(ctx.bot)
    await update.message.reply_text("✅ Уведомления отправлены")

# ════════════════════════════════════════════════════
#  ЗАПУСК БОТА
# ════════════════════════════════════════════════════
def start_bot():
    if not TG_AVAILABLE or BOT_TOKEN=="ВСТАВЬ_ТОКЕН_СЮДА" or ADMIN_ID==0:
        print("⚠️  Telegram бот не настроен."); return

    async def run():
        tg = ApplicationBuilder().token(BOT_TOKEN).build()
        tg.add_handler(CommandHandler("start",        tg_start))
        tg.add_handler(CommandHandler("ученики",      tg_students))
        tg.add_handler(CommandHandler("добавить",     tg_add))
        tg.add_handler(CommandHandler("задание",      tg_task))
        tg.add_handler(CommandHandler("оценка",       tg_grade))
        tg.add_handler(CommandHandler("занятие",      tg_lesson))
        tg.add_handler(CommandHandler("провести",     tg_conduct))
        tg.add_handler(CommandHandler("абонемент",    tg_subscription))
        tg.add_handler(CommandHandler("оплата",       tg_payment))
        tg.add_handler(CommandHandler("финансы",      tg_finance))
        tg.add_handler(CommandHandler("долги",        tg_debts))
        tg.add_handler(CommandHandler("сообщение",    tg_message_cmd))
        tg.add_handler(CommandHandler("прогресс",     tg_progress_cmd))
        tg.add_handler(CommandHandler("кабинет",      tg_cabinet_cmd))
        tg.add_handler(CommandHandler("уведомления",  tg_notify_cmd))
        tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tg_text))
        asyncio.create_task(notification_scheduler(tg.bot))
        print("✅ Telegram бот запущен")
        await tg.initialize(); await tg.start()
        await tg.updater.start_polling()
        await asyncio.Event().wait()

    def thread_runner():
        loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.run_until_complete(run())
    threading.Thread(target=thread_runner, daemon=True).start()

@app.on_event("startup")
async def on_startup():
    start_bot()
    print("🚀 http://localhost:8000  |  /admin — панель репетитора")
