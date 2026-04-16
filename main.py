"""
Кабинет репетитора Котельникова Ильи Станиславовича
FastAPI + JSON + Telegram Bot
"""
import json, os, hashlib, asyncio, threading
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from telegram import Update
    from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
    TG_AVAILABLE = True
except ImportError:
    TG_AVAILABLE = False

# ── CONFIG ──────────────────────────────────────────────────────────────────
BOT_TOKEN      = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")
ADMIN_ID       = int(os.getenv("ADMIN_ID", "0"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ilya2024")
NOTIFY_HOUR    = int(os.getenv("NOTIFY_HOUR", "9"))
TZ_OFFSET      = int(os.getenv("TZ_OFFSET", "3"))

DATA_DIR   = Path("data")
UPLOAD_DIR = Path("static/uploads")
for d in [DATA_DIR, UPLOAD_DIR, Path("static")]:
    d.mkdir(parents=True, exist_ok=True)

STUDENTS_FILE  = DATA_DIR / "students.json"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
PROGRAMS_FILE  = DATA_DIR / "programs.json"
FINANCE_FILE   = DATA_DIR / "finance.json"
TG_CHATS_FILE  = DATA_DIR / "tg_chats.json"

def load_json(p, d):
    try: return json.load(open(p, encoding="utf-8")) if p.exists() else d
    except: return d

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
    f["monthly"][mk]["revenue"] += amount; save_finance(f)

def add_monthly_lesson():
    f = get_finance(); mk = month_key()
    f["monthly"].setdefault(mk, {"revenue": 0, "lessons": 0})
    f["monthly"][mk]["lessons"] += 1; save_finance(f)

tg_app_ref = None

# ── APP ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="Кабинет репетитора")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
if Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

def require_admin(r: Request):
    if r.headers.get("X-Admin-Token","") != hash_pw(ADMIN_PASSWORD):
        raise HTTPException(401, "Нет доступа")

@app.get("/",      response_class=HTMLResponse)
async def root():  return FileResponse("static/cabinet.html")
@app.get("/admin", response_class=HTMLResponse)
async def admin(): return FileResponse("static/admin.html")

# ── AUTH ─────────────────────────────────────────────────────────────────────
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
    if req.password != ADMIN_PASSWORD: raise HTTPException(403)
    return {"ok": True, "token": hash_pw(ADMIN_PASSWORD)}

# ── STUDENTS ─────────────────────────────────────────────────────────────────
@app.get("/api/student/{name}")
async def get_student(name: str):
    s = get_students()
    if name not in s: raise HTTPException(404)
    st = dict(s[name]); st.pop("password", None); return st

@app.get("/api/students")
async def list_students(r: Request):
    require_admin(r)
    s = get_students()
    return [{"name":k, **{kk:vv for kk,vv in v.items() if kk!="password"}} for k,v in s.items()]

def empty_student(name, pw, color, subject, program, topics, lp, sp, sl):
    return {
        "password":pw, "color":color, "subject":subject,
        "program":program, "program_topics":topics,
        "streak":0, "lessonsTotal":0, "avgGrade":0, "doneTasks":0,
        "progress":[{"t":t,"p":0} for t in topics[:6]],
        "achievements":[], "tasks":[], "grades":[], "schedule":[],
        "completed_lessons":[], "materials":[],
        "messages":[{"f":"tutor","text":"Привет! Добро пожаловать в кабинет 👋","time":now_time()}],
        "next":None, "created":today_str(),
        "lesson_price":lp, "subscription_price":sp, "subscription_lessons":sl,
        "current_sub":None, "balance":0, "payments":[], "tg_id":None,
        "recurring_schedule":[]
    }

class NewStudent(BaseModel):
    name:str; password:str="1234"; subject:str=""; program:str="Индивидуальная"
    color:str="#4a7c59"; lesson_price:float=0; subscription_price:float=0; subscription_lessons:int=8

@app.post("/api/admin/students")
async def create_student(req: NewStudent, r: Request):
    require_admin(r)
    s = get_students()
    if req.name in s: raise HTTPException(400, "Уже существует")
    topics = get_programs().get(req.program, [])
    s[req.name] = empty_student(req.name, req.password, req.color,
        req.subject or req.program, req.program, topics,
        req.lesson_price, req.subscription_price, req.subscription_lessons)
    save_students(s); return {"ok":True,"name":req.name}

@app.put("/api/admin/students/{name}")
async def update_student(name:str, data:dict, r:Request):
    require_admin(r)
    s = get_students()
    if name not in s: raise HTTPException(404)
    allowed = {"color","subject","program","streak","lessonsTotal","avgGrade","doneTasks",
               "next","program_topics","progress","lesson_price","subscription_price",
               "subscription_lessons","tg_id","recurring_schedule"}
    for k,v in data.items():
        if k in allowed: s[name][k] = v
    save_students(s); return {"ok":True}

@app.delete("/api/admin/students/{name}")
async def delete_student(name:str, r:Request):
    require_admin(r)
    s = get_students()
    if name not in s: raise HTTPException(404)
    del s[name]; save_students(s); return {"ok":True}

# ── FINANCE ───────────────────────────────────────────────────────────────────
class SubReq(BaseModel):
    student:str; lessons:int=8; price:float=0; paid:bool=True; note:str=""

@app.post("/api/admin/subscription")
async def create_subscription(req:SubReq, r:Request):
    require_admin(r)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]
    st["current_sub"] = {"type":"subscription","lessons_total":req.lessons,"lessons_left":req.lessons,
                          "price":req.price,"paid":req.paid,"date":today_str(),"note":req.note}
    if req.paid and req.price > 0:
        st.setdefault("payments",[])
        st["payments"].insert(0,{"date":today_str(),"type":"Абонемент","amount":req.price,
                                   "lessons":req.lessons,"paid":True,"note":req.note})
        add_monthly_revenue(req.price)
    save_students(s); return {"ok":True}

class PaymentReq(BaseModel):
    student:str; amount:float; payment_type:str="Оплата"; note:str=""

@app.post("/api/admin/payment")
async def add_payment(req:PaymentReq, r:Request):
    require_admin(r)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]
    st.setdefault("payments",[])
    st["payments"].insert(0,{"date":today_str(),"type":req.payment_type,"amount":req.amount,"paid":True,"note":req.note})
    st["balance"] = st.get("balance",0) + req.amount
    add_monthly_revenue(req.amount); save_students(s); return {"ok":True}

