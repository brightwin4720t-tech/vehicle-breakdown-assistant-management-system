import os
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Form, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware 
from pydantic import BaseModel
from typing import List, Dict
import uvicorn
import google.genai as genai
from dotenv import load_dotenv
import time
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import logging
import json
import urllib.request
import urllib.parse

# Load environment variables from .env file (resolved relative to this script's location)
base_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(base_dir, ".env"))

# Configure logging to output to stdout for Render logs visibility
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("VBAMS")


app = FastAPI()

# Make sure to replace this secret key with a random string when taking your project live!
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "your_super_secret_session_key"))

templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
def startup_db_setup():
    try:
        db = database()
        cursor = db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS password_resets (
                reset_id SERIAL PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                role VARCHAR(50) NOT NULL,
                token VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used SMALLINT DEFAULT 0
            )
        """)
        db.commit()
        # Add mail_status column if it doesn't exist
        try:
            cursor.execute("ALTER TABLE password_resets ADD COLUMN mail_status VARCHAR(255) DEFAULT 'Pending'")
            db.commit()
        except Exception:
            pass
        cursor.close()
        db.close()
        print("Database initialized successfully.")
    except Exception as e:
        print("Database startup initialization error:", e)

# Configure Google Gemini API
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "YOUR_GOOGLE_GEMINI_API_KEY_HERE")
if GEMINI_API_KEY != "YOUR_GOOGLE_GEMINI_API_KEY_HERE":
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    client = None
    print("[WARNING] GEMINI_API_KEY not set. AI features will be disabled.")

class PostgresConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, dictionary=False, **kwargs):
        if dictionary:
            return self._conn.cursor(cursor_factory=RealDictCursor, **kwargs)
        return self._conn.cursor(**kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

def database():
    db_port = os.getenv("DB_PORT")
    port = int(db_port) if db_port and db_port.strip() else 5432
    return PostgresConnectionWrapper(
        psycopg2.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            port=port
        )
    )

def send_email_message(to_email: str, subject: str, text_body: str, html_body: str) -> bool:
    """
    Sends an email using the best available configured method:
    1. Brevo HTTP API (if BREVO_API_KEY is configured)
    2. SendGrid HTTP API (if SENDGRID_API_KEY is configured)
    3. Standard SMTP (if SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD are configured)
    """
    brevo_key = os.getenv("BREVO_API_KEY")
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = os.getenv("SMTP_PORT", "587")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    
    sender_email = smtp_user or os.getenv("SENDER_EMAIL", "vehiclebreakdownassistant.vbams@gmail.com")
    sender_name = os.getenv("SENDER_NAME", "BreakdownAssist Team")
    
    # 1. Try Brevo HTTP API
    if brevo_key:
        logger.info(f"Attempting to send email to {to_email} via Brevo API...")
        try:
            url = "https://api.brevo.com/v3/smtp/email"
            headers = {
                "api-key": brevo_key,
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            data = {
                "sender": {"name": sender_name, "email": sender_email},
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html_body,
                "textContent": text_body
            }
            req = urllib.request.Request(
                url, 
                data=json.dumps(data).encode("utf-8"), 
                headers=headers, 
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                status_code = response.getcode()
                response_body = response.read().decode("utf-8")
                if status_code in (200, 201, 202):
                    logger.info(f"Email successfully sent to {to_email} via Brevo.")
                    return True
                else:
                    raise Exception(f"Brevo API returned status {status_code}: {response_body}")
        except Exception as e:
            logger.error(f"Failed to send email via Brevo API: {e}")
            # If Brevo fails, try other configured options if they exist
            
    # 2. Try SendGrid HTTP API
    if sendgrid_key:
        logger.info(f"Attempting to send email to {to_email} via SendGrid API...")
        try:
            url = "https://api.sendgrid.com/v3/mail/send"
            headers = {
                "Authorization": f"Bearer {sendgrid_key}",
                "Content-Type": "application/json"
            }
            data = {
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": {"email": sender_email, "name": sender_name},
                "subject": subject,
                "content": [
                    {"type": "text/plain", "value": text_body},
                    {"type": "text/html", "value": html_body}
                ]
            }
            req = urllib.request.Request(
                url, 
                data=json.dumps(data).encode("utf-8"), 
                headers=headers, 
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                status_code = response.getcode()
                if status_code in (200, 201, 202):
                    logger.info(f"Email successfully sent to {to_email} via SendGrid.")
                    return True
                else:
                    response_body = response.read().decode("utf-8")
                    raise Exception(f"SendGrid API returned status {status_code}: {response_body}")
        except Exception as e:
            logger.error(f"Failed to send email via SendGrid API: {e}")
            # If SendGrid fails, try standard SMTP if configured

    # 3. Fallback to SMTP
    if smtp_host and smtp_user and "your_email" not in smtp_user and smtp_password and "your_app_password" not in smtp_password:
        logger.info(f"Attempting to send email to {to_email} via SMTP ({smtp_host}:{smtp_port})...")
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = f"{sender_name} <{smtp_user}>"
            msg['To'] = to_email
            msg['Subject'] = subject
            
            msg.attach(MIMEText(text_body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            
            server = smtplib.SMTP(smtp_host, int(smtp_port))
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(smtp_user, to_email, msg.as_string())
            server.quit()
            logger.info(f"Email successfully sent to {to_email} via SMTP.")
            return True
        except Exception as e:
            logger.error(f"Failed to send email via SMTP: {e}")
            raise e
            
    raise Exception("No email service is configured (Missing Brevo, SendGrid, or SMTP credentials).")
@app.get("/dbtest")
def dbtest():
    try:
        conn = database()
        conn.close()
        return {"status": "Database Connected"}
    except Exception as e:
        return {"error": str(e)}

# Pydantic models for request bodies
class ChatRequest(BaseModel):
    message: str
    username: str = None

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request}
    )

@app.get("/reguser", response_class=HTMLResponse)
async def regpage(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={"request": request}
    )

@app.post("/reguser", response_class=HTMLResponse)
def createuser(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...)):
    
    db = database()
    cursor = db.cursor(dictionary=True)
    
    # Check if email already exists
    cursor.execute('SELECT COUNT(*) as count FROM users WHERE email = %s', (email,))
    result = cursor.fetchone()
    
    if result['count'] > 0:
        cursor.close()
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "request": request,
                "error": "Email already exists. Please use a different email.",
                "username": username,
                "email": email
            }
        )
    
    # Check if username already exists
    cursor.execute('SELECT COUNT(*) as count FROM users WHERE username = %s', (username,))
    result_uname = cursor.fetchone()
    
    if result_uname['count'] > 0:
        cursor.close()
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "request": request,
                "error": "Username already exists. Please use a different username.",
                "username": username,
                "email": email
            }
        )
    
    # Insert new user if email and username don't exist
    sql = 'INSERT INTO users(username,email,password) VALUES(%s,%s,%s)'
    val = (username, email, password)
    cursor.execute(sql, val)
    db.commit()
    cursor.close()
    db.close()
    
    return RedirectResponse(url="/login", status_code=303)

@app.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request}
    )

@app.post("/login", response_class=HTMLResponse)
def login_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)):
    
    db = database()
    cursor = db.cursor(dictionary=True)
    
    sql_query = 'SELECT * FROM users WHERE email = %s AND password = %s'
    cursor.execute(sql_query, (email, password))
    user = cursor.fetchone()
    
    cursor.close()
    db.close()
    
    if user:
        request.session["username"] = user["username"]
        return RedirectResponse(url="/frontpage", status_code=303)
    else:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
@app.get("/frontpage", response_class=HTMLResponse)
async def front_page(request: Request):
    username = request.session.get("username")
    if not username:
        return RedirectResponse(url="/login") 

    user_context = {"username": username}
    return templates.TemplateResponse(
        request=request,
        name="front.html",
        context={"request": request, "user": user_context} 
    )

@app.post("/complaint")
def regcomplaint(
    request: Request,
    location: str = Form(...),
    issue: str = Form(...),
    other: str = Form(...),
    phone: str = Form(...),
    latitude: str = Form(default=""),
    longitude: str = Form(default=""),
    area_name: str = Form(default="")):
    
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="Please log in first")
    
    db = database()
    cursor = db.cursor(dictionary=True)
    
    # Convert coordinates to float if provided
    lat = float(latitude) if latitude else None
    lng = float(longitude) if longitude else None
    
    sql = "INSERT INTO complaint(username, location, issue, other, phone, latitude, longitude, area_name) VALUES(%s, %s, %s, %s, %s, %s, %s, %s)"
    val = (username, location, issue, other, phone, lat, lng, area_name)
    
    cursor.execute(sql, val)
    db.commit()
    
    cursor.close()
    db.close()
    
    return RedirectResponse(url="/frontpage", status_code=303)

@app.get("/user_dashboard", response_class=HTMLResponse)
async def user_dashboard(request: Request):
    username = request.session.get("username")
    if not username:
        return RedirectResponse(url="/login") 
        
    db = database()
    cursor = db.cursor(dictionary=True)

    sql = "SELECT * FROM complaint WHERE username = %s "
    cursor.execute(sql, (username,))
    history = cursor.fetchall()
    total_count = len(history)

    cursor.close()
    db.close()    

    return templates.TemplateResponse(
        request=request,
        name="user_dashboard.html",
        context={
            "request": request, 
            "username": username, 
            "history": history, 
            "total_count": total_count
        } 
    )

# --- MECHANIC MODULE ROUTES ---

@app.get("/mechanic_login", response_class=HTMLResponse)
async def mechanic_login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mechanic_login.html",
        context={"request": request}
    )

@app.post("/mechanic_login", response_class=HTMLResponse)
def login_mechanic(request: Request, email: str = Form(...), password: str = Form(...)):
    db = database()
    cursor = db.cursor(dictionary=True)
    
    sql_query = "SELECT mechanicname, email FROM mechanic WHERE email = %s AND password = %s"
    cursor.execute(sql_query, (email, password))
    mechanic = cursor.fetchone()
    
    cursor.close()
    db.close()
    
    if mechanic:
        request.session["mechanicname"] = mechanic["mechanicname"]
        return RedirectResponse(url="/mechanic_dashboard", status_code=303)
    else:
        raise HTTPException(status_code=401, detail="Invalid email or password")

@app.get("/mechanic_dashboard", response_class=HTMLResponse)
async def show_dashboard(request: Request):
    mechanicname = request.session.get("mechanicname")
    if not mechanicname:
        return RedirectResponse(url="/mechanic_login")

    db = database()
    cursor = db.cursor(dictionary=True)
    
    sql_active = """
        SELECT * FROM complaint 
        WHERE status = 'Pending' 
        OR (status = 'In Progress' AND mechanicassigned = %s)
    """
    cursor.execute(sql_active, (mechanicname,))
    active_jobs = cursor.fetchall()
    
    cursor.execute("SELECT * FROM complaint WHERE status = 'Resolved'")
    completed_jobs = cursor.fetchall()
    
    cursor.close()
    db.close()

    return templates.TemplateResponse(
        request=request,
        name="mechanic_dashboard.html",
        context={
            "request": request,
            "mechanicname": mechanicname,
            "active_jobs": active_jobs,
            "completed_jobs": completed_jobs
        }
    )

def send_twilio_message_helper(sid: str, token: str, from_num: str, to_num: str, body: str):
    import urllib.request
    import urllib.parse
    import base64
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode({
        'From': from_num,
        'To': to_num,
        'Body': body
    }).encode('utf-8')
    
    auth_str = f"{sid}:{token}"
    auth_b64 = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
    
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Authorization', f'Basic {auth_b64}')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    
    with urllib.request.urlopen(req, timeout=10) as response:
        return True

def send_user_notification(base_url: str, complaint_id: int, username: str, user_phone: str, user_email: str, mechanic_name: str, mechanic_phone: str):
    track_link = f"{base_url}track_mechanic/{complaint_id}"
    
    # 1. Send Email
    email_sent = False
    
    if user_email:
        try: 
            subject = f"✅ Mechanic Assigned - Request #{complaint_id}"
            text_body = (
                f"Hello {username},\n\n"
                f"Your breakdown request #{complaint_id} has been accepted by mechanic {mechanic_name}.\n"
                f"Mechanic Contact: {mechanic_phone}\n\n"
                f"You can track the mechanic's location here: {track_link}\n\n"
                f"Best regards,\nBreakdownAssist Team"
            )
            
            html_body = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <title>Mechanic Assigned - BreakdownAssist</title>
                <style>
                    body {{
                        margin: 0;
                        padding: 0;
                        background-color: #f8fafc;
                        font-family: 'Segoe UI', Arial, sans-serif;
                    }}
                    .email-wrapper {{
                        width: 100%;
                        background-color: #f8fafc;
                        padding: 40px 0;
                    }}
                    .email-card {{
                        max-width: 500px;
                        margin: 0 auto;
                        background-color: #ffffff;
                        border-radius: 12px;
                        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
                        border: 1px solid #e2e8f0;
                        overflow: hidden;
                    }}
                    .email-header {{
                        background: linear-gradient(135deg, #10b981 0%, #059669 100%);
                        padding: 30px;
                        text-align: center;
                        color: #ffffff;
                    }}
                    .email-header h1 {{                    margin: 0;
                        font-size: 24px;
                        font-weight: 700;
                    }}
                    .email-body {{                    padding: 40px 30px;
                        color: #334155;
                        line-height: 1.6;
                    }}
                    .email-body h2 {{
                        margin-top: 0;
                        font-size: 20px;
                        font-weight: 600;
                        color: #0f172a;
                    }}
                    .info-box {{                    background-color: #f1f5f9;
                        border-radius: 8px;
                        padding: 20px;
                        margin: 25px 0;
                        border-left: 4px solid #10b981;
                    }}
                    .info-row {{                    margin-bottom: 10px;
                        font-size: 15px;
                    }}
                    .info-row:last-child {{
                        margin-bottom: 0;
                    }}
                    .info-label {{
                        font-weight: bold;
                        color: #64748b;
                        width: 130px;
                        display: inline-block;
                    }}
                    .info-value {{                    color: #0f172a;
                        font-weight: 600;
                    }}
                    .btn-container {{                    text-align: center;
                        margin: 30px 0;
                    }}
                    .btn-track {{                    display: inline-block;
                        padding: 14px 30px;
                        background-color: #10b981;
                        color: #ffffff !important;
                        text-decoration: none;
                        font-weight: 700;
                        font-size: 16px;
                        border-radius: 8px;
                        box-shadow: 0 4px 12px rgba(16, 185, 129, 0.25);
                    }}
                    .email-footer {{                    background-color: #f1f5f9;
                        padding: 20px;
                        text-align: center;
                        font-size: 12px;
                        color: #64748b;
                        border-top: 1px solid #e2e8f0;
                    }}
                </style>
            </head>
            <body>
                <div class="email-wrapper">
                    <div class="email-card">
                        <div class="email-header">
                            <h1>🛠️ Mechanic Assigned</h1>
                        </div>
                        <div class="email-body">
                            <h2>Hello {username},</h2>
                            <p>Good news! A mechanic has accepted your request and is on the way to help you.</p>
                            
                            <div class="info-box">
                                <div class="info-row">
                                    <span class="info-label">Mechanic Name:</span>
                                    <span class="info-value">{mechanic_name}</span>
                                </div>
                                <div class="info-row">
                                    <span class="info-label">Contact Phone:</span>
                                    <span class="info-value">{mechanic_phone}</span>
                                </div>
                                <div class="info-row">
                                    <span class="info-label">Request ID:</span>
                                    <span class="info-value">#{complaint_id}</span>
                                </div>
                            </div>
                            
                            <div class="btn-container">
                                <a href="{track_link}" class="btn-track">Track Mechanic Live</a>
                            </div>
                            
                            <p style="font-size: 13px; color: #94a3b8; border-top: 1px dashed #e2e8f0; padding-top: 15px; margin-bottom: 0;">
                                Please ensure your phone is reachable so the mechanic can contact you if needed.
                            </p>
                        </div>
                        <div class="email-footer">
                            <p>&copy; 2026 BreakdownAssist Management System. All rights reserved.</p>
                        </div>
                    </div>
                </div>
            </body>
            </html>
            """
            send_email_message(user_email, subject, text_body, html_body)
            email_sent = True
            logger.info(f"Notification Email sent successfully to {user_email}.")
        except Exception as e:
            logger.error(f"Notification email sending failed: {e}")
    else:
        logger.info("User email address empty or missing. Email sending skipped.")

    # 2. Twilio SMS Notification
    twilio_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_from_sms = os.getenv("TWILIO_FROM_SMS", "")
    
    # Format user phone number
    formatted_to = user_phone.strip()
    if formatted_to and not formatted_to.startswith('+'):
        if len(formatted_to) == 10:
            formatted_to = f"+91{formatted_to}"
        else:
            formatted_to = f"+{formatted_to}"
            
    sms_body = (
        f"BreakdownAssist: Hi {username}, mechanic {mechanic_name} has accepted your request #{complaint_id}. "
        f"Phone: {mechanic_phone}. Track live: {track_link}"
    )

    # Trigger SMS
    sms_sent = False
    if twilio_sid and "your_twilio" not in twilio_sid and twilio_token and "your_twilio" not in twilio_token and twilio_from_sms and "your_twilio" not in twilio_from_sms:
        try:
            sms_sent = send_twilio_message_helper(twilio_sid, twilio_token, twilio_from_sms, formatted_to, sms_body)
        except Exception as e:
            logger.error(f"Twilio SMS Error: {e}")

    # Simulated Logging to Console for Local Debugging
    logger.info(f"Notification Console Output:\n"
                f"EMAIL to [{user_email or 'N/A'}]:\n"
                f"   Subject: Mechanic Assigned - Request #{complaint_id}\n"
                f"   Mechanic Name: {mechanic_name}\n"
                f"   Mechanic Phone: {mechanic_phone}\n"
                f"   Track Link: {track_link}\n"
                f"   Status: {'SENT' if email_sent else 'FAILED / SKIPPED'}\n"
                f"SMS to [{formatted_to}]:\n"
                f"   Content: {sms_body}\n"
                f"   Status: {'SENT' if sms_sent else 'FAILED / SKIPPED'}")


