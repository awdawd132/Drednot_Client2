# drednot_bot.py (High-Performance, Memory-Stable, Fully-Featured, Production-Ready)

import os
import re
import json
import time
import shutil
import queue
import html # For web UI
import atexit
import threading # <-- Confirmed Present
import traceback
import requests
from datetime import datetime, timedelta
from collections import deque
from threading import Lock
from concurrent.futures import ThreadPoolExecutor

# Flask imports for the advanced UI
from flask import Flask, Response, request, redirect, url_for
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException, ElementClickInterceptedException

# --- CONFIGURATION ---
BOT_SERVER_URL = os.environ.get("BOT_SERVER_URL")
API_KEY = 'drednot123'
MESSAGE_DELAY_SECONDS = 0.2
ZWSP = '\u200B'
INACTIVITY_TIMEOUT_SECONDS = 2 * 60
MAIN_LOOP_POLLING_INTERVAL_SECONDS = 0.05
MAX_WORKER_THREADS = 10

ANONYMOUS_LOGIN_KEY = '_M85tFxFxIRDax_nh-HYm1gT'
SHIP_INVITE_LINK = 'https://drednot.io/invite/DkOtAEo9xavwyVlIq0qB-HvG'
WELCOME_MESSAGE_COOLDOWN_SECONDS = 5 * 60

if not BOT_SERVER_URL:
    print("CRITICAL: BOT_SERVER_URL environment variable is not set!")
    exit(1)

# --- JAVASCRIPT INJECTION SCRIPT (WITH MEMORY MANAGEMENT & PLAYER JOIN DETECTION) ---
MUTATION_OBSERVER_SCRIPT = """
    console.log('[Bot-JS] Initializing Observer with Memory Management, Spam Detection & Player Join Detection...');
    window.py_bot_events = [];

    // Arguments from Python
    const zwsp = arguments[0], allCommands = arguments[1], cooldownMs = arguments[2] * 1000,
          spamStrikeLimit = arguments[3], spamTimeoutMs = arguments[4] * 1000, spamResetMs = arguments[5] * 1000;

    window.botUserCooldowns = window.botUserCooldowns || {};
    window.botSpamTracker = window.botSpamTracker || {};
    const targetNode = document.getElementById('chat-content');
    if (!targetNode) { return '[Bot-JS-Error] Chat content not found.'; }

    const pruneChatDom = () => {
        const MAX_CHAT_MESSAGES = 250;
        const chatContent = document.getElementById('chat-content');
        if (chatContent && chatContent.children.length > MAX_CHAT_MESSAGES) {
            console.log(`[Bot-JS-Mem] Pruning DOM. Current: ${chatContent.children.length}`);
            while (chatContent.children.length > MAX_CHAT_MESSAGES) {
                chatContent.removeChild(chatContent.firstChild);
            }
            console.log(`[Bot-JS-Mem] Pruning complete. New: ${chatContent.children.length}`);
        }
    };
    if (!window.botDomPruneInterval) {
        console.log('[Bot-JS-Mem] Setting up periodic DOM pruning.');
        window.botDomPruneInterval = setInterval(pruneChatDom, 5 * 60 * 1000);
    }

    const callback = (mutationList, observer) => {
        const now = Date.now();
        for (const mutation of mutationList) {
            if (mutation.type !== 'childList') continue;
            for (const node of mutation.addedNodes) {
                if (node.nodeType !== 1 || node.tagName !== 'P' || node.dataset.botProcessed) continue;
                node.dataset.botProcessed = 'true';
                const pText = node.textContent || "";
                if (pText.startsWith(zwsp)) continue;
                if (pText.includes("Joined ship '")) {
                    const match = pText.match(/{[A-Z\\d]+}/);
                    if (match && match[0]) window.py_bot_events.push({ type: 'ship_joined', id: match[0] });
                    continue;
                }
                if (pText.includes(' joined the ship.')) {
                    const bdiElement = node.querySelector("bdi");
                    if (bdiElement) {
                        const username = bdiElement.innerText.trim();
                        if (!pText.includes(' left the ship.')) {
                            window.py_bot_events.push({ type: 'player_joined', username: username });
                        }
                    }
                    continue;
                }
                const colonIdx = pText.indexOf(':');
                if (colonIdx === -1) continue;
                const bdiElement = node.querySelector("bdi");
                if (!bdiElement) continue;
                const username = bdiElement.innerText.trim();
                const msgTxt = pText.substring(colonIdx + 1).trim();
                if (!msgTxt.startsWith('!')) continue;
                const parts = msgTxt.slice(1).trim().split(/ +/);
                const command = parts.shift().toLowerCase();
                if (!allCommands.includes(command)) continue;
                const spamTracker = window.botSpamTracker[username] = window.botSpamTracker[username] || { count: 0, lastCmd: '', lastTime: 0, penaltyUntil: 0 };
                if (now < spamTracker.penaltyUntil) continue;
                const lastCmdTime = window.botUserCooldowns[username] || 0;
                if (now - lastCmdTime < cooldownMs) continue;
                window.botUserCooldowns[username] = now;
                if (now - spamTracker.lastTime > spamResetMs || command !== spamTracker.lastCmd) { spamTracker.count = 1; } else { spamTracker.count++; }
                spamTracker.lastCmd = command; spamTracker.lastTime = now;
                if (spamTracker.count >= spamStrikeLimit) {
                    spamTracker.penaltyUntil = now + spamTimeoutMs; spamTracker.count = 0;
                    window.py_bot_events.push({ type: 'spam_detected', username: username, command: command });
                    continue;
                }
                window.py_bot_events.push({ type: 'command', command: command, username: username, args: parts });
            }
        }
    };
    const observer = new MutationObserver(callback);
    observer.observe(targetNode, { childList: true });
    console.log('[Bot-JS] Advanced Observer with Memory Management is now active.');
    return '[Bot-JS] Initialization successful.';
"""

