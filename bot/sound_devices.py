from __future__ import annotations
from enum import Enum
import logging
import sys
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from bot import Bot


class SoundDevice:
    def __init__(self, name: str, id: Union[int, str], type: SoundDeviceType) -> None:
        self.name = name
        self.id = id
        self.type = type


class SoundDeviceType(Enum):
    Output = 0
    Input = 1


class SoundDeviceManager:
    def __init__(self, bot: Bot) -> None:
        self.config = bot.config
        self.output_device_index = self.config.sound_devices.output_device
        self.input_device_index = self.config.sound_devices.input_device
        self.player = bot.player
        self.ttclient = bot.ttclient
        self.output_devices = self.player.get_output_devices()
        self.input_devices = self.ttclient.get_input_devices()

    def _find_matching_cable(self, target_cable: str, devices: List[SoundDevice]) -> Optional[SoundDevice]:
        target_lower = target_cable.lower()
        for num in range(1, 10):
            tag = f"line {num}"
            if tag in target_lower:
                for dev in devices:
                    d_name = dev.name.lower()
                    if "loopback" in d_name:
                        continue
                    if tag in d_name:
                        return dev
        for dev in devices:
            d_name = dev.name.lower()
            if "loopback" in d_name:
                continue
            if "virtual audio cable" in d_name:
                return dev
        return None

    def initialize(self) -> None:
        logging.debug("Initializing sound devices")
        try:
            out_dev = self.output_devices[self.output_device_index]
        except (IndexError, TypeError):
            out_dev = self.output_devices[0] if self.output_devices else None

        try:
            in_dev = self.input_devices[self.input_device_index]
        except (IndexError, TypeError):
            in_dev = self.input_devices[0] if self.input_devices else None

        # Auto-alignment check between Player Output and TeamTalk Input:
        # If either is configured to a Virtual Audio Cable (Line 1, Line 2, etc.),
        # ensure BOTH devices use the exact same cable so music properly flows into TeamTalk
        # instead of accidentally blasting out to physical Speakers / Headphones.
        if in_dev and ("virtual audio cable" in in_dev.name.lower() or "line " in in_dev.name.lower()):
            matched_out = self._find_matching_cable(in_dev.name, self.output_devices)
            if matched_out and out_dev != matched_out:
                logging.info(
                    f"Auto-aligning player output device from '{out_dev.name if out_dev else None}' to '{matched_out.name}' to match TeamTalk input"
                )
                out_dev = matched_out
        elif out_dev and ("virtual audio cable" in out_dev.name.lower() or "line " in out_dev.name.lower()):
            matched_in = self._find_matching_cable(out_dev.name, self.input_devices)
            if matched_in and in_dev != matched_in:
                logging.info(
                    f"Auto-aligning TeamTalk input device from '{in_dev.name if in_dev else None}' to '{matched_in.name}' to match player output"
                )
                in_dev = matched_in

        if out_dev:
            self.player.set_output_device(str(out_dev.id))
            logging.info(f"Player output device initialized to: {out_dev.name} ({out_dev.id})")
        else:
            logging.error("No valid player output device found")

        if in_dev:
            self.ttclient.set_input_device(int(in_dev.id))
            logging.info(f"TeamTalk input device initialized to: {in_dev.name} ({in_dev.id})")
        else:
            logging.error("No valid TeamTalk input device found")

        logging.debug("Sound devices initialized")
