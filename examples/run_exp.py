# ========= Copyright 2023-2024 @ CAMEL-AI.org. All Rights Reserved. =========
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ========= Copyright 2023-2024 @ CAMEL-AI.org. All Rights Reserved. =========
import sys
import pathlib
from dotenv import load_dotenv
from camel.models import ModelFactory
from camel.toolkits import (
    AudioAnalysisToolkit,
    CodeExecutionToolkit,
    ExcelToolkit,
    ImageAnalysisToolkit,
    SearchToolkit,
    VideoAnalysisToolkit,
    BrowserToolkit,
    FileWriteToolkit,
    FunctionTool,
    get_openai_tool_schema
)
from camel.types import ModelPlatformType, ModelType
from camel.logger import set_log_level, get_logger
from owl.utils.enhanced_role_playing import OwlRolePlaying
from owl.utils import run_society, DocumentProcessingToolkit
from typing import Union
import logging
import functools
from typing import Tuple, List, Dict
import random
import time

base_dir = pathlib.Path(__file__).parent.parent
env_path = base_dir / "owl" / ".env"
load_dotenv(dotenv_path=str(env_path))

# --- Chrome Debugging Automation Helpers ---
import os
import socket
from camel.toolkits.browser_toolkit import BaseBrowser
import subprocess

# Configure toolkits
global raw_browser_toolkit, browser  # Make accessible to tools
raw_browser_toolkit = None
browser = None

def is_debug_port_open(port=9222):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()

_original_init = BaseBrowser.init
def _init_cdp(self):
    # if no debug port, try launching Chrome with remote debugging
    if not is_debug_port_open():
        subprocess.Popen([
            "open",
            "-n",  # open new instance even if Chrome already running
            "-a",
            "Google Chrome",
            "--args",
            "--remote-debugging-port=9222",
        ])
        time.sleep(1)
        if not is_debug_port_open():
            raise RuntimeError(
                "Failed to launch Chrome with remote-debugging-port=9222"
            )
    browser = self.playwright.chromium.connect_over_cdp(
        "http://127.0.0.1:9222"
    )
    self.browser = browser  # ensure attribute exists for later close() etc.
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    self.context = ctx
    self.page = ctx.new_page()
    self.page.set_viewport_size({"width": 1920, "height": 1080})  # Full HD
BaseBrowser.init = _init_cdp

set_log_level(level="DEBUG")
logger = get_logger(__name__)

# --- Patch Browser fill_input_id to better target real editable fields ---
from camel.toolkits.browser_toolkit import BaseBrowser


def _patched_fill_input_id(self, identifier: Union[str, int], text: str) -> str:
    """Improved version that is able to type into a descendant editable
    element when the supplied ID points to a non-input container (e.g. a link
    that wraps the actual search box)."""
    from playwright.sync_api import TimeoutError  # local import to avoid breaking when Playwright missing during linting

    # Normalise identifier
    if isinstance(identifier, int):
        identifier = str(identifier)

    # First try to find the LinkedIn search input
    try:
        target = self.page.locator('input[type="text"][placeholder*="Search"]').first
        target.wait_for(timeout=1000)
        target.fill(text)
        target.press("Enter")
        return "Action was successful."
    except Exception:
        # Fallback to original behavior if search input not found
        # Locate the element corresponding to the supplied __elementId
        try:
            target = self.page.locator(f"[__elementId='{identifier}']")
        except Exception:
            target = None

        if target is None or target.count() == 0:
            # Fallback: attempt to locate common editable inputs like global search bars
            generic_selector = "input[placeholder*='Search'], input[aria-label='Search'], input[type='search'], [role='search'] input"
            try:
                target = self.page.locator(generic_selector).first
                if target.count() == 0:
                    return f"Element with identifier '{identifier}' not found and no generic search input detected."
            except Exception:
                return f"Element with identifier '{identifier}' not found."

        target.scroll_into_view_if_needed()
        target.focus()

        def _try_fill(loc):
            try:
                loc.fill(text)
                loc.press("Enter")
                return True
            except Exception:
                return False

        success = _try_fill(target)

        # If the direct fill fails (e.g. element is not an <input>), look for a
        # descendant real input / textarea / content-editable element.
        if not success:
            try:
                editable = target.locator("input, textarea, [contenteditable='true']").first
                editable.wait_for(timeout=1000)
                success = _try_fill(editable)
            except Exception:
                success = False

        # Fallback: press keys sequentially on the target element.
        if not success:
            try:
                target.press_sequentially(text)
                success = True
            except Exception:
                pass

        # Ultimate fallback: click the region, then type via the page keyboard.
        if not success:
            try:
                target.click()
                # Use keyboard input as fallback
                self.page.keyboard.type(text)
                success = True
            except Exception:
                return f"Failed to fill input for identifier '{identifier}'."

        # Submit with Enter key
        try:
            target.press("Enter")
        except Exception:
            self.page.keyboard.press("Enter")

        self._wait_for_load()
        return (
            f"Filled input related to '{identifier}' with text '{text}' and pressed Enter."
        )