@app.post("/api/admin/conduct-lesson")
async def conduct_lesson(data:dict, r:Request):
    require_admin(r)
    name = data.get("student","")
    s = get_students()
    if name not in s: raise HTTPException(404)
    st = s[name]; sub = st.get("current_sub"); msg = ""
    if sub and sub.get("lessons_left",0) > 0:
        sub["lessons_left"] -= 1
        msg = f"Урок списан. Осталось: {sub['lessons_left']}/{sub['lessons_total']}"
        if sub["lessons_left"] == 0: st["current_sub"] = None; msg += " — абонемент закончился!"
    else:
        price = st.get("lesson_price",0); st["balance"] = st.get("balance",0) - price
        msg = f"Разовый урок. {'Долг: '+str(price)+' ₽' if price else ''}"
    st["lessonsTotal"] = st.get("lessonsTotal",0) + 1
    add_monthly_lesson(); save_students(s); return {"ok":True,"message":msg}

@app.post("/api/admin/cancel-lesson")
async def cancel_lesson(data:dict, r:Request):
    require_admin(r)
    name = data.get("student","")
    s = get_students()
    if name not in s: raise HTTPException(404)
    st = s[name]; sub = st.get("current_sub"); msg = ""
    if sub:
        sub["lessons_left"] = min(sub["lessons_left"]+1, sub["lessons_total"])
        msg = f"Урок возвращён. Осталось: {sub['lessons_left']}/{sub['lessons_total']}"
    else:
        price = st.get("lesson_price",0); st["balance"] = st.get("balance",0) + price
        msg = f"Урок отменён. {'Долг уменьшен на '+str(price)+' ₽' if price else ''}"
    st["lessonsTotal"] = max(0, st.get("lessonsTotal",0) - 1)
    f = get_finance(); mk = month_key()
    if mk in f["monthly"]: f["monthly"][mk]["lessons"] = max(0,f["monthly"][mk].get("lessons",0)-1)
    save_finance(f); save_students(s); return {"ok":True,"message":msg}

@app.post("/api/admin/writeoff-debt")
async def writeoff_debt(data:dict, r:Request):
    require_admin(r)
    name = data.get("student","")
    s = get_students()
    if name not in s: raise HTTPException(404)
    st = s[name]; bal = st.get("balance",0)
    if bal >= 0: return {"ok":True,"message":"Долгов нет"}
    debt = -bal; st["balance"] = 0
    st.setdefault("payments",[])
    st["payments"].insert(0,{"date":today_str(),"type":"Списание долга","amount":0,"paid":True,"note":f"Долг {debt:.0f} ₽ списан"})
    save_students(s); return {"ok":True,"message":f"Долг {debt:.0f} ₽ списан"}

@app.get("/api/admin/finance-summary")
async def finance_summary(r:Request):
    require_admin(r)
    s = get_students(); f = get_finance(); mk = month_key()
    md = f["monthly"].get(mk,{"revenue":0,"lessons":0})
    debtors=[]; active_subs=[]; total_debt=total_prepay=0
    for name,st in s.items():
        bal=st.get("balance",0)
        if bal<0: debtors.append({"name":name,"debt":-bal}); total_debt+=-bal
        elif bal>0: total_prepay+=bal
        sub=st.get("current_sub")
        if sub: active_subs.append({"name":name,"lessons_left":sub.get("lessons_left",0),"lessons_total":sub.get("lessons_total",0)})
    return {"month_revenue":md["revenue"],"month_lessons":md["lessons"],"total_debt":total_debt,
            "total_prepay":total_prepay,"debtors":debtors,"active_subscriptions":active_subs,"monthly_history":f["monthly"]}

# ── TASKS ─────────────────────────────────────────────────────────────────────
class TaskReq(BaseModel):
    student:str; title:str; subj:str; due:str=""; priority:str="normal"

@app.post("/api/admin/task")
async def add_task(req:TaskReq, r:Request):
    require_admin(r)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]; st.setdefault("tasks",[])
    tid = max((t["id"] for t in st["tasks"]),default=0)+1
    st["tasks"].append({"id":tid,"title":req.title,"subj":req.subj,
        "due":req.due or str(date.today()),"status":"active","pri":req.priority,
        "photo_url":"","answer_url":"","answer_date":""})
    save_students(s); return {"ok":True,"id":tid}

