import os
import re
import json
import time
import shutil
import queue
import html # New import for escaping HTML in the web UI
import threading
import traceback
import requests
from datetime import datetime
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

if not BOT_SERVER_URL:
    print("CRITICAL: BOT_SERVER_URL environment variable is not set!")
    exit(1)

# --- JAVASCRIPT INJECTION SCRIPT (WITH WELCOME MESSAGE & PLAYER JOIN DETECTION) ---
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

    // Memory management functions (unchanged)
    const pruneChatDom = () => { /* ... snip ... */ };
    const cleanupOldUsers = () => { /* ... snip ... */ };
    if (!window.botDomPruneInterval) { setInterval(pruneChatDom, 5 * 60 * 1000); }
    if (!window.botCleanupInterval) { setInterval(cleanupOldUsers, 15 * 60 * 1000); }

    const callback = (mutationList, observer) => {
        const now = Date.now();
        for (const mutation of mutationList) {
            if (mutation.type === 'childList') {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType !== 1 || node.tagName !== 'P' || node.dataset.botProcessed) continue;
                    node.dataset.botProcessed = 'true';

                    const pText = node.textContent || "";
                    if (pText.startsWith(zwsp)) continue; // Ignore bot's own messages

                    // --- Event Handlers ---
                    // 1. Ship ID detection
                    if (pText.includes("Joined ship '")) {
                        const match = pText.match(/{[A-Z\\d]+}/);
                        if (match && match[0]) window.py_bot_events.push({ type: 'ship_joined', id: match[0] });
                        continue;
                    }

                    // 2. NEW: Player Join detection
                    if (pText.includes(' joined the ship.')) {
                        const bdiElement = node.querySelector("bdi");
                        if (bdiElement) {
                            const username = bdiElement.innerText.trim();
                            // Don't welcome players who are leaving and rejoining in the same message.
                            if (!pText.includes(' left the ship.')) {
                                window.py_bot_events.push({ type: 'player_joined', username: username });
                            }
                        }
                        continue; // This is a system message, not a command.
                    }

                    // 3. Command detection
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

                    // --- SPAM & COOLDOWN CHECKS (unchanged) ---
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
                    // --- END CHECKS ---

                    window.py_bot_events.push({ type: 'command', command: command, username: username, args: parts });
                }
            }
        }
    };
    const observer = new MutationObserver(callback);
    observer.observe(targetNode, { childList: true });
    console.log('[Bot-JS] Advanced Observer with Welcome Message is now active.');
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

# --- UPDATED BOT STATE with Welcome Message ---
BOT_STATE = {
    "status": "Initializing...",
    "start_time": datetime.now(),
    "current_ship_id": "N/A",
    "last_command_info": "None yet.",
    "last_message_sent": "None yet.",
    "event_log": deque(maxlen=20),
    "welcome_message": "Welcome to the ship, {player}!", # Default welcome message
}

def log_event(message):
    timestamp = datetime.now().strftime('%H:%M:%S')
    BOT_STATE["event_log"].appendleft(f"[{timestamp}] {message}")

# --- BROWSER & FLASK SETUP ---
def find_chromium_executable():
    path = shutil.which('chromium') or shutil.which('chromium-browser')
    if path: return path
    raise FileNotFoundError("Could not find chromium or chromium-browser.")

def setup_driver():
    print("Launching headless browser with performance flags...")
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    # ... other options are the same
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

