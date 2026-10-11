"""Reset memory and run the agent week by week.

Usage: python run.py          # weeks 2-6
       python run.py 2 3      # only the given weeks
"""
import sys

from agent import process_week
from memory import reset_memory


def main():
    weeks = [int(w) for w in sys.argv[1:]] or list(range(2, 7))
    reset_memory()
    print("Memory reset.")
    for week in weeks:
        process_week(week)


if __name__ == "__main__":
    main()