@app.post("/update_status/{complaint_id}")
async def update_job_status(request: Request, complaint_id: int, background_tasks: BackgroundTasks, status: str = Form(...)):
    mechanicname = request.session.get("mechanicname") 
    if not mechanicname:
         return RedirectResponse(url="/mechanic_login")

    db = database()
    cursor = db.cursor(dictionary=True)

    if status == "In Progress":
        # Get user details for notification before updating status
        cursor.execute("""
            SELECT c.username, c.phone, u.email 
            FROM complaint c 
            LEFT JOIN users u ON c.username = u.username 
            WHERE c.complaint_id = %s
        """, (complaint_id,))
        user_info = cursor.fetchone()

        cursor.execute("SELECT mechanicname, mechanicphoneno FROM mechanic WHERE mechanicname = %s", (mechanicname,))
        mech_info = cursor.fetchone()

        sql = """
            UPDATE complaint 
            SET status = %s, mechanicassigned = %s, mechanicphoneno = %s 
            WHERE complaint_id = %s
        """
        cursor.execute(sql, (status, mech_info['mechanicname'], mech_info['mechanicphoneno'], complaint_id))
        db.commit()

        # Trigger user notification
        if user_info and mech_info:
            try:
                base_url = str(request.base_url)
                background_tasks.add_task(
                    send_user_notification,
                    base_url,
                    complaint_id,
                    user_info['username'],
                    user_info['phone'],
                    user_info['email'],
                    mech_info['mechanicname'],
                    mech_info['mechanicphoneno']
                )
            except Exception as notification_error:
                print(f"Error scheduling send_user_notification: {notification_error}")
    
    elif status == "Pending":
        sql = """
            UPDATE complaint 
            SET status = %s, mechanicassigned = NULL, mechanicphoneno = NULL 
            WHERE complaint_id = %s
        """
        cursor.execute(sql, (status, complaint_id))
        db.commit()
        
    else:
        cursor.execute("UPDATE complaint SET status = %s WHERE complaint_id = %s", (status, complaint_id))
        db.commit()

    cursor.close()
    db.close()
    return RedirectResponse(url="/mechanic_dashboard", status_code=303)

