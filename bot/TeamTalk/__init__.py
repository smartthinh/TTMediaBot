from __future__ import annotations
import logging
import os
import re
import sys
from typing import AnyStr, Callable, List, TYPE_CHECKING, Optional, Union
import threading
from queue import Queue

from bot import app_vars
from bot.sound_devices import SoundDevice, SoundDeviceType


if sys.platform == "win32":
    if sys.version_info.major == 3 and sys.version_info.minor >= 8:
        os.add_dll_directory(app_vars.directory)
    else:
        os.chdir(app_vars.directory)

from bot.TeamTalk.thread import TeamTalkThread
from bot.TeamTalk.structs import *

import TeamTalkPy


re_line_endings = re.compile("[\\r\\n]")

if TYPE_CHECKING:
    from bot import Bot


def _str(data: AnyStr) -> AnyStr:
    if isinstance(data, str):
        if os.supports_bytes_environ:
            return bytes(data, "utf-8")
        else:
            return data
    else:
        return str(data, "utf-8")


def split(text: str, max_length: int = app_vars.max_message_length) -> List[str]:
    sections = text.split("\f")
    all_chunks: List[str] = []
    for section in sections:
        section = section.strip("\r\n")
        if not section:
            continue
        if len(section) <= max_length:
            all_chunks.append(section)
        else:
            lines = [""]
            for line in section.split("\n"):
                line = line.strip("\r")
                if len(line) <= max_length:
                    if len(lines[-1]) > 0 and len(lines[-1]) + len(line) + 1 <= max_length:
                        lines[-1] += "\n" + line
                    elif len(lines) == 1 and len(lines[0]) == 0:
                        lines[0] = line
                    else:
                        lines.append(line)
                else:
                    words = [""]
                    for word in line.split(" "):
                        if len(word) <= max_length:
                            if (
                                len(words[-1]) > 0
                                and len(words[-1]) + len(word) + 1 <= max_length
                            ):
                                words[-1] += " " + word
                            elif len(words) == 1 and len(words[0]) == 0:
                                words[0] = word
                            else:
                                words.append(word)
                        else:
                            chunk = word
                            for _ in range(0, int(len(chunk) / max_length) + 1):
                                words.append(chunk[0:max_length])
                                chunk = chunk[max_length::]
                    lines += words
            all_chunks.extend([l for l in lines if l])
    return all_chunks if all_chunks else [text]