# Apply the monkey-patch
BaseBrowser.fill_input_id = _patched_fill_input_id

# --- Patch Browser click_text to reliably click by visible text ---
def _patched_click_text(self, text: str, timeout: int = 15000) -> str:
    """Find and click the first element containing the specified visible text.

    The search is robust across buttons, links, and generic elements, providing
    multiple fallback strategies to maximise reliability on dynamic pages like
    LinkedIn.
    """
    from playwright.sync_api import TimeoutError  # Local import to avoid issues when Playwright is not installed during static analysis

    # Order matters: try the most specific/common patterns first.
    selectors = [
        # Standard buttons or links that directly contain the text
        f"button:has-text('{text}')",
        f"a:has-text('{text}')",

        # Generic clickable element with the given text
        f"[role='button']:has-text('{text}')",

        # Elements whose accessible name contains the text (e.g. aria-label)
        f"button[aria-label*='{text}']",
        f"[aria-label*='{text}']",

        # Fallback: broad text selector
        f"text={text}",
    ]

    target = None

    # Try each selector in turn
    for selector in selectors:
        try:
            candidate = self.page.locator(selector).first
            candidate.wait_for(state="visible", timeout=timeout)
            if candidate.count() > 0:
                target = candidate
                break
        except TimeoutError:
            continue
        except Exception:
            continue

    # Fallback: XPath partial-text match (case-sensitive)
    if target is None or target.count() == 0:
        try:
            candidate = self.page.locator(
                f"xpath=//*[contains(normalize-space(text()), \"{text}\")]"
            ).first
            candidate.wait_for(state="visible", timeout=timeout)
            target = candidate
        except Exception:
            target = None

    if target is None or target.count() == 0:
        return f"Element containing text '{text}' not found."

    # Scroll into view, focus, then click
    try:
        target.scroll_into_view_if_needed()
        target.focus()
        target.click()
    except Exception as e:
        return f"Failed to click element containing text '{text}': {e}"

    # Wait for any navigation/load triggered by the click
    self._wait_for_load()
    return f"Clicked element containing text '{text}'."


# Apply the monkey-patch
BaseBrowser.click_text = _patched_click_text

# --- Custom tool: Only send message if recipient matches expected name ---
def send_message_if_recipient_matches(self, message: str, expected_name: str = "Nonye Ekpe") -> str:
    """Send a LinkedIn message ONLY if the recipient's visible name matches expected_name."""
    # Updated with fallback selectors
    recipient_selector = """
        div.msg-overlay-bubble-header__details span[aria-hidden='true'], 
        div.msg-thread__thread-title span,
        div.profile-topcard-person-entity__name,
        span.chat-drawer__display-name
    """
    message_input_selector = """
        textarea.msg-form__contenteditable,
        div.msg-form__contenteditable[role='textbox']
    """
    send_button_selector = """
        button.msg-form__send-button,
        button[data-control-name='send']
    """
    
    try:
        recipient_name = self.page.locator(recipient_selector).first.inner_text(timeout=10000).strip()
        if recipient_name != expected_name:
            raise ValueError(f"Recipient mismatch: {recipient_name} vs {expected_name}")
        
        self.page.locator(message_input_selector).first.fill(message)
        self.page.locator(send_button_selector).first.click()
        return f"Message sent to {recipient_name}!"
    except Exception as e:
        self.page.screenshot(path='error_screenshot.png')
        raise

# Patch this tool onto your BaseBrowser
setattr(BaseBrowser, "send_message_if_recipient_matches", send_message_if_recipient_matches)


