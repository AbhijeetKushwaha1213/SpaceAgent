import random
from datetime import datetime

CRASH_MODES = ["thermal", "structural", "electrical", "hydraulic"]

def generate_crash_dump() -> dict:
    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "mode": random.choice(CRASH_MODES),
        "error_code": random.randint(1000, 9999),
        "message": "Simulated crash dump data for investigation.",
    }

if __name__ == "__main__":
    print(generate_crash_dump())
