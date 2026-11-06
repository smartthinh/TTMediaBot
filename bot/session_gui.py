import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import wx
import wx.adv

from bot.TeamTalk.structs import Event, EventType, MessageType, State
from bot.gui import translate
from bot import sound_events
from bot import accessibility


def _safe_str(val: Any) -> str:
    """Helper to convert TeamTalk string/bytes safely into a unicode string."""
    if val is None:
        return ""
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace").strip()
    return str(val).strip()


def _t(text: str) -> str:
    """Translation helper with built-in fallbacks for tree view and channel dialogs."""
    res = translate(text)
    if res == text:
        try:
            from bot.gui import get_gui_translator
            locale = get_gui_translator().get_locale()
        except Exception:
            locale = "en"
        if locale == "vi":
            vi_dict = {
                "Channels and Users": "Kênh và người dùng",
                "&Channels and Users:": "&Kênh và người dùng:",
                "Channels and Users (%d)": "Kênh và người dùng (%d)",
                "Password protected": "Có mật khẩu",
                "Enter Password": "Nhập mật khẩu",
                "Channel Password": "Mật khẩu kênh",
                "Please enter password for channel '{channel}':": "Vui lòng nhập mật khẩu cho kênh '{channel}':",
                "&Password:": "&Mật khẩu:",
                "&Show password": "&Hiển thị mật khẩu",
                "&Join Channel": "&Tham gia kênh",
                "&Copy Channel Name": "&Sao chép tên kênh",
                "Failed to join channel": "Không thể tham gia kênh",
                "Current": "Hiện tại",
                "{user} has logged in": "{user} đã đăng nhập",
                "{user} has logged out": "{user} đã đăng xuất",
                "{user} joined channel": "{user} đã tham gia kênh",
                "{user} left channel": "{user} đã rời khỏi kênh",
                "Private message from {user}: {message}": "Tin nhắn riêng từ {user}: {message}",
                "Private message sent: {message}": "Đã gửi tin nhắn riêng: {message}",
                "Channel message from {user}: {message}": "{user}: {message}",
                "Channel message sent: {message}": "{message}",
                "Broadcast message from {user}: {message}": "Tin nhắn từ {user}: {message}",
                "Microphone enabled": "Đã bật micro",
                "Microphone disabled": "Đã tắt micro",
                "Sound output muted": "Đã tắt tiếng",
                "Sound output unmuted": "Đã bật tiếng",
                "Joined channel {channel}": "Đã tham gia kênh {channel}",
                "Connection lost to server": "Mất kết nối tới máy chủ",
                "&OK": "&Đồng ý",
                "&Cancel": "&Hủy",
                "&Refresh Devices": "&Làm mới thiết bị",
                "Refresh Devices": "Làm mới thiết bị",
                "Sound settings saved successfully": "Đã lưu thiết lập âm thanh thành công",
                "Audio devices list refreshed": "Đã làm mới danh sách thiết bị âm thanh",
                "New audio device detected. Device list refreshed.": "Đã phát hiện thiết bị âm thanh mới. Danh sách thiết bị đã được tự động làm mới.",
                "Audio device disconnected. Device list updated.": "Thiết bị âm thanh đã ngắt kết nối. Danh sách thiết bị đã được cập nhật.",
            }
            return vi_dict.get(text, res)
    return res