@app.post("/api/admin/task-with-photo")
async def add_task_with_photo(r:Request,
    student:str=Form(...), title:str=Form(...), subj:str=Form(...),
    due:str=Form(""), priority:str=Form("normal"),
    photo:Optional[UploadFile]=File(None)):
    require_admin(r)
    s = get_students()
    if student not in s: raise HTTPException(404)
    st = s[student]; st.setdefault("tasks",[])
    tid = max((t["id"] for t in st["tasks"]),default=0)+1
    photo_url = ""
    if photo and photo.filename:
        safe = f"task_{student.replace(' ','_')}_{tid}_{photo.filename}"
        with open(UPLOAD_DIR/safe,"wb") as f_: f_.write(await photo.read())
        photo_url = f"/static/uploads/{safe}"
    st["tasks"].append({"id":tid,"title":title,"subj":subj,
        "due":due or str(date.today()),"status":"active","pri":priority,
        "photo_url":photo_url,"answer_url":"","answer_date":""})
    save_students(s)
    tg_id = st.get("tg_id")
    if tg_id and tg_app_ref:
        try:
            bot = tg_app_ref.bot
            await bot.send_message(tg_id, f"📚 Новое задание!\n*{subj}*: {title}\nСрок: {due or 'не указан'}", parse_mode="Markdown")
            if photo_url:
                await bot.send_photo(tg_id, open(UPLOAD_DIR/safe,"rb"), caption="📎 Фото задания")
        except: pass
    return {"ok":True,"id":tid,"photo_url":photo_url}

# Ученик меняет статус на "review" (на проверке)
@app.patch("/api/student/{name}/task/{task_id}/review")
async def task_to_review(name:str, task_id:int):
    s = get_students()
    if name not in s: raise HTTPException(404)
    for t in s[name].get("tasks",[]):
        if t["id"] == task_id:
            t["status"] = "review"; break
    save_students(s)
    # уведомляем репетитора
    if ADMIN_ID and tg_app_ref:
        try:
            task = next((t for t in s[name].get("tasks",[]) if t["id"]==task_id), {})
            await tg_app_ref.bot.send_message(ADMIN_ID,
                f"📬 *{name}* сдал(а) задание на проверку!\n*{task.get('subj','')}*: {task.get('title','')}",
                parse_mode="Markdown")
        except: pass
    return {"ok":True}

# Репетитор одобряет (done) или возвращает (active)
@app.patch("/api/admin/task/{name}/{task_id}/status")
async def set_task_status(name:str, task_id:int, data:dict, r:Request):
    require_admin(r)
    s = get_students()
    if name not in s: raise HTTPException(404)
    new_status = data.get("status","done")
    for t in s[name].get("tasks",[]):
        if t["id"] == task_id:
            t["status"] = new_status
            if new_status == "done": s[name]["doneTasks"] = s[name].get("doneTasks",0)+1
            break
    save_students(s); return {"ok":True}

# Сдать фото ответа
@app.post("/api/student/{name}/task/{task_id}/answer")
async def submit_answer(name:str, task_id:int, photo:UploadFile=File(...)):
    s = get_students()
    if name not in s: raise HTTPException(404)
    task = next((t for t in s[name].get("tasks",[]) if t["id"]==task_id), None)
    if not task: raise HTTPException(404)
    safe = f"answer_{name.replace(' ','_')}_{task_id}_{photo.filename}"
    with open(UPLOAD_DIR/safe,"wb") as f_: f_.write(await photo.read())
    task["answer_url"] = f"/static/uploads/{safe}"
    task["answer_date"] = today_str()
    task["status"] = "review"
    save_students(s)
    if ADMIN_ID and tg_app_ref:
        try:
            await tg_app_ref.bot.send_message(ADMIN_ID,
                f"📬 *{name}* сдал(а) задание!\n*{task.get('subj','')}*: {task.get('title','')}",
                parse_mode="Markdown")
            await tg_app_ref.bot.send_photo(ADMIN_ID, open(UPLOAD_DIR/safe,"rb"),
                caption=f"Ответ: {name}")
        except: pass
    return {"ok":True,"answer_url":task["answer_url"]}

# ── GRADES ────────────────────────────────────────────────────────────────────
class GradeReq(BaseModel):
    student:str; subject:str; topic:str; grade_type:str="Оценка"; grade:int; comment:str=""

@app.post("/api/admin/grade")
async def add_grade(req:GradeReq, r:Request):
    require_admin(r)
    if req.grade not in (2,3,4,5): raise HTTPException(400)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]; st.setdefault("grades",[])
    st["grades"].insert(0,{"d":today_str(),"s":req.subject,"t":req.topic,
                            "tp":req.grade_type,"g":req.grade,"c":req.comment})
    g = st["grades"]
    st["avgGrade"]=round(sum(x["g"] for x in g[:10])/min(len(g),10),1)
    save_students(s); return {"ok":True}

# ── SCHEDULE ──────────────────────────────────────────────────────────────────
class LessonReq(BaseModel):
    student:str; day:str; time:str; subject:str; topic:str=""; color:str="#4a7c59"
    zoom_link:str=""; recurring:bool=False

