# drednot_bot.py (High-Performance, Memory-Stable, Fully-Featured, Click-Intercept-Fixed)

import os
import re
import json
import time
# ... (all other imports are the same)
from selenium.common.exceptions import WebDriverException, TimeoutException, ElementClickInterceptedException # Added for clarity

# ... (all code down to start_bot is the same)

# --- MAIN BOT LOGIC ---
def start_bot(use_key_login):
    global driver
    BOT_STATE["status"] = "Launching Browser..."; log_event("Performing full start...")
    driver = setup_driver()
    # Login and setup logic remains the same...
    with driver_lock:
        driver.get(SHIP_INVITE_LINK)
        # ... full login sequence as before ...
        wait = WebDriverWait(driver, 20) # Slightly increased wait time for robustness
        try:
            # Click the initial "Accept" notice
            accept_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ".modal-container .btn-green")))
            driver.execute_script("arguments[0].click();", accept_button)
            log_event("Clicked 'Accept' on notice.")

            if ANONYMOUS_LOGIN_KEY and use_key_login:
                # --- FIX: Use a JavaScript click to prevent interception ---
                log_event("Attempting to click 'Restore Key' link...")
                restore_link = wait.until(EC.presence_of_element_located((By.XPATH, "//a[contains(., 'Restore old anonymous key')]")))
                driver.execute_script("arguments[0].click();", restore_link)
                log_event("Clicked 'Restore old anonymous key' link via JS.")
                # --- END FIX ---

                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'div.modal-window input[maxlength="24"]'))).send_keys(ANONYMOUS_LOGIN_KEY)
                
                # Use JS click for the submit button as well, just in case.
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
        except TimeoutException as e: 
            log_event(f"Login timed out: {e.msg}")
            raise
        except ElementClickInterceptedException as e:
            log_event(f"Login click intercepted: {e.msg}")
            raise
        except Exception as e: 
            log_event(f"Login failed critically: {e}")
            raise
        
        WebDriverWait(driver, 60).until(EC.presence_of_element_located((By.ID, "chat-input")));
        print("[SYSTEM] Injecting JS Observer with Memory Management...");
        init_result = driver.execute_script(MUTATION_OBSERVER_SCRIPT, ZWSP, ALL_COMMANDS, USER_COOLDOWN_SECONDS, SPAM_STRIKE_LIMIT, SPAM_TIMEOUT_SECONDS, SPAM_RESET_SECONDS);
        log_event(f"Chat observer injected. Status: {init_result}")

    BOT_STATE["status"] = "Running"; queue_reply("Bot online."); reset_inactivity_timer();
    print(f"Event-driven chat monitor active. Polling every {MAIN_LOOP_POLLING_INTERVAL_SECONDS}s.")
    
    last_cooldown_cleanup = datetime.now()
    
    # ... (The rest of the script is unchanged and correct) ...
    while True:
        try:
            with driver_lock: new_events = driver.execute_script("return window.py_bot_events.splice(0, window.py_bot_events.length);")
            if new_events:
                reset_inactivity_timer()
                for event in new_events:
                    # --- MERGED: Event handling with all features ---
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

# --- MAIN EXECUTION (Unchanged) ---
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
