from fastapi import FastAPI, HTTPException, Request, Form, Depends, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
import sqlite3
from datetime import datetime
import os
import logging
import random
import string
from typing import List, Dict, Optional
import hashlib
import requests

app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="supersecretkey")

DB_PATH = "db/time_tracking.db"

# Проверка на существование дефолт папок
os.makedirs("db", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

# Статик файлы
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Инициализация дб
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            token TEXT UNIQUE NOT NULL,
            department TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user'
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS work_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            hours_worked INTEGER NOT NULL,
            entered_by INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (entered_by) REFERENCES users (id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        points INTEGER NOT NULL DEFAULT 0,
        department TEXT NOT NULL,
        assigned_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        taken_by INTEGER,
        taken_at TEXT,
        completed_at TEXT,
        adjust_comment TEXT,
        FOREIGN KEY (assigned_by) REFERENCES users (id),
        FOREIGN KEY (taken_by) REFERENCES users (id)
        )
    ''')
    c.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_work_log_unique
        ON work_log(user_id, date)
    ''')
    # Добавление суперадмина, если ещё не существует
    password_hash = hashlib.sha256("oreonk35256123".encode()).hexdigest()
    c.execute("SELECT id FROM users WHERE username = ?", ("oreonk",))
    if not c.fetchone():
        c.execute('''
            INSERT INTO users (id, username, token, department, password_hash, role)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (0, "oreonk", "000000", "Администрация", password_hash, "superadmin"))

    conn.commit()
    conn.close()
init_db()

#Данные тг
TELEGRAM_BOT_TOKEN = "7223802681:AAGPqBmjHfSbSNP8V6WzYQgXbDOsgqiay38"
TELEGRAM_CHAT_ID = "-4861559460"
DEPARTMENT_CHATS = {
    "Монтажеры": -4966795783,
    "Корреспонденты": -4605602165,
    "Газета": -0,
    "Операторы": -0,
}

def send_telegram_notification(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

class TimeReport(BaseModel):
    token: str
    date: str
    seconds_worked: int

def get_current_user(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def require_role(*allowed_roles):
    def checker(user=Depends(get_current_user)):
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )
        return user
    return checker

@app.post("/api/track")
def track_time(report: TimeReport):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE token = ?", (report.token,))
    row = c.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    user_id = row[0]
    c.execute("SELECT id FROM work_log WHERE user_id = ? AND date = ?", (user_id, report.date))
    if c.fetchone():
        raise HTTPException(status_code=400, detail="Already submitted")
    c.execute("""
        INSERT INTO work_log (user_id, date, hours_worked, entered_by)
        VALUES (?, ?, ?, ?)
    """, (user_id, report.date, report.hours_worked, user_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    user = request.session.get("user")
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})
    
@app.post("/login")
def login(request: Request, response: Response, username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    c.execute("SELECT id, username, role, department FROM users WHERE username = ? AND password_hash = ?", (username, password_hash))
    row = c.fetchone()
    conn.close()
    if not row:
        return templates.TemplateResponse("login.html", {"request": request, "error": True})
    user_data = {"id": row[0], "username": row[1], "role": row[2], "department": row[3]}
    request.session["user"] = user_data
    return RedirectResponse(url="/", status_code=302)
    
@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)

@app.get("/admin", response_class=HTMLResponse)
def admin_index(request: Request, user=Depends(require_role("admin", "superadmin"))):
    return templates.TemplateResponse("admin_index.html", {"request": request, "user": user})

@app.get("/admin/time_report", response_class=HTMLResponse)
def admin_time_report(request: Request, user=Depends(require_role("admin", "superadmin")),
                      month: Optional[int] = None, year: Optional[int] = None,
                      department: Optional[str] = None, selected_user: Optional[str] = None):
    now = datetime.now()
    month = month if month else now.month
    year = year if year else now.year
    date_prefix = f"{year:04d}-{month:02d}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Список всех пользователей и отделов
    c.execute("SELECT DISTINCT username FROM users ORDER BY username")
    all_users = [row[0] for row in c.fetchall()]
    c.execute("SELECT DISTINCT department FROM users ORDER BY department")
    all_departments = [row[0] for row in c.fetchall()]
    department_users = []
    if department:
        c.execute("SELECT username FROM users WHERE department = ? ORDER BY username", (department,))
        department_users = [row[0] for row in c.fetchall()]
    # Получение отработанных часов
    params = [date_prefix]
    query = '''
        SELECT u.username, u.department, w.date, w.hours_worked
        FROM work_log w
        JOIN users u ON w.user_id = u.id
        WHERE strftime('%Y-%m', w.date) = ?
    '''
    if selected_user:
        query += " AND u.username = ?"
        params.append(selected_user)
    elif department:
        query += " AND u.department = ?"
        params.append(department)
    query += " ORDER BY u.department, u.username, w.date ASC"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    summary: Dict[str, Dict[str, List[Dict]]] = {}
    totals: Dict[str, Dict[str, float]] = {}
    for username_, department_, date, hours in rows:
        if department_ not in summary:
            summary[department_] = {}
            totals[department_] = {}
        if username_ not in summary[department_]:
            summary[department_][username_] = []
            totals[department_][username_] = 0
        summary[department_][username_].append({"date": date, "hours": hours})
        totals[department_][username_] += hours
    return templates.TemplateResponse("admin_time_report.html", {
        "request": request,
        "summary": summary,
        "totals": totals,
        "month": month,
        "year": year,
        "all_users": all_users,
        "selected_user": selected_user,
        "department": department,
        "all_departments": all_departments,
        "department_users": department_users,
        "user": user
    })
    
@app.get("/api/admin/time_report")
def api_admin_time_report(month: int, year: int, department: Optional[str] = None, selected_user: Optional[str] = None):
    date_prefix = f"{year:04d}-{month:02d}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    params = [date_prefix]
    query = '''
        SELECT u.username, u.department, w.date, w.hours_worked
        FROM work_log w
        JOIN users u ON w.user_id = u.id
        WHERE strftime('%Y-%m', w.date) = ?
    '''
    if selected_user:
        query += " AND u.username = ?"
        params.append(selected_user)
    elif department:
        query += " AND u.department = ?"
        params.append(department)
    query += " ORDER BY u.department, u.username, w.date ASC"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    summary: Dict[str, Dict[str, List[Dict]]] = {}
    totals: Dict[str, Dict[str, float]] = {}
    for username_, department_, date, hours in rows:
        summary.setdefault(department_, {}).setdefault(username_, []).append({
            "date": date,
            "hours": hours
        })
        totals.setdefault(department_, {}).setdefault(username_, 0)
        totals[department_][username_] += hours
    return {"summary": summary, "totals": totals}

@app.get("/admin/time_report_async", response_class=HTMLResponse)
def admin_time_report_async(request: Request, user=Depends(require_role("admin", "superadmin")),
                            month: Optional[int] = None, year: Optional[int] = None,
                            department: Optional[str] = None, selected_user: Optional[str] = None):
    now = datetime.now()
    month = month if month else now.month
    year = year if year else now.year
    date_prefix = f"{year:04d}-{month:02d}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    params = [date_prefix]
    query = '''
        SELECT u.username, u.department, w.date, w.hours_worked
        FROM work_log w
        JOIN users u ON w.user_id = u.id
        WHERE strftime('%Y-%m', w.date) = ?
    '''
    if selected_user:
        query += " AND u.username = ?"
        params.append(selected_user)
    elif department:
        query += " AND u.department = ?"
        params.append(department)
    query += " ORDER BY u.department, u.username, w.date ASC"
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    summary: Dict[str, Dict[str, List[Dict]]] = {}
    totals: Dict[str, Dict[str, float]] = {}
    for username_, department_, date, hours in rows:
        if department_ not in summary:
            summary[department_] = {}
            totals[department_] = {}
        if username_ not in summary[department_]:
            summary[department_][username_] = []
            totals[department_][username_] = 0
        summary[department_][username_].append({"date": date, "hours": hours})
        totals[department_][username_] += hours
    return templates.TemplateResponse("admin_time_report_table.html", {
        "request": request,
        "summary": summary,
        "totals": totals
    })

@app.get("/api/users_by_department")
def users_by_department(department: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE department = ? ORDER BY username", (department,))
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return {"users": users}
    
@app.get("/admin/register", response_class=HTMLResponse)
def register_form(request: Request, user=Depends(require_role("admin", "superadmin"))):
    return templates.TemplateResponse("register.html", {
        "request": request,
        "user": user,
        "success": False,
        "token": None
    })
    
@app.post("/admin/register", response_class=HTMLResponse)
def register_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    department: str = Form(...),
    role: Optional[str] = Form(None),
    user=Depends(require_role("admin", "superadmin"))
):
    allowed_departments = {"Монтажеры", "Корреспонденты", "Газета", "Операторы"}
    if department not in allowed_departments:
        raise HTTPException(status_code=400, detail="Недопустимый отдел")
    allowed_roles = {"user", "admin"}
    # Если текущий пользователь — не superadmin, принудительно назначаем "user"
    if user["role"] != "superadmin":
        role = "user"
    elif role not in allowed_roles:
        role = "user"  # fallback, если superadmin не выбрал роль
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    def generate_unique_token():
        while True:
            token = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
            c.execute("SELECT id FROM users WHERE token = ?", (token,))
            if not c.fetchone():
                return token
    token = generate_unique_token()
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    c.execute(
        "INSERT INTO users (username, token, department, password_hash, role) VALUES (?, ?, ?, ?, ?)",
        (username, token, department, password_hash, role)
    )
    conn.commit()
    conn.close()
    return templates.TemplateResponse("register.html", {
        "request": request,
        "success": True,
        "token": token,
        "user": user
    })
    
def pluralize_points(n: int) -> str:
    if n == 1:
        return "балл"
    elif 2 <= n <= 4:
        return "балла"
    else:
        return "баллов" 
        
@app.get("/tasks", response_class=HTMLResponse)
def list_tasks(
    request: Request,
    user=Depends(get_current_user),
    department: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    status: Optional[str] = None
):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # отдел пользователя/суперадмин
    departments = []
    if user["role"] == "superadmin":
        c.execute("SELECT DISTINCT department FROM users ORDER BY department")
        departments = [r[0] for r in c.fetchall()]
        if department not in departments:
            department = departments[0] if departments else None
    else:
        department = user["department"]
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    ym = f"{year:04d}-{month:02d}"
    query = """
        SELECT t.id, t.title, t.description, t.points, t.adjust_comment,
               t.created_at, t.taken_by, t.taken_at, t.completed_at,
               creator.username as creator_username,
               taker.username   as taken_by_username
        FROM tasks t
        JOIN users creator ON t.assigned_by = creator.id
        LEFT JOIN users taker ON t.taken_by = taker.id
        WHERE t.department = ?
          AND strftime('%Y-%m', t.created_at) = ?
    """
    params = [department, ym]
    # фильтрация по роли
    if user["role"] == "user":
        # без статуса показываем только свои или свободные
        if not status:
            query += " AND (t.taken_by IS NULL OR t.taken_by = ?)"
            params.append(user["id"])
    # фильтрация по статусу
    if status == "free":
        query += " AND t.taken_by IS NULL"
    elif status == "taken":
        # только свои взятые
        query += " AND t.taken_by = ?"
        params.append(user["id"])
    elif status == "reviewed":
        # только свои оценённые
        query += " AND t.taken_by = ? AND t.adjust_comment IS NOT NULL"
        params.append(user["id"])
    query += " ORDER BY t.created_at DESC"
    c.execute(query, params)
    tasks = []
    for r in c.fetchall():
        tasks.append({
            "id": r[0], "title": r[1], "description": r[2],
            "points": r[3], "adjust_comment": r[4],
            "created_at": r[5], "taken_by": r[6],
            "taken_at": r[7], "completed_at": r[8],
            "creator_username": r[9], "taken_by_username": r[10]
        })
    conn.close()
    total_points = sum(t["points"] for t in tasks
                       if t["adjust_comment"] and t["completed_at"]
                       and t["completed_at"].startswith(f"{year:04d}-{month:02d}"))
    plural_points = pluralize_points(total_points)
    return templates.TemplateResponse("tasks.html", {
        "request": request,
        "user": user,
        "departments": departments,
        "selected_department": department,
        "year": year, "month": month, "status": status,
        "tasks": tasks,
        "total_points": total_points,
        "plural_points": plural_points,
        "pluralize_points": pluralize_points
    })
    
@app.get("/tasks/create", response_class=HTMLResponse)
def create_task_form(request: Request, user=Depends(require_role("admin", "superadmin"))):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if user["role"] == "superadmin":
        c.execute("SELECT DISTINCT department FROM users ORDER BY department")
        departments = [row[0] for row in c.fetchall()]
        c.execute(
            """
            SELECT id, username, department
            FROM users
            WHERE role = 'user'
            ORDER BY department, username
            """
        )
    else:
        departments = [user["department"]]
        c.execute(
            """
            SELECT id, username, department
            FROM users
            WHERE department = ? AND role = 'user'
            ORDER BY username
            """,
            (user["department"],)
        )
    employees = [
        {"id": row[0], "username": row[1], "department": row[2]}
        for row in c.fetchall()
    ]
    conn.close()
    return templates.TemplateResponse("task_create.html", {
        "request": request, "user": user,
        "departments": departments,
        "employees": employees
    })
    

def send_task_notification(department: str, title: str, description: str):
    chat_id = DEPARTMENT_CHATS.get(department)
    if not chat_id:
        return  # если для отдела нет чата
    text = f"Новая задача для отдела *{department}*:\n{title}\n{description}"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"Ошибка отправки уведомления: {e}")

def send_task_taken_notification(from_department: str, to_department: str, title: str):
    if not from_department or not to_department or from_department == to_department:
        return
    chat_id = DEPARTMENT_CHATS.get(to_department)
    if not chat_id:
        return
    message = (
        f"*Отдел получил задачу от другого отдела*\n"
        f"Отдел-инициатор: *{from_department}*\n"
        f"Задача: *{title}*"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"Ошибка отправки уведомления: {e}")
        
@app.post("/tasks/create", response_class=HTMLResponse)
def create_task(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    points: int = Form(...),
    department: Optional[str] = Form(None),
    assignee_id: Optional[str] = Form(None),
    user=Depends(require_role("admin", "superadmin"))
):
    # Определение отдела
    if user["role"] == "admin":
        department = user["department"]
    else:
        allowed_departments = {"Монтажеры", "Корреспонденты", "Газета", "Операторы"}
        if department not in allowed_departments:
            raise HTTPException(status_code=400, detail="Недопустимый отдел")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    taken_by = None
    taken_at = None
    if assignee_id:
        try:
            assignee_id_int = int(assignee_id)
        except ValueError:
            conn.close()
            raise HTTPException(status_code=400, detail="Некорректный сотрудник")
        c.execute(
            "SELECT id FROM users WHERE id = ? AND department = ? AND role = 'user'",
            (assignee_id_int, department)
        )
        assignee_row = c.fetchone()
        if not assignee_row:
            conn.close()
            raise HTTPException(status_code=400, detail="Выбранный сотрудник не найден в указанном отделе")
        taken_by = assignee_id_int
        taken_at = now

    c.execute("""
        INSERT INTO tasks (
            title, description, points, department, assigned_by,
            created_at, taken_by, taken_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (title, description, points, department, user["id"], now, taken_by, taken_at))
    conn.commit()
    if user["role"] == "superadmin":
        c.execute("SELECT DISTINCT department FROM users ORDER BY department")
        departments = [row[0] for row in c.fetchall()]
    else:
        departments = [user["department"]]

    if user["role"] == "superadmin":
        c.execute(
            """
            SELECT id, username, department
            FROM users
            WHERE role = 'user'
            ORDER BY department, username
            """
        )
    else:
        c.execute(
            """
            SELECT id, username, department
            FROM users
            WHERE department = ? AND role = 'user'
            ORDER BY username
            """,
            (user["department"],)
        )
    employees = [
        {"id": row[0], "username": row[1], "department": row[2]}
        for row in c.fetchall()
    ]
    conn.close()

    send_task_notification(department, title, description)

    return templates.TemplateResponse("task_create.html", {
        "request": request,
        "user": user,
        "departments": departments,
        "employees": employees,
        "success": True
    })
    
@app.post("/tasks/take/{task_id}")
def take_task(task_id: int, user=Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Проверка, свободна ли задача и от отдела пользователя
    c.execute("SELECT department, taken_by, assigned_by, title FROM tasks WHERE id = ?", (task_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Задача не найдена")
    dept, taken, assigned_by, title = row
    if dept != user["department"] or taken is not None:
        conn.close()
        raise HTTPException(403, "Нельзя взять задачу")
    c.execute("SELECT department FROM users WHERE id = ?", (assigned_by,))
    assigned_row = c.fetchone()
    from_department = assigned_row[0] if assigned_row else ""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE tasks SET taken_by = ?, taken_at = ? WHERE id = ?", (user["id"], now, task_id))
    conn.commit()
    conn.close()
    send_task_taken_notification(from_department, dept, title)
    return RedirectResponse(url="/tasks", status_code=303)
    
@app.post("/tasks/complete/{task_id}")
def complete_task(task_id: int, user=Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT taken_by, title FROM tasks WHERE id = ?", (task_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Задача не найдена")
    taken_by, task_title = row
    if taken_by != user["id"]:
        conn.close()
        raise HTTPException(403, "Вы не можете отметить эту задачу")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (now, task_id))
    conn.commit()
    conn.close()
    msg = (
        f"*Задача выполнена*\n"
        f"Пользователь: *{user['username']}*\n"
        f"Отдел: *{user['department']}*\n"
        f"Задача: *{task_title}*\n"
    )
    send_telegram_notification(msg)
    return RedirectResponse(url="/tasks", status_code=303)
    
@app.get("/admin/completed_tasks", response_class=HTMLResponse)
def admin_completed_tasks(
    request: Request,
    user=Depends(require_role("admin", "superadmin")),
    department: Optional[str] = None,
    username: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None
):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT department FROM users ORDER BY department")
    user_departments = [r[0] for r in c.fetchall()]
    c.execute("SELECT DISTINCT department FROM tasks ORDER BY department")
    task_departments = [r[0] for r in c.fetchall()]
    all_departments = sorted({*user_departments, *task_departments})
    # Получаем список отделов и пользователей
    departments = []
    if user["role"] == "superadmin":
        departments = all_departments
        if department not in departments:
            department = departments[0] if departments else None
    else:
        department = user["department"]
    # Получаем пользователей отдела
    c.execute("SELECT username FROM users WHERE department = ? ORDER BY username", (department,))
    users = [r[0] for r in c.fetchall()]
    if username not in users:
        username = users[0] if users else None
    # Фильтрация по дате
    now = datetime.now()
    year = year or now.year
    month = month or now.month
    ym = f"{year:04d}-{month:02d}"
    # Извлекаем задачи пользователя помеченные выполненными
    query = """
        SELECT t.id, t.title, t.points, t.completed_at, t.adjust_comment
        FROM tasks t
        JOIN users u ON t.taken_by = u.id
        WHERE t.department = ?
          AND u.username = ?
          AND t.completed_at IS NOT NULL
          AND strftime('%Y-%m', t.completed_at) = ?
        ORDER BY t.completed_at DESC
    """
    c.execute(query, (department, username, ym))
    tasks = [{"id": r[0], "title": r[1], "points": r[2], "completed_at": r[3], "adjust_comment": r[4]} for r in c.fetchall()]
    # Сумма баллов пользователя за месяц
    total = sum(t["points"] for t in tasks)
    conn.close()
    return templates.TemplateResponse("admin_completed_tasks.html", {
        "request": request,
        "user": user,
        "departments": departments,
        "selected_department": department,
        "users": users,
        "selected_user": username,
        "all_departments": all_departments,
        "year": year,
        "month": month,
        "tasks": tasks,
        "total_points": total,
        "plural_points": pluralize_points(total)
    })
    
@app.post("/admin/adjust_points/{task_id}")
def adjust_points(
    task_id: int,
    request: Request,
    new_points: int = Form(...),
    reason: Optional[str] = Form(None),
    copy_department: Optional[str] = Form(None),
    user=Depends(require_role("admin", "superadmin"))
):
    department = request.query_params.get("department", "")
    username = request.query_params.get("username", "")
    year_str = request.query_params.get("year", "")
    month_str = request.query_params.get("month", "")
    year = int(year_str) if year_str.isdigit() else ""
    month = int(month_str) if month_str.isdigit() else ""
    redirect_url = f"/admin/completed_tasks?department={department}&username={username}"
    if year: redirect_url += f"&year={year}"
    if month: redirect_url += f"&month={month}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT title, description, department, assigned_by, created_at,
               taken_by, taken_at, completed_at
        FROM tasks
        WHERE id = ?
    """, (task_id,))
    task_row = c.fetchone()
    if not task_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    (
        title,
        description,
        original_department,
        assigned_by,
        created_at,
        taken_by,
        taken_at,
        completed_at,
    ) = task_row
    forwarded_department = ""
    if copy_department and copy_department != original_department:
        c.execute(
            """
            SELECT 1
            FROM tasks
            WHERE department = ?
              AND title = ?
              AND description = ?
              AND assigned_by = ?
              AND created_at = ?
            LIMIT 1
            """,
            (copy_department, title, description, assigned_by, created_at),
        )
        duplicate_exists = c.fetchone() is not None
        if duplicate_exists:
            conn.commit()
            conn.close()
            return RedirectResponse(url=redirect_url, status_code=303)
        c.execute("""
            INSERT INTO tasks (
                title, description, points, department, assigned_by,
                created_at, taken_by, taken_at, completed_at, adjust_comment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            title,
            description,
            new_points,
            copy_department,
            assigned_by,
            created_at,
            None,
            None,
            None,
            None,
        ))
        forwarded_department = copy_department
    else:
        c.execute(
            "UPDATE tasks SET points = ?, adjust_comment = ? WHERE id = ?",
            (new_points, (reason or "").strip(), task_id)
        )
    conn.commit()
    conn.close()
    if forwarded_department:
        send_task_taken_notification(original_department, forwarded_department, title)
    return RedirectResponse(url=redirect_url, status_code=303)
    
#Логирование AW
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
def write_daily_log(username: str, date_str: str, seconds: int, is_manual: bool):
    log_path = os.path.join(LOG_DIR, f"{date_str}.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as log_file:
        entry = f"{timestamp} | {username} | {seconds} секунд"
        if is_manual:
            entry += " | ВОЗМОЖНО РУЧНОЙ ВВОД"
        log_file.write(entry + "\n")

#Эндпоинт приёма логов от AW
@app.post("/api/aw_activity")
def report_aw_activity(data: TimeReport):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, username FROM users WHERE token = ?", (data.token,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Пользователь не найден")
    user_id, username = row
    c.execute("SELECT hours_worked FROM work_log WHERE user_id = ? AND date = ?", (user_id, data.date))
    row = c.fetchone()
    previous = row[0] if row else 0
    is_manual = abs(previous - data.seconds_worked) > 400
    if row:
        c.execute(
            "UPDATE work_log SET hours_worked = ?, entered_by = ? WHERE user_id = ? AND date = ?",
            (data.seconds_worked, user_id, user_id, data.date)
        )
    else:
        c.execute(
            "INSERT INTO work_log (user_id, date, hours_worked, entered_by) VALUES (?, ?, ?, ?)",
            (user_id, data.date, data.seconds_worked, user_id)
        )
    conn.commit()
    conn.close()
    write_daily_log(username, data.date, data.seconds_worked, is_manual)
    return {"status": "ok"}
    
#Ввод времени вручную
@app.get("/admin/manual_entry", response_class=HTMLResponse)
def manual_entry_form(request: Request, current_user: dict = Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if current_user["role"] == "superadmin":
        c.execute("SELECT DISTINCT department FROM users")
        departments = [r[0] for r in c.fetchall()]
        c.execute("SELECT id, username, department FROM users")
        users = c.fetchall()
    else:
        departments = [current_user["department"]]
        c.execute("SELECT id, username FROM users WHERE department = ?", (current_user["department"],))
        users = [(r[0], r[1], current_user["department"]) for r in c.fetchall()]
    conn.close()
    return templates.TemplateResponse("manual_entry.html", {
        "request": request,
        "user": current_user,
        "departments": departments,
        "users": users,
        "now": datetime.now()
    })
    
@app.post("/admin/manual_entry")
def submit_manual_entry(
    user_id: int = Form(...),
    date: str = Form(...),
    seconds_worked: int = Form(...),
    current_user: dict = Depends(get_current_user)
):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Проверка, имеет ли право текущий пользователь вносить данные для выбранного user_id
    if current_user["role"] != "superadmin":
        c.execute("SELECT department FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        if not row or row[0] != current_user["department"]:
            conn.close()
            raise HTTPException(status_code=403, detail="Недостаточно прав")
    c.execute("SELECT hours_worked FROM work_log WHERE user_id = ? AND date = ?", (user_id, date))
    row = c.fetchone()
    if row:
        total = row[0] + seconds_worked
        c.execute("""
            UPDATE work_log SET hours_worked = ?, entered_by = ?
            WHERE user_id = ? AND date = ?
        """, (total, current_user["id"], user_id, date))
    else:
        c.execute("""
            INSERT INTO work_log (user_id, date, hours_worked, entered_by)
            VALUES (?, ?, ?, ?)
        """, (user_id, date, seconds_worked, current_user["id"]))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin/manual_entry", status_code=302)