@app.post("/api/admin/lesson")
async def add_lesson(req:LessonReq, r:Request):
    require_admin(r)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]; st.setdefault("schedule",[])
    lesson = {"day":req.day,"time":req.time,"name":req.subject,"topic":req.topic,
               "color":req.color,"zoom_link":req.zoom_link,"status":"planned",
               "id":len(st["schedule"])+1,"materials":[]}
    st["schedule"].append(lesson)
    if req.recurring:
        st.setdefault("recurring_schedule",[])
        # Определяем день недели из поля day
        day_lower = req.day.lower()
        dow = next((d for d in ["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"] if d in day_lower), "")
        st["recurring_schedule"].append({"day_of_week":dow,"time":req.time,"name":req.subject,
                                           "topic":req.topic,"color":req.color,"zoom_link":req.zoom_link})
    st["next"]={"day":req.day,"time":req.time.split("–")[0].strip(),"name":req.subject,
                "topic":req.topic,"zoom_link":req.zoom_link}
    save_students(s); return {"ok":True}

@app.patch("/api/admin/lesson/{name}/{lesson_id}/complete")
async def complete_lesson(name:str, lesson_id:int, r:Request,
    action:str=Form("complete"), topic:str=Form(""), materials:str=Form(""),
    grade:int=Form(0), comment:str=Form(""), subject:str=Form(""),
    file:Optional[UploadFile]=File(None),
    transfer_to:str=Form("")):
    require_admin(r)
    s = get_students()
    if name not in s: raise HTTPException(404)
    file_url = ""
    if file and file.filename:
        safe = f"lesson_{name.replace(' ','_')}_{lesson_id}_{file.filename}"
        with open(UPLOAD_DIR/safe,"wb") as f_: f_.write(await file.read())
        file_url = f"/static/uploads/{safe}"
    for l in s[name].get("schedule",[]):
        if l.get("id") == lesson_id:
            l["status"] = action  # "completed", "cancelled", "transferred"
            l["completed_date"] = today_str()
            if topic: l["completed_topic"] = topic
            if materials: l["materials"] = materials
            if file_url: l["material_file"] = file_url; l["material_file_name"] = file.filename if file else ""
            if transfer_to: l["transferred_to"] = transfer_to
            break
    if action == "completed":
        s[name]["lessonsTotal"] = s[name].get("lessonsTotal",0)+1
        add_monthly_lesson()
        # Оценка за занятие
        if grade and grade in (2,3,4,5):
            s[name].setdefault("grades",[])
            s[name]["grades"].insert(0,{"d":today_str(),"s":subject or "Занятие","t":topic or "Урок",
                                        "tp":"Занятие","g":grade,"c":comment})
            g = s[name]["grades"]
            s[name]["avgGrade"]=round(sum(x["g"] for x in g[:10])/min(len(g),10),1)
    elif action == "cancelled":
        # Возврат в абонемент
        sub = s[name].get("current_sub")
        if sub: sub["lessons_left"] = min(sub["lessons_left"]+1, sub["lessons_total"])
    save_students(s); return {"ok":True, "file_url": file_url}

# Перенос занятия
@app.patch("/api/admin/lesson/{name}/{lesson_id}/transfer")
async def transfer_lesson(name:str, lesson_id:int, data:dict, r:Request):
    require_admin(r)
    s = get_students()
    if name not in s: raise HTTPException(404)
    new_day = data.get("new_day",""); new_time = data.get("new_time","")
    for l in s[name].get("schedule",[]):
        if l.get("id") == lesson_id:
            l["status"] = "transferred"
            l["transferred_to"] = f"{new_day} {new_time}".strip()
            # Создаём новое занятие с теми же данными
            new_id = max((x.get("id",0) for x in s[name]["schedule"]),default=0)+1
            new_lesson = dict(l)
            new_lesson["id"] = new_id; new_lesson["day"] = new_day or l["day"]
            new_lesson["time"] = new_time or l["time"]; new_lesson["status"] = "planned"
            del new_lesson["transferred_to"]
            s[name]["schedule"].append(new_lesson)
            break
    save_students(s); return {"ok":True}

# Удалить оценку
@app.delete("/api/admin/grade/{name}/{grade_index}")
async def delete_grade(name:str, grade_index:int, r:Request):
    require_admin(r)
    s = get_students()
    if name not in s: raise HTTPException(404)
    grades = s[name].get("grades",[])
    if 0 <= grade_index < len(grades):
        grades.pop(grade_index)
        if grades: s[name]["avgGrade"]=round(sum(x["g"] for x in grades[:10])/min(len(grades),10),1)
        else: s[name]["avgGrade"]=0
    save_students(s); return {"ok":True}

# Расширенная статистика по ученику
@app.get("/api/admin/student-stats/{name}")
async def student_stats(name:str, r:Request):
    require_admin(r)
    s = get_students()
    if name not in s: raise HTTPException(404)
    st = s[name]
    schedule = st.get("schedule",[])
    completed = [l for l in schedule if l.get("status")=="completed"]
    cancelled  = [l for l in schedule if l.get("status")=="cancelled"]
    transferred= [l for l in schedule if l.get("status")=="transferred"]
    tasks_done = [t for t in st.get("tasks",[]) if t.get("status")=="done"]
    tasks_active=[t for t in st.get("tasks",[]) if t.get("status")=="active"]
    tasks_review=[t for t in st.get("tasks",[]) if t.get("status")=="review"]
    grades = st.get("grades",[])
    return {
        "name": name,
        "lessons_completed": len(completed),
        "lessons_cancelled":  len(cancelled),
        "lessons_transferred":len(transferred),
        "tasks_done":   len(tasks_done),
        "tasks_active": len(tasks_active),
        "tasks_review": len(tasks_review),
        "avg_grade":    st.get("avgGrade",0),
        "grades":       grades,
        "recent_completed": completed[-5:],
        "payments":     st.get("payments",[])[:5],
        "current_sub":  st.get("current_sub"),
        "balance":      st.get("balance",0),
    }