# --- MECHANIC LOCATION TRACKING ---

@app.post("/update_mechanic_location")
async def update_mechanic_location(
    request: Request,
    complaint_id: int = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...)):
    
    mechanicname = request.session.get("mechanicname")
    if not mechanicname:
        raise HTTPException(status_code=401, detail="Please log in first")
    
    db = database()
    cursor = db.cursor()
    
    # Update mechanic location in complaint record
    sql = "UPDATE complaint SET mechanic_latitude = %s, mechanic_longitude = %s WHERE complaint_id = %s"
    cursor.execute(sql, (latitude, longitude, complaint_id))
    db.commit()
    cursor.close()
    db.close()
    
    return {"status": "Location updated"}

@app.get("/track_mechanic/{complaint_id}", response_class=HTMLResponse)
async def track_mechanic(request: Request, complaint_id: int):
    username = request.session.get("username")
    if not username:
        return RedirectResponse(url="/login")
    
    db = database()
    cursor = db.cursor(dictionary=True)
    
    # Get complaint and mechanic details
    cursor.execute("""
        SELECT complaint_id, location, area_name, mechanicassigned, latitude, longitude, mechanic_latitude, mechanic_longitude, mechanicphoneno 
        FROM complaint 
        WHERE complaint_id = %s AND username = %s
    """, (complaint_id, username))
    complaint = cursor.fetchone()
    
    cursor.close()
    db.close()
    
    if not complaint:
        return RedirectResponse(url="/user_dashboard")
    
    return templates.TemplateResponse(
        request=request,
        name="track_mechanic.html",
        context={
            "request": request,
            "complaint": complaint
        }
    )