# --- UPDATED FLASK ROUTE to handle GET and POST for the form ---
@flask_app.route('/', methods=['GET', 'POST'])
def health_check():
    # Handle the form submission to update the welcome message
    if request.method == 'POST':
        new_message = request.form.get('welcome_message')
        if new_message is not None:
            BOT_STATE['welcome_message'] = new_message
            log_event(f"Welcome message updated: '{new_message}'")
            print(f"[CONFIG] Welcome message updated to: '{new_message}'")
        return redirect(url_for('health_check')) # Redirect to the same page to show the update

    # Display the status page for GET requests
    # Use html.escape to prevent issues if the message contains special characters
    safe_welcome_message = html.escape(BOT_STATE['welcome_message'], quote=True)
    html_content = f"""
    <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta http-equiv="refresh" content="15">
    <title>Drednot Bot Status</title><style>body{{font-family:'Courier New',monospace;background-color:#1e1e1e;color:#d4d4d4;padding:20px;}}.container{{max-width:800px;margin:auto;background-color:#252526;border:1px solid #373737;padding:20px;border-radius:8px;}}h1,h2{{color:#4ec9b0;border-bottom:1px solid #4ec9b0;padding-bottom:5px;}}p{{line-height:1.6;}}ul{{list-style-type:none;padding-left:0;}}li{{background-color:#2d2d2d;margin-bottom:8px;padding:10px;border-radius:4px;white-space:pre-wrap;word-break:break-all;}}.label{{color:#9cdcfe;font-weight:bold;}}form label{{display:block;margin-top:10px;margin-bottom:5px;}}input[type="text"]{{width:95%;padding:8px;background-color:#3c3c3c;border:1px solid #555;color:#d4d4d4;border-radius:4px;}}button[type="submit"]{{padding:8px 15px;background-color:#0e639c;color:white;border:none;border-radius:4px;cursor:pointer;margin-top:10px;}}</style></head>
    <body><div class="container"><h1>Drednot Bot Status</h1>
    <p><span class="label">Status:</span> {BOT_STATE['status']}</p>
    <p><span class="label">Current Ship ID:</span> {BOT_STATE['current_ship_id']}</p>
    
    <h2>Configuration</h2>
    <form method="post">
        <label for="welcome_message">Welcome Message (use {{player}} as placeholder):</label>
        <input type="text" id="welcome_message" name="welcome_message" value='{safe_welcome_message}'>
        <button type="submit">Save Message</button>
    </form>
    
    <h2>Bot Activity</h2>
    <p><span class="label">Last Command:</span> {BOT_STATE['last_command_info']}</p>
    <p><span class="label">Last Message Sent:</span> {BOT_STATE['last_message_sent']}</p>
    <h2>Recent Events (Log)</h2><ul>{''.join(f'<li>{event}</li>' for event in BOT_STATE['event_log'])}</ul></div></body></html>
    """
    return Response(html_content, mimetype='text/html')

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    print(f"Health check server listening on port {port}")
    flask_app.run(host='0.0.0.0', port=port)

# --- HELPER & CORE FUNCTIONS (Largely unchanged) ---
def queue_reply(message):
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
    while True:
        message = message_queue.get()
        try:
            with driver_lock:
                if driver: driver.execute_script("const msg=arguments[0];const chatBox=document.getElementById('chat');const chatInp=document.getElementById('chat-input');const chatBtn=document.getElementById('chat-send');if(chatBox&&chatBox.classList.contains('closed')){chatBtn.click();}if(chatInp){chatInp.value=msg;}chatBtn.click();", message)
            clean_msg = message[1:]; print(f"[BOT-SENT] {clean_msg}"); BOT_STATE["last_message_sent"] = clean_msg; log_event(f"SENT: {clean_msg}")
        except WebDriverException: pass
        except Exception as e: print(f"[ERROR] Unexpected error in message processor: {e}"); log_event(f"UNEXPECTED ERROR in message processor: {e}")
        time.sleep(MESSAGE_DELAY_SECONDS)

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

# --- RESTART/REJOIN LOGIC (Unchanged) ---
def reset_inactivity_timer():
    global inactivity_timer
    if inactivity_timer: inactivity_timer.cancel()
    inactivity_timer = threading.Timer(INACTIVITY_TIMEOUT_SECONDS, attempt_soft_rejoin)
    inactivity_timer.start()

def attempt_soft_rejoin():
    # ... This function remains the same ...
    pass # Placeholder for brevity, it is unchanged from your previous version.