# Add logging to send_linkedin_message_with_verification
def log_tool_call(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logging.info(f"[TOOL] Called {func.__name__} with args={args}, kwargs={kwargs}")
        return func(*args, **kwargs)
    return wrapper



@log_tool_call
def click_element_by_visible_text(text: str, profile_name: str):
    """Click button after full page load"""
    try:
        # Wait for complete page load
        browser.page.wait_for_load_state('networkidle', timeout=20000)
        
        # Find name (allow hidden)
        name = browser.page.get_by_text(profile_name, exact=True).first
        
        # Get visible Message buttons
        buttons = browser.page.get_by_text(text, exact=True).all()
        
        # Click first button below profile region
        for btn in buttons:
            if btn.is_visible():
                btn.click(timeout=10000)
                return {'success': True}
                
        return {'error': f"No visible {text} button found"}
    except Exception as e:
        return {'error': f"Final click attempt failed: {str(e)}"}


openai_tool_schema = {
    "type": "function",
    "function": {
        "name": "click_element_by_visible_text",
        "description": "Click button after full page load",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Exact visible text of the element to click"
                },
                "profile_name": {
                    "type": "string",
                    "description": "Exact visible text of the profile name"
                }
            },
            "required": ["text", "profile_name"]
        }
    }
}
# Proper tool registration
click_by_text_tool = FunctionTool(
    func=click_element_by_visible_text,
    openai_tool_schema=openai_tool_schema
)

@log_tool_call
def send_linkedin_message_with_verification(message: str, expected_name: str):
    """
    Verify recipient name appears in chat header (case-insensitive partial match)
    before sending message.
    """
    try:
        # Flexible name verification
        header_selector = '.msg-thread__thread-title, .msg-overlay-bubble-header__details'
        visible_text = browser.page.locator(header_selector).inner_text()
        
        if expected_name.lower() not in visible_text.lower():
            return f"Error: Recipient '{expected_name}' not found"
            
        browser.send_message(message)
        return "Message sent"
    except Exception as e:
        return f"Error: {str(e)}"

# Simple tool wrapper maintains existing flow
send_linkedin_message_with_verification_tool = FunctionTool(
    send_linkedin_message_with_verification
)