class InvalidKeyError(Exception): pass

# --- GLOBAL STATE & THREADING PRIMITIVES ---
message_queue = queue.Queue(maxsize=100)
driver_lock = Lock()
inactivity_timer = None
driver = None
USER_COOLDOWN_SECONDS = 2.0
SPAM_STRIKE_LIMIT = 3
SPAM_TIMEOUT_SECONDS = 30
SPAM_RESET_SECONDS = 5
ALL_COMMANDS = ["bal", "balance", "craft", "cs", "csb", "crateshopbuy", "daily", "eat", "flip", "gather", "info", "inv", "inventory", "lb", "leaderboard", "m", "market", "marketbuy", "marketcancel", "marketsell", "mb", "mc", "ms", "n", "next", "p", "pay", "previous", "recipes", "slots", "smelt", "timers", "traitroll", "traits", "verify", "work","hourly"]

BOT_STATE = {
    "status": "Initializing...",
    "start_time": datetime.now(),
    "current_ship_id": "N/A",
    "last_command_info": "None yet.",
    "last_message_sent": "None yet.",
    "event_log": deque(maxlen=20),
    "welcome_message": "Welcome to the ship, {player}!",
    "welcome_message_delay": WELCOME_MESSAGE_COOLDOWN_SECONDS,
}
PLAYER_WELCOME_COOLDOWNS = {}

command_executor = ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS, thread_name_prefix='CmdWorker')
atexit.register(lambda: command_executor.shutdown(wait=True))

def log_event(message):
    timestamp = datetime.now().strftime('%H:%M:%S')
    BOT_STATE["event_log"].appendleft(f"[{timestamp}] {message}")

# --- BROWSER SETUP ---
def find_chromium_executable():
    path = shutil.which('chromium') or shutil.which('chromium-browser')
    if path: return path
    raise FileNotFoundError("Could not find chromium or chromium-browser.")

