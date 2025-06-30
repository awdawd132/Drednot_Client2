# drednot_bot.py (Faster Version with Advanced Spam Detection)

import os
import re
import json
import time
import shutil
import queue
import threading
import traceback
import requests
from datetime import datetime
from collections import deque
from threading import Lock

from flask import Flask, Response
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
MAIN_LOOP_POLLING_INTERVAL_SECONDS = 0.1

ANONYMOUS_LOGIN_KEY = '_M85tFxFxIRDax_nh-HYm1gT' # Replace with your key if needed
SHIP_INVITE_LINK = 'https://drednot.io/invite/DkOtAEo9xavwyVlIq0qB-HvG'

if not BOT_SERVER_URL:
    print("CRITICAL: BOT_SERVER_URL environment variable is not set!")
    exit(1)

# --- JAVASCRIPT INJECTION SCRIPT (WITH ADVANCED SPAM DETECTION) ---
MUTATION_OBSERVER_SCRIPT = """
    console.log('[Bot-JS] Initializing Observer with Advanced Spam Detection...');
    window.py_bot_events = []; // Holds events for Python to poll

    // Arguments from Python
    const zwsp = arguments[0];
    const allCommands = arguments[1];
    const cooldownMs = arguments[2] * 1000;
    const spamStrikeLimit = arguments[3];
    const spamTimeoutMs = arguments[4] * 1000;
    const spamResetMs = arguments[5] * 1000;

    // State storage (persists between script runs if page isn't reloaded)
    window.botUserCooldowns = window.botUserCooldowns || {}; // For the basic 2s cooldown
    window.botSpamTracker = window.botSpamTracker || {};   // For advanced spam tracking

    const targetNode = document.getElementById('chat-content');
    if (!targetNode) { return; }

    const callback = (mutationList, observer) => {
        const now = Date.now();
        for (const mutation of mutationList) {
            if (mutation.type === 'childList') {
                for (const node of mutation.addedNodes) {
                    if (node.nodeType !== 1 || node.tagName !== 'P' || node.dataset.botProcessed) continue;
                    node.dataset.botProcessed = 'true'; // Process each node only once

                    const pText = node.textContent || "";
                    if (pText.startsWith(zwsp)) continue;

                    // --- Event Handlers ---
                    if (pText.includes("Joined ship '")) {
                        const match = pText.match(/{[A-Z\\d]+}/);
                        if (match && match[0]) window.py_bot_events.push({ type: 'ship_joined', id: match[0] });
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

                    // --- SPAM & COOLDOWN CHECKS ---
                    const spamTracker = window.botSpamTracker[username] = window.botSpamTracker[username] || { count: 0, lastCmd: '', lastTime: 0, penaltyUntil: 0 };

                    // 1. Is user on a penalty timeout?
                    if (now < spamTracker.penaltyUntil) {
                        continue; // Silently ignore. They are timed out.
                    }

                    // 2. Is user on the basic command cooldown?
                    const lastCmdTime = window.botUserCooldowns[username] || 0;
                    if (now - lastCmdTime < cooldownMs) {
                        continue; // Silently ignore.
                    }
                    window.botUserCooldowns[username] = now; // Update basic cooldown time

                    // 3. Process spam strikes
                    // Reset strike count if too much time has passed or command is different
                    if (now - spamTracker.lastTime > spamResetMs || command !== spamTracker.lastCmd) {
                        spamTracker.count = 1;
                    } else {
                        spamTracker.count++;
                    }
                    spamTracker.lastCmd = command;
                    spamTracker.lastTime = now;

                    // 4. Check if strike limit is reached
                    if (spamTracker.count >= spamStrikeLimit) {
                        spamTracker.penaltyUntil = now + spamTimeoutMs; // Apply penalty
                        spamTracker.count = 0; // Reset counter
                        window.py_bot_events.push({ type: 'spam_detected', username: username, command: command });
                        continue; // Ignore the command that triggered the penalty
                    }
                    // --- END CHECKS ---

                    // If all checks pass, queue the command for Python
                    window.py_bot_events.push({
                        type: 'command',
                        command: command,
                        username: username,
                        args: parts
                    });
                }
            }
        }
    };
    const observer = new MutationObserver(callback);
    observer.observe(targetNode, { childList: true });
    console.log('[Bot-JS] Advanced Spam Detection is now active.');
"""


