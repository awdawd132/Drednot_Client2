import os
import re
import json
import time
import shutil
import queue
import html # Used for escaping HTML in the web UI
import threading
import traceback
import requests
from datetime import datetime, timedelta
from collections import deque
from threading import Lock

# New Flask imports for handling form submissions
from flask import Flask, Response, request, redirect, url_for
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException, TimeoutException

# --- CONFIGURATION ---
BOT_SERVER_URL = os.environ.get("BOT_SERVER_URL")
API_KEY = 'drednot123'
MESSAGE_DELAY_SECONDS = 0.2
ZWSP = '\u200B'
INACTIVITY_TIMEOUT_SECONDS = 2 * 60
MAIN_LOOP_POLLING_INTERVAL_SECONDS = 0.25

ANONYMOUS_LOGIN_KEY = '_M85tFxFxIRDax_nh-HYm1gT' # Replace with your key if needed
SHIP_INVITE_LINK = 'https://drednot.io/invite/DkOtAEo9xavwyVlIq0qB-HvG'

# --- NEW: WELCOME MESSAGE COOLDOWN ---
WELCOME_MESSAGE_COOLDOWN_SECONDS = 5 * 60 # 5 minutes

if not BOT_SERVER_URL:
    print("CRITICAL: BOT_SERVER_URL environment variable is not set!")
    exit(1)

# --- JAVASCRIPT INJECTION SCRIPT (WITH MEMORY MANAGEMENT) ---
# --- MODIFIED: Added implementation for pruneChatDom to prevent memory leaks ---
MUTATION_OBSERVER_SCRIPT = """
    console.log('[Bot-JS] Initializing Observer with Memory Management, Spam Detection & Player Join Detection...');
    window.py_bot_events = []; // Holds events for Python to poll

    // Arguments from Python
    const zwsp = arguments[0];
    const allCommands = arguments[1];
    const cooldownMs = arguments[2] * 1000;
    const spamStrikeLimit = arguments[3];
    const spamTimeoutMs = arguments[4] * 1000;
    const spamResetMs = arguments[5] * 1000;

    // State storage
    window.botUserCooldowns = window.botUserCooldowns || {};
    window.botSpamTracker = window.botSpamTracker || {};

    const targetNode = document.getElementById('chat-content');
    if (!targetNode) { return '[Bot-JS-Error] Chat content not found.'; }

    // --- NEW: MEMORY MANAGEMENT IMPLEMENTATION ---
    const pruneChatDom = () => {
        const MAX_CHAT_MESSAGES = 250; // Keep the last 250 messages
        const chatContent = document.getElementById('chat-content');
        if (chatContent && chatContent.children.length > MAX_CHAT_MESSAGES) {
            console.log(`[Bot-JS-Mem] Pruning DOM. Current: ${chatContent.children.length}`);
            while (chatContent.children.length > MAX_CHAT_MESSAGES) {
                chatContent.removeChild(chatContent.firstChild);
            }
            console.log(`[Bot-JS-Mem] Pruning complete. New: ${chatContent.children.length}`);
        }
    };

    // Set up periodic pruning if it hasn't been done already.
    if (!window.botDomPruneInterval) {
        console.log('[Bot-JS-Mem] Setting up periodic DOM pruning.');
        window.botDomPruneInterval = setInterval(pruneChatDom, 5 * 60 * 1000); // Run every 5 minutes
    }
    // --- END NEW MEMORY MANAGEMENT ---


    const callback = (mutationList, observer) => {
        const now = Date.now();
        for (const mutation of mutationList) {
            if (mutation.type === 'childList') {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType !== 1 || node.tagName !== 'P' || node.dataset.botProcessed) continue;
                    node.dataset.botProcessed = 'true';

                    const pText = node.textContent || "";
                    if (pText.startsWith(zwsp)) continue;

                    // --- Event Handlers ---
                    // 1. Ship ID detection
                    if (pText.includes("Joined ship '")) {
                        const match = pText.match(/{[A-Z\\d]+}/);
                        if (match && match[0]) window.py_bot_events.push({ type: 'ship_joined', id: match[0] });
                        continue;
                    }

                    // 2. Player Join detection
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

                    // 3. Command detection (rest of the script is unchanged)
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
                        spamTracker.penaltyUntil = now + spamTimeoutMs;
                        spamTracker.count = 0;
                        window.py_bot_events.push({ type: 'spam_detected', username: username, command: command });
                        continue;
                    }
                    window.py_bot_events.push({ type: 'command', command: command, username: username, args: parts });
                }
            }
        }
    };
    const observer = new MutationObserver(callback);
    observer.observe(targetNode, { childList: true });
    console.log('[Bot-JS] Advanced Observer with Memory Management is now active.');
    return '[Bot-JS] Initialization successful.';
"""

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

