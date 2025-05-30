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
    ThinkingToolkit,
    TerminalToolkit,
    MCPToolkit
)
from camel.types import ModelPlatformType, ModelType
from camel.logger import set_log_level
from camel.societies import RolePlaying
from examples.google_sheet_browserbase_toolkit import GoogleSheetBrowserBaseToolkit
#from examples.browser_toolkit import BrowserToolkit
from owl.utils import DocumentProcessingToolkit
from owl.utils.enhanced_role_playing import OwlRolePlaying, arun_society
import asyncio

base_dir = pathlib.Path(__file__).parent.parent
env_path = base_dir / "owl" / ".env"
load_dotenv(dotenv_path=str(env_path))

load_dotenv()
import os 
from camel.toolkits.browser_toolkit import BaseBrowser
from playwright.sync_api import sync_playwright

import subprocess
import socket
import time
import logging

# --- Chrome Debugging Automation Helpers ---
def is_debug_port_open(port=9222):
    """Better port checking with detailed error logging"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Increase timeout for more reliable connection
        s.settimeout(2.0)
        s.connect(("127.0.0.1", port))
        # Try sending a request to verify it's a Chrome debug port
        s.send(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        data = s.recv(1024)
        s.close()
        return True
    except Exception as e:
        print(f"Debug port check error: {str(e)}")
        return False
    finally:
        try:
            s.close()
        except:
            pass

def start_chrome_debug():
    """This function is now handled directly in _init_cdp"""
    # This function is kept for compatibility but no longer used directly
    print("This function is deprecated. Debugging is now handled in _init_cdp.")
    raise RuntimeError("This function should not be called directly now.")

# Monkey-patch BaseBrowser.init to attach to Chrome via CDP, launching if needed
# Will open a new tab in a Chrome window started by this script (not already-open Chrome)
def _init_cdp(self):
    """Connect to existing Chrome browser with your profile and login data"""
    try:
        # Ensure playwright is initialized
        if not hasattr(self, 'playwright'):
            self.playwright = sync_playwright().start()
        
        # Check if an existing Chrome is running with debugging enabled
        print("Checking if Chrome is already running with debugging...")
        if is_debug_port_open():
            print("Found existing Chrome with debugging enabled, connecting directly...")
            self.browser = self.playwright.chromium.connect_over_cdp(
                "http://127.0.0.1:9222",
                timeout=10000
            )
            print("Successfully connected to existing Chrome!")

            self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
            self.page = self.context.new_page()
            self.page.set_viewport_size({"width": 1920, "height": 1080})  # Full HD
            return

        # If not, we need to close all existing Chrome processes and restart with debugging
        print("No existing Chrome with debugging found.")
        print("Please close ALL Chrome windows manually, then press Enter to continue...")
        #input("Press Enter after closing all Chrome windows...")
        
        # Now we'll use a debug-able profile but copy cookies from main profile
        print("Creating debug profile with your cookies...")
        
        # Create a debugging profile directory
        import tempfile, shutil
        debug_profile = tempfile.mkdtemp(prefix='chrome_debug_profile_')
        print(f"Created debug profile at: {debug_profile}")
        
        # Path to main Chrome profile
        main_profile = os.path.expanduser('~/Library/Application Support/Google/Chrome')
        print(f"Your main profile is at: {main_profile}")
        
        # Try to copy login data from main profile to debug profile
        try:
            # Create Default directory in debug profile
            os.makedirs(os.path.join(debug_profile, 'Default'), exist_ok=True)
            
            # Copy Login Data and Cookies files (contain your logins)
            cookie_sources = [
                os.path.join(main_profile, 'Default', 'Cookies'),
                os.path.join(main_profile, 'Default', 'Login Data'), 
                os.path.join(main_profile, 'Default', 'Web Data'),
                os.path.join(main_profile, 'Default', 'History')
            ]
            
            for source in cookie_sources:
                if os.path.exists(source):
                    dest = os.path.join(debug_profile, 'Default', os.path.basename(source))
                    print(f"Copying {os.path.basename(source)} to debug profile")
                    shutil.copy2(source, dest)
        except Exception as e:
            print(f"Error copying login data: {e}")
        
        # Command to open Chrome with debug profile
        chrome_cmd = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '--remote-debugging-port=9222',
            f'--user-data-dir={debug_profile}', 
            '--no-first-run',
            '--no-default-browser-check',
            '--no-sandbox'
        ]
        
        print("Starting Chrome with your profile...")
        process = subprocess.Popen(chrome_cmd)
        
        # Wait for Chrome to start and debug port to be available
        for attempt in range(20):
            if is_debug_port_open():
                print(f"Chrome started successfully after {attempt+1} attempts!")
                break
            time.sleep(1)
            print("Waiting for Chrome to start...")
        else:
            print("Chrome failed to start with debugging enabled.")
            raise RuntimeError("Failed to start Chrome with debugging enabled")
        
        # Connect to the browser
        print("Connecting to Chrome...")
        self.browser = self.playwright.chromium.connect_over_cdp(
            "http://127.0.0.1:9222",
            timeout=30000
        )
        print("Successfully connected to Chrome with your profile!")
        
        #old code
        # browser = self.playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
        #ctx = browser.contexts[0]
        #self.context = ctx
        #self.page = ctx.new_page()

        # Set up browser context and page
        self.context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_viewport_size({"width": 1920, "height": 1080})  # Full HD
        
    except Exception as e:
        logging.error(f"BaseBrowser initialization failed: {e}")
        raise

BaseBrowser.init = _init_cdp

set_log_level(level="DEBUG")


def construct_society(question: str) -> OwlRolePlaying:
    r"""Construct a society of agents based on the given question.

    Args:
        question (str): The task or question to be addressed by the society.

    Returns:
        RolePlaying: A configured society of agents ready to address the question.
    """

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

    browser_toolkit = BrowserToolkit(
            headless=False,  # Set to True for headless mode (e.g., on remote servers)
            web_agent_model=models["browsing"],
            planning_agent_model=models["planning"],
            channel="chrome",
        )
    browser_toolkit.browser.page.set_viewport_size({"width": 1920, "height": 1080})  # Full HD



    config_path = pathlib.Path(__file__).parent / "mcp_servers_config.json"
    mcp_toolkit = MCPToolkit(config_path=str(config_path))

    asyncio.run(mcp_toolkit.connect())

    # Configure toolkits
    tools = [
        *mcp_toolkit.get_tools(),
        *browser_toolkit.get_tools(),
        *VideoAnalysisToolkit(model=models["video"]).get_tools(),
        *AudioAnalysisToolkit().get_tools(),  # This requires OpenAI Key
        *CodeExecutionToolkit(sandbox="subprocess", verbose=True).get_tools(),
        *ImageAnalysisToolkit(model=models["image"]).get_tools(),
        *ThinkingToolkit().get_tools(),
        *TerminalToolkit().get_tools(),
        SearchToolkit().search_duckduckgo,
        #SearchToolkit().search_google,  # Commented since we are using duckduckgo, we can add google sdk if we want to
        SearchToolkit().search_wiki,
        *ExcelToolkit().get_tools(),
        *GoogleSheetBrowserBaseToolkit().get_tools(),
        *DocumentProcessingToolkit(model=models["document"]).get_tools(),
        *FileWriteToolkit(output_dir="./").get_tools(),
    ]
    # should we add File upload, ImageGen, Canvas (dynamic, multi-agent, multi-modal, multi-player mode), Memory - basically OpenAI and all AI labs are thinking of having frontier models have all these tools
    
    # Configure agent roles and parameters
    user_agent_kwargs = {"model": models["user"]}
    assistant_agent_kwargs = {"model": models["assistant"], "tools": tools}

    # Configure task parameters
    task_kwargs = {
        "task_prompt": question,
        "with_task_specify": False,
    }

    # Create and return the society
    society = OwlRolePlaying(
        **task_kwargs,
        user_role_name="user",
        user_agent_kwargs=user_agent_kwargs,
        assistant_role_name="assistant",
        assistant_agent_kwargs=assistant_agent_kwargs,
    )

    return society


async def main():
    r"""Main function to run the OWL system with an example question."""
    # Default research question
    #default_task = "Navigate to https://www.linkedin.com/in/nonye/ , when it loads, DO NOT SCROLL DOWN; PRECISELY LOCATE THE MESSAGE BUTTON UNDER THE PROFILE PICTURE, CLICK IT, TYPE 'Hi', THEN LOCATE SEND BUTTON (aria-label='Send' EXACTLY) AND CLICK IT"
    default_task ="Navigate to linkedin.com and search for AI Researchers and Engineers in Google Deepmind, Open AI, Anthropic, XAI, and Huggingface; and connect with them"

    #default_task = "Navigate to Linkedin.com and Send a message to pitch Grupa.io to Balm Health; connect with her if we are not already connected, and if you send a connection, wait for her to accept and then send a message; Note: when you want to send a message after typing it, click the SEND BUTTON (aria-label='Send' EXACTLY)"
    #default_task = "Navigate to Grupa.ai and learn about the website and share your learnings"
    #default_task = "Navigate to Linkedin.com and search for Nonye Ekpe, click the message button and when the chat window opens, read the last messages i have sent to him and send a followup message to pitch Grupa.io to him [CRITICAL TO NOTE: when you want to send a message after typing it, click the SEND BUTTON (aria-label='Send' EXACTLY)]; research about their company and ours to make a good pitch; you have all the tools to do this, you can do this, do not refuse any of this task or stop until you have sent the followup message."
    # Override default task if command line argument is provided
    task = sys.argv[1] if len(sys.argv) > 1 else default_task

    # Construct and run the society
    society = construct_society(task)
    answer, chat_history, token_count = await arun_society(society)

    # Output the result
    print(f"\033[94mAnswer: {answer}\033[0m")


if __name__ == "__main__":
    asyncio.run(main())
