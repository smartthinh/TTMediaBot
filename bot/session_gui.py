import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import wx
import wx.adv

from bot.TeamTalk.structs import Event, EventType, MessageType, State
from bot.gui import translate


def _safe_str(val: Any) -> str:
    """Helper to convert TeamTalk string/bytes safely into a unicode string."""
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace").strip()
    return str(val).strip()


class ServerSessionDialog(wx.Dialog):
    """Accessible dialog to interact and chat on TeamTalk using the bot account.

    Designed for 100% accessibility with screen readers (NVDA, JAWS) using
    standard Win32 dialog mechanisms and keyboard navigation.

    Features:
    - Real-time TeamTalk audio output to default speakers/headphones while open.
    - Public channel message input field with Enter-to-send support.
    - Full Tab / Shift+Tab keyboard navigation across all controls.
    - Real-time server and channel event history list (accessible ListBox).
    - Channel user list strictly displaying nicknames of users in the current channel.
    - Voice transmission (microphone) toggle button/hotkey.
    - Audio output mute toggle.
    """

    def __init__(self, parent: wx.Window, s_id: str, server_data: Dict[str, Any]):
        self.server_name = server_data.get("name") or translate("Server")
        title = translate("{server_name} - TeamTalk Session").format(server_name=self.server_name)
        super().__init__(
            parent,
            title=title,
            size=(760, 560),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX | wx.MINIMIZE_BOX,
        )
        self.SetMinSize((520, 420))

        self.parent = parent
        self.s_id = s_id
        self.server_data = server_data

        self.bot = None
        self._is_attached = False
        self._last_channel_id = -1
        self._last_users: List[tuple] = []
        self._is_muted = False
        self._closing = False
        self.private_chat_window: Optional[Any] = None
        self.private_chat_histories: Dict[int, List[str]] = {}

        self._init_ui()
        self.CentreOnParent()

        # Timer for polling connection state and channel synchronization
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        self.timer.Start(250)

        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        # Trigger initial check immediately
        self._check_bot_connection()

    def _init_ui(self):
        # Dialog-level sizer
        dialog_sizer = wx.BoxSizer(wx.VERTICAL)

        # Main container panel with TAB_TRAVERSAL
        panel = wx.Panel(self, style=wx.TAB_TRAVERSAL)
        self.panel = panel
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # ---------------- Status Header (Information only) ----------------
        top_box = wx.BoxSizer(wx.HORIZONTAL)
        status_box = wx.BoxSizer(wx.VERTICAL)
        self.lbl_status = wx.StaticText(panel, label=translate("Status: Connecting..."))
        font_status = self.lbl_status.GetFont()
        font_status.SetWeight(wx.FONTWEIGHT_BOLD)
        self.lbl_status.SetFont(font_status)
        status_box.Add(self.lbl_status, 0, wx.BOTTOM, 2)

        self.lbl_channel = wx.StaticText(panel, label=translate("Channel: Waiting for connection..."))
        status_box.Add(self.lbl_channel, 0, wx.EXPAND)
        top_box.Add(status_box, 1, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(top_box, 0, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(wx.StaticLine(panel), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # ---------------- Create Controls in EXACT Tab Order ----------------
        # 1. History List (Shift+Tab from Message input lands here)
        self.lbl_history = wx.StaticText(panel, label=translate("&Public Chat History:"))
        self.list_history = wx.ListBox(panel, style=wx.LB_SINGLE | wx.LB_HSCROLL)
        self.list_history.SetName(translate("Public Chat History"))
        self.list_history.Bind(wx.EVT_CONTEXT_MENU, self.on_history_context_menu)
        self.list_history.Bind(wx.EVT_KEY_DOWN, self.on_history_key_down)

        # 2. Public Message Input (Initial focus upon dialog open)
        self.lbl_msg = wx.StaticText(panel, label=translate("&Public Message:"))
        self.txt_message = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.txt_message.SetName(translate("Public Message"))
        self.txt_message.SetHint(translate("Type message here and press Enter to send..."))
        self.txt_message.Bind(wx.EVT_TEXT, self.on_text_changed)
        self.txt_message.Bind(wx.EVT_TEXT_ENTER, self.on_send_message)

        # 3. Send Button (placed next to text box; hidden when text box is empty)
        self.btn_send = wx.Button(panel, label=translate("&Send"))
        self.btn_send.SetName(translate("Send"))
        self.btn_send.SetToolTip(translate("Send public message to the current channel (Enter)"))
        self.btn_send.Bind(wx.EVT_BUTTON, self.on_send_message)
        self.btn_send.Show(False)

        # 4. Channel Users List (Tab from Message input lands here)
        self.lbl_users = wx.StaticText(panel, label=translate("&Users in channel (0):"))
        self.list_users = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.list_users.SetName(translate("Users in channel"))
        self.list_users.Bind(wx.EVT_LISTBOX_DCLICK, self.on_user_activated)
        self.list_users.Bind(wx.EVT_KEY_DOWN, self.on_user_key_down)
        self.list_users.Bind(wx.EVT_CONTEXT_MENU, self.on_user_context_menu)

        # 5. Checkboxes (Tab from Users lands here)
        self.selected_mic_device_id = self.server_data.get("selected_mic_device_id")
        self.chk_mic = wx.CheckBox(panel, label=translate("&Microphone (Voice)"))
        if self.server_data.get("mic_enabled", False):
            self.chk_mic.SetValue(True)
        self.chk_mic.SetName(translate("Microphone transmission"))
        self.chk_mic.SetToolTip(translate("Toggle voice transmission through the bot's microphone (Hotkey: F8)"))
        self.chk_mic.Bind(wx.EVT_CHECKBOX, self.on_toggle_mic)
        self.chk_mic.Bind(wx.EVT_CONTEXT_MENU, self.on_mic_context_menu)

        self.chk_mute_out = wx.CheckBox(panel, label=translate("M&ute Audio"))
        self.chk_mute_out.SetName(translate("Mute TeamTalk audio"))
        self.chk_mute_out.SetToolTip(translate("Temporarily mute incoming audio from TeamTalk"))
        self.chk_mute_out.Bind(wx.EVT_CHECKBOX, self.on_toggle_mute_out)

        # 6. Action Buttons (Tab from Checkboxes lands here)
        self.btn_audio_devices = wx.Button(panel, label=translate("&Input & Output Devices..."))
        self.btn_audio_devices.SetName(translate("Input and Output Devices"))
        self.btn_audio_devices.SetToolTip(translate("Configure sound input and output devices for TeamTalk (Hotkeys, computer audio, mic)"))
        self.btn_audio_devices.Bind(wx.EVT_BUTTON, self.on_audio_devices_click)

        self.btn_disconnect = wx.Button(panel, label=translate("&Disconnect"))
        self.btn_disconnect.SetName(translate("Disconnect"))
        self.btn_disconnect.SetToolTip(translate("Disconnect from this TeamTalk server"))
        self.btn_disconnect.Bind(wx.EVT_BUTTON, self.on_disconnect_click)

        self.btn_close = wx.Button(panel, wx.ID_ANY, label=translate("&Close"))
        self.btn_close.SetName(translate("Close"))
        self.btn_close.SetToolTip(translate("Close this session window"))
        self.btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())

        # ---------------- Layout Controls in Sizers ----------------
        # Content row: History (Left) and Users (Right)
        content_sizer = wx.BoxSizer(wx.HORIZONTAL)

        history_col = wx.BoxSizer(wx.VERTICAL)
        history_col.Add(self.lbl_history, 0, wx.BOTTOM, 4)
        history_col.Add(self.list_history, 1, wx.EXPAND)
        content_sizer.Add(history_col, 3, wx.EXPAND | wx.RIGHT, 8)

        users_col = wx.BoxSizer(wx.VERTICAL)
        users_col.Add(self.lbl_users, 0, wx.BOTTOM, 4)
        users_col.Add(self.list_users, 1, wx.EXPAND)
        content_sizer.Add(users_col, 2, wx.EXPAND)
        main_sizer.Add(content_sizer, 1, wx.EXPAND | wx.ALL, 10)

        # Controls row: Checkboxes and Action buttons
        ctrls_row = wx.BoxSizer(wx.HORIZONTAL)
        ctrls_row.Add(self.chk_mic, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        ctrls_row.Add(self.chk_mute_out, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        ctrls_row.AddStretchSpacer(1)
        ctrls_row.Add(self.btn_audio_devices, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        ctrls_row.Add(self.btn_disconnect, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        ctrls_row.Add(self.btn_close, 0, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(ctrls_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Message row: Public message input and dynamic Send button
        msg_box = wx.BoxSizer(wx.HORIZONTAL)
        self.msg_box = msg_box
        msg_box.Add(self.lbl_msg, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        msg_box.Add(self.txt_message, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        msg_box.Add(self.btn_send, 0, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(msg_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        panel.SetSizer(main_sizer)

        dialog_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(dialog_sizer)
        self.Layout()

        # Focus initial message field for rapid typing
        wx.CallAfter(self.txt_message.SetFocus)

    def on_char_hook(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_F8:
            # Toggle microphone transmission
            self.chk_mic.SetValue(not self.chk_mic.GetValue())
            self.on_toggle_mic()
            return
        elif key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            # If user presses Enter while focusing the channel user list, activate private chat immediately
            focused = self.FindFocus()
            if focused == self.list_users:
                self.on_user_activated(event)
                return
        elif key == wx.WXK_ESCAPE:
            # Do not close on Escape key in main session dialog; user requested to click Close button
            return
        event.Skip()

    def add_history(self, text: str):
        """No-op: History list is strictly reserved for public channel messages only."""
        logging.debug(f"[SessionDialog] Non-public event ignored for history: {text}")

    def add_public_message(self, sender: str, text: str, timestamp: str = ""):
        """Appends a public channel message to the history list box."""
        if not timestamp:
            timestamp = time.strftime("%H:%M:%S")
        entry = f"{sender}: {text} [{timestamp}]"
        self.list_history.Append(entry)

        # Auto-scroll to the newest item if the user isn't actively navigating the history list
        focused = self.FindFocus()
        if focused != self.list_history:
            cnt = self.list_history.GetCount()
            if cnt > 0:
                self.list_history.SetSelection(cnt - 1)

    def on_timer(self, event):
        """Polls connection state and syncs channel users and audio output."""
        if self._closing:
            return
        self._check_bot_connection()

    def _check_bot_connection(self):
        active_bots = getattr(self.parent, "active_bots", {})
        bot = active_bots.get(self.s_id)

        if bot and hasattr(bot, "ttclient") and bot.ttclient:
            self.bot = bot
            state = getattr(bot.ttclient, "state", State.NOT_CONNECTED)

            if state == State.CONNECTED:
                if not self._is_attached:
                    self._attach_bot(bot)

                # Channel sync
                try:
                    cid = bot.ttclient.tt.getMyChannelID()
                    if cid != self._last_channel_id:
                        self._last_channel_id = cid
                        channel_obj = bot.ttclient.get_channel(cid)
                        c_name = channel_obj.name if channel_obj and channel_obj.name else f"Channel #{cid}"
                        self.lbl_channel.SetLabel(f"{translate('Channel:')} {c_name}")
                        self.SetTitle(f"{self.server_name} - {c_name} - TeamTalk")
                        self.refresh_users_in_channel()
                except Exception as e:
                    logging.debug(f"Error checking channel in session dialog: {e}")

            elif state == State.CONNECTING:
                self.lbl_status.SetLabel(translate("Status: Connecting..."))
            else:
                if self._is_attached:
                    self.on_bot_disconnected()
        else:
            if self._is_attached:
                self.on_bot_disconnected()

    def _attach_bot(self, bot):
        """Attaches to the active connected bot and restores audio settings."""
        self._is_attached = True
        self.lbl_status.SetLabel(translate("Status: Connected"))

        # 1. Restore TeamTalk sound output device so user hears real-time audio
        try:
            saved_sys = self.server_data.get("session_sound_system")
            saved_out_id = self.server_data.get("session_output_device_id")
            saved_out_name = self.server_data.get("session_output_device_name")
            output_devs = bot.ttclient.get_output_devices(sound_system=saved_sys)

            target_out_id = None
            if saved_out_id is not None:
                for d in output_devs:
                    if d.id == saved_out_id:
                        target_out_id = d.id
                        break
            if target_out_id is None and saved_out_name:
                for d in output_devs:
                    if d.name == saved_out_name:
                        target_out_id = d.id
                        break

            bot.ttclient.enable_sound_output(target_out_id)
        except Exception as e:
            logging.error(f"Error enabling sound output: {e}")

        # Check microphone preference
        saved_mic_state = self.server_data.get("mic_enabled", False)

        # 2. Restore live input device only if mic mode is not enabled
        if not saved_mic_state:
            try:
                saved_in_id = self.server_data.get("session_input_device_id")
                saved_in_name = self.server_data.get("session_input_device_name")
                if saved_in_name or saved_in_id is not None:
                    input_devs = bot.ttclient.get_input_devices(sound_system=saved_sys)
                    target_in_id = None
                    if saved_in_id is not None:
                        for d in input_devs:
                            if d.id == saved_in_id:
                                target_in_id = d.id
                                break
                    if target_in_id is None and saved_in_name:
                        for d in input_devs:
                            if d.name == saved_in_name:
                                target_in_id = d.id
                                break
                    if target_in_id is not None:
                        bot.ttclient.set_input_device_live(target_in_id)
            except Exception as e:
                logging.error(f"Error restoring live input device: {e}")

        # 3. Restore input gain level
        if "session_gain" in self.server_data:
            try:
                import TeamTalkPy
                gain_val = int(self.server_data["session_gain"]) * 10
                TeamTalkPy.TeamTalk5.dll.TT_SetSoundInputGainLevel(bot.ttclient.tt._tt, gain_val)
            except Exception as e:
                logging.error(f"Error restoring gain: {e}")

        # 4. Restore microphone (voice transmission) state
        try:
            saved_mic_id = self.server_data.get("selected_mic_device_id")
            saved_in_id = self.server_data.get("session_input_device_id")
            saved_mic_name = self.server_data.get("selected_mic_device_name")

            all_inputs = bot.ttclient.get_input_devices(sound_system=saved_sys)
            target_mic_id = None
            if saved_mic_id is not None:
                for d in all_inputs:
                    if d.id == saved_mic_id:
                        target_mic_id = d.id
                        break
            if target_mic_id is None and saved_in_id is not None:
                for d in all_inputs:
                    if d.id == saved_in_id:
                        target_mic_id = d.id
                        break
            if target_mic_id is None and saved_mic_name:
                for d in all_inputs:
                    if d.name == saved_mic_name:
                        target_mic_id = d.id
                        break
            if target_mic_id is None:
                target_mic_id = bot.ttclient.get_microphone_device_id()

            if target_mic_id:
                self.selected_mic_device_id = target_mic_id

            if saved_mic_state:
                self.chk_mic.SetValue(True)
                res = bot.ttclient.switch_to_microphone(self.selected_mic_device_id)
                if not res:
                    self.chk_mic.SetValue(False)
            else:
                self.chk_mic.SetValue(False)
        except Exception as e:
            logging.error(f"Error restoring microphone state: {e}")
            self.chk_mic.SetValue(False)

        # Register event listener for real-time history & updates
        bot.ttclient.add_event_listener(self.on_tt_event)

        # Initial welcome history entry
        srv_name = self.server_data.get("name") or "Server"
        cid = bot.ttclient.tt.getMyChannelID()
        c_name = ""
        try:
            c_obj = bot.ttclient.get_channel(cid)
            c_name = c_obj.name if c_obj else ""
        except Exception:
            pass

        if c_name:
            self.SetTitle(f"{self.server_name} - {c_name} - TeamTalk")

        self.refresh_users_in_channel()

    def on_bot_disconnected(self):
        """Handles bot disconnection."""
        if not self._is_attached:
            return
        self._is_attached = False
        self._last_channel_id = -1
        self._last_users = []
        self.lbl_status.SetLabel(translate("Status: Disconnected"))
        self.lbl_channel.SetLabel(translate("Channel: Disconnected"))
        self.chk_mic.SetValue(False)
        if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            try:
                if getattr(self.bot.ttclient, "is_mic_active", False):
                    self.bot.ttclient.restore_input_device()
            except Exception:
                pass
        self.list_users.Clear()
        self.lbl_users.SetLabel(f"{translate('&Users in channel')} (0):")
        # Clear private chat histories only when bot disconnects from server
        self.private_chat_histories.clear()
        if self.private_chat_window:
            try:
                self.private_chat_window.Destroy()
            except Exception:
                pass
            self.private_chat_window = None

    def receive_user_pm_command(self, user_id: int, user_nickname: str, text: str):
        """Called when a user sends a command to contact the bot operator (e.g. !pm, !msg, !chat)."""
        time_str = time.strftime("%H:%M:%S")
        formatted_line = f"{user_nickname}: {text} [{time_str}]"

        # Save to persistent session history
        if user_id not in self.private_chat_histories:
            self.private_chat_histories[user_id] = []
        self.private_chat_histories[user_id].append(formatted_line)

        # Open and bring to front the dedicated private chat window
        self.open_private_chat(user_id, user_nickname)
        if self.private_chat_window and self.private_chat_window.user_id == user_id:
            cnt = self.private_chat_window.list_history.GetCount()
            if cnt == 0 or self.private_chat_window.list_history.GetString(cnt - 1) != formatted_line:
                self.private_chat_window.add_message(user_nickname, text, time_str)

    def on_tt_event(self, event: Event):
        """Receives events from TeamTalkThread and posts to wx main GUI thread."""
        if self._closing:
            return
        wx.CallAfter(self._handle_event_in_gui, event)

    def _handle_event_in_gui(self, event: Event):
        if self._closing or not event:
            return

        ev_type = event.event_type

        if ev_type == EventType.USER_JOINED:
            self.refresh_users_in_channel()

        elif ev_type == EventType.USER_LEFT:
            self.refresh_users_in_channel()

        elif ev_type == EventType.USER_LOGGEDIN:
            pass

        elif ev_type == EventType.USER_LOGGEDOUT:
            self.refresh_users_in_channel()

        elif ev_type == EventType.USER_UPDATE:
            self.refresh_users_in_channel()

        elif ev_type == EventType.USER_TEXT_MESSAGE:
            msg = event.message
            u_name = _safe_str(msg.user.nickname) or _safe_str(msg.user.username) or f"User #{msg.user.id}"
            msg_text = _safe_str(msg.text)
            my_uid = self.bot.ttclient.tt.getMyUserID() if self.bot else 0

            # Format: <Nickname>: <Content> [Time]
            time_str = time.strftime("%H:%M:%S")

            # Check if this message is a command to send private message to bot operator (strictly 'pm <content>')
            text_stripped = msg_text.strip()
            is_pm_cmd = text_stripped.lower().startswith("pm ")
            session_enabled = getattr(self.bot.config.general, "session_chat_enabled", False) if (self.bot and hasattr(self.bot, "config")) else True

            if session_enabled and is_pm_cmd and my_uid > 0 and msg.user.id != my_uid:
                pm_content = text_stripped[3:].strip()
                if pm_content:
                    self.receive_user_pm_command(msg.user.id, u_name, pm_content)

            # Route to private chat dialog if direct TeamTalk private message
            elif msg.type == MessageType.User:
                sender_uid = msg.user.id
                target_uid = getattr(msg, "nToUserID", None) or getattr(msg, "to_user_id", None)
                partner_uid = sender_uid if (my_uid > 0 and sender_uid != my_uid) else target_uid
                
                # Append to persistent private history for this conversation partner
                if partner_uid:
                    if partner_uid not in self.private_chat_histories:
                        self.private_chat_histories[partner_uid] = []
                    formatted_line = f"{u_name}: {msg_text} [{time_str}]"
                    self.private_chat_histories[partner_uid].append(formatted_line)

                # If this message was sent from someone else to us, automatically open the private chat window
                if my_uid > 0 and sender_uid != my_uid:
                    self.open_private_chat(sender_uid, u_name)
                    # If window was just opened or already active, add the message to the active list
                    if self.private_chat_window and self.private_chat_window.user_id == sender_uid:
                        # Only append to list if not already restored by switch_user
                        cnt = self.private_chat_window.list_history.GetCount()
                        if cnt == 0 or self.private_chat_window.list_history.GetString(cnt - 1) != formatted_line:
                            self.private_chat_window.add_message(u_name, msg_text, time_str)
                elif self.private_chat_window:
                    # Message sent by us via another channel or bot reflection
                    if partner_uid == self.private_chat_window.user_id:
                        self.private_chat_window.add_message(u_name, msg_text, time_str)

            # Skip echoing public messages sent by ourselves if already added
            if my_uid > 0 and msg.user.id == my_uid:
                return

            # Chỉ lưu trữ và hiển thị tin nhắn công khai trong kênh (MessageType.Channel)
            if msg.type == MessageType.Channel:
                self.add_public_message(u_name, msg_text, time_str)

        elif ev_type == EventType.CHANNEL_NEW:
            pass

        elif ev_type == EventType.CHANNEL_UPDATE:
            c_name = _safe_str(event.channel.name) or f"#{event.channel.id}"
            my_cid = self.bot.ttclient.tt.getMyChannelID() if self.bot else 0
            if event.channel.id == my_cid:
                self.lbl_channel.SetLabel(f"{translate('Channel:')} {c_name}")
                self.SetTitle(f"{self.server_name} - {c_name} - TeamTalk")
                self.refresh_users_in_channel()

        elif ev_type == EventType.CHANNEL_REMOVE:
            pass

        elif ev_type == EventType.SERVER_UPDATE:
            pass

        elif ev_type == EventType.BANNED_USER:
            pass

        elif ev_type == EventType.MYSELF_KICKED:
            self.refresh_users_in_channel()

        elif ev_type in (EventType.CON_LOST, EventType.CON_FAILED):
            self.on_bot_disconnected()

    def _format_user_display(self, u) -> str:
        """Formats a user for the channel users list: Nickname + Gender + Status."""
        nick = _safe_str(getattr(u, "szNickname", ""))
        if not nick:
            nick = _safe_str(getattr(u, "szUsername", ""))
        if not nick:
            nick = f"User #{getattr(u, 'nUserID', 0)}"

        # Parse gender and status flags from nStatusMode
        status_mode = getattr(u, "nStatusMode", 0)
        STATUSMODE_AWAY = 0x01
        STATUSMODE_QUESTION = 0x02
        STATUSMODE_FEMALE = 0x100
        STATUSMODE_GENDER_NONE = 0x1000

        if status_mode & STATUSMODE_FEMALE:
            gender_str = translate("Female")
        elif status_mode & STATUSMODE_GENDER_NONE:
            gender_str = translate("None")
        else:
            gender_str = translate("Male")

        details = [gender_str]

        if status_mode & STATUSMODE_AWAY:
            details.append(translate("Away"))
        if status_mode & STATUSMODE_QUESTION:
            details.append(translate("Question"))

        status_msg = _safe_str(getattr(u, "szStatusMsg", ""))
        if status_msg:
            details.append(status_msg)

        details_str = ", ".join(details)
        return f"{nick} ({details_str})"

    def refresh_users_in_channel(self):
        """Updates the channel users listbox with nickname, gender, and status."""
        if not self.bot or not hasattr(self.bot, "ttclient") or not self.bot.ttclient:
            self.list_users.Clear()
            self.lbl_users.SetLabel(f"{translate('&Users in channel')} (0):")
            self._last_users = []
            return

        try:
            cid = self.bot.ttclient.tt.getMyChannelID()
            if cid <= 0:
                self.list_users.Clear()
                self.lbl_users.SetLabel(f"{translate('&Users in channel')} (0):")
                self._last_users = []
                return

            raw_users = self.bot.ttclient.tt.getChannelUsers(cid)
            users_data: List[tuple] = []
            for u in raw_users:
                u_id = getattr(u, "nUserID", 0)
                disp_str = self._format_user_display(u)
                nick = _safe_str(getattr(u, "szNickname", "")) or _safe_str(getattr(u, "szUsername", "")) or f"User #{u_id}"
                users_data.append((u_id, disp_str, nick))

            users_data.sort(key=lambda item: item[2].lower())

            # Only refresh if the user list has actually changed
            if users_data != self._last_users:
                curr_sel = self.list_users.GetSelection()
                sel_uid = None
                if curr_sel != wx.NOT_FOUND and curr_sel < len(self._last_users):
                    sel_uid = self._last_users[curr_sel][0]

                self._last_users = list(users_data)
                self.list_users.Clear()
                sel_idx = wx.NOT_FOUND

                for i, (u_id, disp_str, nick) in enumerate(users_data):
                    self.list_users.Append(disp_str)
                    if sel_uid is not None and u_id == sel_uid:
                        sel_idx = i

                if sel_idx != wx.NOT_FOUND:
                    self.list_users.SetSelection(sel_idx)

                self.lbl_users.SetLabel(f"{translate('&Users in channel')} ({len(users_data)}):")
        except Exception as e:
            logging.debug(f"Error refreshing channel users: {e}")

    def on_text_changed(self, event=None):
        """Shows the Send button only when there is non-empty text in the message box."""
        has_text = bool(self.txt_message.GetValue().strip())
        if self.btn_send.IsShown() != has_text:
            self.btn_send.Show(has_text)
            if hasattr(self, "msg_box"):
                self.msg_box.Layout()
            if hasattr(self, "panel"):
                self.panel.Layout()
        if event:
            event.Skip()

    def on_send_message(self, event=None):
        """Sends the public message to the current channel and logs it in the history."""
        msg = self.txt_message.GetValue().strip()
        if not msg:
            return

        if (
            not self.bot
            or not hasattr(self.bot, "ttclient")
            or not self.bot.ttclient
            or self.bot.ttclient.state != State.CONNECTED
        ):
            wx.MessageBox(
                translate("Cannot send message: Not connected to server."),
                translate("Warning"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return

        try:
            # Type 2 = Channel / Public message
            self.bot.ttclient.send_message(msg, type=2)
            my_nick = _safe_str(self.bot.ttclient.nickname) or "Bot"
            now_str = time.strftime("%H:%M:%S")
            self.add_public_message(my_nick, msg, now_str)

            self.txt_message.Clear()
            if self.btn_send.IsShown():
                self.btn_send.Show(False)
                if hasattr(self, "msg_box"):
                    self.msg_box.Layout()
                if hasattr(self, "panel"):
                    self.panel.Layout()
            self.txt_message.SetFocus()
        except Exception as e:
            logging.error(f"Error sending channel message: {e}")
            wx.MessageBox(
                f"{translate('Failed to send message:')}\n{e}",
                translate("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def on_mic_context_menu(self, event=None):
        """Displays a context menu allowing the user to select their microphone."""
        if not self.bot or not hasattr(self.bot, "ttclient") or not self.bot.ttclient:
            return
        devices = self.bot.ttclient.get_input_devices()
        if not devices:
            return

        current_id = self.selected_mic_device_id
        if current_id is None:
            current_id = self.bot.ttclient.get_microphone_device_id()

        menu = wx.Menu()
        for dev in devices:
            if "loopback" in dev.name.lower():
                continue
            item = menu.AppendRadioItem(wx.ID_ANY, dev.name)
            if dev.id == current_id:
                item.Check(True)
            self.Bind(
                wx.EVT_MENU,
                lambda evt, d_id=dev.id: self._select_mic_device(d_id),
                item,
            )

        self.PopupMenu(menu)
        menu.Destroy()

    def save_server_settings(self):
        """Persists current server_data to servers.json."""
        try:
            if hasattr(self.parent, "server_mgr") and self.parent.server_mgr:
                mgr = self.parent.server_mgr
                for s in mgr.servers:
                    if (self.s_id and s.get("id") == self.s_id) or (
                        s.get("hostname") == self.server_data.get("hostname")
                        and s.get("tcp_port") == self.server_data.get("tcp_port")
                    ):
                        s.update(self.server_data)
                        break
                if hasattr(self.parent, "servers"):
                    for s in self.parent.servers:
                        if (self.s_id and s.get("id") == self.s_id) or (
                            s.get("hostname") == self.server_data.get("hostname")
                            and s.get("tcp_port") == self.server_data.get("tcp_port")
                        ):
                            s.update(self.server_data)
                            break
                mgr.save_servers()
                logging.info(f"Persisted audio settings for server '{self.server_name}' to servers.json")
        except Exception as e:
            logging.error(f"Error saving server settings in session dialog: {e}")

    def _select_mic_device(self, dev_id: int):
        self.selected_mic_device_id = dev_id
        self.server_data["selected_mic_device_id"] = dev_id
        dev_name = ""
        if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            for d in self.bot.ttclient.get_input_devices():
                if d.id == dev_id:
                    dev_name = d.name
                    break
        if dev_name:
            self.server_data["selected_mic_device_name"] = dev_name
        self.save_server_settings()
        if self.chk_mic.GetValue():
            if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
                self.bot.ttclient.switch_to_microphone(dev_id)

    def on_toggle_mic(self, event=None):
        """Toggles voice transmission through the bot account using user microphone."""
        if not self.bot or not hasattr(self.bot, "ttclient") or not self.bot.ttclient:
            self.chk_mic.SetValue(False)
            return

        is_enabled = self.chk_mic.GetValue()
        try:
            if is_enabled:
                success = self.bot.ttclient.switch_to_microphone(self.selected_mic_device_id)
                if not success:
                    self.chk_mic.SetValue(False)
                    self.server_data["mic_enabled"] = False
                    self.save_server_settings()
                    wx.MessageBox(
                        translate("Failed to activate microphone. Please check your microphone connection."),
                        translate("Error"),
                        wx.OK | wx.ICON_ERROR,
                        self,
                    )
                else:
                    self.server_data["mic_enabled"] = True
                    self.server_data["selected_mic_device_id"] = self.selected_mic_device_id
                    self.save_server_settings()
            else:
                self.bot.ttclient.restore_input_device()
                self.server_data["mic_enabled"] = False
                self.save_server_settings()
        except Exception as e:
            logging.error(f"Error toggling voice transmission: {e}")
            self.chk_mic.SetValue(False)
            self.server_data["mic_enabled"] = False
            self.save_server_settings()

    def on_toggle_mute_out(self, event=None):
        """Toggles mute on TeamTalk sound output."""
        self._is_muted = self.chk_mute_out.GetValue()
        if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            try:
                import TeamTalkPy
                TeamTalkPy.TeamTalk5.dll.TT_SetSoundOutputMute(self.bot.ttclient.tt._tt, self._is_muted)
            except Exception as e:
                logging.error(f"Error setting sound output mute: {e}")

    def on_audio_devices_click(self, event=None):
        """Opens the sound input/output devices configuration dialog."""
        if not self.bot or not hasattr(self.bot, "ttclient") or not self.bot.ttclient:
            wx.MessageBox(
                translate("Not connected to server."),
                translate("Notice"),
                wx.OK | wx.ICON_INFORMATION,
                self,
            )
            return

        dlg = SessionAudioDevicesDialog(self, self.bot)
        dlg.ShowModal()
        dlg.Destroy()

    def on_disconnect_click(self, event=None):
        """Disconnects this server session and closes the dialog."""
        res = wx.MessageBox(
            translate("Are you sure you want to disconnect from this server?"),
            translate("Confirm Disconnect"),
            wx.YES_NO | wx.ICON_QUESTION,
            self,
        )
        if res == wx.YES:
            if hasattr(self.parent, "disconnect_single_server"):
                self.parent.disconnect_single_server(self.server_data)
            self.Close()

    def open_private_chat(self, user_id: int, user_nickname: str):
        """Opens or switches the single private chat window to chat with a user."""
        if self.private_chat_window:
            frame = self.private_chat_window
            if frame.user_id != user_id:
                frame.switch_user(user_id, user_nickname)
            if frame.IsIconized():
                frame.Iconize(False)
            frame.Show(True)
            frame.Raise()
            frame.focus_input()
            return

        frame = PrivateChatDialog(self, user_id, user_nickname, self.bot)
        self.private_chat_window = frame
        frame.Show(True)
        frame.Raise()
        frame.focus_input()

    def on_user_activated(self, event=None):
        """When user is double-clicked or Enter is pressed in user list, open a new private chat window."""
        sel = self.list_users.GetSelection()
        if sel == wx.NOT_FOUND and self.list_users.GetCount() > 0:
            sel = 0

        if sel != wx.NOT_FOUND and sel < len(self._last_users):
            u_id, disp_str, nick = self._last_users[sel]
            self.open_private_chat(u_id, nick)
        elif sel != wx.NOT_FOUND and sel < self.list_users.GetCount():
            # Fallback if _last_users is desynced
            text = self.list_users.GetString(sel)
            # Find in raw users if possible
            u_id = 0
            nick = text.split(" (")[0].strip()
            if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
                try:
                    cid = self.bot.ttclient.tt.getMyChannelID()
                    for u in self.bot.ttclient.tt.getChannelUsers(cid):
                        u_nick = _safe_str(getattr(u, "szNickname", "")) or _safe_str(getattr(u, "szUsername", ""))
                        if u_nick == nick or f"User #{getattr(u, 'nUserID', 0)}" == nick:
                            u_id = getattr(u, "nUserID", 0)
                            break
                except Exception:
                    pass
            if u_id > 0:
                self.open_private_chat(u_id, nick)

    def on_user_key_down(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.on_user_activated(event)
            return
        elif key == ord("C") and event.ControlDown():
            self._copy_selected_user()
            return
        event.Skip()

    def on_user_context_menu(self, event):
        menu = wx.Menu()
        sel = self.list_users.GetSelection()
        if sel != wx.NOT_FOUND and sel < len(self._last_users):
            u_id, disp_str, nick = self._last_users[sel]
            item_private = menu.Append(wx.ID_ANY, translate("&Private Message..."))
            self.Bind(wx.EVT_MENU, lambda e: self.open_private_chat(u_id, nick), item_private)

            item_mention = menu.Append(wx.ID_ANY, translate("&Mention '{name}'").format(name=nick))
            self.Bind(wx.EVT_MENU, lambda e: self._mention_user(nick), item_mention)

            item_copy = menu.Append(wx.ID_ANY, translate("&Copy User Info"))
            self.Bind(wx.EVT_MENU, lambda e: self._copy_selected_user(), item_copy)

        self.PopupMenu(menu)
        menu.Destroy()

    def _mention_user(self, nick: str):
        self.txt_message.SetValue(f"@{nick} ")
        self.txt_message.SetInsertionPointEnd()
        self.txt_message.SetFocus()

    def _copy_selected_user(self):
        sel = self.list_users.GetSelection()
        if sel != wx.NOT_FOUND and sel < len(self._last_users):
            u_id, disp_str, nick = self._last_users[sel]
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(disp_str))
                wx.TheClipboard.Close()

    def on_history_key_down(self, event):
        key = event.GetKeyCode()
        if key == ord("C") and event.ControlDown():
            self._copy_selected_history()
            return
        event.Skip()

    def on_history_context_menu(self, event):
        menu = wx.Menu()
        sel = self.list_history.GetSelection()
        if sel != wx.NOT_FOUND:
            item_copy = menu.Append(wx.ID_ANY, translate("&Copy Selected Line"))
            self.Bind(wx.EVT_MENU, lambda e: self._copy_selected_history(), item_copy)

        item_clear = menu.Append(wx.ID_ANY, translate("Clear &History"))
        self.Bind(wx.EVT_MENU, lambda e: self.list_history.Clear(), item_clear)

        self.PopupMenu(menu)
        menu.Destroy()

    def _copy_selected_history(self):
        sel = self.list_history.GetSelection()
        if sel != wx.NOT_FOUND:
            text = self.list_history.GetString(sel)
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(text))
                wx.TheClipboard.Close()

    def on_close(self, event=None):
        """Cleans up audio output and listeners upon closing the dialog."""
        self._closing = True
        self.timer.Stop()

        # Disable TeamTalk sound output device so background playback stops
        if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            try:
                self.bot.ttclient.remove_event_listener(self.on_tt_event)
            except Exception:
                pass
            try:
                if getattr(self.bot.ttclient, "is_mic_active", False):
                    self.bot.ttclient.restore_input_device()
            except Exception as e:
                logging.error(f"Error restoring input device on session close: {e}")
            try:
                self.bot.ttclient.disable_sound_output()
            except Exception as e:
                logging.error(f"Error disabling sound output on session close: {e}")

        # Close the single private chat window for this session if open
        if self.private_chat_window:
            try:
                self.private_chat_window.Destroy()
            except Exception:
                pass
            self.private_chat_window = None

        # Unregister from parent session windows
        if hasattr(self.parent, "session_windows") and self.s_id in self.parent.session_windows:
            del self.parent.session_windows[self.s_id]

        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()


class PrivateChatDialog(wx.Frame):
    """Accessible standalone window for private chat with a specific user.

    Accessible with screen readers (NVDA, JAWS):
    - Appears in Alt+Tab and taskbar as an independent window.
    - Default cursor focus is placed in txt_message.
    - Shift+Tab from txt_message goes to list_history.
    - Tab from txt_message goes to btn_send, then btn_close.
    - Enter in txt_message sends the message.
    - Pressing Escape closes the private chat window.
    """

    def __init__(self, parent: wx.Window, user_id: int, user_nickname: str, bot: Any):
        self.session_dialog = parent
        self.user_id = user_id
        self.user_nickname = user_nickname or f"User #{user_id}"
        self.bot = bot
        self.chat_histories: Dict[int, List[str]] = {}

        title = translate("Private Chat - {user}").format(user=self.user_nickname)
        super().__init__(
            parent,
            title=title,
            size=(580, 460),
            style=wx.DEFAULT_FRAME_STYLE,
        )
        self.SetMinSize((420, 320))

        self._init_ui()
        self.CentreOnParent()

        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

    def switch_user(self, user_id: int, user_nickname: str):
        """Switches the active private chat to another user without opening a new window."""
        # Save current history to persistent session dictionary
        current_lines = [self.list_history.GetString(i) for i in range(self.list_history.GetCount())]
        if hasattr(self.session_dialog, "private_chat_histories"):
            self.session_dialog.private_chat_histories[self.user_id] = current_lines

        self.user_id = user_id
        self.user_nickname = user_nickname or f"User #{user_id}"
        self.SetTitle(translate("Private Chat - {user}").format(user=self.user_nickname))

        # Restore history for the new user or start clean
        self.list_history.Clear()
        saved_lines = []
        if hasattr(self.session_dialog, "private_chat_histories"):
            saved_lines = self.session_dialog.private_chat_histories.get(user_id, [])
        for line in saved_lines:
            self.list_history.Append(line)

        cnt = self.list_history.GetCount()
        if cnt > 0:
            self.list_history.SetSelection(cnt - 1)

        self.txt_message.Clear()
        if self.btn_send.IsShown():
            self.btn_send.Show(False)
            self.msg_row.Layout()
            self.panel.Layout()
        self.focus_input()

    def _init_ui(self):
        panel = wx.Panel(self, style=wx.TAB_TRAVERSAL)
        self.panel = panel
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # 1. Message History (accessible ListBox, Shift+Tab from message input lands here)
        lbl_history = wx.StaticText(panel, label=translate("&Message History:"))
        self.list_history = wx.ListBox(panel, style=wx.LB_SINGLE | wx.LB_HSCROLL)
        self.list_history.SetName(translate("Message History"))
        self.list_history.Bind(wx.EVT_CONTEXT_MENU, self.on_history_context_menu)
        self.list_history.Bind(wx.EVT_KEY_DOWN, self.on_history_key_down)

        main_sizer.Add(lbl_history, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        main_sizer.Add(self.list_history, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # 2. Private Message Input (Initial focus)
        lbl_msg = wx.StaticText(panel, label=translate("&Message:"))
        self.txt_message = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.txt_message.SetName(translate("Message"))
        self.txt_message.SetHint(translate("Type private message and press Enter..."))
        self.txt_message.Bind(wx.EVT_TEXT, self.on_text_changed)
        self.txt_message.Bind(wx.EVT_TEXT_ENTER, self.on_send)

        # 3. Send and Close Buttons
        self.btn_send = wx.Button(panel, label=translate("&Send"))
        self.btn_send.SetName(translate("Send"))
        self.btn_send.SetToolTip(translate("Send private message (Enter)"))
        self.btn_send.Bind(wx.EVT_BUTTON, self.on_send)
        self.btn_send.Show(False)

        self.btn_close = wx.Button(panel, label=translate("&Close"))
        self.btn_close.SetName(translate("Close"))
        self.btn_close.SetToolTip(translate("Close this chat window (Escape)"))
        self.btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())

        btn_box = wx.BoxSizer(wx.HORIZONTAL)
        btn_box.Add(self.btn_send, 0, wx.RIGHT, 6)
        btn_box.Add(self.btn_close, 0)

        msg_row = wx.BoxSizer(wx.HORIZONTAL)
        self.msg_row = msg_row
        msg_row.Add(lbl_msg, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        msg_row.Add(self.txt_message, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        msg_row.Add(btn_box, 0, wx.ALIGN_CENTER_VERTICAL)

        main_sizer.Add(msg_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Restore previously stored private chat messages for this user if any
        if hasattr(self.session_dialog, "private_chat_histories"):
            for line in self.session_dialog.private_chat_histories.get(self.user_id, []):
                self.list_history.Append(line)
            cnt = self.list_history.GetCount()
            if cnt > 0:
                self.list_history.SetSelection(cnt - 1)

        panel.SetSizer(main_sizer)
        self.Layout()

        wx.CallAfter(self.focus_input)

    def focus_input(self):
        self.txt_message.SetFocus()

    def on_char_hook(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.Close()
            return
        event.Skip()

    def on_text_changed(self, event=None):
        has_text = bool(self.txt_message.GetValue().strip())
        if self.btn_send.IsShown() != has_text:
            self.btn_send.Show(has_text)
            self.msg_row.Layout()
            self.panel.Layout()
        if event:
            event.Skip()

    def on_send(self, event=None):
        msg = self.txt_message.GetValue().strip()
        if not msg:
            return

        ttclient = self.bot.ttclient if self.bot and hasattr(self.bot, "ttclient") else None
        if not ttclient or ttclient.state != State.CONNECTED:
            wx.MessageBox(
                translate("Cannot send message: Not connected to server."),
                translate("Warning"),
                wx.OK | wx.ICON_WARNING,
                self,
            )
            return

        try:
            # Type 1 = Private User message
            ttclient.send_message(msg, user=self.user_id, type=1)
            my_nick = _safe_str(ttclient.nickname) or "Bot"
            now_str = time.strftime("%H:%M:%S")
            self.add_message(my_nick, msg, now_str)

            self.txt_message.Clear()
            if self.btn_send.IsShown():
                self.btn_send.Show(False)
                self.msg_row.Layout()
                self.panel.Layout()
            self.focus_input()
        except Exception as e:
            logging.error(f"Error sending private message: {e}")
            wx.MessageBox(
                f"{translate('Failed to send message:')}\n{e}",
                translate("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def add_message(self, sender: str, text: str, timestamp: str):
        # Format: <Nickname>: <Content> [Time]
        entry = f"{sender}: {text} [{timestamp}]"
        self.list_history.Append(entry)
        focused = self.FindFocus()
        if focused != self.list_history:
            cnt = self.list_history.GetCount()
            if cnt > 0:
                self.list_history.SetSelection(cnt - 1)

    def on_history_key_down(self, event):
        key = event.GetKeyCode()
        if key == ord("C") and event.ControlDown():
            self._copy_selected_history()
            return
        event.Skip()

    def on_history_context_menu(self, event):
        menu = wx.Menu()
        sel = self.list_history.GetSelection()
        if sel != wx.NOT_FOUND:
            item_copy = menu.Append(wx.ID_ANY, translate("&Copy Selected Line"))
            self.Bind(wx.EVT_MENU, lambda e: self._copy_selected_history(), item_copy)

        item_clear = menu.Append(wx.ID_ANY, translate("Clear &History"))
        self.Bind(wx.EVT_MENU, lambda e: self.list_history.Clear(), item_clear)

        self.PopupMenu(menu)
        menu.Destroy()

    def _copy_selected_history(self):
        sel = self.list_history.GetSelection()
        if sel != wx.NOT_FOUND:
            text = self.list_history.GetString(sel)
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(text))
                wx.TheClipboard.Close()

    def on_close(self, event):
        # Save current history to persistent session dictionary before closing
        current_lines = [self.list_history.GetString(i) for i in range(self.list_history.GetCount())]
        if hasattr(self.session_dialog, "private_chat_histories"):
            self.session_dialog.private_chat_histories[self.user_id] = current_lines

        if hasattr(self.session_dialog, "private_chat_window"):
            self.session_dialog.private_chat_window = None
        self.Destroy()


# Backward compatibility alias
ServerSessionFrame = ServerSessionDialog


class SessionAudioDevicesDialog(wx.Dialog):
    """Accessible dialog to configure TeamTalk Sound System, Input, and Output devices."""

    def __init__(self, parent: wx.Window, bot: Any):
        super().__init__(
            parent,
            title=translate("Audio Devices (Input & Output) - TeamTalk"),
            size=(540, 390),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.parent = parent
        self.bot = bot
        self.ttclient = bot.ttclient if bot and hasattr(bot, "ttclient") else None

        # Sound systems available on TeamTalk
        self.sound_systems = []
        if sys.platform == "win32":
            try:
                import TeamTalkPy
                self.sound_systems = [
                    (TeamTalkPy.SoundSystem.SOUNDSYSTEM_WASAPI, translate("Windows Audio Session (WASAPI) [Recommended]")),
                    (TeamTalkPy.SoundSystem.SOUNDSYSTEM_DSOUND, translate("DirectSound")),
                    (TeamTalkPy.SoundSystem.SOUNDSYSTEM_WINMM, translate("Windows Multimedia (WaveIn/WaveOut)")),
                ]
            except Exception:
                self.sound_systems = [(5, translate("Windows Audio Session (WASAPI) [Recommended]"))]
        else:
            self.sound_systems = [
                (0, translate("Default Sound System")),
            ]

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # 1. Sound system
        lbl_sys = wx.StaticText(panel, label=translate("Sound &System:"))
        self.choice_system = wx.Choice(
            panel,
            choices=[name for _, name in self.sound_systems],
        )
        self.choice_system.SetName(translate("Sound System"))

        # Select saved sound system if previously configured
        server_data = getattr(parent, "server_data", {})
        saved_sys = server_data.get("session_sound_system")
        sys_sel = 0
        if saved_sys is not None:
            for idx, (sys_code, _) in enumerate(self.sound_systems):
                if sys_code == saved_sys:
                    sys_sel = idx
                    break
        self.choice_system.SetSelection(sys_sel)
        self.choice_system.Bind(wx.EVT_CHOICE, self.on_system_changed)

        sys_box = wx.BoxSizer(wx.VERTICAL)
        sys_box.Add(lbl_sys, 0, wx.BOTTOM, 4)
        sys_box.Add(self.choice_system, 0, wx.EXPAND)
        main_sizer.Add(sys_box, 0, wx.EXPAND | wx.ALL, 10)

        # 2. Input Device
        lbl_in = wx.StaticText(panel, label=translate("&Input Device (Microphone / Computer Audio / VAC):"))
        self.choice_input = wx.Choice(panel)
        self.choice_input.SetName(translate("Input Device"))
        in_box = wx.BoxSizer(wx.VERTICAL)
        in_box.Add(lbl_in, 0, wx.BOTTOM, 4)
        in_box.Add(self.choice_input, 0, wx.EXPAND)
        main_sizer.Add(in_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # 3. Output Device
        lbl_out = wx.StaticText(panel, label=translate("&Output Device (Headphones / Speakers):"))
        self.choice_output = wx.Choice(panel)
        self.choice_output.SetName(translate("Output Device"))
        out_box = wx.BoxSizer(wx.VERTICAL)
        out_box.Add(lbl_out, 0, wx.BOTTOM, 4)
        out_box.Add(self.choice_output, 0, wx.EXPAND)
        main_sizer.Add(out_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # 4. Gain level & Voice Transmission
        options_box = wx.BoxSizer(wx.HORIZONTAL)

        lbl_gain = wx.StaticText(panel, label=translate("Input &Gain (%):"))
        self.spin_gain = wx.SpinCtrl(panel, min=0, max=400, initial=100)
        self.spin_gain.SetName(translate("Input Gain Percent"))
        options_box.Add(lbl_gain, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        options_box.Add(self.spin_gain, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 20)

        self.chk_transmit = wx.CheckBox(panel, label=translate("Enable &Voice Transmission"))
        self.chk_transmit.SetName(translate("Enable Voice Transmission"))
        saved_mic = server_data.get("mic_enabled")
        if saved_mic is not None:
            self.chk_transmit.SetValue(bool(saved_mic))
        elif self.ttclient:
            self.chk_transmit.SetValue(bool(getattr(self.ttclient, "is_voice_transmission_enabled", False)))
        options_box.Add(self.chk_transmit, 0, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(options_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # 5. Buttons (OK, Cancel)
        btn_sizer = wx.StdDialogButtonSizer()
        self.btn_ok = wx.Button(panel, wx.ID_OK, label=translate("&OK"))
        self.btn_ok.SetDefault()
        self.btn_ok.Bind(wx.EVT_BUTTON, self.on_ok)
        self.btn_cancel = wx.Button(panel, wx.ID_CANCEL, label=translate("&Cancel"))
        btn_sizer.AddButton(self.btn_ok)
        btn_sizer.AddButton(self.btn_cancel)
        btn_sizer.Realize()
        main_sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(main_sizer)

        dlg_sizer = wx.BoxSizer(wx.VERTICAL)
        dlg_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(dlg_sizer)

        # Populate devices
        self._input_devices = []
        self._output_devices = []
        self.populate_devices()

        # Load current gain
        saved_gain = server_data.get("session_gain")
        if saved_gain is not None:
            self.spin_gain.SetValue(int(saved_gain))
        elif self.ttclient:
            try:
                import TeamTalkPy
                cur_gain = TeamTalkPy.TeamTalk5.dll.TT_GetSoundInputGainLevel(self.ttclient.tt._tt)
                gain_pct = max(0, min(400, int(cur_gain / 10)))
                self.spin_gain.SetValue(gain_pct)
            except Exception:
                pass

        self.CentreOnParent()

    def get_selected_sound_system(self):
        sel = self.choice_system.GetSelection()
        if 0 <= sel < len(self.sound_systems):
            return self.sound_systems[sel][0]
        return 5

    def populate_devices(self):
        if not self.ttclient:
            return

        sys_id = self.get_selected_sound_system()
        self._input_devices = self.ttclient.get_input_devices(sound_system=sys_id)
        self._output_devices = self.ttclient.get_output_devices(sound_system=sys_id)

        server_data = getattr(self.parent, "server_data", {})
        saved_in_id = server_data.get("session_input_device_id")
        saved_in_name = server_data.get("session_input_device_name")
        saved_out_id = server_data.get("session_output_device_id")
        saved_out_name = server_data.get("session_output_device_name")

        # Populate Input choices
        self.choice_input.Clear()
        cur_in_id = getattr(self.ttclient, "current_input_device_id", None)
        selected_in_idx = 0

        for idx, dev in enumerate(self._input_devices):
            label = dev.name
            lower = label.lower()
            if "loopback" in lower:
                label = f"{label} ({translate('Computer Audio / Stereo Mix')})"
            elif "line 1" in lower or "virtual audio cable" in lower:
                label = f"{label} ({translate('Bot Music Player')})"
            elif "mic" in lower or "micro" in lower:
                label = f"{label} ({translate('Microphone')})"

            label = f"{label} [ID: {dev.id}]"

            self.choice_input.Append(label)
            if saved_in_id is not None and dev.id == saved_in_id:
                selected_in_idx = idx
            elif selected_in_idx == 0 and saved_in_name and dev.name == saved_in_name:
                selected_in_idx = idx
            elif selected_in_idx == 0 and cur_in_id is not None and dev.id == cur_in_id:
                selected_in_idx = idx

        if self._input_devices:
            self.choice_input.SetSelection(selected_in_idx)

        # Populate Output choices
        self.choice_output.Clear()
        cur_out_id = getattr(self.ttclient, "current_output_device_id", None)
        selected_out_idx = 0

        for idx, dev in enumerate(self._output_devices):
            label = dev.name
            lower = label.lower()
            if "headphone" in lower:
                label = f"{label} ({translate('Headphones')})"
            elif "speaker" in lower:
                label = f"{label} ({translate('Speakers')})"

            label = f"{label} [ID: {dev.id}]"

            self.choice_output.Append(label)
            if saved_out_id is not None and dev.id == saved_out_id:
                selected_out_idx = idx
            elif selected_out_idx == 0 and saved_out_name and dev.name == saved_out_name:
                selected_out_idx = idx
            elif selected_out_idx == 0 and cur_out_id is not None and dev.id == cur_out_id:
                selected_out_idx = idx

        if self._output_devices:
            self.choice_output.SetSelection(selected_out_idx)

    def on_system_changed(self, event):
        self.populate_devices()

    def on_ok(self, event):
        if not self.ttclient:
            self.EndModal(wx.ID_OK)
            return

        in_sel = self.choice_input.GetSelection()
        out_sel = self.choice_output.GetSelection()

        in_dev = self._input_devices[in_sel] if 0 <= in_sel < len(self._input_devices) else None
        out_dev = self._output_devices[out_sel] if 0 <= out_sel < len(self._output_devices) else None
        sys_id = self.get_selected_sound_system()

        # 1. Update Output Device
        if out_dev:
            self.ttclient.set_output_device(out_dev.id)
            if hasattr(self.parent, "server_data"):
                self.parent.server_data["session_output_device_id"] = out_dev.id
                self.parent.server_data["session_output_device_name"] = out_dev.name

        # 2. Update Input Device
        if in_dev:
            self.ttclient.set_input_device_live(in_dev.id)
            if hasattr(self.parent, "selected_mic_device_id"):
                self.parent.selected_mic_device_id = in_dev.id
            if hasattr(self.parent, "server_data"):
                self.parent.server_data["session_input_device_id"] = in_dev.id
                self.parent.server_data["session_input_device_name"] = in_dev.name
                self.parent.server_data["selected_mic_device_id"] = in_dev.id
                self.parent.server_data["selected_mic_device_name"] = in_dev.name

        # 3. Update Gain Level
        gain_pct = self.spin_gain.GetValue()
        gain_val = gain_pct * 10
        try:
            import TeamTalkPy
            TeamTalkPy.TeamTalk5.dll.TT_SetSoundInputGainLevel(self.ttclient.tt._tt, gain_val)
        except Exception as e:
            logging.error(f"Error setting gain level: {e}")
        if hasattr(self.parent, "server_data"):
            self.parent.server_data["session_gain"] = gain_pct

        # 4. Update Voice Transmission
        enable_tx = self.chk_transmit.GetValue()
        if enable_tx:
            self.ttclient.enable_voice_transmission()
            if hasattr(self.parent, "chk_mic"):
                self.parent.chk_mic.SetValue(True)
        else:
            self.ttclient.disable_voice_transmission()
            if hasattr(self.parent, "chk_mic"):
                self.parent.chk_mic.SetValue(False)

        if hasattr(self.parent, "server_data"):
            self.parent.server_data["mic_enabled"] = enable_tx
            self.parent.server_data["session_sound_system"] = sys_id

        # Persist all audio settings to servers.json
        if hasattr(self.parent, "save_server_settings"):
            self.parent.save_server_settings()

        self.EndModal(wx.ID_OK)
