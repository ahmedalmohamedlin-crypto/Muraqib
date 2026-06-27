import datetime
import os
import threading
import gspread
import yt_dlp
import sys
import json
import time
from flask import Flask, render_template, request, redirect, url_for
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user
from flask_apscheduler import APScheduler
from werkzeug.security import check_password_hash
from bot import run_telegram_bot

sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-me-in-render')
scheduler = APScheduler()
BOT_THREAD = None

STORED_HASH = "scrypt:32768:8:1$lPt4wnaE4ekUPGw3$9e79dd254d8b161113955f8ecce3af14fd28992a67a685d23915e423471442719ea6c6881c813a9b8205f90621af7410bf38c963900926d59757e245ce457d6b"
DEFAULT_LOCAL_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DB_FILE = os.path.join(BASE_DIR, 'sheets_db.json')

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:  # If file exists but is empty
                    return []
                return json.loads(content)
        except json.JSONDecodeError:
            print("⚠️ Warning: DB file was corrupted or empty. Resetting to empty list.")
            return []
    return []

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f: 
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- Google Sheets Setup ---
CREDENTIALS_PATH = os.path.join(BASE_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')

def initialize_google_client():
    global gc
    credentials_json = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    token_json = os.environ.get('GOOGLE_TOKEN_JSON')

    if credentials_json:
        with open(CREDENTIALS_PATH, 'w', encoding='utf-8') as f:
            f.write(credentials_json)
    if token_json:
        with open(TOKEN_PATH, 'w', encoding='utf-8') as f:
            f.write(token_json)

    gc = None
    try:
        gc = gspread.oauth(credentials_filename=CREDENTIALS_PATH, authorized_user_filename=TOKEN_PATH)
    except Exception as e:
        print(f"⚠️ Google OAuth Error: {e}")


gc = None
initialize_google_client()

def get_tiktok_stats(url):
    if not url or not url.startswith('http'):
        return None
        
    cookiefile = os.path.join(BASE_DIR, 'cookies.txt')
    ydl_opts = {
        'quiet': True, 
        'no_warnings': True, 
        'extract_flat': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    if os.path.exists(cookiefile):
        ydl_opts['cookiefile'] = cookiefile
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            raw_date = info.get('upload_date')
            formatted_date = datetime.datetime.strptime(raw_date, '%Y%m%d').strftime('%Y/%m/%d') if raw_date else "N/A"
            return {
                'caption': info.get('title', 'N/A'),
                'views': info.get('view_count', 0),
                'likes': info.get('like_count', 0),
                'comments': info.get('comment_count', 0),
                'upload_date': formatted_date
            }
        except Exception as e:
            print(f"Error fetching TikTok stats: {e}")
            return None

def update_all_sheets():
    if gc is None:
        print("⚠️ Google Sheets client is unavailable. Skipping sync.")
        return

    print("🕒 [Sync] Starting background update for all sheets...")
    db = load_db()
    for item in db:
        try:
            sh = gc.open_by_key(item['id'])
            wks = sh.worksheet(item['name'])
            raw_data = wks.get_all_values()
            
            # Prepare batch updates to avoid hitting API limits
            updates = []
            
            # raw_data[0] is the header
            for i, row in enumerate(raw_data[1:], start=2):
                if len(row) < 10: continue # Skip if row is too short
                
                video_url = row[9].strip() # Column J
                if not video_url: continue
                
                stats = get_tiktok_stats(video_url)
                if stats:
                    # Collect updates for this row
                    # Column 1: Caption, Column 2: Date, Col 7: Likes, Col 8: Comments, Col 9: Views
                    updates.append({'range': f'A{i}', 'values': [[stats['caption']]]})
                    updates.append({'range': f'B{i}', 'values': [[stats['upload_date']]]})
                    updates.append({'range': f'G{i}:I{i}', 'values': [[stats['likes'], stats['comments'], stats['views']]]})
                
                # Sleep briefly to avoid TikTok rate limits if you have many videos
                time.sleep(0.5)

            if updates:
                wks.batch_update(updates)
                print(f"✅ Successfully Updated: {item['name']}")
            
        except Exception as e:
            print(f"❌ Failed to update {item['name']}: {e}")

# --- Flask Auth ---
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, id): self.id = id

@login_manager.user_loader
def load_user(user_id): return User(user_id)

@app.route('/health')
def health():
    return {'status': 'ok'}, 200

def is_valid_password(password):
    if not password:
        return False
    if os.environ.get("ADMIN_PASSWORD"):
        return password == os.environ.get("ADMIN_PASSWORD")
    if password == DEFAULT_LOCAL_PASSWORD:
        return True
    return check_password_hash(STORED_HASH, password)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if is_valid_password(request.form.get('password')):
            login_user(User("admin"))
            return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    db = load_db()
    msg = request.args.get('msg')
    if request.method == 'POST':
        if gc is None:
            return redirect(url_for('index', msg='gdocs_unavailable'))

        sheet_id = request.form.get('sheet_id')
        wks_name = request.form.get('worksheet_name')
        try:
            sh = gc.open_by_key(sheet_id)
            wks = sh.worksheet(wks_name)
            row_count = len(wks.get_all_values())
            if not any(d['id'] == sheet_id and d['name'] == wks_name for d in db):
                db.append({'id': sheet_id, 'name': wks_name, 'rows': row_count})
                save_db(db)
        except Exception as e: 
            print(f"Error adding sheet: {e}")
        return redirect(url_for('index'))
    
    return render_template('index.html', stored_sheets=db, msg=msg)

@app.route('/update_now')
@login_required
def update_now():
    thread = threading.Thread(target=update_all_sheets)
    thread.start()
    return redirect(url_for('index', msg="started"))

@app.route('/delete/<int:index>')
@login_required
def delete_sheet(index):
    db = load_db()
    if 0 <= index < len(db):
        db.pop(index)
        save_db(db)
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('login'))

def run_flask():
    port = int(os.environ.get("PORT", 5050))
    app.run(host='0.0.0.0', port=port, use_reloader=False, threaded=True)


def start_background_services():
    global BOT_THREAD
    if os.getenv("ENABLE_TELEGRAM_BOT", "true").lower() not in {"1", "true", "yes", "on"}:
        print("ℹ️ Telegram bot disabled by configuration.")
        return
    if BOT_THREAD is not None and BOT_THREAD.is_alive():
        return
    try:
        BOT_THREAD = threading.Thread(target=run_telegram_bot, daemon=True)
        BOT_THREAD.start()
    except Exception as e:
        print(f"⚠️ Telegram bot could not start: {e}")


def initialize_background_services():
    if not scheduler.running:
        scheduler.add_job(id='sync_6h', func=update_all_sheets, trigger='interval', hours=6)
        scheduler.start()
    start_background_services()


# Start background services automatically for Render/Gunicorn and local runs.
initialize_background_services()


if __name__ == "__main__":
    # Start Web Server in the main process so Render keeps the service alive
    run_flask()