@app.get("/get_mechanic_location/{complaint_id}")
async def get_mechanic_location(request: Request, complaint_id: int):
    db = database()
    cursor = db.cursor(dictionary=True)
    
    cursor.execute("""
        SELECT mechanic_latitude, mechanic_longitude, mechanicassigned, mechanicphoneno 
        FROM complaint 
        WHERE complaint_id = %s
    """, (complaint_id,))
    complaint = cursor.fetchone()
    
    cursor.close()
    db.close()
    
    if complaint and complaint['mechanic_latitude'] and complaint['mechanic_longitude']:
        return {
            "latitude": complaint['mechanic_latitude'],
            "longitude": complaint['mechanic_longitude'],
            "mechanic_name": complaint['mechanicassigned'],
            "mechanic_phone": complaint['mechanicphoneno']
        }
    
    return {"error": "Location not available"}

@app.post("/ai_chat")
async def ai_chat(request: Request, chat_request: ChatRequest):
    """Handle AI chat requests with retry logic"""
    if not client:
        return JSONResponse(
            status_code=400,
            content={"error": "AI service not configured. Please set GEMINI_API_KEY environment variable."}
        )
    
    try:
        # System prompt for the AI assistant
        system_prompt = """You are a helpful customer support assistant for BreakdownAssist, a 24/7 roadside vehicle assistance service. 
        You help users with:
        - General questions about our service
        - Troubleshooting common vehicle issues
        - Explaining how to use the app
        - Providing tips for vehicle maintenance
        - Answering questions about breakdown coverage
        
        Be friendly, professional, and concise. Keep responses brief and helpful."""
        
        # Create the full prompt
        full_prompt = f"{system_prompt}\n\nUser: {chat_request.message}"
        
        # Retry logic for handling temporary unavailability
        max_retries = 3
        retry_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                # Generate response using Gemini with retry fallback
                models_to_try = [
                    'models/gemini-2.0-flash-lite',
                    'models/gemini-2.0-flash',
                    'models/gemini-2.5-flash'
                ]
                
                response = None
                for model_name in models_to_try:
                    try:
                        response = client.models.generate_content(
                            model=model_name,
                            contents=full_prompt
                        )
                        break  # Success, exit the loop
                    except Exception as model_error:
                        if attempt < max_retries - 1:
                            continue  # Try next model
                        raise model_error
                
                if response:
                    return {
                        "response": response.text,
                        "status": "success"
                    }
            except Exception as e:
                error_str = str(e)
                # If it's a 503 (unavailable), retry
                if "503" in error_str or "UNAVAILABLE" in error_str:
                    if attempt < max_retries - 1:
                        print(f"Model unavailable, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                # For other errors, raise immediately
                raise e
        
        return JSONResponse(
            status_code=503,
            content={"error": "AI models are temporarily unavailable. Please try again in a moment."}
        )
        
    except Exception as e:
        print(f"AI Chat Error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": f"Error processing your request: {str(e)}"}
        )

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/")