# --- MODIFIED BOT STATE with Welcome Message and Cooldown tracking ---
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
# --- NEW: Dictionary to track welcome message cooldowns per player ---
PLAYER_WELCOME_COOLDOWNS = {}


def log_event(message):
    timestamp = datetime.now().strftime('%H:%M:%S')
    BOT_STATE["event_log"].appendleft(f"[{timestamp}] {message}")

# --- BROWSER & FLASK SETUP ---
def find_chromium_executable():
    # This function is unchanged
    path = shutil.which('chromium') or shutil.which('chromium-browser')
    if path: return path
    raise FileNotFoundError("Could not find chromium or chromium-browser.")

def setup_driver():
    print("Launching headless browser with performance flags...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--mute-audio")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("--disable-images")
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    chrome_options.binary_location = find_chromium_executable()
    return webdriver.Chrome(options=chrome_options)

flask_app = Flask('')

# --- MODIFIED: FLASK ROUTE to handle GET and POST for the form ---
@flask_app.route('/', methods=['GET', 'POST'])
def health_check():
    # Handle the form submission to update configuration
    if request.method == 'POST':
        new_message = request.form.get('welcome_message')
        new_delay_str = request.form.get('welcome_delay', '').strip()

        if new_message is not None:
            BOT_STATE['welcome_message'] = new_message
            log_event(f"Welcome message updated via UI.")
            print(f"[CONFIG] Welcome message updated to: '{new_message}'")

        if new_delay_str.isdigit():
            new_delay = int(new_delay_str)
            BOT_STATE['welcome_message_delay'] = new_delay
            # Also update the global variable for the running logic
            global WELCOME_MESSAGE_COOLDOWN_SECONDS
            WELCOME_MESSAGE_COOLDOWN_SECONDS = new_delay
            log_event(f"Welcome delay updated to {new_delay}s via UI.")
            print(f"[CONFIG] Welcome delay updated to: {new_delay}s")

        return redirect(url_for('health_check'))

    # Display the status page for GET requests
    safe_welcome_message = html.escape(BOT_STATE['welcome_message'], quote=True)
    html_content = f"""
    <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta http-equiv="refresh" content="15">
    <title>Drednot Bot Status</title><style>body{{font-family:'Courier New',monospace;background-color:#1e1e1e;color:#d4d4d4;padding:20px;}}.container{{max-width:800px;margin:auto;background-color:#252526;border:1px solid #373737;padding:20px;border-radius:8px;}}h1,h2{{color:#4ec9b0;border-bottom:1px solid #4ec9b0;padding-bottom:5px;}}p{{line-height:1.6;}}ul{{list-style-type:none;padding-left:0;}}li{{background-color:#2d2d2d;margin-bottom:8px;padding:10px;border-radius:4px;white-space:pre-wrap;word-break:break-all;}}.label{{color:#9cdcfe;font-weight:bold;}}form label{{display:block;margin-top:15px;margin-bottom:5px;}}input[type="text"],input[type="number"]{{width:95%;padding:8px;background-color:#3c3c3c;border:1px solid #555;color:#d4d4d4;border-radius:4px;}}button[type="submit"]{{padding:10px 18px;background-color:#0e639c;color:white;border:none;border-radius:4px;cursor:pointer;margin-top:15px;}}</style></head>
    <body><div class="container"><h1>Drednot Bot Status</h1>
    <p><span class="label">Status:</span> {html.escape(BOT_STATE['status'])}</p>
    <p><span class="label">Current Ship ID:</span> {html.escape(BOT_STATE['current_ship_id'])}</p>
    
    <h2>Configuration</h2>
    <form method="post">
        <label for="welcome_message">Welcome Message (use {{player}} as placeholder):</label>
        <input type="text" id="welcome_message" name="welcome_message" value="{safe_welcome_message}">
        
        <label for="welcome_delay">Welcome Message Cooldown (seconds):</label>
        <input type="number" id="welcome_delay" name="welcome_delay" value="{BOT_STATE['welcome_message_delay']}" min="0">
        
        <button type="submit">Save Configuration</button>
    </form>
    
    <h2>Bot Activity</h2>
    <p><span class="label">Last Command:</span> {html.escape(BOT_STATE['last_command_info'])}</p>
    <p><span class="label">Last Message Sent:</span> {html.escape(BOT_STATE['last_message_sent'])}</p>
    <h2>Recent Events (Log)</h2><ul>{''.join(f'<li>{html.escape(event)}</li>' for event in BOT_STATE['event_log'])}</ul></div></body></html>
    """
    return Response(html_content, mimetype='text/html')

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    print(f"Health check server listening on port {port}")
    # Use waitress or gunicorn in a real deployment instead of flask's dev server
    from waitress import serve
    serve(flask_app, host='0.0.0.0', port=port)