# Генератор занятий на 4 недели вперёд из постоянного расписания
@app.post("/api/admin/generate-schedule/{name}")
async def generate_schedule(name:str, r:Request):
    require_admin(r)
    s = get_students()
    if name not in s: raise HTTPException(404)
    st = s[name]
    recurring = st.get("recurring_schedule",[])
    if not recurring: return {"ok":True,"generated":0}
    days_ru = {"понедельник":0,"вторник":1,"среда":2,"четверг":3,"пятница":4,"суббота":5,"воскресенье":6}
    existing_planned = {(l["day"],l["time"]) for l in st.get("schedule",[]) if l.get("status")=="planned"}
    today = date.today()
    generated = 0
    max_id = max((l.get("id",0) for l in st.get("schedule",[])),default=0)
    for rec in recurring:
        day_name = rec.get("day_of_week","").lower()
        day_num = days_ru.get(day_name)
        if day_num is None: continue
        for week in range(4):
            days_ahead = (day_num - today.weekday()) % 7 + week*7
            target = today + __import__('datetime').timedelta(days=days_ahead)
            day_label = f"{['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][day_num]}, {target.strftime('%d.%m')}"
            key = (day_label, rec["time"])
            if key not in existing_planned:
                max_id += 1
                st["schedule"].append({
                    "id":max_id,"day":day_label,"time":rec["time"],
                    "name":rec["name"],"topic":rec.get("topic",""),"color":rec.get("color","#4a7c59"),
                    "zoom_link":rec.get("zoom_link",""),"status":"planned","materials":[]
                })
                existing_planned.add(key)
                generated += 1
    save_students(s)
    return {"ok":True,"generated":generated}

# ── MESSAGES ──────────────────────────────────────────────────────────────────
class MsgReq(BaseModel): student:str; text:str; zoom_link:str=""

@app.post("/api/admin/message")
async def send_message(req:MsgReq, r:Request):
    require_admin(r)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    s[req.student].setdefault("messages",[])
    text = req.text
    if req.zoom_link: text += f"\n🔗 [Ссылка на Zoom]({req.zoom_link})"
    s[req.student]["messages"].append({"f":"tutor","text":text,"time":now_time(),"zoom_link":req.zoom_link})
    save_students(s); return {"ok":True}

@app.post("/api/student/{name}/message")
async def student_message(name:str, req:dict):
    s = get_students()
    if name not in s: raise HTTPException(404)
    s[name].setdefault("messages",[])
    s[name]["messages"].append({"f":"student","text":req.get("text",""),"time":now_time()})
    save_students(s); return {"ok":True}

# ── PROGRESS ──────────────────────────────────────────────────────────────────
class ProgressReq(BaseModel): student:str; topic:str; value:int

@app.post("/api/admin/progress")
async def update_progress(req:ProgressReq, r:Request):
    require_admin(r)
    s = get_students()
    if req.student not in s: raise HTTPException(404)
    st = s[req.student]; st.setdefault("progress",[])
    found = False
    for p in st["progress"]:
        if p["t"]==req.topic: p["p"]=max(0,min(100,req.value)); found=True; break
    if not found: st["progress"].append({"t":req.topic,"p":max(0,min(100,req.value))})
    save_students(s); return {"ok":True}

# ── PROGRAMS ──────────────────────────────────────────────────────────────────
@app.get("/api/programs")
async def get_programs_api(): return get_programs()

@app.post("/api/admin/programs")
async def add_program(data:dict, r:Request):
    require_admin(r)
    progs = get_programs(); name=data.get("name","").strip()
    if not name: raise HTTPException(400)
    progs[name]=data.get("topics",[]); save_json(PROGRAMS_FILE,progs); return {"ok":True}

# ── KNOWLEDGE ─────────────────────────────────────────────────────────────────
@app.get("/api/knowledge")
async def knowledge_list(subject:str="", student:str=""):
    items=get_knowledge()
    if subject: items=[i for i in items if i.get("subject","").lower()==subject.lower()]
    if student: items=[i for i in items if not i.get("for_student") or i.get("for_student")==student]
    return items

@app.post("/api/admin/knowledge")
async def add_knowledge(r:Request,
    title:str=Form(...), subject:str=Form(...), kind:str=Form(...),
    content:str=Form(""), for_student:str=Form(""),
    file:Optional[UploadFile]=File(None)):
    require_admin(r)
    items=get_knowledge()
    item={"id":len(items)+1,"title":title,"subject":subject,"kind":kind,
          "content":content,"for_student":for_student,"date":today_str(),"file_url":""}
    if file and file.filename:
        safe=f"{len(items)+1}_{file.filename}"
        with open(UPLOAD_DIR/safe,"wb") as f_: f_.write(await file.read())
        item["file_url"]=f"/static/uploads/{safe}"; item["file_name"]=file.filename
    items.insert(0,item); save_knowledge(items); return {"ok":True}

@app.delete("/api/admin/knowledge/{item_id}")
async def delete_knowledge(item_id:int, r:Request):
    require_admin(r)
    items=[i for i in get_knowledge() if i["id"]!=item_id]
    save_knowledge(items); return {"ok":True}

class AchReq(BaseModel): student:str; text:str