def setup_driver():
    print("Launching headless browser with performance flags...")
    chrome_options = Options(); chrome_options.add_argument("--headless=new"); chrome_options.add_argument("--no-sandbox"); chrome_options.add_argument("--disable-dev-shm-usage"); chrome_options.add_argument("--disable-gpu"); chrome_options.add_argument("--disable-extensions"); chrome_options.add_argument("--disable-infobars"); chrome_options.add_argument("--mute-audio"); chrome_options.add_argument("--disable-setuid-sandbox"); chrome_options.add_argument("--disable-images"); chrome_options.add_argument("--blink-settings=imagesEnabled=false"); chrome_options.binary_location = find_chromium_executable()
    return webdriver.Chrome(options=chrome_options)

# --- FLASK WEB UI ---
flask_app = Flask('')
@flask_app.route('/', methods=['GET', 'POST'])
def health_check():
    if request.method == 'POST':
        new_message = request.form.get('welcome_message')
        new_delay_str = request.form.get('welcome_delay', '').strip()
        if new_message is not None: BOT_STATE['welcome_message'] = new_message; log_event(f"Welcome message updated via UI.")
        if new_delay_str.isdigit():
            new_delay = int(new_delay_str)
            BOT_STATE['welcome_message_delay'] = new_delay
            log_event(f"Welcome delay updated to {new_delay}s via UI.")
        return redirect(url_for('health_check'))
    
    safe_welcome_message = html.escape(BOT_STATE['welcome_message'], quote=True)
    html_content = f"""
    <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta http-equiv="refresh" content="15">
    <title>Drednot Bot Status</title><style>body{{font-family:'Courier New',monospace;background-color:#1e1e1e;color:#d4d4d4;padding:20px;}}.container{{max-width:800px;margin:auto;background-color:#252526;border:1px solid #373737;padding:20px;border-radius:8px;}}h1,h2{{color:#4ec9b0;border-bottom:1px solid #4ec9b0;padding-bottom:5px;}}p{{line-height:1.6;}}ul{{list-style-type:none;padding-left:0;}}li{{background-color:#2d2d2d;margin-bottom:8px;padding:10px;border-radius:4px;white-space:pre-wrap;word-break:break-all;}}.label{{color:#9cdcfe;font-weight:bold;}}form label{{display:block;margin-top:15px;margin-bottom:5px;}}input[type="text"],input[type="number"]{{width:95%;padding:8px;background-color:#3c3c3c;border:1px solid #555;color:#d4d4d4;border-radius:4px;}}button[type="submit"]{{padding:10px 18px;background-color:#0e639c;color:white;border:none;border-radius:4px;cursor:pointer;margin-top:15px;}}</style></head>
    <body><div class="container"><h1>Drednot Bot Status</h1>
    <p><span class="label">Status:</span> {html.escape(BOT_STATE['status'])}</p>
    <p><span class="label">Current Ship ID:</span> {html.escape(BOT_STATE['current_ship_id'])}</p>
    <h2>Configuration</h2><form method="post">
        <label for="welcome_message">Welcome Message (use {{player}} as placeholder):</label>
        <input type="text" id="welcome_message" name="welcome_message" value="{safe_welcome_message}">
        <label for="welcome_delay">Welcome Message Cooldown (seconds):</label>
        <input type="number" id="welcome_delay" name="welcome_delay" value="{BOT_STATE['welcome_message_delay']}" min="0">
        <button type="submit">Save Configuration</button>
    </form>
    <h2>Bot Activity</h2><p><span class="label">Last Command:</span> {html.escape(BOT_STATE['last_command_info'])}</p>
    <p><span class="label">Last Message Sent:</span> {html.escape(BOT_STATE['last_message_sent'])}</p>
    <h2>Recent Events (Log)</h2><ul>{''.join(f'<li>{html.escape(event)}</li>' for event in BOT_STATE['event_log'])}</ul></div></body></html>
    """
    return Response(html_content, mimetype='text/html')

def run_flask():
    from waitress import serve
    port = int(os.environ.get("PORT", 10000))
    print(f"Health check server listening on port {port}")
    serve(flask_app, host='0.0.0.0', port=port)