# ******************************** ADMIN MODULE ***************************

@app.get("/admin_login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"request": request}
    )

@app.post("/admin_login", response_class=HTMLResponse)
def login_admin(request: Request, username: str = Form(...), password: str = Form(...)):
    db = database()
    cursor = db.cursor(dictionary=True)
    
    sql_query = "SELECT username, password FROM admin WHERE username = %s AND password = %s"
    cursor.execute(sql_query, (username, password))
    admin = cursor.fetchone()
    
    cursor.close()
    db.close()
    
    if admin:
        request.session["username"] = admin["username"]
        return RedirectResponse(url="/admin_dashboard", status_code=303)
    else:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
@app.get("/admin_dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    admin_user = request.session.get("username") 
    if not admin_user:
        return RedirectResponse(url="/admin_login")

    db = database()
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) as count FROM mechanic")
    total_mechanics = cursor.fetchone()['count']

    cursor.execute("SELECT COUNT(*) as count FROM complaint")
    total_complaints = cursor.fetchone()['count']
    
    cursor.execute("SELECT COUNT(*) as count FROM feedback")
    total_feedbacks_count = cursor.fetchone()['count']

    cursor.execute("SELECT * FROM complaint ORDER BY complaint_id")
    all_jobs = cursor.fetchall()

    cursor.execute("SELECT mechanicname, mechanicphoneno, address, email FROM mechanic")
    mechanic_list = cursor.fetchall()
    
    cursor.close()
    db.close()
    
    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={ 
            "request": request,
            "adminname": admin_user,
            "total_mechs": total_mechanics,
            "total_jobs": total_complaints,
            "all_jobs": all_jobs,
            "feedback_count": total_feedbacks_count,
            "mechanics": mechanic_list
        }
    )

