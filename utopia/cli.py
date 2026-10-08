import argparse
import json
import logging

import uvicorn


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "timestamp": self.formatTime(record),
                "severity": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def main():
    parser = argparse.ArgumentParser(description="Agent Utopia local operator server")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    uvicorn.run(
        "utopia.api:create_app", factory=True, host="127.0.0.1", port=args.port, log_config=None
    )


if __name__ == "__main__":
    main()