# --- HELPER & CORE FUNCTIONS ---
def queue_reply(message):
    MAX_LEN = 199; lines = message if isinstance(message, list) else [message]
    for line in lines:
        text = str(line)
        while len(text) > 0:
            try:
                if len(text) <= MAX_LEN:
                    if text.strip(): message_queue.put(ZWSP + text, timeout=5); break
                else:
                    bp = text.rfind(' ', 0, MAX_LEN); chunk = text[:bp if bp > 0 else MAX_LEN].strip()
                    if chunk: message_queue.put(ZWSP + chunk, timeout=5)
                    text = text[bp if bp > 0 else MAX_LEN:].strip()
            except queue.Full: print("[WARN] Message queue is full."); log_event("WARN: Message queue full."); break

def message_processor_thread():
    while True:
        message = message_queue.get()
        try:
            with driver_lock:
                if driver: driver.execute_script("const msg=arguments[0];const chatBox=document.getElementById('chat');const chatInp=document.getElementById('chat-input');const chatBtn=document.getElementById('chat-send');if(chatBox&&chatBox.classList.contains('closed')){chatBtn.click();}if(chatInp){chatInp.value=msg;}chatBtn.click();", message)
            clean_msg = message[1:]; print(f"[BOT-SENT] {clean_msg}"); BOT_STATE["last_message_sent"] = clean_msg; log_event(f"SENT: {clean_msg}")
        except WebDriverException: pass
        except Exception as e: print(f"[ERROR] Unexpected error in message processor: {e}"); log_event(f"UNEXPECTED ERROR in message processor: {e}")
        time.sleep(MESSAGE_DELAY_SECONDS)

def process_api_call(command, username, args):
    command_str = f"!{command} {' '.join(args)}"
    try:
        response = requests.post(BOT_SERVER_URL, json={"command": command, "username": username, "args": args}, headers={"Content-Type": "application/json", "x-api-key": API_KEY}, timeout=15)
        response.raise_for_status(); data = response.json()
        if data.get("reply"): queue_reply(data["reply"])
    except requests.exceptions.RequestException as e: print(f"[API-ERROR] Failed to contact economy server for '{command_str}': {e}"); log_event(f"API-ERROR for '{username}': {e}"); queue_reply(f"@{username} Sorry, an error occurred while processing your command.")
    except Exception as e: print(f"[API-ERROR] Unexpected error processing '{command_str}': {e}"); log_event(f"UNEXPECTED API-ERROR for '{username}': {e}")

# --- RESTART/REJOIN LOGIC ---
def reset_inactivity_timer():
    global inactivity_timer
    if inactivity_timer: inactivity_timer.cancel()
    inactivity_timer = threading.Timer(INACTIVITY_TIMEOUT_SECONDS, attempt_soft_rejoin)
    inactivity_timer.start()

def attempt_soft_rejoin():
    log_event("Game inactivity detected. Attempting proactive rejoin."); BOT_STATE["status"] = "Proactive Rejoin..."; print(f"[REJOIN] No game activity for {INACTIVITY_TIMEOUT_SECONDS}s. Performing a proactive rejoin.")
    global driver
    try:
        with driver_lock:
            ship_id = BOT_STATE.get('current_ship_id');
            if not ship_id or ship_id == 'N/A': raise ValueError("Cannot rejoin, no known Ship ID.")
            try: driver.find_element(By.CSS_SELECTOR, "#disconnect-popup button").click()
            except:
                try: driver.find_element(By.ID, "exit_button").click()
                except: pass
            wait = WebDriverWait(driver, 15); wait.until(EC.presence_of_element_located((By.ID, 'shipyard')));
            clicked = driver.execute_script("const sid=arguments[0];const s=Array.from(document.querySelectorAll('.sy-id')).find(e=>e.textContent===sid);if(s){s.click();return true}document.querySelector('#shipyard section:nth-of-type(3) .btn-small')?.click();return false", ship_id)
            if not clicked: time.sleep(0.5); clicked = driver.execute_script("const sid=arguments[0];const s=Array.from(document.querySelectorAll('.sy-id')).find(e=>e.textContent===sid);if(s){s.click();return true}return false", ship_id)
            if not clicked: raise RuntimeError(f"Could not find ship {ship_id} in list.")
            wait.until(EC.presence_of_element_located((By.ID, 'chat-input'))); print("✅ Proactive rejoin successful!"); log_event("Proactive rejoin successful."); BOT_STATE["status"] = "Running"
            driver.execute_script(MUTATION_OBSERVER_SCRIPT, ZWSP, ALL_COMMANDS, USER_COOLDOWN_SECONDS, SPAM_STRIKE_LIMIT, SPAM_TIMEOUT_SECONDS, SPAM_RESET_SECONDS); reset_inactivity_timer()
    except Exception as e: log_event(f"Rejoin FAILED: {e}"); print(f"[REJOIN] Proactive rejoin failed: {e}. Triggering full restart."); driver.quit()