# --- MAIN BOT LOGIC (Largely unchanged) ---
def start_bot(use_key_login):
    # ... This function remains largely the same ...
    # It just needs to use the updated MUTATION_OBSERVER_SCRIPT
    global driver
    BOT_STATE["status"] = "Launching Browser..."
    log_event("Performing full start...")
    driver = setup_driver()
    # ... The rest of the start_bot function is the same as the previous memory-optimized version
    # It will automatically use the new MUTATION_OBSERVER_SCRIPT defined above.
    # We will just paste the rest of it here for completeness
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
        print("[SYSTEM] Injecting JS Observer with Welcome Message Detection...");
        init_result = driver.execute_script(MUTATION_OBSERVER_SCRIPT, ZWSP, ALL_COMMANDS, USER_COOLDOWN_SECONDS, SPAM_STRIKE_LIMIT, SPAM_TIMEOUT_SECONDS, SPAM_RESET_SECONDS);
        log_event(f"Chat observer injected. Status: {init_result}")
        # ... rest of the function continues as before...
        log_event("Proactively scanning for Ship ID...")
        PROACTIVE_SCAN_SCRIPT = "return Array.from(document.querySelectorAll('#chat-content p')).map(p => p.textContent).join('\\n').match(/{[A-Z\\d]+}/)?.[0] || null;"
        found_id = driver.execute_script(PROACTIVE_SCAN_SCRIPT)
        if found_id:
            BOT_STATE["current_ship_id"] = found_id
            log_event(f"Confirmed Ship ID via scan: {found_id}")
            print(f"✅ Confirmed Ship ID via scan: {found_id}")

    BOT_STATE["status"] = "Running"; queue_reply("Bot online."); reset_inactivity_timer(); print(f"Event-driven chat monitor active. Polling every {MAIN_LOOP_POLLING_INTERVAL_SECONDS}s.")
    while True:
        try:
            with driver_lock: new_events = driver.execute_script("return window.py_bot_events.splice(0, window.py_bot_events.length);")
            if new_events:
                reset_inactivity_timer()
                for event in new_events:
                    # --- UPDATED EVENT HANDLING ---
                    if event['type'] == 'ship_joined':
                         if event['id'] != BOT_STATE["current_ship_id"]:
                             BOT_STATE["current_ship_id"] = event['id']
                             log_event(f"Switched to new ship: {BOT_STATE['current_ship_id']}")
                         elif BOT_STATE["current_ship_id"] == 'N/A':
                             BOT_STATE["current_ship_id"] = event['id']
                             log_event(f"Confirmed Ship ID via event: {event['id']}")
                             print(f"✅ Confirmed Ship ID via event: {event['id']}")
                    
                    elif event['type'] == 'player_joined':
                        username = event['username']
                        # Format the welcome message from the stored template
                        welcome_template = BOT_STATE["welcome_message"]
                        message_to_send = welcome_template.replace("{player}", username)
                        queue_reply(message_to_send)
                        log_event(f"Welcomed new player: {username}")

                    elif event['type'] == 'command':
                        process_remote_command(event['command'], event['username'], event['args'])
                    elif event['type'] == 'spam_detected':
                        username, command = event['username'], event['command']
                        log_event(f"SPAM: Timed out '{username}' for {SPAM_TIMEOUT_SECONDS}s for spamming '!{command}'.")
                        print(f"[SPAM-DETECT] Timed out '{username}' for spamming '!{command}'.")

            if BOT_STATE["current_ship_id"] == 'N/A' and (datetime.now() - BOT_STATE["start_time"]).total_seconds() > 30:
                 raise RuntimeError("Failed to get Ship ID after 30 seconds.")
        except WebDriverException as e: print(f"[ERROR] WebDriver exception in main loop. Assuming disconnect."); log_event(f"WebDriver error in main loop: {e.msg}"); raise
        time.sleep(MAIN_LOOP_POLLING_INTERVAL_SECONDS)

# --- MAIN EXECUTION (Unchanged) ---
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=message_processor_thread, daemon=True).start()
    use_key_login = True; restart_count = 0; last_restart_time = time.time()
    while True:
        # ... This function remains the same ...
        current_time = time.time();
        if current_time - last_restart_time < 3600: restart_count += 1
        else: restart_count = 1
        last_restart_time = current_time
        if restart_count > 10:
            log_event("CRITICAL: Bot is thrashing. Pausing for 5 minutes."); print("\n" + "="*60 + "\nCRITICAL: BOT RESTARTED >10 TIMES/HOUR. PAUSING FOR 5 MINS.\n" + "="*60 + "\n"); time.sleep(300)
        try:
            BOT_STATE["start_time"] = datetime.now()
            start_bot(use_key_login)
        except InvalidKeyError as e:
            BOT_STATE["status"] = "Invalid Key!"; err_msg = f"CRITICAL: {e}. Switching to Guest Mode."; log_event(err_msg); print(f"[SYSTEM] {err_msg}"); use_key_login = False
        except Exception as e:
            BOT_STATE["status"] = f"Crashed! Restarting..."; log_event(f"CRITICAL ERROR: {e}"); print(f"[SYSTEM] Full restart. Reason: {e}"); traceback.print_exc()
        finally:
            global driver;
            if inactivity_timer: inactivity_timer.cancel()
            if driver:
                try: driver.quit()
                except: pass
            driver = None; time.sleep(5)

if __name__ == "__main__":
    main()