class InvalidKeyError(Exception): pass

# --- GLOBAL STATE & THREADING PRIMITIVES ---
message_queue = queue.Queue(maxsize=100)
driver_lock = Lock()
inactivity_timer = None
driver = None
USER_COOLDOWN_SECONDS = 2.0  # Cooldown of 2 seconds per user.

# --- NEW: ADVANCED SPAM DETECTION SETTINGS ---
SPAM_STRIKE_LIMIT = 3        # How many times a user can repeat a command before a timeout.
SPAM_TIMEOUT_SECONDS = 30    # How long the user is ignored after being flagged.
SPAM_RESET_SECONDS = 5       # If this many seconds pass, the strike counter resets.

ALL_COMMANDS = ["bal", "balance", "craft", "cs", "csb", "crateshopbuy", "daily", "eat", "flip", "gather", "info", "inv", "inventory", "lb", "leaderboard", "m", "market", "marketbuy", "marketcancel", "marketsell", "mb", "mc", "ms", "n", "next", "p", "pay", "previous", "recipes", "slots", "smelt", "timers", "traitroll", "traits", "verify", "work","hourly"]
BOT_STATE = {"status": "Initializing...", "start_time": datetime.now(), "current_ship_id": "N/A", "last_command_info": "None yet.", "last_message_sent": "None yet.", "event_log": deque(maxlen=20)}

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
@flask_app.route('/')
def health_check():
    html = f"""
    <!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta http-equiv="refresh" content="10">
    <title>Drednot Bot Status</title><style>body{{font-family:'Courier New',monospace;background-color:#1e1e1e;color:#d4d4d4;padding:20px;}}.container{{max-width:800px;margin:auto;background-color:#252526;border:1px solid #373737;padding:20px;border-radius:8px;}}h1,h2{{color:#4ec9b0;border-bottom:1px solid #4ec9b0;padding-bottom:5px;}}p{{line-height:1.6;}}.status-ok{{color:#73c991;font-weight:bold;}}.status-warn{{color:#dccd85;font-weight:bold;}}.status-err{{color:#f44747;font-weight:bold;}}ul{{list-style-type:none;padding-left:0;}}li{{background-color:#2d2d2d;margin-bottom:8px;padding:10px;border-radius:4px;white-space:pre-wrap;word-break:break-all;}}.label{{color:#9cdcfe;font-weight:bold;}}</style></head>
    <body><div class="container"><h1>Drednot Bot Status</h1>
    <p><span class="label">Status:</span><span class="status-ok">{BOT_STATE['status']}</span></p>
    <p><span class="label">Current Ship ID:</span>{BOT_STATE['current_ship_id']}</p>
    <p><span class="label">Last Command:</span>{BOT_STATE['last_command_info']}</p>
    <p><span class="label">Last Message Sent:</span>{BOT_STATE['last_message_sent']}</p>
    <h2>Recent Events (Log)</h2><ul>{''.join(f'<li>{event}</li>' for event in BOT_STATE['event_log'])}</ul></div></body></html>
    """
    return Response(html, mimetype='text/html')

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    print(f"Health check server listening on port {port}")
    flask_app.run(host='0.0.0.0', port=port)

