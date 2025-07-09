import os
import time
import base64
import subprocess
import json
import random
from datetime import datetime
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from PIL import Image
import io
import logging
from dotenv import load_dotenv

# Load API Key from .env
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DEVICE_NAME = "emulator-5554"

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

class PokemonVLMBot:
    def __init__(self, api_key: str, device_name: str = "emulator-5554"):
        self.api_key = api_key
        self.device_name = device_name
        self.screenshot_path = "current_screen.png"

        self.game_state = {
            "current_location": "",
            "party_pokemon": [],
            "inventory": [],
            "objectives": [],
            "panic_mode": False,
            "stuck_counter": 0,
            "last_action": "",
            "reasoning_history": []
        }

        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash-exp')

        self.button_mappings = {
            "a": "KEYCODE_BUTTON_A",
            "b": "KEYCODE_BUTTON_B",
            "start": "KEYCODE_BUTTON_START",
            "select": "KEYCODE_BUTTON_SELECT",
            "up": "KEYCODE_DPAD_UP",
            "down": "KEYCODE_DPAD_DOWN",
            "left": "KEYCODE_DPAD_LEFT",
            "right": "KEYCODE_DPAD_RIGHT",
            "l": "KEYCODE_BUTTON_L1",
            "r": "KEYCODE_BUTTON_R1",

            # Added flexible mappings
            "move_up": "KEYCODE_DPAD_UP",
            "move_down": "KEYCODE_DPAD_DOWN",
            "move_left": "KEYCODE_DPAD_LEFT",
            "move_right": "KEYCODE_DPAD_RIGHT",
            "go_up": "KEYCODE_DPAD_UP",
            "go_down": "KEYCODE_DPAD_DOWN",
            "go_left": "KEYCODE_DPAD_LEFT",
            "go_right": "KEYCODE_DPAD_RIGHT",
            "press_a": "KEYCODE_BUTTON_A",
            "press_b": "KEYCODE_BUTTON_B",
            "press_start": "KEYCODE_BUTTON_START",
            "press_select": "KEYCODE_BUTTON_SELECT"
        }

        self._check_adb_connection()

    def _check_adb_connection(self):
        try:
            result = subprocess.run(['adb', 'devices'], capture_output=True, text=True)
            if self.device_name in result.stdout:
                logger.info(f"Connected to {self.device_name}")
                return True
            else:
                logger.error(f"Device {self.device_name} not found")
                logger.info("Available devices:")
                logger.info(result.stdout)
                return False
        except Exception as e:
            logger.error(f"ADB connection failed: {e}")
            return False

    def take_screenshot(self) -> Optional[str]:
        try:
            subprocess.run(['adb', '-s', self.device_name, 'shell', 'screencap', '-p',
                            f'/sdcard/{self.screenshot_path}'], check=True)
            subprocess.run(['adb', '-s', self.device_name, 'pull',
                            f'/sdcard/{self.screenshot_path}', self.screenshot_path], check=True)
            return self.screenshot_path
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return None

    def send_input(self, action: str, duration: float = 0.1) -> bool:
        try:
            keycode = self.button_mappings.get(action)
            if keycode:
                subprocess.run(['adb', '-s', self.device_name, 'shell', 'input', 'keyevent', keycode], check=True)
                logger.info(f"Sent input: {action}")
                time.sleep(duration)
                return True
            else:
                logger.error(f"Unknown action: {action}")
                return False
        except Exception as e:
            logger.error(f"Input failed: {e}")
            return False

    def analyze_screen_with_gemini(self, screenshot_path: str) -> Dict[str, Any]:
        try:
            with open(screenshot_path, 'rb') as img_file:
                image = Image.open(io.BytesIO(img_file.read()))
            prompt = self._create_analysis_prompt()
            response = self.model.generate_content([prompt, image])
            return self._parse_gemini_response(response.text)
        except Exception as e:
            logger.error(f"Gemini analysis failed: {e}")
            return {"error": str(e), "action": "wait", "reasoning": "Analysis failed"}

    def _create_analysis_prompt(self) -> str:
        return f"""You are an AI playing Pokemon FireRed. Analyze this screenshot and provide a JSON response.

Current Game State:
- Location: {self.game_state['current_location']}
- Last Action: {self.game_state['last_action']}
- Stuck Counter: {self.game_state['stuck_counter']}
- Panic Mode: {self.game_state['panic_mode']}

Expected JSON format:
{{
    "scene_description": "...",
    "current_location": "...",
    "pokemon_visible": [...],
    "menu_state": "...",
    "health_status": "...",
    "action": "...",
    "reasoning": "...",
    "panic_level": 0-10,
    "objectives": [...],
    "confidence": 0-10
}}"""

    def _parse_gemini_response(self, response_text: str) -> Dict[str, Any]:
        try:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            if start != -1 and end != -1:
                parsed = json.loads(response_text[start:end])
                for field in ['action', 'reasoning', 'scene_description']:
                    parsed.setdefault(field, "Not provided")
                return parsed
            return self._fallback_parse(response_text)
        except json.JSONDecodeError:
            logger.warning("JSON parsing failed, using fallback")
            return self._fallback_parse(response_text)

    def _fallback_parse(self, text: str) -> Dict[str, Any]:
        for action in self.button_mappings:
            if action in text.lower():
                return {
                    "action": action,
                    "reasoning": text,
                    "scene_description": "Parsing failed, used fallback.",
                    "confidence": 3
                }
        return {
            "action": "wait",
            "reasoning": "Unable to determine action",
            "scene_description": text,
            "confidence": 1
        }

    def update_game_state(self, analysis: Dict[str, Any]):
        self.game_state["current_location"] = analysis.get("current_location", "Unknown")
        self.game_state["last_action"] = analysis.get("action", "wait")

        try:
            panic_level = int(analysis.get("panic_level", 0))
        except (ValueError, TypeError):
            panic_level = 0

        self.game_state["panic_mode"] = panic_level >= 7
        if panic_level >= 7:
            logger.warning("PANIC MODE ACTIVATED!")
        elif panic_level <= 3:
            self.game_state["panic_mode"] = False

        if analysis.get("action") == self.game_state.get("last_action"):
            self.game_state["stuck_counter"] += 1
        else:
            self.game_state["stuck_counter"] = 0

        self.game_state["reasoning_history"].append({
            "timestamp": datetime.now().isoformat(),
            "reasoning": analysis.get("reasoning", ""),
            "action": analysis.get("action", "wait"),
            "confidence": analysis.get("confidence", 0)
        })

        if len(self.game_state["reasoning_history"]) > 50:
            self.game_state["reasoning_history"] = self.game_state["reasoning_history"][-50:]

    def log_analysis(self, analysis: Dict[str, Any]):
        logger.info("Analysis Summary:")
        logger.info(f"   Scene: {analysis.get('scene_description', 'N/A')}")
        logger.info(f"   Action: {analysis.get('action', 'N/A')}")
        logger.info(f"   Confidence: {analysis.get('confidence', 'N/A')}/10")
        logger.info(f"   Panic Level: {analysis.get('panic_level', 'N/A')}/10")
        logger.info(f"   Reasoning: {analysis.get('reasoning', 'N/A')}")

        if self.game_state["panic_mode"]:
            logger.warning("AI is in PANIC MODE!")
        if self.game_state["stuck_counter"] > 5:
            logger.warning(f"Possible stuck state detected ({self.game_state['stuck_counter']} repeats)")

    def handle_stuck_state(self) -> bool:
        if self.game_state["stuck_counter"] > 10:
            logger.warning("AI seems stuck. Trying random input.")
            self.send_input(random.choice(['up', 'down', 'left', 'right', 'b', 'start']))
            self.game_state["stuck_counter"] = 0
            time.sleep(1)
            return True
        return False

    def run_game_loop(self, max_iterations: int = 1000, delay: float = 3.0):
        logger.info("Starting the Pokemon VLM bot.")
        for i in range(max_iterations):
            try:
                logger.info(f"Iteration {i + 1}/{max_iterations}")
                screenshot = self.take_screenshot()
                if not screenshot:
                    logger.error("Screenshot failed, skipping iteration")
                    time.sleep(delay)
                    continue

                analysis = self.analyze_screen_with_gemini(screenshot)
                self.update_game_state(analysis)
                self.log_analysis(analysis)

                if self.handle_stuck_state():
                    continue

                action = analysis.get("action", "wait")
                if action != "wait":
                    self.send_input(action)

                time.sleep(delay)

            except KeyboardInterrupt:
                logger.info("Interrupted by user.")
                break
            except Exception as e:
                logger.error(f"Error during loop: {e}")
                time.sleep(delay)
        logger.info("Game loop finished.")

    def save_game_state(self, filename: str = "game_state.json"):
        try:
            with open(filename, 'w') as f:
                json.dump(self.game_state, f, indent=2)
            logger.info(f"Saved game state to {filename}")
        except Exception as e:
            logger.error(f"Couldn't save game state: {e}")

    def load_game_state(self, filename: str = "game_state.json"):
        try:
            with open(filename, 'r') as f:
                self.game_state = json.load(f)
            logger.info(f"Loaded game state from {filename}")
        except Exception as e:
            logger.error(f"Couldn't load game state: {e}")

def main():
    if not GEMINI_API_KEY:
        logger.error("Gemini API key not set in .env file.")
        return

    bot = PokemonVLMBot(GEMINI_API_KEY, DEVICE_NAME)
    bot.load_game_state()

    try:
        bot.run_game_loop(max_iterations=500, delay=2.0)
    except KeyboardInterrupt:
        logger.info("Bot execution manually stopped.")
    finally:
        bot.save_game_state()

if __name__ == "__main__":
    main()