class TeamTalk:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.config = bot.config.teamtalk
        self.translator = bot.translator
        TeamTalkPy.setLicense(
            _str(self.config.license_name), _str(self.config.license_key)
        )
        self.tt = TeamTalkPy.TeamTalk()
        self.state = State.NOT_CONNECTED
        self.is_voice_transmission_enabled = False
        self.is_sound_output_enabled = False
        self.is_mic_active = False
        self.current_input_device_id: Optional[int] = None
        self.current_output_device_id: Optional[int] = None
        self.default_bot_input_device_id: Optional[int] = None
        self._event_listeners: List[Callable[[Event], None]] = []
        self._listener_lock = threading.Lock()
        self.nickname = self.config.nickname
        self.gender = UserStatusMode.__members__[self.config.gender.upper()]
        self.status = self.default_status
        self.errors_queue: Queue[Error] = Queue()
        self.event_success_queue: Queue[Event] = Queue()
        self.message_queue: Queue[Message] = Queue()
        self.myself_event_queue: Queue[Event] = Queue()
        self.uploaded_files_queue: Queue[File] = Queue()
        self.thread = TeamTalkThread(bot, self)
        self.reconnect = False
        self.reconnect_attempt = 0
        self.user_account: UserAccount

    def initialize(self) -> None:
        logging.debug("Initializing TeamTalk")
        self.thread.start()
        self.connect()
        logging.debug("TeamTalk initialized")

    def close(self) -> None:
        logging.debug("Closing teamtalk")
        if self.is_mic_active:
            self.restore_input_device()
        self.disable_sound_output()
        self.thread.close()
        self.disconnect()
        self.state = State.NOT_CONNECTED
        self.tt.closeTeamTalk()
        logging.debug("Teamtalk closed")

    def connect(self) -> None:
        self.state = State.CONNECTING
        if self.config.encrypted:
            ctx = TeamTalkPy.EncryptionContext()
            ctx.bVerifyPeer = False
            ca_path = None
            for candidate in [
                os.path.join(app_vars.data_dir, "ttservercert.pem"),
                os.path.join(app_vars.data_dir, "data", "ttservercert.pem"),
                os.path.join(app_vars.directory, "data", "ttservercert.pem"),
            ]:
                if os.path.exists(candidate):
                    ca_path = candidate
                    break

            if ca_path:
                ctx.szCAFile = _str(ca_path)
                ctx.bVerifyPeer = True
            else:
                # Dynamically fetch the server certificate to trust it
                import ssl
                logging.info(f"Attempting to fetch SSL certificate from {self.config.hostname}:{self.config.tcp_port}")
                try:
                    cert_pem = ssl.get_server_certificate((self.config.hostname, self.config.tcp_port))
                    temp_cert_file = os.path.join(app_vars.temp_dir, f"tt_{self.config.hostname}_{self.config.tcp_port}.pem")
                    with open(temp_cert_file, "w") as f:
                        f.write(cert_pem)
                    ctx.szCAFile = _str(temp_cert_file)
                    ctx.bVerifyPeer = True
                    logging.info(f"Dynamically trusted certificate at {temp_cert_file}")
                except Exception as e:
                    logging.warning(f"Failed to dynamically fetch server certificate: {e}")
            self.tt.setEncryptionContext(ctx)
        self.tt.connect(
            _str(self.config.hostname),
            self.config.tcp_port,
            self.config.udp_port,
            0,
            0,
            self.config.encrypted,
        )

    def disconnect(self) -> None:
        self.tt.disconnect()
        self.state = State.NOT_CONNECTED

    def login(self) -> None:
        self.tt.doLogin(
            _str(self.config.nickname),
            _str(self.config.username),
            _str(self.config.password),
            _str(app_vars.client_name),
        )

    def join(self) -> None:
        if isinstance(self.config.channel, int):
            channel_id = int(self.config.channel)
        else:
            channel_id = self.tt.getChannelIDFromPath(_str(self.config.channel))
            if channel_id == 0:
                channel_id = 1
        self.tt.doJoinChannelByID(channel_id, _str(self.config.channel_password))

    @property
    def default_status(self) -> str:
        if self.config.status:
            return self.config.status
        else:
            return self.translator.translate('Send "h" for help')

    def send_message(
        self, text: str, user: Optional[User] = None, type: int = 1
    ) -> None:
        for string in split(text):
            message = TeamTalkPy.TextMessage()
            message.nFromUserID = self.tt.getMyUserID()
            message.nMsgType = type
            message.szMessage = _str(string)
            if type == 1:
                if user is not None:
                    if isinstance(user, int):
                        message.nToUserID = user
                    else:
                        message.nToUserID = user.id
            elif type == 2:
                cid = self.tt.getMyChannelID()
                if cid == 0 and hasattr(self, "channel") and getattr(self.channel, "id", 0) != 0:
                    cid = self.channel.id
                message.nChannelID = cid
            self.tt.doTextMessage(message)

    def send_file(self, channel: Union[int, str], file_path: str):
        if isinstance(channel, int):
            channel_id = channel
        else:
            channel_id = self.tt.getChannelIDFromPath(_str(channel))
            if channel_id == 0:
                raise ValueError()
        return self.tt.doSendFile(channel_id, _str(file_path))

    def delete_file(self, channel: Union[int, str], file_id: int) -> int:
        if isinstance(channel, int):
            channel_id = channel
        else:
            channel_id = self.tt.getChannelIDFromPath(_str(channel))
            if channel_id == 0 or file_id == 0:
                raise ValueError()
        return self.tt.doDeleteFile(channel_id, file_id)

    def get_channel_files(self, channel: Union[int, str]) -> List[File]:
        if isinstance(channel, int):
            channel_id = channel
        else:
            channel_id = self.tt.getChannelIDFromPath(_str(channel))
            if channel_id == 0:
                raise ValueError()
        try:
            raw_files = self.tt.getChannelFiles(channel_id)
            return [self.get_file(f) for f in raw_files]
        except Exception as e:
            logging.error(f"Error getting files for channel {channel_id}: {e}")
            return []

    def join_channel(self, channel: Union[str, int], password: str) -> int:
        if isinstance(channel, int):
            channel_id = channel
        else:
            channel_id = self.tt.getChannelIDFromPath(_str(channel))
            if channel_id == 0:
                raise ValueError()
        return self.tt.doJoinChannelByID(channel_id, _str(password))

    def move_user(self, user_id: int, channel: Union[str, int]) -> int:
        if isinstance(channel, int):
            channel_id = channel
        else:
            channel_id = self.tt.getChannelIDFromPath(_str(channel))
            if channel_id == 0:
                raise ValueError()
        return self.tt.doMoveUser(user_id, channel_id)

    def change_nickname(self, nickname: str) -> None:
        self.tt.doChangeNickname(_str(nickname))

    def change_status_text(self, text: str) -> None:
        if text:
            self.status = split(text)[0]
        else:
            self.status = split(self.default_status)[0]
        self.tt.doChangeStatus(self.gender.value, _str(self.status))

    def change_gender(self, gender: str) -> None:
        self.gender = UserStatusMode.__members__[gender.upper()]
        self.tt.doChangeStatus(self.gender.value, _str(self.status))

    def get_channel(self, channel_id: int) -> Channel:
        channel = self.tt.getChannel(channel_id)
        return self.get_channel_from_obj(channel)

    def get_channel_from_obj(self, obj: TeamTalkPy.Channel) -> Channel:
        try:
            return Channel(
                obj.nChannelID,
                obj.szName,
                obj.szTopic,
                obj.nMaxUsers,
                ChannelType(obj.uChannelType),
            )
        except ValueError:
            return Channel(0, "", "", 0, ChannelType.Default)

    @property
    def flags(self) -> Flags:
        return Flags(self.tt.getFlags())

    def get_error(self, error_no: int, cmdid: int) -> Error:
        try:
            error_type = ErrorType(error_no)
        except ValueError:
            error_type = ErrorType(0)
        return Error(_str(self.tt.getErrorMessage(error_no)), error_type, cmdid)

    def get_message(self, msg: TeamTalkPy.TextMessage) -> Message:
        try:
            return Message(
                re.sub(re_line_endings, "", _str(msg.szMessage)),
                self.get_user(msg.nFromUserID),
                self.get_channel(msg.nChannelID),
                MessageType(msg.nMsgType),
            )
        except Exception:
            return Message("", self.get_user(1), self.get_channel(1), MessageType.User)

    def get_file(self, file: TeamTalkPy.RemoteFile) -> File:
        return File(
            file.nFileID,
            _str(file.szFileName),
            self.get_channel(file.nChannelID),
            file.nFileSize,
            _str(file.szUsername),
        )

    @property
    def user(self) -> User:
        user = self.get_user(self.tt.getMyUserID())
        user.user_account = self.user_account
        return user

    @property
    def channel(self) -> Channel:
        return self.get_channel(self.tt.getMyChannelID())

    def get_user(self, id: int) -> User:
        user = self.tt.getUser(id)
        gender = UserStatusMode(user.nStatusMode)
        return User(
            user.nUserID,
            _str(user.szNickname),
            _str(user.szUsername),
            _str(user.szStatusMsg),
            gender,
            UserState(user.uUserState),
            self.get_channel(user.nChannelID),
            _str(user.szClientName),
            user.uVersion,
            self.get_user_account(_str(user.szUsername)),
            UserType(user.uUserType),
            True
            if _str(user.szUsername) in self.config.users.admins or user.uUserType == 2
            else False,
            _str(user.szUsername) in self.config.users.banned_users,
        )

    def get_user_account(self, username: str) -> UserAccount:
        return UserAccount(username, "", "", UserType.Null, UserRight.Null, "/")

    def get_user_account_by_tt_obj(self, obj: TeamTalkPy.UserAccount) -> UserAccount:
        return UserAccount(
            _str(obj.szUsername),
            _str(obj.szPassword),
            _str(obj.szNote),
            UserType(obj.uUserType),
            UserRight(obj.uUserRights),
            _str(obj.szInitChannel),
        )

    def get_event(self, obj: TeamTalkPy.TTMessage) -> Event:
        try:
            channel = self.get_channel_from_obj(obj.channel)
        except (UnicodeDecodeError, ValueError):
            channel = Channel(1, "", "", 0, ChannelType.Default)
        try:
            error = self.get_error(obj.clienterrormsg.nErrorNo, obj.nSource)
        except (UnicodeDecodeError, ValueError):
            error = Error("", ErrorType.Success, 1)
        try:
            file = self.get_file(obj.remotefile)
        except (UnicodeDecodeError, ValueError):
            file = File(1, "", channel, 0, "")
        try:
            user_account = self.get_user_account_by_tt_obj(obj.useraccount)
        except (UnicodeDecodeError, ValueError):
            user_account = UserAccount("", "", "", UserType.Null, UserRight.Null, "")
        try:
            user = self.get_user(obj.user.nUserID)
        except (UnicodeDecodeError, ValueError):
            user = User(
                1,
                "",
                "",
                "",
                UserStatusMode.M,
                UserState.Null,
                channel,
                "",
                1,
                user_account,
                UserType.Null,
                False,
                False,
            )
        try:
            message = self.get_message(obj.textmessage)
        except (UnicodeDecodeError, ValueError):
            message = Message("", user, channel, MessageType.NONE)
        return Event(
            EventType(obj.nClientEvent),
            obj.nSource,
            channel,
            error,
            file,
            message,
            user,
            user_account,
        )

    def get_input_devices(self, sound_system: Optional[int] = None) -> List[SoundDevice]:
        devices: List[SoundDevice] = []
        device_list = [i for i in self.tt.getSoundDevices()]
        for device in device_list:
            if device.nMaxInputChannels > 0:
                if sound_system is not None:
                    if device.nSoundSystem == sound_system:
                        devices.append(
                            SoundDevice(
                                _str(device.szDeviceName),
                                device.nDeviceID,
                                SoundDeviceType.Input,
                            )
                        )
                elif sys.platform == "win32":
                    if device.nSoundSystem == TeamTalkPy.SoundSystem.SOUNDSYSTEM_WASAPI:
                        devices.append(
                            SoundDevice(
                                _str(device.szDeviceName),
                                device.nDeviceID,
                                SoundDeviceType.Input,
                            )
                        )
                else:
                    devices.append(
                        SoundDevice(
                            _str(device.szDeviceName),
                            device.nDeviceID,
                            SoundDeviceType.Input,
                        )
                    )
        if sys.platform == "win32" and not devices and sound_system is None:
            for device in device_list:
                if device.nMaxInputChannels > 0:
                    devices.append(
                        SoundDevice(
                            _str(device.szDeviceName),
                            device.nDeviceID,
                            SoundDeviceType.Input,
                        )
                    )
        return devices

    def get_output_devices(self, sound_system: Optional[int] = None) -> List[SoundDevice]:
        devices: List[SoundDevice] = []
        device_list = [i for i in self.tt.getSoundDevices()]
        for device in device_list:
            if device.nMaxOutputChannels > 0:
                if sound_system is not None:
                    if device.nSoundSystem == sound_system:
                        devices.append(
                            SoundDevice(
                                _str(device.szDeviceName),
                                device.nDeviceID,
                                SoundDeviceType.Output,
                            )
                        )
                elif sys.platform == "win32":
                    if device.nSoundSystem == TeamTalkPy.SoundSystem.SOUNDSYSTEM_WASAPI:
                        devices.append(
                            SoundDevice(
                                _str(device.szDeviceName),
                                device.nDeviceID,
                                SoundDeviceType.Output,
                            )
                        )
                else:
                    devices.append(
                        SoundDevice(
                            _str(device.szDeviceName),
                            device.nDeviceID,
                            SoundDeviceType.Output,
                        )
                    )
        if sys.platform == "win32" and not devices and sound_system is None:
            for device in device_list:
                if device.nMaxOutputChannels > 0:
                    devices.append(
                        SoundDevice(
                            _str(device.szDeviceName),
                            device.nDeviceID,
                            SoundDeviceType.Output,
                        )
                    )
        return devices

    def set_output_device(self, id: int) -> bool:
        """Sets the sound output device for TeamTalk audio playback."""
        try:
            import TeamTalkPy
            dll = TeamTalkPy.TeamTalk5.dll
            try:
                dll.TT_CloseSoundOutputDevice(self.tt._tt)
            except Exception:
                pass
            res = self.tt.initSoundOutputDevice(id)
            if res:
                self.current_output_device_id = id
                self.is_sound_output_enabled = True
                try:
                    dll.TT_SetSoundOutputMute(self.tt._tt, False)
                except Exception:
                    pass
                logging.info(f"TeamTalk sound output device set to ID {id}")
                return True
            else:
                logging.error(f"Failed to set TeamTalk sound output device to ID {id}")
                return False
        except Exception as e:
            logging.error(f"Error setting output device: {e}")
            return False

    def set_input_device_live(self, id: int) -> bool:
        """Changes the sound input device live while connected."""
        try:
            import TeamTalkPy
            dll = TeamTalkPy.TeamTalk5.dll
            try:
                dll.TT_CloseSoundInputDevice(self.tt._tt)
            except Exception:
                pass
            res = self.tt.initSoundInputDevice(id)
            if res:
                self.current_input_device_id = id
                try:
                    dll.TT_EnableVoiceActivation(self.tt._tt, False)
                except Exception:
                    pass
                logging.info(f"TeamTalk sound input device set to ID {id}")

                # If changed to a virtual audio cable, also align player output device
                if hasattr(self, "bot") and hasattr(self.bot, "sound_device_manager") and hasattr(self.bot, "player"):
                    try:
                        sdm = self.bot.sound_device_manager
                        in_dev_obj = next((d for d in sdm.input_devices if int(d.id) == id), None)
                        if in_dev_obj and ("virtual audio cable" in in_dev_obj.name.lower() or "line " in in_dev_obj.name.lower()):
                            matched_out = sdm._find_matching_cable(in_dev_obj.name, sdm.output_devices)
                            if matched_out:
                                self.bot.player.set_output_device(str(matched_out.id))
                                logging.info(f"Auto-aligned live player output device to: {matched_out.name}")
                    except Exception as ex:
                        logging.debug(f"Notice auto-aligning player output on live input switch: {ex}")

                return True
            else:
                logging.error(f"Failed to set TeamTalk sound input device to ID {id}")
                return False
        except Exception as e:
            logging.error(f"Error setting input device live: {e}")
            return False

    def set_input_device(self, id: int) -> None:
        self.current_input_device_id = id
        if not self.is_mic_active:
            self.default_bot_input_device_id = id
        self.tt.initSoundInputDevice(id)

    def enable_voice_transmission(self) -> None:
        self.tt.enableVoiceTransmission(True)
        self.is_voice_transmission_enabled = True

    def disable_voice_transmission(self) -> None:
        self.tt.enableVoiceTransmission(False)
        self.is_voice_transmission_enabled = False

    def get_microphone_device_id(self) -> Optional[int]:
        """Finds the most suitable microphone device ID for voice chat."""
        devices = self.get_input_devices()
        # 1. Prefer an input device with 'mic' or 'micro' in name that isn't loopback or line
        for dev in devices:
            name_lower = dev.name.lower()
            if any(x in name_lower for x in ["loopback", "line", "stereo mix", "wave", "what u hear"]):
                continue
            if "mic" in name_lower or "micro" in name_lower:
                return dev.id

        # 2. Check system default input device from TeamTalk
        try:
            in_dev, _ = self.tt.getDefaultSoundDevices()
            if in_dev.value > 0:
                return in_dev.value
        except Exception:
            pass

        # 3. Fallback to any non-loopback device
        for dev in devices:
            name_lower = dev.name.lower()
            if "loopback" not in name_lower and "line" not in name_lower:
                return dev.id

        if devices:
            return devices[0].id
        return None

    def switch_to_microphone(self, mic_device_id: Optional[int] = None) -> bool:
        """Switches the TeamTalk input device to the user's physical microphone and enables voice transmission."""
        try:
            import TeamTalkPy
            dll = TeamTalkPy.TeamTalk5.dll

            if mic_device_id is None or mic_device_id <= 0:
                mic_device_id = self.get_microphone_device_id()

            if mic_device_id is None or mic_device_id <= 0:
                logging.error("No suitable microphone device found")
                return False

            logging.info(f"Switching input device to microphone ID {mic_device_id}")

            # Close existing sound input device before re-initializing
            try:
                dll.TT_CloseSoundInputDevice(self.tt._tt)
            except Exception as e:
                logging.debug(f"TT_CloseSoundInputDevice notice: {e}")

            # Initialize microphone sound input device
            res = self.tt.initSoundInputDevice(mic_device_id)
            if not res:
                logging.error(f"Failed to initSoundInputDevice with ID {mic_device_id}")
                return False

            self.current_input_device_id = mic_device_id
            self.is_mic_active = True

            # Disable VAD (Voice Activation Detection) so audio is transmitted continuously without threshold cut-off
            try:
                dll.TT_EnableVoiceActivation(self.tt._tt, False)
            except Exception as e:
                logging.debug(f"TT_EnableVoiceActivation notice: {e}")

            # Ensure input gain level is standard or higher (1000 is 100%)
            try:
                gain = dll.TT_GetSoundInputGainLevel(self.tt._tt)
                if gain < 1000:
                    dll.TT_SetSoundInputGainLevel(self.tt._tt, 1000)
            except Exception as e:
                logging.debug(f"TT_SetSoundInputGainLevel notice: {e}")

            # Start voice transmission
            self.enable_voice_transmission()
            logging.info("Microphone voice transmission successfully activated")
            return True
        except Exception as e:
            logging.error(f"Error in switch_to_microphone: {e}")
            return False

    def restore_input_device(self) -> None:
        """Restores the bot's original sound input device (e.g. VAC) and resets voice transmission."""
        try:
            import TeamTalkPy
            dll = TeamTalkPy.TeamTalk5.dll

            self.is_mic_active = False

            # Stop microphone voice transmission
            self.disable_voice_transmission()

            # Target device: default bot input device
            target_device = self.default_bot_input_device_id
            if target_device is None:
                try:
                    if hasattr(self, "bot") and hasattr(self.bot, "sound_device_manager"):
                        sdm = self.bot.sound_device_manager
                        idx = sdm.input_device_index
                        target_device = int(sdm.input_devices[idx].id)
                except Exception:
                    pass

            if target_device is not None:
                try:
                    dll.TT_CloseSoundInputDevice(self.tt._tt)
                except Exception as e:
                    logging.debug(f"TT_CloseSoundInputDevice notice: {e}")

                res = self.tt.initSoundInputDevice(target_device)
                if res:
                    self.current_input_device_id = target_device
                    logging.info(f"Restored sound input device to ID {target_device}")
                else:
                    logging.error(f"Failed to restore sound input device to ID {target_device}")

            # If the media player is currently playing, resume voice transmission for the music
            if hasattr(self, "bot") and hasattr(self.bot, "player") and self.bot.player:
                try:
                    from bot.player.enums import State as PlayerState
                    if getattr(self.bot.player, "state", None) == PlayerState.Playing:
                        self.enable_voice_transmission()
                except Exception:
                    pass
        except Exception as e:
            logging.error(f"Error in restore_input_device: {e}")

    def add_event_listener(self, listener: Callable[[Event], None]) -> None:
        with self._listener_lock:
            if listener not in self._event_listeners:
                self._event_listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[Event], None]) -> None:
        with self._listener_lock:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

    def dispatch_event(self, event: Event) -> None:
        with self._listener_lock:
            listeners = list(self._event_listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception as e:
                logging.error(f"Error in event listener: {e}")

    def enable_sound_output(self, device_id: Optional[int] = None) -> bool:
        try:
            if device_id is None or device_id <= 0:
                if self.current_output_device_id and self.current_output_device_id > 0:
                    device_id = self.current_output_device_id
                else:
                    _, out_dev = self.tt.getDefaultSoundDevices()
                    device_id = out_dev.value
                    if device_id <= 0:
                        devs = [d for d in self.tt.getSoundDevices() if d.nMaxOutputChannels > 0]
                        if devs:
                            device_id = devs[0].nDeviceID
            if device_id and device_id > 0:
                self.tt.initSoundOutputDevice(device_id)
                self.current_output_device_id = device_id
                try:
                    import TeamTalkPy
                    TeamTalkPy.TeamTalk5.dll.TT_SetSoundOutputMute(self.tt._tt, False)
                except Exception:
                    pass
                self.is_sound_output_enabled = True
                logging.info(f"TeamTalk sound output enabled on device {device_id}")
                return True
        except Exception as e:
            logging.error(f"Error enabling sound output: {e}")
        return False

    def disable_sound_output(self) -> None:
        try:
            import TeamTalkPy
            TeamTalkPy.TeamTalk5.dll.TT_CloseSoundOutputDevice(self.tt._tt)
            self.is_sound_output_enabled = False
            logging.info("TeamTalk sound output disabled")
        except Exception as e:
            logging.error(f"Error disabling sound output: {e}")
