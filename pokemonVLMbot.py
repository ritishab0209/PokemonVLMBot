import os
import time
import json
import random
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional

import google.generativeai as genai
from PIL import Image
from dotenv import load_dotenv
import io

# Load API key from .env file
load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
DEVICE_NAME = "emulator-5554"

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
            "r": "KEYCODE_BUTTON_R1"
        }

        self._check_adb_connection()

    def _check_adb_connection(self):
        try:
            result = subprocess.run(['adb', 'devices'], capture_output=True, text=True)
            if self.device_name in result.stdout:
                print(f"Connected to {self.device_name}")
            else:
                print(f"Device {self.device_name} not found.")
                print("Available devices:")
                print(result.stdout)
                exit(1)
        except Exception as e:
            print(f"ADB connection failed: {e}")
            exit(1)

    def take_screenshot(self) -> Optional[str]:
        try:
            subprocess.run(['adb', '-s', self.device_name, 'shell', 'screencap', '-p',
                            f'/sdcard/{self.screenshot_path}'], check=True)

            subprocess.run(['adb', '-s', self.device_name, 'pull',
                            f'/sdcard/{self.screenshot_path}', self.screenshot_path], check=True)

            return self.screenshot_path
        except Exception as e:
            print(f"Screenshot failed: {e}")
            return None

    def _normalize_action(self, action: str) -> str:
        action = action.strip().lower()

        mappings = {
            "walk down": "down",
            "go down": "down",
            "move down": "down",
            "walk up": "up",
            "go up": "up",
            "move up": "up",
            "walk left": "left",
            "go left": "left",
            "move left": "left",
            "walk right": "right",
            "go right": "right",
            "move right": "right",
            "press a": "a",
            "press b": "b",
            "press start": "start",
            "press select": "select",
        }

        return mappings.get(action, action)

    def send_input(self, action: str, duration: float = 0.1) -> bool:
        normalized = self._normalize_action(action)
        keycode = self.button_mappings.get(normalized)

        if keycode:
            try:
                subprocess.run(['adb', '-s', self.device_name, 'shell', 'input', 'keyevent', keycode], check=True)
                print(f"Sent input: {normalized}")
                time.sleep(duration)
                return True
            except Exception as e:
                print(f"Failed to send input '{normalized}': {e}")
                return False
        else:
            print(f"Unknown action: {action}")
            return False


    def analyze_screen_with_gemini(self, screenshot_path: str) -> Dict[str, Any]:
        try:
            with open(screenshot_path, 'rb') as img_file:
                image = Image.open(io.BytesIO(img_file.read()))

            prompt = self._create_analysis_prompt()
            response = self.model.generate_content([prompt, image])

            return self._parse_gemini_response(response.text)
        except Exception as e:
            print(f"Gemini analysis failed: {e}")
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
            return self._fallback_parse(response_text)

    def _fallback_parse(self, text: str) -> Dict[str, Any]:
        for action in self.button_mappings:
            if action in text.lower():
                return {
                    "action": action,
                    "reasoning": text,
                    "scene_description": "Parsing failed, fallback used.",
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
        print("--- Analysis Summary ---")
        print(f"Scene: {analysis.get('scene_description', 'N/A')}")
        print(f"Action: {analysis.get('action', 'N/A')}")
        print(f"Confidence: {analysis.get('confidence', 'N/A')} / 10")
        print(f"Panic Level: {analysis.get('panic_level', 'N/A')} / 10")
        print(f"Reasoning: {analysis.get('reasoning', 'N/A')}")

        if self.game_state["panic_mode"]:
            print("AI is in PANIC MODE.")
        if self.game_state["stuck_counter"] > 5:
            print(f"Possible stuck state detected ({self.game_state['stuck_counter']} repeats)")

    def handle_stuck_state(self) -> bool:
        if self.game_state["stuck_counter"] > 10:
            print("AI seems stuck. Trying random input.")
            self.send_input(random.choice(['up', 'down', 'left', 'right', 'b', 'start']))
            self.game_state["stuck_counter"] = 0
            time.sleep(1)
            return True
        return False

    def run_game_loop(self, max_iterations: int = 1000, delay: float = 3.0):
        print("Starting the Pokemon VLM bot.\n")

        for i in range(max_iterations):
            try:
                print(f"Iteration {i + 1}/{max_iterations}")

                screenshot = self.take_screenshot()
                if not screenshot:
                    print("Screenshot failed. Skipping.")
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
                print("Bot interrupted by user.")
                break
            except Exception as e:
                print(f"Unexpected error: {e}")
                time.sleep(delay)

        print("Game loop finished.")

    def save_game_state(self, filename: str = "game_state.json"):
        try:
            with open(filename, 'w') as f:
                json.dump(self.game_state, f, indent=2)
            print(f"Saved game state to {filename}")
        except Exception as e:
            print(f"Couldn't save game state: {e}")

    def load_game_state(self, filename: str = "game_state.json"):
        try:
            with open(filename, 'r') as f:
                self.game_state = json.load(f)
            print(f"Loaded game state from {filename}")
        except Exception:
            print("Couldn’t load previous game state. Starting fresh.")


def main():
    if not API_KEY:
        print("Gemini API key is missing. Please set it in the .env file.")
        return

    bot = PokemonVLMBot(API_KEY, DEVICE_NAME)
    bot.load_game_state()

    try:
        bot.run_game_loop(max_iterations=500, delay=2.0)
    finally:
        bot.save_game_state()

if __name__ == "__main__":
    main()
