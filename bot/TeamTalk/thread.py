from __future__ import annotations
from importlib.machinery import SourceFileLoader
import logging
import os
from threading import Thread
import time
from typing import Any, Callable, Optional, Tuple, TYPE_CHECKING
from types import ModuleType


import types
import sys

from bot.TeamTalk.structs import *
from bot import app_vars

if TYPE_CHECKING:
    from bot import Bot
    from bot.TeamTalk import TeamTalk


class TeamTalkThread(Thread):
    def __init__(self, bot: Bot, ttclient: TeamTalk):
        Thread.__init__(self, daemon=True)
        self.name = "TeamTalkThread"
        self.bot = bot
        self.config = ttclient.config
        self.ttclient = ttclient

    def run(self) -> None:
        if self.config.event_handling.load_event_handlers:
            self.event_handlers = self.import_event_handlers()
        self._close = False
        while not self._close:
            try:
                self._process_next_event()
            except Exception as e:
                logging.error(f"Error in TeamTalk event pump: {e}", exc_info=True)
                time.sleep(0.05)

    def _process_next_event(self) -> None:
        event = self.ttclient.get_event(self.ttclient.tt.getMessage())
        if event.event_type == EventType.NONE:
            return
        self.ttclient.dispatch_event(event)
        if (
            event.event_type == EventType.ERROR
            and self.ttclient.state == State.CONNECTED
        ):
            self.ttclient.errors_queue.put(event.error)
        elif (
            event.event_type == EventType.SUCCESS
            and self.ttclient.state == State.CONNECTED
        ):
            self.ttclient.event_success_queue.put(event)
        elif (
            event.event_type == EventType.USER_TEXT_MESSAGE
            and event.message.type == MessageType.User
        ):
            self.ttclient.message_queue.put(event.message)
        elif (
            event.event_type == EventType.USER_TEXT_MESSAGE
            and event.message.type == MessageType.Channel
        ):
            text = (event.message.text or "").strip()
            if text.lower().startswith("pm "):
                self.ttclient.message_queue.put(event.message)
            self._handle_channel_message(event.message)
        elif (
            event.event_type == EventType.FILE_NEW
            and event.file.username == self.config.username
            and event.file.channel.id == self.ttclient.channel.id
        ):
            self.ttclient.uploaded_files_queue.put(event.file)
        elif (
            event.event_type == EventType.CON_FAILED
            or event.event_type == EventType.CON_LOST
            or event.event_type == EventType.MYSELF_KICKED
        ):
            if event.event_type == EventType.CON_FAILED:
                logging.warning("Connection failed")
            elif event.event_type == EventType.CON_LOST:
                logging.warning("Server lost")
            else:
                logging.warning("Kicked")
            self.ttclient.disconnect()
            if (
                self.ttclient.reconnect
                and self.ttclient.reconnect_attempt
                < self.config.reconnection_attempts
                or self.config.reconnection_attempts < 0
            ):
                self.ttclient.disconnect()
                self._interruptible_sleep(self.config.reconnection_timeout)
                if self._close:
                    return
                self.ttclient.connect()
                self.ttclient.reconnect_attempt += 1
            else:
                logging.error("Connection error")
                sys.exit(1)
        elif event.event_type == EventType.CON_SUCCESS:
            self.ttclient.reconnect_attempt = 0
            self.ttclient.login()
        elif event.event_type == EventType.ERROR:
            if self.ttclient.flags & Flags.AUTHORIZED == Flags(0):
                logging.warning("Login failed")
                if (
                    self.ttclient.reconnect
                    and self.ttclient.reconnect_attempt
                    < self.config.reconnection_attempts
                    or self.config.reconnection_attempts < 0
                ):
                    self._interruptible_sleep(self.config.reconnection_timeout)
                    if self._close:
                        return
                    self.ttclient.login()
                else:
                    logging.error("Login error")
                    sys.exit(1)
            else:
                logging.warning("Failed to join channel")
                if (
                    self.ttclient.reconnect
                    and self.ttclient.reconnect_attempt
                    < self.config.reconnection_attempts
                    or self.config.reconnection_attempts < 0
                ):
                    self._interruptible_sleep(self.config.reconnection_timeout)
                    if self._close:
                        return
                    self.ttclient.join()
                else:
                    logging.error("Error joining channel")
                    sys.exit(1)
        elif event.event_type == EventType.MYSELF_LOGGEDIN:
            self.ttclient.user_account = event.user_account
            self.ttclient.reconnect_attempt = 0
            self.ttclient.join()
        elif (
            event.event_type == EventType.SUCCESS
            and self.ttclient.state == State.CONNECTING
        ):
            self.ttclient.reconnect_attempt = 0
            self.ttclient.reconnect = True
            self.ttclient.state = State.CONNECTED
            self.ttclient.change_status_text(self.ttclient.status)
        elif event.event_type == EventType.USER_LEFT:
            # Auto-return is only valid for a temporary move requested through
            # the "jc" command. Manual TeamTalk moves must never cause the bot
            # to stop playback or jump back to the configured/root channel.
            try:
                requested_user_id = getattr(
                    self.bot, "jc_requested_by_user_id", None
                )
                configured_channel = getattr(
                    self.bot, "default_channel", self.config.channel
                )

                if requested_user_id is None:
                    logging.debug(
                        "Auto-return skipped: bot was not moved by the jc command."
                    )
                elif event.user.id != requested_user_id:
                    logging.debug(
                        "Auto-return skipped: user %s left, but jc was requested by %s.",
                        event.user.id,
                        requested_user_id,
                    )
                elif (
                    isinstance(configured_channel, str)
                    and not configured_channel.strip()
                ):
                    logging.info(
                        "Auto-return skipped: no default channel is configured."
                    )
                    self.bot.jc_requested_by_user_id = None
                else:
                    current_channel_id = self.ttclient.channel.id

                    if isinstance(configured_channel, int):
                        default_channel_id = configured_channel
                    else:
                        def _str(data):
                            if isinstance(data, str):
                                return (
                                    bytes(data, "utf-8")
                                    if os.supports_bytes_environ
                                    else data
                                )
                            return str(data, "utf-8")

                        default_channel_id = self.ttclient.tt.getChannelIDFromPath(
                            _str(configured_channel)
                        )

                    if default_channel_id == 0:
                        logging.warning(
                            "Auto-return skipped: configured default channel "
                            "could not be resolved: %r",
                            configured_channel,
                        )
                        self.bot.jc_requested_by_user_id = None
                    elif current_channel_id == default_channel_id:
                        self.bot.jc_requested_by_user_id = None
                    else:
                        # Issue the move first. If it fails synchronously,
                        # playback must remain untouched.
                        self.ttclient.move_user(
                            self.ttclient.user.id, default_channel_id
                        )

                        from bot.player.enums import State as PlayerState

                        if self.bot.player.state != PlayerState.Stopped:
                            self.bot.player.stop()

                        self.bot.jc_requested_by_user_id = None
                        logging.info(
                            "Auto-return triggered: jc requester left; "
                            "returning bot to configured default channel."
                        )
            except Exception as e:
                logging.error(f"Error in auto-return logic: {e}")


        if self.config.event_handling.load_event_handlers:
            self.run_event_handler(event)

    def _handle_channel_message(self, message: Message) -> None:
        try:
            general_cfg = getattr(self.bot.config, "general", None) or getattr(self.config, "general", None)
            if not general_cfg or not getattr(general_cfg, "channel_translation", False):
                return

            text = (message.text or "").strip()
            if not text:
                return

            # Prevent infinite echo loops: ignore messages sent by this bot instance
            my_user_id = self.ttclient.tt.getMyUserID()
            if my_user_id > 0 and message.user.id == my_user_id:
                return
            if getattr(self.ttclient, "user", None) and message.user.id == self.ttclient.user.id:
                return

            # Check bot channel match if channel ID is known
            my_channel_id = self.ttclient.tt.getMyChannelID()
            if message.channel.id != 0 and my_channel_id != 0 and message.channel.id != my_channel_id:
                return

            Thread(
                target=self._translate_and_post_channel_message,
                args=(message,),
                daemon=True,
            ).start()
        except Exception as e:
            logging.error(f"Error handling channel message for translation: {e}", exc_info=True)

    def _translate_and_post_channel_message(self, message: Message) -> None:
        try:
            text = (message.text or "").strip()
            if not text:
                return

            general_cfg = getattr(self.bot.config, "general", None) or getattr(self.config, "general", None)
            if not general_cfg or not getattr(general_cfg, "channel_translation", False):
                return

            lang_from = getattr(general_cfg, "channel_translation_from", "auto") or "auto"
            lang_to = getattr(general_cfg, "channel_translation_to", "en") or "en"
            if lang_to == "auto":
                lang_to = "en"

            from bot.services.channel_translator import (
                translate_channel_text,
                is_same_or_parent_language,
            )

            if lang_from == "auto":
                # Mode 1: One-way translation (auto-detect source -> lang_to)
                translated, detected = translate_channel_text(
                    text, lang_from="auto", lang_to=lang_to
                )
                if not translated:
                    return

                # If detected language matches target language, skip
                if detected and is_same_or_parent_language(detected, lang_to):
                    logging.debug(
                        f"Channel translation skipped: detected language '{detected}' matches target '{lang_to}'"
                    )
                    return
            else:
                # Mode 2: Bidirectional translation (both lang_from and lang_to are specific)
                translated, detected = translate_channel_text(
                    text, lang_from="auto", lang_to=lang_to
                )
                if not translated:
                    return

                if detected and is_same_or_parent_language(detected, lang_to):
                    # Message is in target language -> translate into source language!
                    translated, _ = translate_channel_text(
                        text, lang_from=lang_to, lang_to=lang_from
                    )
                    if not translated:
                        return
                elif detected and is_same_or_parent_language(detected, lang_from):
                    # Message is in source language -> translated is already in target language!
                    pass
                else:
                    # Message is in a third language -> keep translation in target language
                    pass

            # If translation matches original text (e.g. symbols, numbers), skip
            if translated.strip().lower() == text.strip().lower():
                logging.debug("Channel translation skipped: translation matches source text")
                return

            nickname = message.user.nickname or message.user.username or "User"
            response_text = f"[{nickname}]: {translated.strip()}"
            self.ttclient.send_message(response_text, type=2)
        except Exception as e:
            logging.error(f"Error translating and posting channel message: {e}", exc_info=True)

    def close(self) -> None:
        self._close = True

    def _interruptible_sleep(self, seconds: float) -> None:
        end_time = time.time() + seconds
        while not getattr(self, "_close", False) and time.time() < end_time:
            time.sleep(0.1)

    def get_function_name_by_event_type(self, event_type: EventType) -> str:
        return f"on_{event_type.name.lower()}"

    def import_event_handlers(self) -> ModuleType:
        try:
            handler_file = self.config.event_handling.event_handlers_file_name
            if not os.path.isabs(handler_file):
                for base in [app_vars.data_dir, app_vars.directory]:
                    c = os.path.join(base, handler_file)
                    if os.path.exists(c):
                        handler_file = c
                        break

            if (
                os.path.isfile(handler_file)
                and os.path.splitext(handler_file)[1] == ".py"
            ):
                module = SourceFileLoader(
                    os.path.splitext(os.path.basename(handler_file))[0],
                    handler_file,
                ).load_module()
            elif os.path.isdir(handler_file) and "__init__.py" in os.listdir(
                handler_file
            ):
                module = SourceFileLoader(
                    os.path.basename(handler_file),
                    os.path.join(handler_file, "__init__.py"),
                ).load_module()
            else:
                logging.error(
                    f"Incorrect path to event handlers ('{handler_file}'). An empty module will be used"
                )
                module = types.ModuleType("event_handlers")
        except Exception as e:
            logging.error(
                "Can't load specified event handlers. Error: {}. An empty module will be used.".format(
                    e
                )
            )
            module = types.ModuleType("event_handlers")
        return module

    def parse_event(self, event: Event) -> Tuple[Any, ...]:
        if event.event_type in (
            EventType.USER_UPDATE,
            EventType.USER_JOINED,
            EventType.USER_LOGGEDIN,
            EventType.USER_LOGGEDOUT,
        ):
            return (event.user,)
        elif event.event_type == EventType.USER_LEFT:
            return (event.source, event.user)
        elif event.event_type == EventType.USER_TEXT_MESSAGE:
            return (event.message,)
        elif event.event_type in (
            EventType.CHANNEL_NEW,
            EventType.CHANNEL_UPDATE,
            EventType.CHANNEL_REMOVE,
        ):
            return (event.channel,)
        elif event.event_type in (
            EventType.FILE_NEW,
            EventType.FILE_REMOVE,
        ):
            return (event.file,)
        else:
            return (1, 2)

    def run_event_handler(self, event: Event) -> None:
        try:
            event_handler: Optional[Callable[..., None]] = getattr(
                self.event_handlers,
                self.get_function_name_by_event_type(event.event_type),
                None,
            )
            if not event_handler:
                return
            try:
                event_handler(*self.parse_event(event), self.bot)
            except Exception as e:
                print("Error in event handling {}".format(e))
        except AttributeError:
            self.event_handlers = self.import_event_handlers()
            self.run_event_handler(event)