@app.get("/select_mechanic")
async def select_mechanic(request: Request):
    if not request.session.get("username"):
        return RedirectResponse(url="/admin_login")
        
    db = database()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT mechanicname, mechanicphoneno, address, email FROM mechanic ORDER BY mechanicname ASC")
    mechanic_list = cursor.fetchall()
    cursor.close()
    db.close()
    
    return templates.TemplateResponse(
        request=request,
        name="total_mechanic.html",
        context={ 
            "request": request,
            "mechanic": mechanic_list
        }
    )

@app.get("/delete_complaint/{complaint_id}")
async def delete_complaint(request: Request, complaint_id: int):
    if not request.session.get("username"):
        return RedirectResponse(url="/admin_login")
        
    db = database()
    cursor = db.cursor()
    cursor.execute("DELETE FROM complaint WHERE complaint_id = %s", (complaint_id,))
    db.commit()
    cursor.close()
    db.close()
    return RedirectResponse(url="/admin_dashboard")

# ***************************** ADD MECHANIC *************************

@app.get("/add_mechanic", response_class=HTMLResponse)
async def add_mechanic_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="add_mechanic.html",
        context={"request": request}
    )

@app.post("/add_mechanic")
async def submit_mechanic(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    address: str = Form(...),
    email: str = Form(...),
    password: str = Form(...)
):
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="Please log in first")
        
    db = database()
    cursor = db.cursor()
    sql = "INSERT INTO mechanic(mechanicname, mechanicphoneno, address, email, password) VALUES(%s,%s,%s,%s,%s)"
    val = (name, phone, address, email, password)
    cursor.execute(sql, val)
    db.commit()
    cursor.close()
    db.close()
    
    return RedirectResponse(url="/admin_dashboard", status_code=303)

@app.get("/delete_mechanic/{mechanicname}")
async def delete_mechanic(request: Request, mechanicname: str):
    if not request.session.get("username"):
        return RedirectResponse(url="/admin_login")
        
    db = database()
    cursor = db.cursor()
    cursor.execute("DELETE FROM mechanic WHERE mechanicname = %s", (mechanicname,))
    db.commit()
    cursor.close()
    db.close()
    return RedirectResponse(url="/admin_dashboard")

#****************************** FEEDBACK ******************************

@app.post("/submit-inquiry")
async def submit_inquiry(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    content: str = Form(...)
):
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="Please log in first")
        
    db = database()
    cursor = db.cursor()
    sql = "INSERT INTO feedback(name, email, content) VALUES(%s,%s,%s)"
    val = (name, email, content)
    cursor.execute(sql, val)
    db.commit()
    cursor.close()
    db.close()
    
    return RedirectResponse(url="/frontpage", status_code=303)

@app.get("/select_feedback")
async def select_feedback(request: Request):
    if not request.session.get("username"):
        return RedirectResponse(url="/admin_login")
        
    db = database()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM feedback ORDER BY name ASC")
    feedback = cursor.fetchall()
    cursor.close()
    db.close()
    
    return templates.TemplateResponse(
        request=request,
        name="total_feedback.html",
        context={ 
            "request": request,
            "feedback_": feedback # Match variable name inside total_feedback.html
        }
    )

