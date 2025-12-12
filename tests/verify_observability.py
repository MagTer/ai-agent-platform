
import os
import sys
import logging
from unittest.mock import MagicMock, patch

# Mock libraries not available in the test env context if needed
sys.modules["opentelemetry"] = MagicMock()
sys.modules["opentelemetry.trace"] = MagicMock()
sys.modules["opentelemetry.sdk.trace"] = MagicMock()
sys.modules["opentelemetry.sdk.trace.export"] = MagicMock()
sys.modules["rich.logging"] = MagicMock()

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "services", "agent", "src"))

from core.observability.logging import setup_logging
from core.observability.tracing import configure_tracing

def test_logging_setup():
    print("Testing logging setup...")
    # Test Rich path
    os.environ["LOG_FORMAT"] = "text"
    setup_logging()
    print("Logging setup (Rich) OK")

    # Test JSON path
    os.environ["LOG_FORMAT"] = "json"
    setup_logging()
    print("Logging setup (JSON) OK")

def test_tracing_setup():
    print("Testing tracing setup...")
    
    # Test Default (Console + OTLP)
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    configure_tracing("test-service")
    print("Tracing setup (OTLP) OK")

    # Test Console forced
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
    configure_tracing("test-service")
    print("Tracing setup (Console fallback) OK")

if __name__ == "__main__":
    try:
        test_logging_setup()
        test_tracing_setup()
        print("VERIFICATION SUCCESSFUL")
    except Exception as e:
        print(f"VERIFICATION FAILED: {e}")
        sys.exit(1)
