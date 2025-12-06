#!/usr/bin/env python3
"""
Troubleshooting Helper Script.

Aggregates logs from all Docker Compose services, filters by trace_id or keyword,
and displays them in a unified, sorted timeline.

Usage:
    python scripts/troubleshoot.py <trace_id_or_keyword> [--lines 500]
"""

import argparse
import json
import re  # Import re module
import subprocess
from datetime import datetime
from typing import Any, NamedTuple


def run_docker_logs(lines: int) -> list[str]:
    """Fetch raw logs from docker compose."""
    # We use checking=False to ignore errors if stack isn't fully up
    cmd = [
        "docker",
        "compose",
        "logs",
        "--no-color",
        "--tail",
        str(lines),
        "--timestamps",
    ]
    result = subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.stdout.splitlines()


class LogEntry(NamedTuple):
    timestamp: datetime
    service: str
    level: str
    message: str
    payload: dict[str, Any] | None
    raw: str


def parse_log_line(line: str) -> LogEntry | None:
    """Parse a docker log line: `service | timestamp message`."""
    try:
        # Docker compose logs format:
        # service-1 | 2024-05-20T10:00:00.000000000Z message content
        main_parts = line.split(" | ", 1)
        if len(main_parts) < 2:
            return None

        service = main_parts[0].strip()
        rest_of_line = main_parts[1].strip()

        # Extract the ISO 8601 timestamp (first string that matches YYYY-MM-DDTHH:MM:SS)
        # It's always like YYYY-MM-DDTHH:MM:SS.fracZ
        timestamp_match = re.match(
            r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s", rest_of_line
        )

        timestamp_str_full = ""
        if timestamp_match:
            timestamp_str_full = timestamp_match.group(1)
            message_content_raw = rest_of_line[len(timestamp_str_full) :].strip()
        else:
            # Fallback if timestamp not found at start (shouldn't happen with --timestamps)
            # Or if it's not a log line we want to parse (e.g. non-timestamped output)
            return None

        # Parse timestamp
        # Remove Z and truncate nanoseconds for datetime.strptime
        ts_str = timestamp_str_full.replace("Z", "")
        if "." in ts_str:
            main_part, frac = ts_str.split(".", 1)
            ts_str = f"{main_part}.{frac[:6]}"

        try:
            if "." in ts_str:
                timestamp = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%f")
            else:
                timestamp = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            # Fallback if parsing still fails
            timestamp = datetime.now()

        # Try to parse content as JSON
        payload = None
        level = "INFO"
        message = message_content_raw

        # Check if message_content_raw starts with JSON
        try:
            # Find first { and last } to extract potential JSON
            json_start = message_content_raw.find("{")
            json_end = message_content_raw.rfind("}")

            if json_start != -1 and json_end != -1 and json_end > json_start:
                potential_json_str = message_content_raw[json_start : json_end + 1]
                payload = json.loads(potential_json_str)
                if isinstance(payload, dict):
                    # Extract common fields, prioritize app's log level/message
                    level = payload.get("level", level)
                    message = payload.get("message", message)
                    if "event_type" in payload:
                        message = f"[{payload['event_type']}] {message}"

                    # If the entire message_content_raw was JSON, update message.
                    if message_content_raw.strip() == potential_json_str.strip():
                        message = f"Structured log: {message}"

        except json.JSONDecodeError:
            # Not JSON, proceed with raw message
            pass

        return LogEntry(
            timestamp=timestamp,
            service=service,
            level=str(level).upper(),  # Ensure level is uppercase string
            message=message,
            payload=payload,
            raw=line,
        )
    except Exception:
        # Catch all for parsing issues to avoid crashing the script
        return None


def main():
    parser = argparse.ArgumentParser(description="Troubleshoot agent stack.")
    parser.add_argument("query", nargs="?", help="Trace ID or keyword to filter by.")
    parser.add_argument(
        "--lines",
        type=int,
        default=1000,
        help="Number of log lines to fetch per service.",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON.")
    args = parser.parse_args()

    print(f"Fetching last {args.lines} lines from stack...")
    raw_logs = run_docker_logs(args.lines)

    parsed_logs = []
    for line in raw_logs:
        entry = parse_log_line(line)
        if entry:
            parsed_logs.append(entry)

    # Sort by timestamp
    parsed_logs.sort(key=lambda x: x.timestamp)

    # Filter
    if args.query:
        query = args.query.lower()
        filtered = []
        for entry in parsed_logs:
            # Search in raw line or parsed fields
            if query in entry.raw.lower():
                filtered.append(entry)
                continue
            if entry.payload and query in str(entry.payload).lower():
                filtered.append(entry)
                continue

        print(f"Found {len(filtered)} entries matching '{args.query}'.")
        display_logs = filtered
    else:
        display_logs = parsed_logs

    # Render
    print("-" * 80)
    for entry in display_logs:
        ts = entry.timestamp.strftime("%H:%M:%S.%f")[:-3]

        # Colorize based on level
        level_color = ""
        if entry.level in ("ERROR", "CRITICAL"):
            level_color = "\033[91m"  # Red
        elif entry.level == "WARNING":
            level_color = "\033[93m"  # Yellow
        reset = "\033[0m"

        service_color = "\033[96m"  # Cyan

        msg = entry.message
        if args.json and entry.payload:
            msg = json.dumps(entry.payload)

        print(
            f"{ts} {service_color}{entry.service:<15}{reset} "
            f"{level_color}{entry.level:<8}{reset} {msg}"
        )

        # If structured event data exists and matches query, show context
        if entry.payload and "event_data" in entry.payload and args.query:
            print(
                f"    \033[90m{json.dumps(entry.payload['event_data'], indent=2)}\033[0m"
            )

    print("-" * 80)


if __name__ == "__main__":
    main()