# --- HELPER & CORE FUNCTIONS ---
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
            try: driver.find_element(By.CSS_SELECTOR, "#disconnect-popup button").click(); print("[REJOIN] Disconnect pop-up found.")
            except:
                try: driver.find_element(By.ID, "exit_button").click(); print("[REJOIN] Still in game, exiting ship.")
                except: print("[REJOIN] Not in game and no pop-up. Assuming at main menu.")
            wait = WebDriverWait(driver, 15); wait.until(EC.presence_of_element_located((By.ID, 'shipyard'))); print(f"[REJOIN] At main menu. Searching for ship: {ship_id}")
            clicked = driver.execute_script("const sid=arguments[0];const s=Array.from(document.querySelectorAll('.sy-id')).find(e=>e.textContent===sid);if(s){s.click();return true}document.querySelector('#shipyard section:nth-of-type(3) .btn-small')?.click();return false", ship_id)
            if not clicked: time.sleep(0.5); clicked = driver.execute_script("const sid=arguments[0];const s=Array.from(document.querySelectorAll('.sy-id')).find(e=>e.textContent===sid);if(s){s.click();return true}return false", ship_id)
            if not clicked: raise RuntimeError(f"Could not find ship {ship_id} in list.")
            wait.until(EC.presence_of_element_located((By.ID, 'chat-input'))); print("✅ Proactive rejoin successful!"); log_event("Proactive rejoin successful."); BOT_STATE["status"] = "Running"
            print("[REJOIN] Re-injecting chat monitoring script."); driver.execute_script(MUTATION_OBSERVER_SCRIPT, ZWSP, ALL_COMMANDS, USER_COOLDOWN_SECONDS, SPAM_STRIKE_LIMIT, SPAM_TIMEOUT_SECONDS, SPAM_RESET_SECONDS); reset_inactivity_timer()
    except Exception as e: log_event(f"Rejoin FAILED: {e}"); print(f"[REJOIN] Proactive rejoin failed: {e}. Triggering full restart."); driver.quit()