class ChannelPasswordDialog(wx.Dialog):
    """Accessible dialog prompting for channel password with hidden input and show password checkbox."""

    def __init__(self, parent: wx.Window, channel_name: str, channel_id: int, initial_password: str = ""):
        title = _t("Channel Password")
        super().__init__(
            parent,
            title=title,
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.channel_name = channel_name
        self.channel_id = channel_id

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        prompt_text = _t("Please enter password for channel '{channel}':").format(channel=channel_name)
        self.lbl_prompt = wx.StaticText(panel, label=prompt_text)
        main_sizer.Add(self.lbl_prompt, 0, wx.ALL, 10)

        pass_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_pass = wx.StaticText(panel, label=_t("&Password:"))
        self.txt_pass = wx.TextCtrl(panel, value=initial_password, style=wx.TE_PASSWORD | wx.TE_PROCESS_ENTER)
        self.txt_pass.Bind(wx.EVT_TEXT_ENTER, self.on_ok)
        pass_sizer.Add(self.lbl_pass, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        pass_sizer.Add(self.txt_pass, 1, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(pass_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.chk_show = wx.CheckBox(panel, label=_t("&Show password"))
        self.chk_show.SetValue(False)
        self.chk_show.Bind(wx.EVT_CHECKBOX, self.on_toggle_show)
        main_sizer.Add(self.chk_show, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        btn_sizer = wx.StdDialogButtonSizer()
        self.btn_ok = wx.Button(panel, wx.ID_OK, label=translate("&OK"))
        self.btn_cancel = wx.Button(panel, wx.ID_CANCEL, label=translate("&Cancel"))
        self.btn_ok.SetDefault()
        btn_sizer.AddButton(self.btn_ok)
        btn_sizer.AddButton(self.btn_cancel)
        btn_sizer.Realize()

        self.btn_ok.Bind(wx.EVT_BUTTON, self.on_ok)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        main_sizer.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)

        panel.SetSizer(main_sizer)
        dlg_sizer = wx.BoxSizer(wx.VERTICAL)
        dlg_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizerAndFit(dlg_sizer)
        self.SetMinSize((380, 180))
        self.CentreOnParent()

        wx.CallAfter(self.txt_pass.SetFocus)

    def on_toggle_show(self, event=None):
        show = self.chk_show.GetValue()
        hwnd = self.txt_pass.GetHandle()
        try:
            import ctypes
            char_code = 0 if show else ord('*')
            ctypes.windll.user32.SendMessageW(hwnd, 0x00CC, char_code, 0)
            ctypes.windll.user32.InvalidateRect(hwnd, None, True)
            self.txt_pass.Refresh()
        except Exception:
            pass

    def on_ok(self, event=None):
        self.EndModal(wx.ID_OK)

    def on_cancel(self, event=None):
        self.EndModal(wx.ID_CANCEL)

    def get_password(self) -> str:
        return self.txt_pass.GetValue()


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
        self._last_channel_uids: Optional[set] = None
        self._last_users: List[tuple] = []
        self._channel_items: Dict[int, wx.TreeItemId] = {}
        self._user_items: Dict[int, wx.TreeItemId] = {}
        self._channel_passwords: Dict[int, str] = {}
        self._pending_expand_cids: set = set()
        self._last_tree_snapshot = None
        self._known_users: Dict[int, str] = {}
        self._is_muted = False
        self._closing = False
        self.private_chat_window: Optional[Any] = None
        self.private_chat_histories: Dict[int, List[str]] = {}
        self.audio_devices_dialog: Optional[Any] = None

        self._init_ui()
        self.CentreOnParent()

        self._init_device_change_notification()

        # Timer for polling connection state and channel synchronization
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        self.timer.Start(250)

        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        # Trigger initial check immediately
        self._check_bot_connection()

    def is_sound_events_enabled(self) -> bool:
        if hasattr(self, "chk_sound_events") and bool(self.chk_sound_events):
            return self.chk_sound_events.GetValue()
        return bool(self.server_data.get("sound_events_enabled", True))

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

        # 4. Channels and Users Tree View (Tab from Message input lands here)
        self.lbl_tree = wx.StaticText(panel, label=_t("&Channels and Users:"))
        self.tree_session = wx.TreeCtrl(
            panel,
            style=wx.TR_DEFAULT_STYLE | wx.TR_HAS_BUTTONS | wx.TR_LINES_AT_ROOT | wx.TR_SINGLE,
        )
        self.tree_session.SetName(_t("Channels and Users"))
        self.tree_session.Bind(wx.EVT_TREE_ITEM_ACTIVATED, self.on_tree_item_activated)
        self.tree_session.Bind(wx.EVT_KEY_DOWN, self.on_tree_key_down)
        self.tree_session.Bind(wx.EVT_TREE_ITEM_MENU, self.on_tree_context_menu)
        self.tree_session.Bind(wx.EVT_CONTEXT_MENU, self.on_tree_context_menu)

        # Aliases for backwards compatibility
        self.lbl_users = self.lbl_tree
        self.list_users = self.tree_session

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

        self.chk_sound_events = wx.CheckBox(panel, label=translate("Sound &Events"))
        self.chk_sound_events.SetValue(self.server_data.get("sound_events_enabled", True))
        self.chk_sound_events.SetName(translate("Sound Events"))
        self.chk_sound_events.SetToolTip(translate("Play TeamTalk sound notifications for messages, users joining/leaving, etc."))
        self.chk_sound_events.Bind(wx.EVT_CHECKBOX, self.on_toggle_sound_events)

        # 6. Action Buttons (Tab from Checkboxes lands here)
        self.btn_audio_devices = wx.Button(panel, label=translate("&Sound Configuration..."))
        self.btn_audio_devices.SetName(translate("Sound Configuration"))
        self.btn_audio_devices.SetToolTip(translate("Configure TeamTalk sound system, audio devices, and volume levels"))
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
        # Content row: History (Left) and Channels/Users Tree (Right)
        content_sizer = wx.BoxSizer(wx.HORIZONTAL)

        history_col = wx.BoxSizer(wx.VERTICAL)
        history_col.Add(self.lbl_history, 0, wx.BOTTOM, 4)
        history_col.Add(self.list_history, 1, wx.EXPAND)
        content_sizer.Add(history_col, 3, wx.EXPAND | wx.RIGHT, 8)

        tree_col = wx.BoxSizer(wx.VERTICAL)
        tree_col.Add(self.lbl_tree, 0, wx.BOTTOM, 4)
        tree_col.Add(self.tree_session, 1, wx.EXPAND)
        content_sizer.Add(tree_col, 2, wx.EXPAND)
        main_sizer.Add(content_sizer, 1, wx.EXPAND | wx.ALL, 10)

        # Controls row: Checkboxes and Action buttons
        ctrls_row = wx.BoxSizer(wx.HORIZONTAL)
        ctrls_row.Add(self.chk_mic, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        ctrls_row.Add(self.chk_mute_out, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        ctrls_row.Add(self.chk_sound_events, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
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
            # If user presses Enter while focusing the tree view, activate channel/user immediately
            focused = self.FindFocus()
            if focused == self.tree_session or focused == self.list_users:
                self.on_tree_item_activated()
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
        if self._is_attached and self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            self.refresh_channels_and_users()

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
                        self._last_channel_uids = None
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

        # 3. Restore master volume & input gain level (default 50% -> 1000)
        try:
            import TeamTalkPy
            vol_pct = int(self.server_data.get("master_volume", 50))
            vol_val = vol_pct * 20
            TeamTalkPy.TeamTalk5.dll.TT_SetSoundOutputVolume(bot.ttclient.tt._tt, vol_val)

            gain_pct = int(self.server_data.get("mic_gain", 50))
            gain_val = gain_pct * 20
            TeamTalkPy.TeamTalk5.dll.TT_SetSoundInputGainLevel(bot.ttclient.tt._tt, gain_val)
        except Exception as e:
            logging.error(f"Error restoring master volume or gain: {e}")

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
        self._last_channel_uids = None
        self._last_users = []
        sound_events.play_sound(sound_events.SOUND_SERVER_LOST, enabled=self.is_sound_events_enabled())
        accessibility.announce(_t("Connection lost to server"))
        self.lbl_status.SetLabel(translate("Status: Disconnected"))
        self.lbl_channel.SetLabel(translate("Channel: Disconnected"))
        self.chk_mic.SetValue(False)
        if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            try:
                if getattr(self.bot.ttclient, "is_mic_active", False):
                    self.bot.ttclient.restore_input_device()
            except Exception:
                pass
        if hasattr(self, "tree_session"):
            self.tree_session.DeleteAllItems()
        self._channel_items.clear()
        self._user_items.clear()
        self._last_tree_snapshot = None
        self.lbl_tree.SetLabel(f"{_t('&Channels and Users:')}")
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
        sound_events.play_sound(sound_events.SOUND_USER_MSG, enabled=self.is_sound_events_enabled())
        accessibility.announce(_t("Private message from {user}: {message}").format(user=user_nickname, message=text))
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
            if event.user:
                uid = getattr(event.user, "id", 0)
                u_nick = _safe_str(getattr(event.user, "nickname", "")) or _safe_str(getattr(event.user, "username", ""))
                if uid and u_nick:
                    self._known_users[uid] = u_nick
            # Check if myself joined a channel
            my_uid = self.bot.ttclient.tt.getMyUserID() if self.bot else 0
            if event.user and getattr(event.user, "id", 0) == my_uid:
                chan_name = ""
                try:
                    c = self.bot.ttclient.tt.getChannel(getattr(event.user, "channel_id", 0))
                    if c:
                        chan_name = _safe_str(getattr(c, "szName", ""))
                except Exception:
                    pass
                if chan_name:
                    accessibility.announce(_t("Joined channel {channel}").format(channel=chan_name))
            self.refresh_users_in_channel()

        elif ev_type == EventType.USER_LEFT:
            self.refresh_users_in_channel()

        elif ev_type == EventType.USER_LOGGEDIN:
            if event.user:
                my_uid = self.bot.ttclient.tt.getMyUserID() if self.bot else 0
                uid = getattr(event.user, "id", 0)
                u_name = _safe_str(getattr(event.user, "nickname", "")) or _safe_str(getattr(event.user, "username", ""))
                if uid and u_name:
                    self._known_users[uid] = u_name
                if uid != my_uid:
                    display_name = u_name or self._known_users.get(uid) or f"User #{uid}"
                    sound_events.play_sound(sound_events.SOUND_LOGGED_ON, enabled=self.is_sound_events_enabled())
                    accessibility.announce(_t("{user} has logged in").format(user=display_name))
            self.refresh_channels_and_users()

        elif ev_type == EventType.USER_LOGGEDOUT:
            if event.user:
                my_uid = self.bot.ttclient.tt.getMyUserID() if self.bot else 0
                uid = getattr(event.user, "id", 0)
                u_name = _safe_str(getattr(event.user, "nickname", "")) or _safe_str(getattr(event.user, "username", ""))
                if not u_name and uid in self._known_users:
                    u_name = self._known_users[uid]
                if not u_name and self.bot and hasattr(self.bot, "ttclient") and hasattr(self.bot.ttclient, "user_cache"):
                    cached = self.bot.ttclient.user_cache.get(uid)
                    if cached:
                        u_name = _safe_str(getattr(cached, "nickname", "")) or _safe_str(getattr(cached, "username", ""))
                if not u_name:
                    u_name = f"User #{uid}" if uid else _t("Unknown User")
                if uid != my_uid:
                    sound_events.play_sound(sound_events.SOUND_LOGGED_OFF, enabled=self.is_sound_events_enabled())
                    accessibility.announce(_t("{user} has logged out").format(user=u_name))
                if uid in self._known_users:
                    del self._known_users[uid]
            self.refresh_channels_and_users()

        elif ev_type in (EventType.USER_UPDATE, EventType.STATE_CHANGE):
            if event.user:
                uid = getattr(event.user, "id", 0)
                u_nick = _safe_str(getattr(event.user, "nickname", "")) or _safe_str(getattr(event.user, "username", ""))
                if uid and u_nick:
                    self._known_users[uid] = u_nick
            self.refresh_users_in_channel()

        elif ev_type == EventType.USER_TEXT_MESSAGE:
            msg = event.message
            u_name = _safe_str(msg.user.nickname) or _safe_str(msg.user.username) or f"User #{msg.user.id}"
            msg_text = _safe_str(msg.text)
            my_uid = self.bot.ttclient.tt.getMyUserID() if self.bot else 0

            # Format: <Nickname>: <Content> [Time]
            time_str = time.strftime("%H:%M:%S")

            # Check if this message is a command to send private message to bot operator
            text_stripped = msg_text.strip()
            require_prefix = getattr(self.bot.config.general, "require_command_prefix", True) if (self.bot and hasattr(self.bot, "config")) else True
            if require_prefix:
                is_pm_cmd = text_stripped.lower().startswith("/pm ")
                pm_prefix_len = 4
            else:
                if text_stripped.lower().startswith("pm "):
                    is_pm_cmd = True
                    pm_prefix_len = 3
                else:
                    is_pm_cmd = False
                    pm_prefix_len = 0
            session_enabled = getattr(self.bot.config.general, "session_chat_enabled", False) if (self.bot and hasattr(self.bot, "config")) else True

            if session_enabled and is_pm_cmd and my_uid > 0 and msg.user.id != my_uid:
                pm_content = text_stripped[pm_prefix_len:].strip()
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
                    sound_events.play_sound(sound_events.SOUND_USER_MSG, enabled=self.is_sound_events_enabled())
                    accessibility.announce(_t("Private message from {user}: {message}").format(user=u_name, message=msg_text))
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
                sound_events.play_sound(sound_events.SOUND_CHANNEL_MSG, enabled=self.is_sound_events_enabled())
                accessibility.announce(_t("Channel message from {user}: {message}").format(user=u_name, message=msg_text))
            elif msg.type == MessageType.Broadcast:
                self.add_public_message(f"[Broadcast] {u_name}", msg_text, time_str)
                sound_events.play_sound(sound_events.SOUND_BROADCAST_MSG, enabled=self.is_sound_events_enabled())
                accessibility.announce(_t("Broadcast message from {user}: {message}").format(user=u_name, message=msg_text))

        elif ev_type == EventType.CHANNEL_NEW:
            self.refresh_channels_and_users()

        elif ev_type == EventType.CHANNEL_UPDATE:
            c_name = _safe_str(event.channel.name) or f"#{event.channel.id}"
            my_cid = self.bot.ttclient.tt.getMyChannelID() if self.bot else 0
            if event.channel.id == my_cid:
                if event.channel.id != self._last_channel_id:
                    self._last_channel_id = event.channel.id
                    self._last_channel_uids = None
                self.lbl_channel.SetLabel(f"{translate('Channel:')} {c_name}")
                self.SetTitle(f"{self.server_name} - {c_name} - TeamTalk")
            self.refresh_channels_and_users()

        elif ev_type == EventType.CHANNEL_REMOVE:
            self.refresh_channels_and_users()

        elif ev_type == EventType.SERVER_UPDATE:
            self.refresh_channels_and_users()

        elif ev_type == EventType.BANNED_USER:
            pass

        elif ev_type == EventType.MYSELF_KICKED:
            self.refresh_users_in_channel()

        elif ev_type in (EventType.CON_LOST, EventType.CON_FAILED):
            self.on_bot_disconnected()

        elif ev_type in (getattr(EventType, "SOUNDDEVICE_ADDED", None), getattr(EventType, "SOUNDDEVICE_REMOVED", None), getattr(EventType, "SOUNDDEVICE_UNPLUGGED", None)):
            self._on_wm_devicechange()

    def _format_user_display(self, u) -> str:
        """Formats a user for the channel users list: Nickname + Mic + (Gender, Role, Status)."""
        nick = _safe_str(getattr(u, "szNickname", ""))
        if not nick:
            nick = _safe_str(getattr(u, "szUsername", ""))
        if not nick:
            nick = f"User #{getattr(u, 'nUserID', 0)}"

        u_id = getattr(u, "nUserID", 0)
        u_state = getattr(u, "uUserState", 0)

        # 1. Microphone transmission state (USERSTATE_VOICE = 0x01, USERSTATE_MUTE_VOICE = 0x02)
        is_mic_on = bool((u_state & 0x01) and not (u_state & 0x02))

        # Check local bot microphone state if this user is ourselves
        if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            try:
                my_uid = self.bot.ttclient.tt.getMyUserID()
                if u_id == my_uid:
                    if getattr(self.bot.ttclient, "is_voice_transmission_enabled", False):
                        is_mic_on = True
                    elif hasattr(self, "chk_mic") and self.chk_mic.GetValue():
                        is_mic_on = True
            except Exception:
                pass

        mic_icon = " 🎤" if is_mic_on else ""

        # 2. Gender from nStatusMode
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

        # 3. Role: Admin, Operator, or User
        user_type = getattr(u, "uUserType", 0)
        is_admin = bool(user_type & 0x02)  # USERTYPE_ADMIN = 0x02
        is_op = False
        if not is_admin and self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            try:
                import TeamTalkPy
                cid = getattr(u, "nChannelID", 0) or self.bot.ttclient.tt.getMyChannelID()
                is_op = bool(TeamTalkPy.TeamTalk5.dll.TT_IsChannelOperator(self.bot.ttclient.tt._tt, cid, u_id))
            except Exception:
                pass

        if is_admin:
            role_str = translate("Admin")
        elif is_op:
            role_str = translate("Operator")
        else:
            role_str = translate("User")

        # 4. Status (Away, Question, and custom status message)
        details = [gender_str, role_str]

        status_msg = _safe_str(getattr(u, "szStatusMsg", "")).strip()
        is_away = bool(status_mode & STATUSMODE_AWAY)
        is_question = bool(status_mode & STATUSMODE_QUESTION)

        if is_away:
            details.append(translate("Away"))
        if is_question:
            details.append(translate("Question"))
        if status_msg:
            details.append(status_msg)

        details_str = ", ".join(details)
        return f"{nick}{mic_icon} ({details_str})"

    def refresh_channels_and_users(self):
        """Updates the channels and users tree view with channels and hierarchy."""
        if not self.bot or not hasattr(self.bot, "ttclient") or not self.bot.ttclient:
            if hasattr(self, "tree_session"):
                self.tree_session.DeleteAllItems()
            self._channel_items.clear()
            self._user_items.clear()
            self._last_tree_snapshot = None
            self._last_users = []
            if hasattr(self, "lbl_tree"):
                self.lbl_tree.SetLabel(f"{_t('&Channels and Users:')}")
            return

        try:
            tt = self.bot.ttclient.tt
            my_cid = tt.getMyChannelID()
            my_uid = tt.getMyUserID()

            raw_channels = list(tt.getServerChannels())
            raw_users = list(tt.getServerUsers())
        except Exception as e:
            logging.debug(f"[SessionDialog] Error querying channels/users: {e}")
            return

        for u in raw_users:
            u_id = getattr(u, "nUserID", 0)
            u_nick = _safe_str(getattr(u, "szNickname", "")) or _safe_str(getattr(u, "szUsername", ""))
            if u_id and u_nick:
                self._known_users[u_id] = u_nick
                if self.bot and hasattr(self.bot, "ttclient") and hasattr(self.bot.ttclient, "user_cache"):
                    if u_id not in self.bot.ttclient.user_cache:
                        try:
                            self.bot.ttclient.user_cache[u_id] = self.bot.ttclient.get_user_from_obj(u)
                        except Exception:
                            pass

        # Current channel user sound notification check
        current_chan_raw_users = [u for u in raw_users if getattr(u, "nChannelID", 0) == my_cid]
        curr_uids = {getattr(u, "nUserID", 0) for u in current_chan_raw_users}
        if self._last_channel_uids is not None:
            joined_uids = curr_uids - self._last_channel_uids
            joined_uids.discard(my_uid)
            left_uids = self._last_channel_uids - curr_uids
            left_uids.discard(my_uid)
            if joined_uids:
                sound_events.play_sound(sound_events.SOUND_NEW_USER, enabled=self.is_sound_events_enabled())
                for j_uid in joined_uids:
                    u_obj = next((u for u in current_chan_raw_users if getattr(u, "nUserID", 0) == j_uid), None)
                    j_nick = _safe_str(getattr(u_obj, "szNickname", "")) or _safe_str(getattr(u_obj, "szUsername", "")) or self._known_users.get(j_uid) or f"User #{j_uid}"
                    accessibility.announce(_t("{user} joined channel").format(user=j_nick))
            if left_uids:
                sound_events.play_sound(sound_events.SOUND_REMOVE_USER, enabled=self.is_sound_events_enabled())
                for l_uid in left_uids:
                    l_nick = self._known_users.get(l_uid) or f"User #{l_uid}"
                    for prev_uid, _, p_nick in (self._last_users or []):
                        if prev_uid == l_uid:
                            l_nick = p_nick
                            break
                    accessibility.announce(_t("{user} left channel").format(user=l_nick))
        self._last_channel_uids = curr_uids

        # Update _last_users for current channel
        users_data: List[tuple] = []
        for u in current_chan_raw_users:
            u_id = getattr(u, "nUserID", 0)
            disp_str = self._format_user_display(u)
            nick = _safe_str(getattr(u, "szNickname", "")) or _safe_str(getattr(u, "szUsername", "")) or f"User #{u_id}"
            users_data.append((u_id, disp_str, nick))
        users_data.sort(key=lambda item: item[2].lower())
        self._last_users = users_data

        mic_state = hasattr(self, "chk_mic") and self.chk_mic.GetValue()
        chan_snap = tuple((c.nChannelID, c.nParentID, _safe_str(c.szName), bool(c.bPassword), getattr(c, "uChannelType", 0)) for c in raw_channels)
        user_snap = tuple((getattr(u, "nUserID", 0), getattr(u, "nChannelID", 0), _safe_str(getattr(u, "szNickname", "")), getattr(u, "uUserState", 0), getattr(u, "nStatusMode", 0), _safe_str(getattr(u, "szStatusMsg", ""))) for u in raw_users)
        snapshot = (my_cid, my_uid, mic_state, chan_snap, user_snap)

        if snapshot == self._last_tree_snapshot:
            return

        is_initial = (self._last_tree_snapshot is None)

        # 1. Record expanded channels
        expanded_cids = set()
        for cid, item in self._channel_items.items():
            if item and item.IsOk() and self.tree_session.IsExpanded(item):
                expanded_cids.add(cid)

        # 2. Record selection
        selected_data = None
        sel_item = self.tree_session.GetSelection()
        if sel_item and sel_item.IsOk():
            selected_data = self.tree_session.GetItemData(sel_item)

        # 3. Organize data
        channels_map = {c.nChannelID: c for c in raw_channels}
        children_by_parent: Dict[int, List[Any]] = {}
        users_by_channel: Dict[int, List[Any]] = {}

        for c in raw_channels:
            p_id = c.nParentID
            children_by_parent.setdefault(p_id, []).append(c)

        for u in raw_users:
            c_id = getattr(u, "nChannelID", 0)
            users_by_channel.setdefault(c_id, []).append(u)

        # Sort subchannels by name
        for sub_list in children_by_parent.values():
            sub_list.sort(key=lambda c: _safe_str(getattr(c, "szName", "")).lower())

        # Sort users by nickname
        for u_list in users_by_channel.values():
            u_list.sort(key=lambda u: (_safe_str(getattr(u, "szNickname", "")) or _safe_str(getattr(u, "szUsername", ""))).lower())

        def count_subtree_users(cid: int) -> int:
            cnt = len(users_by_channel.get(cid, []))
            for sub_c in children_by_parent.get(cid, []):
                cnt += count_subtree_users(sub_c.nChannelID)
            return cnt

        try:
            root_cid = tt.getRootChannelID()
        except Exception:
            root_cid = 0

        root_chan = channels_map.get(root_cid)
        if not root_chan:
            for c in raw_channels:
                if c.nParentID == 0 or c.nParentID == c.nChannelID:
                    root_chan = c
                    root_cid = c.nChannelID
                    break

        if not root_chan and raw_channels:
            root_chan = raw_channels[0]
            root_cid = root_chan.nChannelID

        if not root_chan:
            self.tree_session.DeleteAllItems()
            self._channel_items.clear()
            self._user_items.clear()
            self._last_tree_snapshot = snapshot
            return

        try:
            srv_prop = tt.getServerProperties()
            srv_name = _safe_str(getattr(srv_prop, "szServerName", ""))
        except Exception:
            srv_name = ""
        if not srv_name:
            srv_name = _safe_str(getattr(root_chan, "szName", "")) or self.server_name

        # Always keep root channel, pending channels, and current channel (with ancestors) expanded
        expanded_cids.add(root_cid)
        if self._pending_expand_cids:
            expanded_cids.update(self._pending_expand_cids)
            self._pending_expand_cids.clear()

        curr = my_cid
        visited = set()
        while curr and curr not in visited:
            visited.add(curr)
            expanded_cids.add(curr)
            parent_chan = channels_map.get(curr)
            if parent_chan and parent_chan.nParentID and parent_chan.nParentID != curr:
                curr = parent_chan.nParentID
            else:
                break

        self.tree_session.Freeze()
        try:
            self.tree_session.DeleteAllItems()
            self._channel_items.clear()
            self._user_items.clear()

            def format_chan_text(cid: int, name: str, is_pw: bool, is_root: bool = False) -> str:
                direct_cnt = len(users_by_channel.get(cid, []))
                has_subs = bool(children_by_parent.get(cid, []))
                if has_subs:
                    tot_cnt = count_subtree_users(cid)
                    count_str = f"({direct_cnt}/{tot_cnt})"
                else:
                    count_str = f"({direct_cnt})"

                pw_str = f" - [{_t('Password protected')}]" if is_pw else ""
                cur_str = f" *" if (cid == my_cid and not is_root) else ""
                return f"{name} {count_str}{pw_str}{cur_str}"

            root_text = format_chan_text(root_cid, srv_name, bool(root_chan.bPassword), is_root=True)
            root_item = self.tree_session.AddRoot(
                root_text,
                data={
                    "type": "channel",
                    "id": root_cid,
                    "name": srv_name,
                    "has_password": bool(root_chan.bPassword),
                    "channel": root_chan,
                }
            )
            self._channel_items[root_cid] = root_item
            if root_cid == my_cid:
                self.tree_session.SetItemBold(root_item, True)

            def populate_channel(parent_item, cid: int):
                # 1. Users directly in this channel
                for u in users_by_channel.get(cid, []):
                    u_id = getattr(u, "nUserID", 0)
                    nick = _safe_str(getattr(u, "szNickname", "")) or _safe_str(getattr(u, "szUsername", "")) or f"User #{u_id}"
                    disp_str = self._format_user_display(u)
                    u_item = self.tree_session.AppendItem(
                        parent_item,
                        disp_str,
                        data={
                            "type": "user",
                            "id": u_id,
                            "nickname": nick,
                            "channel_id": cid,
                            "display_str": disp_str,
                            "user": u,
                        }
                    )
                    self._user_items[u_id] = u_item
                    if u_id == my_uid:
                        self.tree_session.SetItemBold(u_item, True)

                # 2. Sub-channels of this channel
                for sub_c in children_by_parent.get(cid, []):
                    sub_id = sub_c.nChannelID
                    if sub_id == root_cid or sub_id == cid:
                        continue
                    sub_name = _safe_str(getattr(sub_c, "szName", "")) or f"#{sub_id}"
                    sub_text = format_chan_text(sub_id, sub_name, bool(sub_c.bPassword))
                    sub_item = self.tree_session.AppendItem(
                        parent_item,
                        sub_text,
                        data={
                            "type": "channel",
                            "id": sub_id,
                            "name": sub_name,
                            "has_password": bool(sub_c.bPassword),
                            "channel": sub_c,
                        }
                    )
                    self._channel_items[sub_id] = sub_item
                    if sub_id == my_cid:
                        self.tree_session.SetItemBold(sub_item, True)

                    populate_channel(sub_item, sub_id)

            populate_channel(root_item, root_cid)

            # Orphans check
            for c in raw_channels:
                if c.nChannelID not in self._channel_items:
                    orphan_id = c.nChannelID
                    orphan_name = _safe_str(getattr(c, "szName", "")) or f"#{orphan_id}"
                    orphan_text = format_chan_text(orphan_id, orphan_name, bool(c.bPassword))
                    orphan_item = self.tree_session.AppendItem(
                        root_item,
                        orphan_text,
                        data={
                            "type": "channel",
                            "id": orphan_id,
                            "name": orphan_name,
                            "has_password": bool(c.bPassword),
                            "channel": c,
                        }
                    )
                    self._channel_items[orphan_id] = orphan_item
                    populate_channel(orphan_item, orphan_id)

            # Apply expansions
            for cid, item in self._channel_items.items():
                if cid in expanded_cids and item and item.IsOk():
                    self.tree_session.Expand(item)

            # Restore selection
            restored = False
            if selected_data:
                sel_type = selected_data.get("type")
                sel_id = selected_data.get("id")
                if sel_type == "channel" and sel_id in self._channel_items:
                    self.tree_session.SelectItem(self._channel_items[sel_id])
                    restored = True
                elif sel_type == "user" and sel_id in self._user_items:
                    self.tree_session.SelectItem(self._user_items[sel_id])
                    restored = True

            if not restored:
                if my_cid in self._channel_items:
                    self.tree_session.SelectItem(self._channel_items[my_cid])
                elif root_cid in self._channel_items:
                    self.tree_session.SelectItem(self._channel_items[root_cid])

        finally:
            self.tree_session.Thaw()

        self._last_tree_snapshot = snapshot
        total_users = len(raw_users)
        self.lbl_tree.SetLabel(f"{_t('&Channels and Users (%d)') % total_users}:")

    def refresh_users_in_channel(self):
        """Compatibility wrapper: refreshes the full channels and users tree."""
        self.refresh_channels_and_users()

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
            sound_events.play_sound(sound_events.SOUND_CHANNEL_MSG_SENT, enabled=self.is_sound_events_enabled())
            accessibility.announce(_t("Channel message sent: {message}").format(message=msg))

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
                    sound_events.play_sound(sound_events.SOUND_VOICEACT_ON, enabled=self.is_sound_events_enabled())
                    accessibility.announce(_t("Microphone enabled"))
            else:
                self.bot.ttclient.restore_input_device()
                self.server_data["mic_enabled"] = False
                self.save_server_settings()
                sound_events.play_sound(sound_events.SOUND_VOICEACT_OFF, enabled=self.is_sound_events_enabled())
                accessibility.announce(_t("Microphone disabled"))
        except Exception as e:
            logging.error(f"Error toggling voice transmission: {e}")
            self.chk_mic.SetValue(False)
            self.server_data["mic_enabled"] = False
            self.save_server_settings()
        finally:
            self.refresh_users_in_channel()

    def on_toggle_mute_out(self, event=None):
        """Toggles mute on TeamTalk sound output."""
        self._is_muted = self.chk_mute_out.GetValue()
        if self._is_muted:
            accessibility.announce(_t("Sound output muted"))
        else:
            accessibility.announce(_t("Sound output unmuted"))
        if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
            try:
                import TeamTalkPy
                TeamTalkPy.TeamTalk5.dll.TT_SetSoundOutputMute(self.bot.ttclient.tt._tt, self._is_muted)
            except Exception as e:
                logging.error(f"Error setting sound output mute: {e}")

    def on_toggle_sound_events(self, event=None):
        """Toggles TeamTalk sound events on or off."""
        val = self.chk_sound_events.GetValue()
        self.server_data["sound_events_enabled"] = val
        self.save_server_settings()

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

        self.audio_devices_dialog = SessionAudioDevicesDialog(self, self.bot)
        self.audio_devices_dialog.ShowModal()
        self.audio_devices_dialog.Destroy()
        self.audio_devices_dialog = None

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

    def join_channel_action(self, channel_id: int):
        """Attempts to join the given channel. Prompts for password if channel is password protected."""
        if not self.bot or not hasattr(self.bot, "ttclient") or not self.bot.ttclient:
            return

        tt = self.bot.ttclient.tt
        try:
            my_cid = tt.getMyChannelID()
        except Exception:
            my_cid = 0

        # Channel lookup
        chan = None
        chan_name = f"#{channel_id}"
        has_password = False
        try:
            chan = tt.getChannel(channel_id)
            if chan:
                has_password = bool(getattr(chan, "bPassword", False))
                chan_name = _safe_str(getattr(chan, "szName", "")) or chan_name
        except Exception:
            pass

        password = self._channel_passwords.get(channel_id, "")

        if has_password:
            dlg = ChannelPasswordDialog(self, chan_name, channel_id, initial_password=password)
            res = dlg.ShowModal()
            if res != wx.ID_OK:
                dlg.Destroy()
                return
            password = dlg.get_password()
            dlg.Destroy()
            self._channel_passwords[channel_id] = password

        # Immediately expand this channel and mark for persistent expansion
        self._pending_expand_cids.add(channel_id)
        chan_item = self._channel_items.get(channel_id)
        if chan_item and chan_item.IsOk():
            self.tree_session.Expand(chan_item)
            p = self.tree_session.GetItemParent(chan_item)
            while p and p.IsOk():
                self.tree_session.Expand(p)
                p = self.tree_session.GetItemParent(p)

        try:
            self.bot.ttclient.join_channel(channel_id, password)
        except Exception as e:
            logging.error(f"Error joining channel {channel_id}: {e}")
            wx.MessageBox(
                f"{_t('Failed to join channel')}: {e}",
                _t("Channel Password"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def on_tree_item_activated(self, event=None):
        """When channel or user is double-clicked or Enter is pressed in tree view."""
        item = self.tree_session.GetSelection()
        if not item or not item.IsOk():
            if event and hasattr(event, "GetItem"):
                item = event.GetItem()
        if not item or not item.IsOk():
            return

        data = self.tree_session.GetItemData(item)
        if not data or not isinstance(data, dict):
            return

        item_type = data.get("type")
        if item_type == "channel":
            chan_id = data.get("id")
            if chan_id is not None:
                # Expand immediately so contents are shown as in TeamTalk
                self.tree_session.Expand(item)
                self.join_channel_action(chan_id)
        elif item_type == "user":
            user_id = data.get("id")
            nick = data.get("nickname", f"User #{user_id}")
            if user_id:
                self.open_private_chat(user_id, nick)

    def on_tree_key_down(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.on_tree_item_activated()
            return
        elif key == ord("C") and event.ControlDown():
            self._copy_selected_tree_item()
            return
        event.Skip()

    def on_tree_context_menu(self, event):
        item = self.tree_session.GetSelection()
        if not item or not item.IsOk():
            if hasattr(event, "GetItem"):
                item = event.GetItem()
        if not item or not item.IsOk():
            return

        data = self.tree_session.GetItemData(item)
        if not data or not isinstance(data, dict):
            return

        menu = wx.Menu()
        item_type = data.get("type")

        if item_type == "channel":
            chan_id = data.get("id")
            chan_name = data.get("name", f"#{chan_id}")
            item_join = menu.Append(wx.ID_ANY, _t("&Join Channel"))
            self.Bind(wx.EVT_MENU, lambda e: self.join_channel_action(chan_id), item_join)

            item_copy = menu.Append(wx.ID_ANY, _t("&Copy Channel Name"))
            self.Bind(wx.EVT_MENU, lambda e: self._copy_text(chan_name), item_copy)

        elif item_type == "user":
            u_id = data.get("id")
            nick = data.get("nickname", f"User #{u_id}")
            disp_str = data.get("display_str", nick)

            item_private = menu.Append(wx.ID_ANY, translate("&Private Message..."))
            self.Bind(wx.EVT_MENU, lambda e: self.open_private_chat(u_id, nick), item_private)

            item_mention = menu.Append(wx.ID_ANY, translate("&Mention '{name}'").format(name=nick))
            self.Bind(wx.EVT_MENU, lambda e: self._mention_user(nick), item_mention)

            item_copy = menu.Append(wx.ID_ANY, translate("&Copy User Info"))
            self.Bind(wx.EVT_MENU, lambda e: self._copy_text(disp_str), item_copy)

        self.PopupMenu(menu)
        menu.Destroy()

    def _copy_selected_tree_item(self):
        item = self.tree_session.GetSelection()
        if not item or not item.IsOk():
            return
        data = self.tree_session.GetItemData(item)
        if not data or not isinstance(data, dict):
            return
        if data.get("type") == "user":
            self._copy_text(data.get("display_str", data.get("nickname", "")))
        elif data.get("type") == "channel":
            self._copy_text(data.get("name", ""))

    def _copy_text(self, text: str):
        if not text:
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Close()

    def on_user_activated(self, event=None):
        self.on_tree_item_activated(event)

    def on_user_key_down(self, event):
        self.on_tree_key_down(event)

    def on_user_context_menu(self, event):
        self.on_tree_context_menu(event)

    def _copy_selected_user(self):
        self._copy_selected_tree_item()

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

    def _handle_slider_key(self, slider, on_change_callback, event):
        key = event.GetKeyCode()
        cur_val = slider.GetValue()
        new_val = None

        if key in (wx.WXK_UP, wx.WXK_RIGHT):
            new_val = min(100, cur_val + 1)
        elif key in (wx.WXK_DOWN, wx.WXK_LEFT):
            new_val = max(0, cur_val - 1)
        elif key == wx.WXK_PAGEUP:
            new_val = min(100, cur_val + 5)
        elif key == wx.WXK_PAGEDOWN:
            new_val = max(0, cur_val - 5)
        elif key == wx.WXK_HOME:
            new_val = 0
        elif key == wx.WXK_END:
            new_val = 100

        if new_val is not None:
            if new_val != cur_val:
                slider.SetValue(new_val)
                try:
                    import ctypes
                    ctypes.windll.user32.NotifyWinEvent(0x800E, slider.GetHandle(), -4, 0)
                except Exception:
                    pass
                on_change_callback(event)
            return

        event.Skip()

    def on_master_slider_key(self, event):
        self._handle_slider_key(self.slider_master_vol, self.on_master_vol_changed, event)

    def on_mic_slider_key(self, event):
        self._handle_slider_key(self.slider_mic_vol, self.on_mic_vol_changed, event)

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

        self._cleanup_device_change_notification()

        # Unregister from parent session windows
        if hasattr(self.parent, "session_windows") and self.s_id in self.parent.session_windows:
            del self.parent.session_windows[self.s_id]

        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()

    def _init_device_change_notification(self):
        self._dev_notify_handle = None
        self._device_debounce_timer = None
        self._known_audio_device_ids = set()
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                class DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
                    _fields_ = [
                        ('dbcc_size', wintypes.DWORD),
                        ('dbcc_devicetype', wintypes.DWORD),
                        ('dbcc_reserved', wintypes.DWORD),
                        ('dbcc_classguid', ctypes.c_byte * 16),
                        ('dbcc_name', ctypes.c_wchar * 1)
                    ]
                dbi = DEV_BROADCAST_DEVICEINTERFACE_W()
                dbi.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE_W)
                dbi.dbcc_devicetype = 5
                hwnd = self.GetHandle()
                self._dev_notify_handle = user32.RegisterDeviceNotificationW(
                    wintypes.HANDLE(hwnd),
                    ctypes.byref(dbi),
                    4
                )
            except Exception as e:
                logging.debug(f"RegisterDeviceNotification error: {e}")

    def _cleanup_device_change_notification(self):
        if sys.platform == "win32" and getattr(self, "_dev_notify_handle", None):
            try:
                import ctypes
                ctypes.windll.user32.UnregisterDeviceNotification(self._dev_notify_handle)
            except Exception:
                pass
            self._dev_notify_handle = None

    def MSWWindowProc(self, msg, wParam, lParam):
        # WM_DEVICECHANGE = 0x0219
        if msg == 0x0219:
            self._on_wm_devicechange()
        return super().MSWWindowProc(msg, wParam, lParam)

    def _on_wm_devicechange(self):
        if not hasattr(self, "_device_debounce_timer") or not self._device_debounce_timer:
            self._device_debounce_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._on_device_debounce_timeout, self._device_debounce_timer)
        self._device_debounce_timer.StartOnce(600)

    def _on_device_debounce_timeout(self, event=None):
        if getattr(self, "audio_devices_dialog", None) and bool(self.audio_devices_dialog):
            try:
                self.audio_devices_dialog._auto_detect_devices()
                return
            except Exception:
                pass
        try:
            import TeamTalkPy
            TeamTalkPy.TeamTalk5.dll.TT_RestartSoundSystem()
            if self.bot and hasattr(self.bot, "ttclient") and self.bot.ttclient:
                in_devs = self.bot.ttclient.get_input_devices()
                out_devs = self.bot.ttclient.get_output_devices()
                curr_ids = {d.id for d in in_devs} | {d.id for d in out_devs}
                if hasattr(self, "_known_audio_device_ids") and self._known_audio_device_ids:
                    added = curr_ids - self._known_audio_device_ids
                    removed = self._known_audio_device_ids - curr_ids
                    if added:
                        accessibility.announce(_t("New audio device detected. Device list refreshed."))
                    elif removed:
                        accessibility.announce(_t("Audio device disconnected. Device list updated."))
                self._known_audio_device_ids = curr_ids
        except Exception as e:
            logging.debug(f"Device change check error: {e}")


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

            sound_enabled = True
            if hasattr(self.session_dialog, "is_sound_events_enabled"):
                sound_enabled = self.session_dialog.is_sound_events_enabled()
            sound_events.play_sound(sound_events.SOUND_USER_MSG_SENT, enabled=sound_enabled)
            accessibility.announce(_t("Private message sent: {message}").format(message=msg))

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
    """Accessible dialog to configure TeamTalk Sound System, Input, Output devices, and Volume levels."""

    def __init__(self, parent: wx.Window, bot: Any):
        super().__init__(
            parent,
            title=translate("Sound Configuration - TeamTalk"),
            size=(560, 480),
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

        # 4. Master Volume Slider (Global volume in TeamTalk, default 50%)
        saved_master = server_data.get("master_volume")
        if saved_master is not None:
            init_master = int(saved_master)
        else:
            init_master = 50

        lbl_master = wx.StaticText(panel, label=translate("&Master Volume:"))
        self.slider_master_vol = wx.Slider(panel, value=init_master, minValue=0, maxValue=100, style=wx.SL_HORIZONTAL)
        self.slider_master_vol.SetName(translate("Master Volume"))
        self.slider_master_vol.SetLineSize(1)
        self.slider_master_vol.SetPageSize(5)
        self.slider_master_vol.Bind(wx.EVT_SLIDER, self.on_master_vol_changed)
        self.slider_master_vol.Bind(wx.EVT_KEY_DOWN, self.on_master_slider_key)
        self.lbl_master_val = wx.StaticText(panel, label=f"{init_master}%")

        master_box = wx.BoxSizer(wx.VERTICAL)
        master_hdr = wx.BoxSizer(wx.HORIZONTAL)
        master_hdr.Add(lbl_master, 0, wx.ALIGN_CENTER_VERTICAL)
        master_hdr.Add((8, 0), 0)
        master_hdr.Add(self.lbl_master_val, 0, wx.ALIGN_CENTER_VERTICAL)
        master_box.Add(master_hdr, 0, wx.BOTTOM, 4)
        master_box.Add(self.slider_master_vol, 0, wx.EXPAND)
        main_sizer.Add(master_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # 5. Microphone Volume Slider (default 50%)
        saved_mic = server_data.get("mic_gain")
        if saved_mic is not None:
            init_mic = int(saved_mic)
        else:
            init_mic = 50

        lbl_mic = wx.StaticText(panel, label=translate("Microphone &Volume:"))
        self.slider_mic_vol = wx.Slider(panel, value=init_mic, minValue=0, maxValue=100, style=wx.SL_HORIZONTAL)
        self.slider_mic_vol.SetName(translate("Microphone Volume"))
        self.slider_mic_vol.SetLineSize(1)
        self.slider_mic_vol.SetPageSize(5)
        self.slider_mic_vol.Bind(wx.EVT_SLIDER, self.on_mic_vol_changed)
        self.slider_mic_vol.Bind(wx.EVT_KEY_DOWN, self.on_mic_slider_key)
        self.lbl_mic_val = wx.StaticText(panel, label=f"{init_mic}%")

        mic_box = wx.BoxSizer(wx.VERTICAL)
        mic_hdr = wx.BoxSizer(wx.HORIZONTAL)
        mic_hdr.Add(lbl_mic, 0, wx.ALIGN_CENTER_VERTICAL)
        mic_hdr.Add((8, 0), 0)
        mic_hdr.Add(self.lbl_mic_val, 0, wx.ALIGN_CENTER_VERTICAL)
        mic_box.Add(mic_hdr, 0, wx.BOTTOM, 4)
        mic_box.Add(self.slider_mic_vol, 0, wx.EXPAND)
        main_sizer.Add(mic_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # 6. Action buttons: Refresh, OK, Cancel
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_refresh = wx.Button(panel, label=_t("&Refresh Devices"))
        self.btn_refresh.SetName(_t("Refresh Devices"))
        self.btn_refresh.SetToolTip(translate("Refresh sound devices list if new devices were connected"))
        self.btn_refresh.Bind(wx.EVT_BUTTON, lambda e: self.on_refresh(user_triggered=True))
        btn_sizer.Add(self.btn_refresh, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        btn_sizer.AddStretchSpacer()

        self.btn_ok = wx.Button(panel, wx.ID_OK, label=_t("&OK"))
        self.btn_ok.SetDefault()
        self.btn_ok.Bind(wx.EVT_BUTTON, self.on_ok)
        btn_sizer.Add(self.btn_ok, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        self.btn_cancel = wx.Button(panel, wx.ID_CANCEL, label=_t("&Cancel"))
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)
        btn_sizer.Add(self.btn_cancel, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 5)

        main_sizer.Add(btn_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        panel.SetSizer(main_sizer)

        dlg_sizer = wx.BoxSizer(wx.VERTICAL)
        dlg_sizer.Add(panel, 1, wx.EXPAND)
        self.SetSizer(dlg_sizer)

        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        # Populate devices
        self._input_devices = []
        self._output_devices = []
        self.populate_devices()

        self._init_device_change_notification()
        self.CentreOnParent()

    def get_selected_sound_system(self):
        sel = self.choice_system.GetSelection()
        if 0 <= sel < len(self.sound_systems):
            return self.sound_systems[sel][0]
        return 5

    def populate_devices(self, preserve_selection: bool = False):
        if not self.ttclient:
            return

        prev_in_id = None
        prev_in_name = None
        prev_out_id = None
        prev_out_name = None
        if preserve_selection:
            cur_in_sel = self.choice_input.GetSelection()
            if 0 <= cur_in_sel < len(self._input_devices):
                prev_in_id = self._input_devices[cur_in_sel].id
                prev_in_name = self._input_devices[cur_in_sel].name
            cur_out_sel = self.choice_output.GetSelection()
            if 0 <= cur_out_sel < len(self._output_devices):
                prev_out_id = self._output_devices[cur_out_sel].id
                prev_out_name = self._output_devices[cur_out_sel].name

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

            if prev_in_id is not None and dev.id == prev_in_id:
                selected_in_idx = idx
            elif prev_in_id is None and saved_in_id is not None and dev.id == saved_in_id:
                selected_in_idx = idx
            elif selected_in_idx == 0 and prev_in_name and dev.name == prev_in_name:
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

            if prev_out_id is not None and dev.id == prev_out_id:
                selected_out_idx = idx
            elif prev_out_id is None and saved_out_id is not None and dev.id == saved_out_id:
                selected_out_idx = idx
            elif selected_out_idx == 0 and prev_out_name and dev.name == prev_out_name:
                selected_out_idx = idx
            elif selected_out_idx == 0 and saved_out_name and dev.name == saved_out_name:
                selected_out_idx = idx
            elif selected_out_idx == 0 and cur_out_id is not None and dev.id == cur_out_id:
                selected_out_idx = idx

        if self._output_devices:
            self.choice_output.SetSelection(selected_out_idx)

    def on_system_changed(self, event):
        self.populate_devices(preserve_selection=False)

    def on_master_vol_changed(self, event):
        vol_pct = self.slider_master_vol.GetValue()
        if hasattr(self, "lbl_master_val"):
            self.lbl_master_val.SetLabel(f"{vol_pct}%")

    def on_mic_vol_changed(self, event):
        mic_pct = self.slider_mic_vol.GetValue()
        if hasattr(self, "lbl_mic_val"):
            self.lbl_mic_val.SetLabel(f"{mic_pct}%")

    def _handle_slider_key(self, slider, on_change_callback, event):
        key = event.GetKeyCode()
        cur_val = slider.GetValue()
        new_val = None

        if key in (wx.WXK_UP, wx.WXK_RIGHT):
            new_val = min(100, cur_val + 1)
        elif key in (wx.WXK_DOWN, wx.WXK_LEFT):
            new_val = max(0, cur_val - 1)
        elif key == wx.WXK_PAGEUP:
            new_val = min(100, cur_val + 5)
        elif key == wx.WXK_PAGEDOWN:
            new_val = max(0, cur_val - 5)
        elif key == wx.WXK_HOME:
            new_val = 0
        elif key == wx.WXK_END:
            new_val = 100

        if new_val is not None:
            if new_val != cur_val:
                slider.SetValue(new_val)
                try:
                    import ctypes
                    ctypes.windll.user32.NotifyWinEvent(0x800E, slider.GetHandle(), -4, 0)
                except Exception:
                    pass
                on_change_callback(event)
            return

        event.Skip()

    def on_master_slider_key(self, event):
        self._handle_slider_key(self.slider_master_vol, self.on_master_vol_changed, event)

    def on_mic_slider_key(self, event):
        self._handle_slider_key(self.slider_mic_vol, self.on_mic_vol_changed, event)

    def on_refresh(self, event=None, user_triggered: bool = True):
        """Refreshes the audio device list manually or automatically."""
        try:
            import TeamTalkPy
            TeamTalkPy.TeamTalk5.dll.TT_RestartSoundSystem()
        except Exception as e:
            logging.debug(f"TT_RestartSoundSystem error: {e}")

        self.populate_devices(preserve_selection=True)
        if user_triggered:
            accessibility.announce(_t("Audio devices list refreshed"))

    def on_ok(self, event=None):
        """Applies and saves all selected sound settings to TeamTalk and session configuration."""
        if not self.ttclient:
            self._cleanup_device_change_notification()
            self.EndModal(wx.ID_OK)
            return

        try:
            # 1. Apply & save Sound System
            sys_id = self.get_selected_sound_system()
            if hasattr(self.parent, "server_data"):
                self.parent.server_data["session_sound_system"] = sys_id

            # 2. Apply & save Input Device
            in_sel = self.choice_input.GetSelection()
            if 0 <= in_sel < len(self._input_devices):
                in_dev = self._input_devices[in_sel]
                self.ttclient.set_input_device_live(in_dev.id)
                if hasattr(self.parent, "selected_mic_device_id"):
                    self.parent.selected_mic_device_id = in_dev.id
                if hasattr(self.parent, "server_data"):
                    self.parent.server_data["session_input_device_id"] = in_dev.id
                    self.parent.server_data["session_input_device_name"] = in_dev.name
                    self.parent.server_data["selected_mic_device_id"] = in_dev.id
                    self.parent.server_data["selected_mic_device_name"] = in_dev.name

            # 3. Apply & save Output Device
            out_sel = self.choice_output.GetSelection()
            if 0 <= out_sel < len(self._output_devices):
                out_dev = self._output_devices[out_sel]
                self.ttclient.set_output_device(out_dev.id)
                if hasattr(self.parent, "server_data"):
                    self.parent.server_data["session_output_device_id"] = out_dev.id
                    self.parent.server_data["session_output_device_name"] = out_dev.name

            # 4. Master volume
            vol_pct = self.slider_master_vol.GetValue()
            vol_val = int(vol_pct * 20)
            try:
                import TeamTalkPy
                TeamTalkPy.TeamTalk5.dll.TT_SetSoundOutputVolume(self.ttclient.tt._tt, vol_val)
            except Exception as e:
                logging.error(f"Error setting master output volume: {e}")
            if hasattr(self.parent, "server_data"):
                self.parent.server_data["master_volume"] = vol_pct

            # 5. Mic volume
            mic_pct = self.slider_mic_vol.GetValue()
            gain_val = int(mic_pct * 20)
            try:
                import TeamTalkPy
                TeamTalkPy.TeamTalk5.dll.TT_SetSoundInputGainLevel(self.ttclient.tt._tt, gain_val)
            except Exception as e:
                logging.error(f"Error setting mic volume: {e}")
            if hasattr(self.parent, "server_data"):
                self.parent.server_data["mic_gain"] = mic_pct
                self.parent.server_data["session_gain"] = mic_pct

            # 6. Save server settings to disk
            if hasattr(self.parent, "save_server_settings"):
                self.parent.save_server_settings()

            accessibility.announce(_t("Sound settings saved successfully"))
        except Exception as e:
            logging.error(f"Error saving sound configuration: {e}")
            wx.MessageBox(
                f"{translate('Failed to save sound settings:')}\n{e}",
                translate("Error"),
                wx.OK | wx.ICON_ERROR,
                self,
            )

        self._cleanup_device_change_notification()
        self.EndModal(wx.ID_OK)

    def on_cancel(self, event=None):
        self._cleanup_device_change_notification()
        self.EndModal(wx.ID_CANCEL)

    def on_close(self, event=None):
        self.on_cancel()

    def on_char_hook(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            focus = self.FindFocus()
            if focus == self.btn_cancel:
                self.on_cancel()
                return
            elif focus == self.btn_refresh:
                self.on_refresh(user_triggered=True)
                return
            self.on_ok()
            return
        elif key == wx.WXK_ESCAPE:
            self.on_cancel()
            return
        event.Skip()

    def _init_device_change_notification(self):
        self._dev_notify_handle = None
        self._device_debounce_timer = None
        if sys.platform == "win32":
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                class DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
                    _fields_ = [
                        ('dbcc_size', wintypes.DWORD),
                        ('dbcc_devicetype', wintypes.DWORD),
                        ('dbcc_reserved', wintypes.DWORD),
                        ('dbcc_classguid', ctypes.c_byte * 16),
                        ('dbcc_name', ctypes.c_wchar * 1)
                    ]
                dbi = DEV_BROADCAST_DEVICEINTERFACE_W()
                dbi.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE_W)
                dbi.dbcc_devicetype = 5
                hwnd = self.GetHandle()
                self._dev_notify_handle = user32.RegisterDeviceNotificationW(
                    wintypes.HANDLE(hwnd),
                    ctypes.byref(dbi),
                    4
                )
            except Exception as e:
                logging.debug(f"RegisterDeviceNotification error: {e}")

    def _cleanup_device_change_notification(self):
        if sys.platform == "win32" and getattr(self, "_dev_notify_handle", None):
            try:
                import ctypes
                ctypes.windll.user32.UnregisterDeviceNotification(self._dev_notify_handle)
            except Exception:
                pass
            self._dev_notify_handle = None

    def MSWWindowProc(self, msg, wParam, lParam):
        # WM_DEVICECHANGE = 0x0219
        if msg == 0x0219:
            self._on_wm_devicechange()
        return super().MSWWindowProc(msg, wParam, lParam)

    def _on_wm_devicechange(self):
        if not hasattr(self, "_device_debounce_timer") or not self._device_debounce_timer:
            self._device_debounce_timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self._on_device_debounce_timeout, self._device_debounce_timer)
        self._device_debounce_timer.StartOnce(600)

    def _on_device_debounce_timeout(self, event=None):
        self._auto_detect_devices()

    def _auto_detect_devices(self):
        old_in_ids = {d.id for d in self._input_devices}
        old_out_ids = {d.id for d in self._output_devices}

        try:
            import TeamTalkPy
            TeamTalkPy.TeamTalk5.dll.TT_RestartSoundSystem()
        except Exception:
            pass

        self.populate_devices(preserve_selection=True)

        new_in_ids = {d.id for d in self._input_devices}
        new_out_ids = {d.id for d in self._output_devices}

        added = (new_in_ids - old_in_ids) or (new_out_ids - old_out_ids)
        removed = (old_in_ids - new_in_ids) or (old_out_ids - new_out_ids)

        if added:
            accessibility.announce(_t("New audio device detected. Device list refreshed."))
        elif removed:
            accessibility.announce(_t("Audio device disconnected. Device list updated."))