# --- HELPER & CORE FUNCTIONS ---
def queue_reply(message):
    # This function is unchanged
    MAX_LEN = 199; lines = message if isinstance(message, list) else [message]
    for line in lines:
        text = str(line)
        while len(text) > 0:
            try:
                if len(text) <= MAX_LEN:
                    if text.strip(): message_queue.put(ZWSP + text, timeout=5)
                    break
                else:
                    bp = text.rfind(' ', 0, MAX_LEN); chunk = text[:bp if bp > 0 else MAX_LEN].strip()
                    if chunk: message_queue.put(ZWSP + chunk, timeout=5)
                    text = text[bp if bp > 0 else MAX_LEN:].strip()
            except queue.Full: print("[WARN] Message queue is full."); log_event("WARN: Message queue full."); break

def message_processor_thread():
    # This function is unchanged
    while True:
        try:
            message = message_queue.get()
            with driver_lock:
                if driver: driver.execute_script("const msg=arguments[0];const chatBox=document.getElementById('chat');const chatInp=document.getElementById('chat-input');const chatBtn=document.getElementById('chat-send');if(chatBox&&chatBox.classList.contains('closed')){chatBtn.click();}if(chatInp){chatInp.value=msg;}chatBtn.click();", message)
            clean_msg = message[1:]; print(f"[BOT-SENT] {clean_msg}"); BOT_STATE["last_message_sent"] = clean_msg; log_event(f"SENT: {clean_msg}")
        except WebDriverException: pass # Driver likely closed, main loop will handle
        except Exception as e: print(f"[ERROR] Unexpected error in message processor: {e}"); log_event(f"UNEXPECTED ERROR in message processor: {e}")
        time.sleep(MESSAGE_DELAY_SECONDS)

# Other core functions are unchanged...
def process_remote_command(command, username, args):
    reset_inactivity_timer()
    command_str = f"!{command} {' '.join(args)}"
    print(f"[BOT-RECV] {command_str} from {username}")
    BOT_STATE["last_command_info"] = f"{command_str} (from {username})"
    log_event(f"RECV: {command_str} from {username}")
    try:
        response = requests.post(BOT_SERVER_URL, json={"command": command, "username": username, "args": args}, headers={"Content-Type": "application/json", "x-api-key": API_KEY}, timeout=10)
        response.raise_for_status(); data = response.json()
        if data.get("reply"): queue_reply(data["reply"])
    except requests.exceptions.RequestException as e: print(f"[API-ERROR] Failed to contact economy server: {e}"); log_event(f"API-ERROR: {e}")

def reset_inactivity_timer():
    pass # Inactivity re-join logic removed for simplicity, as full restart is more robust

def attempt_soft_rejoin():
    pass # This logic is less effective than a full restart, so we rely on the main loop's restart.

class InvalidKeyError(Exception): pass