@app.post("/api/admin/achievement")
async def add_achievement(req:AchReq, r:Request):
    require_admin(r)
    s=get_students()
    if req.student not in s: raise HTTPException(404)
    s[req.student].setdefault("achievements",[])
    s[req.student]["achievements"].insert(0,req.text)
    save_students(s); return {"ok":True}

# ── STUDENT ACTIVITY (for tutor card) ─────────────────────────────────────────
@app.get("/api/admin/student-activity/{name}")
async def student_activity(name:str, r:Request):
    require_admin(r)
    s=get_students()
    if name not in s: raise HTTPException(404)
    st=s[name]
    tasks_active   = [t for t in st.get("tasks",[]) if t.get("status")=="active"]
    tasks_review   = [t for t in st.get("tasks",[]) if t.get("status")=="review"]
    tasks_done     = [t for t in st.get("tasks",[]) if t.get("status")=="done"]
    recent_lessons = [l for l in st.get("schedule",[]) if l.get("status")=="completed"][-5:]
    recent_payments= st.get("payments",[])[:5]
    recent_msgs    = st.get("messages",[])[-5:]
    return {
        "name":name, "color":st.get("color","#4a7c59"),
        "current_sub":st.get("current_sub"),
        "balance":st.get("balance",0),
        "tasks_active":tasks_active, "tasks_review":tasks_review, "tasks_done_count":len(tasks_done),
        "recent_lessons":recent_lessons, "recent_payments":recent_payments, "recent_msgs":recent_msgs
    }

# ── MORNING NOTIFICATIONS ─────────────────────────────────────────────────────
def get_today_label():
    days=["понедельник","вторник","среда","четверг","пятница","суббота","воскресенье"]
    return days[datetime.now().weekday()]

async def send_morning_notifications(bot):
    today=get_today_label(); s=get_students(); found=[]
    for name,st in s.items():
        for l in st.get("schedule",[]):
            if l.get("status","planned")=="planned" and today in l.get("day","").lower():
                found.append((name,st,l))
    if ADMIN_ID:
        if not found:
            try: await bot.send_message(ADMIN_ID,"📅 Сегодня занятий нет.")
            except: pass
        else:
            lines=[f"☀️ *Занятия сегодня ({today_str()}):*\n"]
            for name,st,l in found:
                sub=st.get("current_sub"); bal=st.get("balance",0)
                sub_str=f"📦 {sub['lessons_left']} ур." if sub else "⚠️ нет абон."
                fin_str="✅" if bal>=0 else f"⚠️ долг {-bal:.0f}₽"
                zoom_str=f"\n🔗 {l['zoom_link']}" if l.get("zoom_link") else ""
                lines.append(f"• *{name}* — {l['name']} · {l['time']}\n  {sub_str} · {fin_str}{zoom_str}")
            try: await bot.send_message(ADMIN_ID,"\n".join(lines),parse_mode="Markdown")
            except Exception as e: print(e)
    for name,st,l in found:
        tg_id=st.get("tg_id")
        if not tg_id: continue
        sub=st.get("current_sub"); sub_str=""
        if sub:
            sub_str=f"\n📦 Абонемент: {sub['lessons_left']}/{sub['lessons_total']}"
            if sub["lessons_left"]<=2: sub_str+="\n⚠️ Скоро заканчивается!"
        zoom_str=f"\n🔗 [Войти в Zoom]({l['zoom_link']})" if l.get("zoom_link") else ""
        text=f"☀️ Привет, {name.split()[0]}!\n\nСегодня занятие:\n*{l['name']}*\n🕐 {l['time']}{zoom_str}{sub_str}\n\nУдачи! 💪"
        try: await bot.send_message(tg_id,text,parse_mode="Markdown")
        except Exception as e: print(e)

async def notification_scheduler(bot):
    sent_today=None
    while True:
        now=datetime.utcnow(); local_hour=(now.hour+TZ_OFFSET)%24; today=now.date()
        if local_hour==NOTIFY_HOUR and sent_today!=today:
            sent_today=today; await send_morning_notifications(bot)
        await asyncio.sleep(60)

# ── TELEGRAM BOT ──────────────────────────────────────────────────────────────
def is_admin(u): return u.effective_user.id == ADMIN_ID

