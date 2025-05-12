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
)
from camel.types import ModelPlatformType, ModelType
from camel.logger import set_log_level
from camel.societies import RolePlaying
from examples.google_sheet_browserbase_toolkit import GoogleSheetBrowserBaseToolkit

from owl.utils import run_society, DocumentProcessingToolkit

base_dir = pathlib.Path(__file__).parent.parent
env_path = base_dir / "owl" / ".env"
load_dotenv(dotenv_path=str(env_path))

load_dotenv()
import os 
from camel.toolkits.browser_toolkit import BaseBrowser
from playwright.sync_api import sync_playwright


def run_debug_with_profile():
    #This function is for when you want to run the new session in debug mode with current user profile
    # This is important if we want a scenario where the current user work on their browser should not be disturbed
    # This is useful when the user want to keep working while the agent works in background
    
    # Terminate existing Chrome instances
    #subprocess.run(['pkill', '-f', 'Google Chrome'], check=False)
    #subprocess.run(['pkill', '-f', 'chromedriver'], check=False)

    # Remove lock files
    chrome_profile = os.path.expanduser('~/Library/Application Support/Google/Chrome')
    #lock_files = [
    #os.path.join(chrome_profile, 'SingletonLock'),
    #os.path.join(chrome_profile, 'SingletonSocket'),
    #os.path.join(chrome_profile, 'SingletonCookie')
    #]
    #for lock_file in lock_files:
    #    try:
    #        if os.path.exists(lock_file):
    #            os.remove(lock_file)
    #    except Exception as e:
    #        logging.warning(f"Failed to remove {lock_file}: {e}")

    chrome_cmd = [
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        '--remote-debugging-port=9222',
        #'--user-data-dir=/tmp/chrome-user-data',    
        '--user-data-dir=' + chrome_profile,
        '--no-first-run',
        '--profile-directory=Default',
        #'--no-sandbox',  # Add this to bypass sandboxing issues
        #'--disable-automation',  # Reduce automation detection
        '--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',  # Realistic user agent
        '--disable-gpu',
        '--no-default-browser-check', 
        #'--disable-features=ProcessPerSite',  # Prevent profile locking
        #'--disable-dev-shm-usage',  # Fix shared memory issues
        #'--disable-crash-reporter',  # Disable Crashpad to avoid macOS errors
        #'--verbose',  # Add verbose logging
        #'--enable-logging',  # Enable Chrome logging
        #'--v=1'  # Set logging verbosity
    ]
    subprocess.Popen(chrome_cmd)
    # Wait for Chrome to start
    for _ in range(10):
        if is_debug_port_open():
            return
        time.sleep(0.5)
    raise RuntimeError("Failed to start Chrome in remote debugging mode.")

# Monkey-patch BaseBrowser.init to attach to Chrome via CDP, launching if needed
# This is to take over user current browser, it will open a new tab in a Chrome window started by this script (not already-open Chrome)
# some users may not mind this, its useful if the user want to just sit and watch the browser while the agent works
def _init_cdp(self):
    import os
    import logging
    import random
    try:
        if not hasattr(self, 'playwright'):
            self.playwright = sync_playwright().start()
        
        chrome_profile = os.path.expanduser('~/Library/Application Support/Google/Chrome')

        # Launch Chrome with existing profile and human-like settings
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=chrome_profile,
            executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            headless=False,  # Visible for debugging; set to True for headless
            args=[
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-dev-shm-usage',
                '--disable-crash-reporter',
                #'--disable-gpu',
                '--no-sandbox',   
                #'--disable-automation',  # Reduce automation detection
                #'--disable-blink-features=AutomationControlled',  # Hide automation flags
                #'--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'  # Realistic user agent
            ],
            #ignore_default_args=['--enable-automation'],  # Further hide automation
        )
        self.browser = self.context.browser
        self.page = self.context.new_page()
        self.page.set_viewport_size({"width": 1920, "height": 1080})

    except Exception as e:
        logging.error(f"BaseBrowser initialization failed: {e}")
        raise

BaseBrowser.init = _init_cdp

set_log_level(level="DEBUG")


def construct_society(question: str) -> RolePlaying:
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

    # Configure toolkits
    tools = [
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
    society = RolePlaying(
        **task_kwargs,
        user_role_name="user",
        user_agent_kwargs=user_agent_kwargs,
        assistant_role_name="assistant",
        assistant_agent_kwargs=assistant_agent_kwargs,
    )

    return society


def main():
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
    answer, chat_history, token_count = run_society(society)

    # Output the result
    print(f"\033[94mAnswer: {answer}\033[0m")


if __name__ == "__main__":
    main()