# --- MAIN BOT LOGIC ---
def start_bot(use_key_login):
    global driver
    BOT_STATE["status"] = "Launching Browser..."; log_event("Performing full start...")
    driver = setup_driver()
    with driver_lock:
        driver.get(SHIP_INVITE_LINK)
        wait = WebDriverWait(driver, 20)
        try:
            accept_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".modal-container .btn-green")))
            driver.execute_script("arguments[0].click();", accept_button)
            log_event("Clicked 'Accept' on notice.")

            if ANONYMOUS_LOGIN_KEY and use_key_login:
                log_event("Attempting to click 'Restore Key' link...")
                restore_link = wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(., 'Restore old anonymous key')]")))
                driver.execute_script("arguments[0].click();", restore_link)
                log_event("Clicked 'Restore old anonymous key' link via JS.")
                
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.modal-window input[maxlength="24"]'))).send_keys(ANONYMOUS_LOGIN_KEY)
                
                submit_btn = wait.until(EC.presence_of_element_located((By.XPATH, "//div[.//h2[text()='Restore Account Key']]//button[contains(@class, 'btn-green')]")))
                driver.execute_script("arguments[0].click();", submit_btn)

                wait.until(EC.invisibility_of_element_located((By.XPATH, "//div[.//h2[text()='Restore Account Key']]")))
                wait.until(EC.any_of(EC.presence_of_element_located((By.ID, "chat-input")), EC.presence_of_element_located((By.XPATH, "//h2[text()='Login Failed']"))))
                if driver.find_elements(By.XPATH, "//h2[text()='Login Failed']"): raise InvalidKeyError("Login Failed! Key may be invalid.")
                log_event("Login with key successful.")
            else:
                play_anon_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Play Anonymously')]")))
                driver.execute_script("arguments[0].click();", play_anon_button)
                log_event("Playing as new guest.")
        except TimeoutException as e: log_event(f"Login timed out: {e.msg}"); raise
        except ElementClickInterceptedException as e: log_event(f"Login click intercepted: {e.msg}"); raise
        except Exception as e: log_event(f"Login failed critically: {e}"); raise
        
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "chat-input")));
        print("[SYSTEM] Injecting JS Observer with Memory Management...");
        init_result = driver.execute_script(MUTATION_OBSERVER_SCRIPT, ZWSP, ALL_COMMANDS, USER_COOLDOWN_SECONDS, SPAM_STRIKE_LIMIT, SPAM_TIMEOUT_SECONDS, SPAM_RESET_SECONDS);
        log_event(f"Chat observer injected. Status: {init_result}")

    BOT_STATE["status"] = "Running"; queue_reply("Bot online."); reset_inactivity_timer();
    print(f"Event-driven chat monitor active. Polling every {MAIN_LOOP_POLLING_INTERVAL_SECONDS}s.")
    
    last_cooldown_cleanup = datetime.now()
    
    while True:
        try:
            with driver_lock: new_events = driver.execute_script("return window.py_bot_events.splice(0, window.py_bot_events.length);")
            if new_events:
                reset_inactivity_timer()
                for event in new_events:
                    if event['type'] == 'ship_joined' and event['id'] != BOT_STATE["current_ship_id"]:
                         BOT_STATE["current_ship_id"] = event['id']; log_event(f"Switched to new ship: {BOT_STATE['current_ship_id']}")
                    elif event['type'] == 'player_joined':
                        username = event['username']; now = datetime.now()
                        last_welcomed = PLAYER_WELCOME_COOLDOWNS.get(username)
                        cooldown_duration = timedelta(seconds=BOT_STATE.get('welcome_message_delay', 300))
                        if not last_welcomed or now - last_welcomed > cooldown_duration:
                            PLAYER_WELCOME_COOLDOWNS[username] = now
                            message_to_send = BOT_STATE["welcome_message"].replace("{player}", username)
                            queue_reply(message_to_send); log_event(f"Welcomed new player: {username}")
                        else:
                            log_event(f"Skipped welcoming {username} due to cooldown.")
                    elif event['type'] == 'command':
                        cmd, user, args = event['command'], event['username'], event['args']
                        command_str = f"!{cmd} {' '.join(args)}"; print(f"[BOT-RECV] Queued '{command_str}' from {user}")
                        BOT_STATE["last_command_info"] = f"{command_str} (from {user})"; log_event(f"RECV: {command_str} from {user}")
                        command_executor.submit(process_api_call, cmd, user, args)
                    elif event['type'] == 'spam_detected':
                        username, command = event['username'], event['command']
                        log_event(f"SPAM: Timed out '{username}' for {SPAM_TIMEOUT_SECONDS}s for spamming '!{command}'.")

            if datetime.now() - last_cooldown_cleanup > timedelta(minutes=15):
                now = datetime.now()
                cooldown_duration = timedelta(seconds=BOT_STATE.get('welcome_message_delay', 300))
                to_delete = [user for user, ts in PLAYER_WELCOME_COOLDOWNS.items() if now - ts > cooldown_duration]
                for user in to_delete: del PLAYER_WELCOME_COOLDOWNS[user]
                last_cooldown_cleanup = now
                if to_delete: log_event(f"Cleaned up {len(to_delete)} stale welcome cooldowns.")

        except WebDriverException as e: print(f"[ERROR] WebDriver exception in main loop. Assuming disconnect."); log_event(f"WebDriver error: {e.msg}"); raise
        time.sleep(MAIN_LOOP_POLLING_INTERVAL_SECONDS)