async def tg_start(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if uid==ADMIN_ID:
        await update.message.reply_text(
            "👋 Привет, Илья!\n\n"
            "/ученики · /добавить Имя; пароль; Программа\n"
            "/задание Имя; Предмет; Текст; Срок\n"
            "/оценка Имя; Предмет; Тема; Оценка; Комм.\n"
            "/занятие Имя; День; Время; Предмет; Тема; ZoomСсылка\n"
            "/провести Имя · /отменить Имя\n"
            "/абонемент Имя; Уроков; Цена\n"
            "/оплата Имя; Сумма; Комм.\n"
            "/финансы · /долги\n"
            "/прогресс Имя; Тема; %\n"
            "/сообщение Имя; Текст\n"
            "/кабинет Имя · /уведомления"
        )
    else:
        chats=get_tg_chats(); name=chats.get(str(uid))
        if name: await update.message.reply_text(f"👋 Привет, {name.split()[0]}! Буду присылать напоминания.")
        else: await update.message.reply_text("👋 Напиши своё имя точно как у репетитора:\nНапример: `Аня Иванова`",parse_mode="Markdown")

async def tg_text(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    if uid==ADMIN_ID: return
    name=update.message.text.strip(); s=get_students()
    if name in s:
        chats=get_tg_chats(); chats[str(uid)]=name; save_tg_chats(chats)
        s[name]["tg_id"]=uid; save_students(s)
        await update.message.reply_text(f"✅ {name.split()[0]}, готово! Буду присылать напоминания о занятиях 🎓")
    else: await update.message.reply_text("❌ Имя не найдено. Уточни у репетитора.")

async def tg_students(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    s=get_students()
    if not s: await update.message.reply_text("Учеников нет."); return
    lines=["📋 *Ученики:*\n"]
    for name,st in s.items():
        active=sum(1 for t in st.get("tasks",[]) if t.get("status")=="active")
        review=sum(1 for t in st.get("tasks",[]) if t.get("status")=="review")
        sub=st.get("current_sub"); bal=st.get("balance",0)
        sub_str=f"📦 {sub['lessons_left']}/{sub['lessons_total']}" if sub else "нет абон."
        bal_str="✅" if bal>=0 else f"⚠️ {-bal:.0f}₽"
        lines.append(f"• *{name}* | {sub_str} | {bal_str} | акт:{active} пров:{review}")
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def tg_add(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    name=parts[0] if parts else ""; pw=parts[1] if len(parts)>1 else "1234"; prog=parts[2] if len(parts)>2 else "Индивидуальная"
    if not name: await update.message.reply_text("⚠️ `/добавить Имя; пароль; Программа`",parse_mode="Markdown"); return
    s=get_students()
    if name in s: await update.message.reply_text(f"❌ «{name}» уже есть"); return
    topics=get_programs().get(prog,[])
    s[name]=empty_student(name,pw,"#4a7c59",prog,prog,topics,0,0,8)
    save_students(s)
    await update.message.reply_text(f"✅ *{name}* добавлен!\nПрограмма: {prog}\nПароль: `{pw}`",parse_mode="Markdown")

async def tg_task(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if len(parts)<3: await update.message.reply_text("⚠️ `/задание Имя; Предмет; Текст; Срок`",parse_mode="Markdown"); return
    name,subj,title=parts[0],parts[1],parts[2]; due=parts[3] if len(parts)>3 else str(date.today())
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; st.setdefault("tasks",[])
    tid=max((t["id"] for t in st["tasks"]),default=0)+1
    st["tasks"].append({"id":tid,"title":title,"subj":subj,"due":due,"status":"active","pri":"normal","photo_url":"","answer_url":"","answer_date":""})
    save_students(s)
    await update.message.reply_text(f"✅ Задание *{name}*\n{subj}: {title}\nдо {due}",parse_mode="Markdown")

async def tg_grade(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(f"{['🔴','🟠','🟡','🟢'][g-2]} *{g}* — *{name}*\n{subj}: {topic}",parse_mode="Markdown")

async def tg_lesson(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if len(parts)<4: await update.message.reply_text("⚠️ `/занятие Имя; День; Время; Предмет; Тема; ZoomСсылка`",parse_mode="Markdown"); return
    name,day,time_,subj=parts[0],parts[1],parts[2],parts[3]
    topic=parts[4] if len(parts)>4 else ""; zoom=parts[5] if len(parts)>5 else ""
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; st.setdefault("schedule",[])
    lid=len(st["schedule"])+1
    st["schedule"].append({"id":lid,"day":day,"time":time_,"name":subj,"topic":topic,"color":"#4a7c59","zoom_link":zoom,"status":"planned","materials":[]})
    st["next"]={"day":day,"time":time_.split("–")[0].strip(),"name":subj,"topic":topic,"zoom_link":zoom}
    save_students(s)
    await update.message.reply_text(f"📅 *{name}*\n{day} · {time_}\n{subj}: {topic}",parse_mode="Markdown")

async def tg_conduct(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    name=" ".join(ctx.args); s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; sub=st.get("current_sub")
    if sub and sub.get("lessons_left",0)>0:
        sub["lessons_left"]-=1
        msg=f"✅ Урок проведён!\n*{name}*\n{sub['lessons_left']}/{sub['lessons_total']} ур. осталось"
        if sub["lessons_left"]==0: st["current_sub"]=None; msg+="\n⚠️ *Абонемент закончился!*"
    else:
        price=st.get("lesson_price",0); st["balance"]=st.get("balance",0)-price
        bal=st["balance"]; msg=f"✅ Разовый урок — *{name}*\n{'Долг: '+str(-bal)+'₽' if bal<0 else '✅'}"
    st["lessonsTotal"]=st.get("lessonsTotal",0)+1; add_monthly_lesson(); save_students(s)
    await update.message.reply_text(msg,parse_mode="Markdown")

async def tg_cancel(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    name=" ".join(ctx.args); s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]; sub=st.get("current_sub")
    if sub: sub["lessons_left"]=min(sub["lessons_left"]+1,sub["lessons_total"]); msg=f"↩️ Урок возвращён в абонемент. Осталось: {sub['lessons_left']}"
    else: price=st.get("lesson_price",0); st["balance"]=st.get("balance",0)+price; msg=f"↩️ Урок отменён"
    st["lessonsTotal"]=max(0,st.get("lessonsTotal",0)-1); save_students(s)
    await update.message.reply_text(f"*{name}*: {msg}",parse_mode="Markdown")

async def tg_subscription(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    name=parts[0]
    try: lessons=int(parts[1]) if len(parts)>1 else 8
    except: lessons=8
    try: price=float(parts[2]) if len(parts)>2 else 0
    except: price=0
    s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]
    st["current_sub"]={"type":"subscription","lessons_total":lessons,"lessons_left":lessons,"price":price,"paid":price>0,"date":today_str(),"note":""}
    if price>0:
        st.setdefault("payments",[])
        st["payments"].insert(0,{"date":today_str(),"type":"Абонемент","amount":price,"lessons":lessons,"paid":True,"note":""})
        add_monthly_revenue(price)
    save_students(s)
    await update.message.reply_text(f"📦 *{name}*: {lessons} ур. · {price:.0f}₽",parse_mode="Markdown")

async def tg_payment(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";")]
    if len(parts)<2: await update.message.reply_text("⚠️ `/оплата Имя; Сумма; Комм.`",parse_mode="Markdown"); return
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
    await update.message.reply_text(f"💰 *{name}*: +{amount:.0f}₽\n{'Долг: '+str(-bal)+'₽' if bal<0 else '✅'}",parse_mode="Markdown")

async def tg_finance(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    s=get_students(); f=get_finance(); mk=month_key()
    md=f["monthly"].get(mk,{"revenue":0,"lessons":0})
    total_debt=sum(-st.get("balance",0) for st in s.values() if st.get("balance",0)<0)
    lines=[f"💰 *Финансы {datetime.now().strftime('%B')}*\n",
           f"Выручка: *{md['revenue']:.0f}₽*",f"Уроков: *{md['lessons']}*",f"Долги: *{total_debt:.0f}₽*"]
    subs=[n for n,st in s.items() if st.get("current_sub")]
    if subs:
        lines.append("\n📦 Абонементы:")
        for n in subs:
            sub=s[n]["current_sub"]; lines.append(f"  • {n}: {sub['lessons_left']}/{sub['lessons_total']}")
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def tg_debts(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    s=get_students()
    debtors=[(n,st) for n,st in s.items() if st.get("balance",0)<0]
    if not debtors: await update.message.reply_text("✅ Долгов нет!"); return
    lines=["⚠️ *Должники:*\n"]+[f"• *{n}*: {-st['balance']:.0f}₽" for n,st in debtors]
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def tg_message_cmd(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    parts=[p.strip() for p in " ".join(ctx.args).split(";",1)]
    if len(parts)<2: await update.message.reply_text("⚠️ `/сообщение Имя; Текст`",parse_mode="Markdown"); return
    name,text=parts[0],parts[1]; s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    s[name].setdefault("messages",[])
    s[name]["messages"].append({"f":"tutor","text":text,"time":now_time()})
    save_students(s)
    await update.message.reply_text(f"💬 Отправлено *{name}*",parse_mode="Markdown")

async def tg_progress_cmd(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
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
    await update.message.reply_text(f"📈 *{name}* — {topic}: {pct}%",parse_mode="Markdown")

async def tg_cabinet_cmd(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    name=" ".join(ctx.args); s=get_students()
    if name not in s: await update.message.reply_text(f"❌ «{name}» не найден"); return
    st=s[name]
    active=sum(1 for t in st.get("tasks",[]) if t.get("status")=="active")
    review=sum(1 for t in st.get("tasks",[]) if t.get("status")=="review")
    sub=st.get("current_sub"); bal=st.get("balance",0)
    lines=[f"📊 *{name}*\n",f"Программа: {st.get('program','')}",f"Занятий: {st.get('lessonsTotal',0)} | Ср.оценка: {st.get('avgGrade',0)}",f"Задания: {active} акт. / {review} на пров."]
    if sub: lines.append(f"📦 Абонемент: {sub['lessons_left']}/{sub['lessons_total']}")
    lines.append(f"Баланс: {'долг '+str(-bal)+'₽' if bal<0 else str(bal)+'₽'}")
    await update.message.reply_text("\n".join(lines),parse_mode="Markdown")

async def tg_notify_cmd(update:Update, ctx:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await send_morning_notifications(ctx.bot)
    await update.message.reply_text("✅ Уведомления отправлены")

# ── BOT START ─────────────────────────────────────────────────────────────────
def start_bot():
    if not TG_AVAILABLE or BOT_TOKEN=="ВСТАВЬ_ТОКЕН_СЮДА" or ADMIN_ID==0:
        print("⚠️  Telegram бот не настроен."); return
    async def run():
        global tg_app_ref
        tg=ApplicationBuilder().token(BOT_TOKEN).build(); tg_app_ref=tg
        tg.add_handler(CommandHandler("start",        tg_start))
        tg.add_handler(CommandHandler("ученики",      tg_students))
        tg.add_handler(CommandHandler("добавить",     tg_add))
        tg.add_handler(CommandHandler("задание",      tg_task))
        tg.add_handler(CommandHandler("оценка",       tg_grade))
        tg.add_handler(CommandHandler("занятие",      tg_lesson))
        tg.add_handler(CommandHandler("провести",     tg_conduct))
        tg.add_handler(CommandHandler("отменить",     tg_cancel))
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
        await tg.initialize(); await tg.start(); await tg.updater.start_polling()
        await asyncio.Event().wait()
    def thread_runner():
        loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop); loop.run_until_complete(run())
    threading.Thread(target=thread_runner,daemon=True).start()

@app.on_event("startup")
async def on_startup():
    start_bot()
    print("🚀 http://localhost:8000  |  /admin")