def send_reset_email_helper(email: str, role: str, reset_link: str, token: str):
    logger.info(f"Starting background task to send password reset email to {email}")
    
    subject = "🔑 Reset Your BreakdownAssist Password"
    text_body = (
        f"Hello,\n\n"
        f"You requested a password reset for your BreakdownAssist account.\n"
        f"Please click the link below to set a new password:\n\n"
        f"{reset_link}\n\n"
        f"This link will expire in 1 hour.\n"
        f"If you did not request this reset, please ignore this email.\n\n"
        f"Best regards,\nBreakdownAssist Team"
    )
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Reset Your Password</title>
        <style>
            body {{
                margin: 0;
                padding: 0;
                background-color: #f8fafc;
                font-family: 'Segoe UI', Arial, sans-serif;
                -webkit-font-smoothing: antialiased;
            }}
            .email-wrapper {{
                width: 100%;
                background-color: #f8fafc;
                padding: 40px 0;
            }}
            .email-card {{
                max-width: 500px;
                margin: 0 auto;
                background-color: #ffffff;
                border-radius: 12px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
                border: 1px solid #e2e8f0;
                overflow: hidden;
            }}
            .email-header {{
                background: linear-gradient(135deg, #ff4757 0%, #764ba2 100%);
                padding: 30px;
                text-align: center;
                color: #ffffff;
            }}
            .email-header h1 {{
                margin: 0;
                font-size: 24px;
                font-weight: 700;
                letter-spacing: -0.5px;
            }}
            .email-body {{
                padding: 40px 30px;
                color: #334155;
                line-height: 1.6;
            }}
            .email-body h2 {{
                margin-top: 0;
                font-size: 20px;
                font-weight: 600;
                color: #0f172a;
            }}
            .email-body p {{
                font-size: 16px;
                margin-bottom: 25px;
                margin-top: 0;
            }}
            .btn-container {{
                text-align: center;
                margin: 35px 0;
            }}
            .btn-reset {{
                display: inline-block;
                padding: 14px 30px;
                background-color: #ff4757;
                color: #ffffff !important;
                text-decoration: none;
                font-weight: 700;
                font-size: 16px;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(255, 71, 87, 0.25);
            }}
            .email-footer {{
                background-color: #f1f5f9;
                padding: 20px;
                text-align: center;
                font-size: 12px;
                color: #64748b;
                border-top: 1px solid #e2e8f0;
            }}
        </style>
    </head>
    <body>
        <div class="email-wrapper">
            <div class="email-card">
                <div class="email-header">
                    <h1>🚨 BreakdownAssist</h1>
                </div>
                <div class="email-body">
                    <h2>Hello,</h2>
                    <p>We received a request to reset the password for your account. Please click the button below to create your new secure password:</p>
                    
                    <div class="btn-container">
                        <a href="{reset_link}" class="btn-reset">Reset Password</a>
                    </div>
                    
                    <p style="font-size: 14px; color: #64748b;">If the button above does not work, copy and paste this link into your web browser:</p>
                    <p style="font-size: 13px; word-break: break-all; color: #764ba2; font-weight: bold; margin-bottom: 25px;">{reset_link}</p>
                    
                    <p style="font-size: 13px; color: #94a3b8; margin-top: 25px; border-top: 1px dashed #e2e8f0; padding-top: 15px; margin-bottom: 0;">
                        ⏱️ This link is only valid for 1 hour.<br>
                        If you did not request this password reset, you can safely ignore this email.
                    </p>
                </div>
                <div class="email-footer">
                    <p>&copy; 2026 BreakdownAssist Management System. All rights reserved.</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

    try:
        send_email_message(email, subject, text_body, html_body)
        
        # Update database status
        try:
            db = database()
            cursor = db.cursor()
            cursor.execute("UPDATE password_resets SET mail_status = 'Sent' WHERE token = %s", (token,))
            db.commit()
            cursor.close()
            db.close()
        except Exception as db_err:
            logger.error(f"Failed to update database status to Sent: {db_err}")
            
    except Exception as e:
        error_msg = str(e)[:250]
        logger.error(f"Reset email dispatch failed: {e}")
        try:
            db = database()
            cursor = db.cursor()
            cursor.execute("UPDATE password_resets SET mail_status = %s WHERE token = %s", (f"Failed: {error_msg}", token))
            db.commit()
            cursor.close()
            db.close()
        except Exception as db_err:
            logger.error(f"Failed to update database status to Failed: {db_err}")


@app.get("/forgot_password", response_class=HTMLResponse)
async def forgot_password_page(request: Request, role: str = "user"):
    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={"request": request, "role": role}
    )