def construct_society(question: str) -> OwlRolePlaying:
    r"""Construct a society of agents based on the given question.

    Args:
        question (str): The task or question to be addressed by the society.

    Returns:
        OwlRolePlaying: A configured society of agents ready to address the question.
    """

    # Parse steps from default_task
    task_steps = []
    for line in question.split('\n'):
        line = line.strip()
        if line and line[0].isdigit() and ':' in line:
            _, step_content = line.split('.', 1)
            task_steps.append(step_content.strip())

    # Create models for different components
    models = {
        "user": ModelFactory.create(
            model_platform=ModelPlatformType.GEMINI,
            model_type=ModelType.GEMINI_2_5_PRO_PREVIEW,
            model_config_dict={"temperature": 0},
        ),
        "assistant": ModelFactory.create(
            model_platform=ModelPlatformType.GEMINI,
            model_type=ModelType.GEMINI_2_5_PRO_PREVIEW,
            model_config_dict={"temperature": 0},
        ),
        "browsing": ModelFactory.create(
            model_platform=ModelPlatformType.GEMINI,
            model_type=ModelType.GEMINI_2_5_PRO_PREVIEW,
            model_config_dict={"temperature": 0},
        ),
        "planning": ModelFactory.create(
            model_platform=ModelPlatformType.GEMINI,
            model_type=ModelType.GEMINI_2_5_PRO_PREVIEW,
            model_config_dict={"temperature": 0},
        ),
        "video": ModelFactory.create(
            model_platform=ModelPlatformType.GEMINI,
            model_type=ModelType.GEMINI_2_5_PRO_PREVIEW,
            model_config_dict={"temperature": 0},
        ),
        "image": ModelFactory.create(
            model_platform=ModelPlatformType.GEMINI,
            model_type=ModelType.GEMINI_2_5_PRO_PREVIEW,
            model_config_dict={"temperature": 0},
        ),
        "document": ModelFactory.create(
            model_platform=ModelPlatformType.GEMINI,
            model_type=ModelType.GEMINI_2_5_PRO_PREVIEW,
            model_config_dict={"temperature": 0},
        ),
    }

    # Initialize browser toolkit HERE
    global raw_browser_toolkit, browser  # Make accessible to tools
    raw_browser_toolkit = BrowserToolkit(
        headless=False,
        web_agent_model=models["browsing"],
        planning_agent_model=models["planning"],
    )
    raw_browser_tools=raw_browser_toolkit.get_tools()
    browser = raw_browser_toolkit.browser

    # --- Filter BrowserToolkit tools for LinkedIn safety ---
    def filter_browser_tools_for_linkedin(tools):
        """
        Remove generic click/fill actions from BrowserToolkit for LinkedIn,
        leaving only robust, safe tools.
        """
        allowed_tool_names = {
            "click_element_by_visible_text",
            "send_linkedin_message_with_verification",
        }
        filtered = []
        for t in tools:
            try:
                if getattr(t, '__name__', None) in allowed_tool_names:
                    filtered.append(t)
                elif hasattr(t, 'func') and getattr(t.func, '__name__', None) in allowed_tool_names:
                    filtered.append(t)
                elif hasattr(t, 'name') and t.name in allowed_tool_names:
                    filtered.append(t)
            except Exception:
                continue
        return filtered


    # Compose final tools list: only allow robust tools for LinkedIn
    browser_tools = filter_browser_tools_for_linkedin(raw_browser_tools)

    @log_tool_call
    def navigate_to_url(url: str) -> str:
        '''Navigates browser to specified URL. Required first step.\n    Args:\n        url (str): Full URL including protocol (e.g. https://linkedin.com)\n    Returns:\n        str: Navigation status message'''
        browser = raw_browser_toolkit.browser
        browser.page.goto(url)
        return f"Navigated to {url}" 

    navigate_tool = FunctionTool(navigate_to_url)

    tools = [
        navigate_tool,
        click_by_text_tool,
        send_linkedin_message_with_verification_tool
    ]

    @log_tool_call
    def click_with_cv(text: str, template_path: str = None):
        """Click using OpenCV template matching"""
        try:
            import cv2
            import numpy as np
            
            # 1. Capture screenshot
            screenshot = np.array(browser.page.screenshot())
            
            # 2. Load template or generate text image
            if template_path:
                template = cv2.imread(template_path, cv2.IMREAD_COLOR)
            else:
                # Generate template dynamically (simple text)
                from PIL import Image, ImageDraw, ImageFont
                font = ImageFont.load_default()
                w, h = font.getsize(text)
                template = np.array(Image.new('RGB', (w, h), (255, 255, 255)))
                ImageDraw.Draw(Image.fromarray(template)).text((0,0), text, font=font, fill=(0,0,0))
            
            # 3. Match template
            result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            
            if max_val < 0.8:  # Confidence threshold
                return {'error': f"{text} button not found (confidence: {max_val:.2f})"}
                
            # 4. Click center of matched region
            x, y = max_loc[0] + template.shape[1]//2, max_loc[1] + template.shape[0]//2
            browser.page.mouse.click(x, y)
            return {'success': True}
            
        except Exception as e:
            return {'error': f"CV click failed: {str(e)}"}

    cv_click_tool = FunctionTool(click_with_cv)

    tools.append(cv_click_tool)

    @log_tool_call
    def click_linkedin_message_button(profile_name: str):
        """Hybrid click: CV primary + DOM fallback"""
        try:
            # 1. Wait for stability
            browser.page.wait_for_load_state('networkidle', timeout=20000)
            
            # 2. Try CV with pre-loaded templates
            templates = [
                "linkedin_message_blue.png",  # Default
                "linkedin_message_gray.png"   # Hover state
            ]
            for template in templates:
                result = click_with_cv("Message", template)
                if result.get("success"):
                    return result
                
            # 3. Fallback to DOM if CV fails
            return click_element_by_visible_text("Message", profile_name)
            
        except Exception as e:
            return {'error': f"Hybrid click failed: {str(e)}"}

    click_message_button_tool = FunctionTool(click_linkedin_message_button)

    tools.append(click_message_button_tool)

    # Configure agent roles and parameters
    user_agent_kwargs = {
        "model": models["user"],
    }
    
    assistant_agent_kwargs = {
        "model": models["assistant"],
        "tools": [
            navigate_to_url,
            click_element_by_visible_text,
            send_linkedin_message_with_verification,
            click_with_cv,
            click_linkedin_message_button
        ],
    }
    
    # Create society with strict automation
    society = OwlRolePlaying(
        task_steps=task_steps,
        user_agent_kwargs=user_agent_kwargs,
        assistant_agent_kwargs=assistant_agent_kwargs
    )


    return society



def main():
    r"""Main function to run the OWL system with an example question."""
    # Default values that can be overridden by command line
    default_task = f"""
STRICT AUTOMATION ONLY - NO CREATIVE TASKS:
1. navigate_to_url: https://www.linkedin.com/in/nonye/
2. click_linkedin_message_button: Nonye Ekpe
3. send_linkedin_message_with_verification: ('Hi', 'Nonye Ekpe')
"""
    
    society = construct_society(default_task)
    answer, chat_history, token_count = run_society(society)

    # Output the result
    print(f"\033[94mAnswer: {answer}\033[0m")


if __name__ == "__main__":
    main()