# --- MAIN BOT LOGIC ---
def start_bot(use_key_login):
    global driver
    BOT_STATE["status"] = "Launching Browser..."; log_event("Performing full start...")
    driver = setup_driver()
    with driver_lock:
        print("Navigating to Drednot.io invite link..."); driver.get(SHIP_INVITE_LINK); print("Page loaded. Handling login procedure...")
        wait = WebDriverWait(driver, 15)
        try:
            btn = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".modal-container .btn-green"))); driver.execute_script("arguments[0].click();", btn); print("Clicked 'Accept' on notice.")
            if ANONYMOUS_LOGIN_KEY and use_key_login:
                print("Attempting to log in with anonymous key."); log_event("Attempting login with key.")
                link = wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(., 'Restore old anonymous key')]"))); driver.execute_script("arguments[0].click();", link); print("Clicked 'Restore old anonymous key'.")
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.modal-window input[maxlength="24"]'))).send_keys(ANONYMOUS_LOGIN_KEY)
                submit_btn = wait.until(EC.presence_of_element_located((By.XPATH, "//div[.//h2[text()='Restore Account Key']]//button[contains(@class, 'btn-green')]")));
                driver.execute_script("arguments[0].click();", submit_btn); print("Submitted key.")
                wait.until(EC.invisibility_of_element_located((By.XPATH, "//div[.//h2[text()='Restore Account Key']]"))); print("Login modal closed.")
                wait.until(EC.any_of(EC.presence_of_element_located((By.ID, "chat-input")), EC.presence_of_element_located((By.XPATH, "//h2[text()='Login Failed']"))))
                if driver.find_elements(By.XPATH, "//h2[text()='Login Failed']"): raise InvalidKeyError("Login Failed! Key may be invalid.")
                print("✅ Successfully logged in with key."); log_event("Login with key successful.")
            else:
                log_event("Playing as new guest."); print("Playing as a new guest.")
                play_btn = wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Play Anonymously')]"))); driver.execute_script("arguments[0].click();", play_btn); print("Clicked 'Play Anonymously'.")
        except TimeoutException: print("Login timed out. Assuming already in-game."); log_event("Login timeout. Assuming in-game.")
        except Exception as e: log_event(f"Login failed critically: {e}"); raise e

        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "chat-input")));
        print("[SYSTEM] Injecting Smart MutationObserver for chat..."); driver.execute_script(MUTATION_OBSERVER_SCRIPT, ZWSP, ALL_COMMANDS, USER_COOLDOWN_SECONDS, SPAM_STRIKE_LIMIT, SPAM_TIMEOUT_SECONDS, SPAM_RESET_SECONDS); log_event("Chat observer active.")

        ship_id_found = False
        print("[SYSTEM] Proactively scanning existing chat for Ship ID...")
        log_event("Proactively scanning for Ship ID...")
        PROACTIVE_SCAN_SCRIPT = """
            const chatContent = document.getElementById('chat-content');
            if (!chatContent) { return null; }
            const paragraphs = chatContent.querySelectorAll('p');
            for (const p of paragraphs) {
                const pText = p.textContent || "";
                if (pText.includes("Joined ship '")) {
                    const match = pText.match(/{[A-Z\\d]+}/);
                    if (match && match[0]) {
                        return match[0]; // Return the found ID
                    }
                }
            }
            return null; // Return null if not found
        """
        found_id = driver.execute_script(PROACTIVE_SCAN_SCRIPT)

        if found_id:
            BOT_STATE["current_ship_id"] = found_id
            ship_id_found = True
            log_event(f"Confirmed Ship ID via scan: {found_id}")
            print(f"✅ Confirmed Ship ID via scan: {found_id}")
        else:
            print("[SYSTEM] No existing ID found. Waiting for live event...")
            log_event("Waiting for live 'join' event...")
            start_time = time.time()
            while time.time() - start_time < 15: # Reduced timeout
                new_events = driver.execute_script("return window.py_bot_events.splice(0, window.py_bot_events.length);")
                for event in new_events:
                    if event['type'] == 'ship_joined':
                        BOT_STATE["current_ship_id"] = event['id']
                        ship_id_found = True
                        log_event(f"Confirmed Ship ID via event: {BOT_STATE['current_ship_id']}")
                        print(f"✅ Confirmed Ship ID via event: {BOT_STATE['current_ship_id']}")
                        break
                if ship_id_found:
                    break
                time.sleep(0.5)

        if not ship_id_found:
            error_message = "Failed to get Ship ID via scan or live event."
            log_event(f"CRITICAL: {error_message}")
            raise RuntimeError(error_message)

    BOT_STATE["status"] = "Running"; queue_reply("Hello"); reset_inactivity_timer(); print(f"Event-driven chat monitor active. Polling every {MAIN_LOOP_POLLING_INTERVAL_SECONDS}s.")
    while True:
        try:
            with driver_lock: new_events = driver.execute_script("return window.py_bot_events.splice(0, window.py_bot_events.length);")
            if new_events:
                reset_inactivity_timer()
                for event in new_events:
                    if event['type'] == 'ship_joined' and event['id'] != BOT_STATE["current_ship_id"]:
                         BOT_STATE["current_ship_id"] = event['id']
                         log_event(f"Switched to new ship: {BOT_STATE['current_ship_id']}")
                    elif event['type'] == 'command':
                        process_remote_command(event['command'], event['username'], event['args'])
                    elif event['type'] == 'spam_detected':
                        # Log that a user has been automatically timed out.
                        username = event['username']
                        command = event['command']
                        log_event(f"SPAM: Timed out '{username}' for {SPAM_TIMEOUT_SECONDS}s for spamming '!{command}'.")
                        print(f"[SPAM-DETECT] Timed out '{username}' for spamming '!{command}'.")

        except WebDriverException as e: print(f"[ERROR] WebDriver exception in main loop. Assuming disconnect."); log_event(f"WebDriver error in main loop: {e.msg}"); raise
        time.sleep(MAIN_LOOP_POLLING_INTERVAL_SECONDS)

# --- MAIN EXECUTION ---
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=message_processor_thread, daemon=True).start()

    use_key_login = True; restart_count = 0; last_restart_time = time.time()
    while True:
        current_time = time.time()
        if current_time - last_restart_time < 3600: restart_count += 1
        else: restart_count = 1
        last_restart_time = current_time
        if restart_count > 10:
            log_event("CRITICAL: Bot is thrashing. Pausing for 5 minutes."); print("\n" + "="*60 + "\nCRITICAL: BOT RESTARTED >10 TIMES/HOUR. PAUSING FOR 5 MINS.\n" + "="*60 + "\n"); time.sleep(300)
        try:
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
            driver = None
            time.sleep(5)

if __name__ == "__main__":
    main()