# --- MAIN BOT LOGIC ---
def start_bot(use_key_login):
    global driver
    BOT_STATE["status"] = "Launching Browser..."
    log_event("Performing full start...")
    driver = setup_driver()
    
    with driver_lock:
        print("Navigating to Drednot.io invite link..."); driver.get(SHIP_INVITE_LINK); print("Page loaded. Handling login procedure...")
        wait = WebDriverWait(driver, 15)
        try:
            btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".modal-container .btn-green"))); driver.execute_script("arguments[0].click();", btn); print("Clicked 'Accept' on notice.")
            if ANONYMOUS_LOGIN_KEY and use_key_login:
                print("Attempting to log in with anonymous key."); log_event("Attempting login with key.")
                link = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(., 'Restore old anonymous key')]"))); driver.execute_script("arguments[0].click();", link); print("Clicked 'Restore old anonymous key'.")
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.modal-window input[maxlength="24"]'))).send_keys(ANONYMOUS_LOGIN_KEY)
                submit_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//div[.//h2[text()='Restore Account Key']]//button[contains(@class, 'btn-green')]")));
                driver.execute_script("arguments[0].click();", submit_btn); print("Submitted key.")
                wait.until(EC.invisibility_of_element_located((By.XPATH, "//div[.//h2[text()='Restore Account Key']]")));
                wait.until(EC.any_of(EC.presence_of_element_located((By.ID, "chat-input")), EC.presence_of_element_located((By.XPATH, "//h2[text()='Login Failed']"))))
                if driver.find_elements(By.XPATH, "//h2[text()='Login Failed']"): raise InvalidKeyError("Login Failed! Key may be invalid.")
                print("✅ Successfully logged in with key."); log_event("Login with key successful.")
            else:
                log_event("Playing as new guest."); print("Playing as a new guest.")
                play_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Play Anonymously')]"))); driver.execute_script("arguments[0].click();", play_btn); print("Clicked 'Play Anonymously'.")
        except TimeoutException: print("Login timed out. Assuming already in-game."); log_event("Login timeout. Assuming in-game.")
        except Exception as e: log_event(f"Login failed critically: {e}"); raise e

        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "chat-input")));
        print("[SYSTEM] Injecting JS Observer with Memory Management...");
        # --- MODIFIED: Uses the updated script with memory management ---
        init_result = driver.execute_script(MUTATION_OBSERVER_SCRIPT, ZWSP, ALL_COMMANDS, USER_COOLDOWN_SECONDS, SPAM_STRIKE_LIMIT, SPAM_TIMEOUT_SECONDS, SPAM_RESET_SECONDS);
        log_event(f"Chat observer injected. Status: {init_result}")

    BOT_STATE["status"] = "Running"; queue_reply("Bot online."); print(f"Event-driven chat monitor active. Polling every {MAIN_LOOP_POLLING_INTERVAL_SECONDS}s.")

    last_cooldown_cleanup = datetime.now()

    while True:
        try:
            with driver_lock: new_events = driver.execute_script("return window.py_bot_events.splice(0, window.py_bot_events.length);")
            if new_events:
                for event in new_events:
                    # --- MODIFIED EVENT HANDLING with Welcome Cooldown ---
                    if event['type'] == 'ship_joined':
                         if event['id'] != BOT_STATE["current_ship_id"]: BOT_STATE["current_ship_id"] = event['id']; log_event(f"Switched to new ship: {BOT_STATE['current_ship_id']}")
                    
                    elif event['type'] == 'player_joined':
                        username = event['username']
                        now = datetime.now()
                        
                        # --- NEW: Welcome message cooldown logic ---
                        last_welcomed = PLAYER_WELCOME_COOLDOWNS.get(username)
                        cooldown_duration = timedelta(seconds=BOT_STATE.get('welcome_message_delay', 300))

                        if not last_welcomed or now - last_welcomed > cooldown_duration:
                            PLAYER_WELCOME_COOLDOWNS[username] = now
                            welcome_template = BOT_STATE["welcome_message"]
                            message_to_send = welcome_template.replace("{player}", username)
                            queue_reply(message_to_send)
                            log_event(f"Welcomed new player: {username}")
                        else:
                            log_event(f"Skipped welcoming {username} due to cooldown.")

                    elif event['type'] == 'command':
                        process_remote_command(event['command'], event['username'], event['args'])
                    elif event['type'] == 'spam_detected':
                        username, command = event['username'], event['command']
                        log_event(f"SPAM: Timed out '{username}' for {SPAM_TIMEOUT_SECONDS}s for spamming '!{command}'.")
                        print(f"[SPAM-DETECT] Timed out '{username}' for spamming '!{command}'.")

            # --- NEW: Periodic cleanup of the welcome cooldown dictionary ---
            if datetime.now() - last_cooldown_cleanup > timedelta(minutes=15):
                now = datetime.now()
                cooldown_duration = timedelta(seconds=BOT_STATE.get('welcome_message_delay', 300))
                # Create a list of users to delete to avoid modifying dict while iterating
                to_delete = [user for user, timestamp in PLAYER_WELCOME_COOLDOWNS.items() if now - timestamp > cooldown_duration]
                for user in to_delete:
                    del PLAYER_WELCOME_COOLDOWNS[user]
                last_cooldown_cleanup = now
                log_event(f"Cleaned up {len(to_delete)} stale entries from welcome cooldowns.")

        except WebDriverException as e: print(f"[ERROR] WebDriver exception in main loop. Assuming disconnect."); log_event(f"WebDriver error in main loop: {e.msg}"); raise
        time.sleep(MAIN_LOOP_POLLING_INTERVAL_SECONDS)

# --- MAIN EXECUTION with enhanced restart logic ---
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=message_processor_thread, daemon=True).start()
    use_key_login = True
    
    # --- NEW: Thrashing detection to prevent rapid restart loops ---
    restart_times = deque(maxlen=10) 

    while True:
        # Check if we are thrashing (restarting too often)
        if len(restart_times) == 10 and time.time() - restart_times[0] < 3600: # 10 restarts in an hour
            log_event("CRITICAL: Bot is thrashing. Pausing for 5 minutes."); 
            print("\n" + "="*60 + "\nCRITICAL: BOT RESTARTED >10 TIMES/HOUR. PAUSING FOR 5 MINS.\n" + "="*60 + "\n"); 
            time.sleep(300)
            # Clear the recent restart times after pausing
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
            global driver
            if driver:
                try: 
                    print("[SYSTEM] Cleaning up old browser instance...")
                    driver.quit()
                except Exception as e:
                    print(f"[SYSTEM] Error during driver cleanup (this is usually okay): {e}")
            driver = None
            time.sleep(5) # Wait 5 seconds before restarting

if __name__ == "__main__":
    main()
