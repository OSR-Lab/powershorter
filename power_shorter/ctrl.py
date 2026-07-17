import logging
import serial
import enum

logger = logging.getLogger("powershorter")

class Engine(enum.IntEnum):
    """PowerShorter Engine"""

    E1 = 0
    E2 = 1

class GPIO(enum.IntEnum):
    """PowerShorter GPIO"""
    GPIO1 = 0
    GPIO2 = 1

class RELAY(enum.IntEnum):
    """PowerShorter RELAY"""
    RELAY1 = 2  # solid-state relay
    RELAY2 = 3  # mechanical relay

class TRIGGER_MODE(enum.IntEnum):
    """PowerShorter Trigger Mode"""
    RISE = 0
    FALL = 1

def _send(dev, ins, data):
    dev.reset_input_buffer()
    dev.reset_output_buffer()
    xor = ins
    for d in data:
        xor ^= d
    to_send = bytes([0x55, ins, *data, xor])
    logger.debug(f">>> {to_send.hex()}")
    dev.write(to_send)

def _recv(dev, data_len) -> bytes:
    xor = 0x00
    resp = dev.read(data_len+3) # frame header + status + data field + frame tail
    logger.debug(f"<<< {resp.hex()}")
    if len(resp) != data_len+3:
        raise TimeoutError('communication timeout')
    if resp[0] != 0xAA:
        raise ValueError('communication error')
    if resp[1] != 0x90:
        raise ValueError('config failed')
    xor = 0x90
    data = resp[2:-1]
    for d in data:
        xor ^= d
    if resp[-1] != xor:
        raise ValueError('communication error')
    return data

class PowerShorter:
    instance = {}

    def __init__(self, port, timeout=1):
        """open power shorter devices

        Args:
            port (str): com port like 'COM3'
        """
        if port in PowerShorter.instance:
            logger.info(f'auto closing opened device {port}')
            try:
                PowerShorter.instance[port].close()
            except:
                pass

        PowerShorter.instance[port] = serial.Serial(port, baudrate=115200, timeout=timeout)
        self._dev = PowerShorter.instance[port]
        self._bytesize = None

    def gpio(self, idx:GPIO, value:int):
        """set gpio value

        Args:
            idx (GPIO): which gpio to be set.
            value (int): 0, 1
        """
        if value not in [0, 1]:
            raise ValueError('gpio value must be 0 or 1')
        _send(self._dev, 0x00, bytes([idx, value]))
        _recv(self._dev, 0)

    def relay(self, idx:RELAY, value:int):
        """set relay open/close

        Args:
            idx (RELAY): which relay to be set.
            value (int): 0, 1. 0 for open, 1 for close.
        """
        if value not in [0, 1]:
            raise ValueError('relay value must be 0 or 1')
        _send(self._dev, 0x00, bytes([idx, value]))
        _recv(self._dev, 0)


    def engine_cfg(self, engine:Engine, pattern:list, trigger_mode:TRIGGER_MODE=TRIGGER_MODE.RISE, pattern_repeat:int=1, trigger_edges:int=1):
        """set trigger and attack pattern for power shorter engine

        Args:
            engine (Engine): which engine to be set.
            pattern (list): list of attack pattern of (level, duration), at most 8 can be set. level=0 means MOSFET off, level=1 means MOSFET on. duration is in unit of 10ns.
                Note that level in the last pattern will last until next arm.
                e.g. [(0, 12), (1, 10), (0, 100), (1, 23), (0, 1)] means set MOSFET to '0' for 12*10ns, '1' for 10*10ns, '0' for 100*10ns, '1' for 23*10ns, '0' for 1*10ns.
            trigger_mode: (TRIGGER_MODE): trigger mode. Defaults to TRIGGER_MODE.RISE.
            pattern_repeat (int, optional): pattern repeats. Defaults to 1.
            trigger_edges (int, optional): how many edges as trigger event. Defaults to 1.
        """
        if len(pattern) > 8:
            raise ValueError('at most 8 pattern can be set')
        if pattern_repeat < 1 or pattern_repeat > 256:
            raise ValueError('pattern repeat must be between 1 and 255')
        if trigger_edges < 1 or trigger_edges > 2**32:
            raise ValueError('trigger edges must be between 1 and 2**32')
        for p in pattern:
            if p[0] not in [0, 1]:
                raise ValueError('pattern level must be 0 or 1')
            if p[1] == 0:
                raise ValueError('pattern duration must be larger than 0')
            if p[1] >= (1<<31):
                raise ValueError('pattern duration must be smaller than 2**31')
        pattern_bytes = b''
        last_level = pattern[-1][0]
        for i in range(8):
            if i < len(pattern):
                pattern_bytes += ((pattern[i][0] << 31)|(pattern[i][1] - 1)).to_bytes(4, 'little')
            else:
                pattern_bytes += (last_level  << 31).to_bytes(4, 'little') # pad to 8 patterns; even when set to 0, the FPGA still waits one clock cycle

        pattern_repeat = pattern_repeat-1
        _send(self._dev, 0x01,
              bytes([engine, trigger_mode]) +
              trigger_edges.to_bytes(4, 'little') +
              pattern_repeat.to_bytes(1, 'little') + pattern_bytes)
        _recv(self._dev, 0)


    def arm(self, engine:Engine, init_level=0):
        """arm power shorter engine

        Args:
            engine (Engine): which engine to be armed.
            init_level (int, optional): initial level of MOSFET. Defaults to 0.
        """
        engine_bytes = bytes((engine, init_level))
        _send(self._dev, 0x02, engine_bytes)
        _recv(self._dev, 0)

    def soft_trigger(self, engine:Engine):
        """soft trigger power shorter engine

        Args:
            engine (Engine): which engine to be triggered.
        """
        engine_bytes = bytes((engine,))
        _send(self._dev, 0x03, engine_bytes)
        _recv(self._dev, 0)

    def state(self, engine:Engine)->str:
        """engine state.

        Args:
            engine (Engine): which engine to be checked.

        Returns:
            str: 'armed' or 'glitched'
        """
        engine_bytes = bytes((engine,))
        _send(self._dev, 0x04, engine_bytes)
        state = _recv(self._dev, 2)
        return 'armed' if state[1] == 0 else 'glitched'