@app.post("/forgot_password", response_class=HTMLResponse)
def handle_forgot_password(
    request: Request,
    background_tasks: BackgroundTasks,
    role: str = Form(...),
    email: str = Form(...)
):
    db = database()
    cursor = db.cursor(dictionary=True)
    
    # 1. Verify email exists in chosen role table
    if role == "user":
        cursor.execute('SELECT * FROM users WHERE email = %s', (email,))
    elif role == "mechanic":
        cursor.execute("SELECT * FROM mechanic WHERE email = %s", (email,))
    else:
        cursor.close()
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={"request": request, "error": "Invalid role selected.", "role": role, "email": email}
        )
        
    entity = cursor.fetchone()
    if not entity:
        cursor.close()
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={"request": request, "error": "Email address not registered.", "role": role, "email": email}
        )
        
    # 2. Generate secure token
    token = str(uuid.uuid4())
    
    # 3. Store token in database
    cursor.execute(
        "INSERT INTO password_resets (email, role, token) VALUES (%s, %s, %s)",
        (email, role, token)
    )
    db.commit()
    cursor.close()
    db.close()
    
    # 4. Construct reset link
    import socket
    def get_local_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    app_url = os.getenv("APP_URL", "")
    if app_url:
        if not app_url.endswith("/"):
            app_url += "/"
        reset_link = f"{app_url}reset_password?token={token}"
    else:
        base_url = str(request.base_url)
        if "localhost" in base_url or "127.0.0.1" in base_url:
            local_ip = get_local_ip()
            base_url = base_url.replace("localhost", local_ip).replace("127.0.0.1", local_ip)
        reset_link = f"{base_url}reset_password?token={token}"
    
    # 5. Send email (queued in background)
    background_tasks.add_task(send_reset_email_helper, email, role, reset_link, token)
    
    # Always log the link to the terminal so developers/users can test locally without setting up SMTP
    logger.info(f"\n========================================\n[RESET LINK] PASSWORD RESET LINK GENERATED:\n{reset_link}\n========================================\n")
    
    brevo_key = os.getenv("BREVO_API_KEY")
    sendgrid_key = os.getenv("SENDGRID_API_KEY")
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    
    has_email_service = (
        brevo_key or 
        sendgrid_key or 
        (smtp_user and "your_email" not in smtp_user and smtp_password and "your_app_password" not in smtp_password)
    )
    
    if has_email_service:
        success_msg = "A password reset link is being sent to your email address."
    else:
        success_msg = f"Reset link generated! (Check terminal console to copy link: {reset_link})"
        
    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={
            "request": request,
            "success": success_msg,
            "redirect_url": "/login" if role == "user" else "/mechanic_login",
            "role": role,
            "email": email
        }
    )

@app.get("/reset_password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    if not token:
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"request": request, "error": "Missing validation token.", "token_error": True}
        )
        
    db = database()
    cursor = db.cursor(dictionary=True)
    
    # Verify token and calculate age timezone-independently on database
    cursor.execute("""
        SELECT *, TIMESTAMPDIFF(SECOND, created_at, NOW()) as age_seconds 
        FROM password_resets 
        WHERE token = %s AND used = 0
    """, (token,))
    reset_req = cursor.fetchone()
    
    if not reset_req:
        cursor.close()
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"request": request, "error": "This reset link is invalid or has already been used.", "token_error": True}
        )
        
    # Check expiration (older than 1 hour / 3600 seconds)
    if reset_req['age_seconds'] > 3600:
        cursor.close()
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"request": request, "error": "This reset link has expired (links are valid for 1 hour).", "token_error": True}
        )
        
    cursor.close()
    db.close()
    
    return templates.TemplateResponse(
        request=request,
        name="reset_password.html",
        context={"request": request, "token": token}
    )

@app.post("/reset_password", response_class=HTMLResponse)
def handle_reset_password(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    if new_password != confirm_password:
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"request": request, "error": "Passwords do not match.", "token": token}
        )
        
    db = database()
    cursor = db.cursor(dictionary=True)
    
    # 1. Verify token and calculate age timezone-independently on database
    cursor.execute("""
        SELECT *, TIMESTAMPDIFF(SECOND, created_at, NOW()) as age_seconds 
        FROM password_resets 
        WHERE token = %s AND used = 0
    """, (token,))
    reset_req = cursor.fetchone()
    
    if not reset_req:
        cursor.close()
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"request": request, "error": "Invalid or expired reset token.", "token_error": True}
        )
        
    # Check expiration (older than 1 hour / 3600 seconds)
    if reset_req['age_seconds'] > 3600:
        cursor.close()
        db.close()
        return templates.TemplateResponse(
            request=request,
            name="reset_password.html",
            context={"request": request, "error": "This reset link has expired.", "token_error": True}
        )
        
    email = reset_req['email']
    role = reset_req['role']
    
    # 2. Update password in target table
    if role == "user":
        cursor.execute('UPDATE users SET password = %s WHERE email = %s', (new_password, email))
        redirect_url = "/login"
    elif role == "mechanic":
        cursor.execute("UPDATE mechanic SET password = %s WHERE email = %s", (new_password, email))
        redirect_url = "/mechanic_login"
    else:
        cursor.close()
        db.close()
        raise HTTPException(status_code=400, detail="Invalid role associated with token")
        
    # 3. Mark token as used
    cursor.execute("UPDATE password_resets SET used = 1 WHERE token = %s", (token,))
    db.commit()
    
    cursor.close()
    db.close()
    
    return templates.TemplateResponse(
        request=request,
        name="reset_password.html",
        context={
            "request": request,
            "success": "Your password has been reset successfully! Redirecting to login...",
            "redirect_url": redirect_url,
            "token_error": True
        }
    )