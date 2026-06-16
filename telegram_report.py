"""CLI: build and send today's daily report. Used by cron or manually.

  python telegram_report.py            # all tasks
  python telegram_report.py <task_id>  # one task
"""
import sys

from reporting import send_report


if __name__ == "__main__":
    task_id = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    result = send_report(task_id=task_id)
    print("--- report ---")
    print(result["text"])
    print(f"sent={result['sent']} | telegram={result['telegram']} | discord={result['discord']}")
