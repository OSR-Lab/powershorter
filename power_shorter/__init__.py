import logging.handlers

# Create a common logger for the whole package
logger = logging.getLogger("powershorter")
logger.setLevel(logging.INFO)

# formatter: how the log will be formatted:
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
# Write logs on stdout
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


from .ctrl import (
    PowerShorter,
    EMPulser,
    CycleWarper,
    Engine,
    RELAY,
    GPIO,
    TRIGGER_MODE
)
