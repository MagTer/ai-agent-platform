import logging
import subprocess

LOGGER = logging.getLogger(__name__)


class GeminiCLIModel:
    """
    Adapter for the Google Gemini CLI (@google/gemini-cli).
    """

    def __init__(self, model: str = "gemini-1.5-pro-latest"):
        self.model = model

    def generate_content(self, prompt: str) -> str:
        """
        Generates content using the Gemini CLI.
        Requires the CLI to be installed and authenticated (or token provided in env).
        """
        try:
            # We rely on the environment variable GOOGLE_API_KEY or local authentication
            # being present in the container/environment.
            command = ["gemini", "prompt", prompt]

            # Execute the command
            # S603: The 'prompt' is passed as a literal argument and not interpreted as shell code.
            # The gemini CLI is expected to handle its own argument parsing securely.
            result = subprocess.run(  # noqa: S603
                command, capture_output=True, text=True, check=True
            )

            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            LOGGER.error(f"Gemini CLI execution failed: {e.stderr}")
            raise RuntimeError(f"Gemini CLI failed: {e.stderr}") from e
        except FileNotFoundError as e:
            LOGGER.error("gemini command not found. Is @google/gemini-cli installed?")
            raise RuntimeError("gemini command not found.") from e