# --- MAIN EXECUTION ---
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=message_processor_thread, daemon=True).start()
    use_key_login = True; restart_times = deque(maxlen=10) 
    while True:
        if len(restart_times) == 10 and time.time() - restart_times[0] < 3600:
            log_event("CRITICAL: Bot is thrashing. Pausing for 5 minutes."); print("\n" + "="*60 + "\nCRITICAL: BOT RESTARTED >10 TIMES/HOUR. PAUSING FOR 5 MINS.\n" + "="*60 + "\n"); time.sleep(300)
            restart_times.clear()
        restart_times.append(time.time())
        try:
            BOT_STATE["start_time"] = datetime.now()
            start_bot(use_key_login)
        except InvalidKeyError as e:
            BOT_STATE["status"] = "Invalid Key! Switching to Guest Mode."; err_msg = f"CRITICAL: {e}. Switching to Guest Mode."; log_event(err_msg); print(f"[SYSTEM] {err_msg}"); use_key_login = False
        except Exception as e:
            BOT_STATE["status"] = f"Crashed! Restarting in 5s..."; log_event(f"CRITICAL ERROR: {e}"); print(f"\n[SYSTEM] Full restart required. Reason: {e}"); traceback.print_exc()
        finally:
            global driver;
            if inactivity_timer: inactivity_timer.cancel()
            if driver:
                try: driver.quit()
                except: pass
            driver = None; time.sleep(5)

if __name__ == "__main__":
    main()