class EMPulser(PowerShorter):
    def __init__(self, port, timeout=1):
        super().__init__(port, timeout)

    def engine_cfg(self, engine: Engine, delay:int, pulse:int, trigger_mode: TRIGGER_MODE = TRIGGER_MODE.RISE, trigger_edges: int = 1):
        """set trigger and delay for power shorter engine, to control EMPulser

        Args:
            engine (Engine): which engine to be set.
            delay (int): delay time from trigger in unit of 10ns.
            pulse (int): pulse width in unit of 10ns, 1~8.
            trigger_mode: (TRIGGER_MODE): trigger mode. Defaults to TRIGGER_MODE.RISE.
            pattern_repeat (int, optional): pattern repeats. Defaults to 1.
            trigger_edges (int, optional): how many edges as trigger event. Defaults to 1.
        """
        if pulse < 1 or pulse > 8:
            raise ValueError('EMPulse only support pulse width 10ns~80ns')
        pattern = [(0, delay), (1, pulse), (0, 1)] # EMPulser only support single pulse, with pulse width 10ns~80ns.
        super().engine_cfg(engine, pattern, trigger_mode, 1, trigger_edges)



class CycleWarper(PowerShorter):
    def __init__(self, port, timeout=1):
        super().__init__(port, timeout)

    def engine_cfg(self, engine: Engine, delay:int, pulse:int, trigger_mode: TRIGGER_MODE = TRIGGER_MODE.RISE, trigger_edges: int = 1):
        """set trigger and delay for power shorter engine, to control CycleWarper

        Args:
            engine (Engine): which engine to be set.
            delay (int): delay time from trigger in unit of 10ns.
            pulse (int): pulse width in unit of 10ns.
            trigger_mode: (TRIGGER_MODE): trigger mode. Defaults to TRIGGER_MODE.RISE.
            trigger_edges (int, optional): how many edges as trigger event. Defaults to 1.
        """
        pattern = [(0, delay), (1, pulse), (0, 1)] # CycleWarper is driven by a single pulse: delay, then pulse high, then low.
        super().engine_cfg(engine, pattern, trigger_mode, 1, trigger_edges)
