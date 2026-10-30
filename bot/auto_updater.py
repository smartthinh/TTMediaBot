import wx
import urllib.request
import json
import threading
import sys
import os
import subprocess
from bot.app_vars import app_version, temp_dir

GITHUB_API_URL = "https://api.github.com/repos/smartthinh/TTMediaBot/releases/latest"
CHANGELOG_URL = "https://raw.githubusercontent.com/smartthinh/TTMediaBot/master/CHANGELOG.md"


def _is_valid_window(win):
    if win is None:
        return False
    try:
        return bool(win) and not win.IsBeingDeleted()
    except Exception:
        return False


def check_for_updates():
    try:
        req = urllib.request.Request(GITHUB_API_URL, headers={'User-Agent': 'TTMediaBot-AutoUpdater'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            latest_version = data.get("tag_name", "").lstrip("v")
            
            if is_newer_version(app_version, latest_version):
                assets = data.get("assets", [])
                download_url = None
                for asset in assets:
                    if asset.get("name", "").endswith(".exe"):
                        download_url = asset.get("browser_download_url")
                        break
                
                if download_url:
                    changelog_text = ""
                    try:
                        url = CHANGELOG_URL
                        if "github.com" in url and "/blob/" in url:
                            url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                        cl_req = urllib.request.Request(url, headers={'User-Agent': 'TTMediaBot-AutoUpdater'})
                        with urllib.request.urlopen(cl_req, timeout=5) as cl_res:
                            changelog_text = cl_res.read().decode('utf-8', errors='ignore')
                    except Exception as e:
                        print(f"Error fetching remote changelog: {e}")
                        if os.path.exists("CHANGELOG.md"):
                            try:
                                with open("CHANGELOG.md", "r", encoding="utf-8") as f:
                                    changelog_text = f.read()
                            except Exception:
                                pass
                        
                    return latest_version, download_url, changelog_text, None
        return None, None, None, None
    except Exception as e:
        print(f"Error checking for updates: {e}")
        return None, None, None, str(e)


def is_newer_version(current, latest):
    def parse_version(v):
        return [int(x) for x in v.split(".") if x.isdigit()]
    try:
        return parse_version(latest) > parse_version(current)
    except Exception:
        return False


class UpdateDialog(wx.Dialog):
    def __init__(self, parent, latest_version, download_url, changelog_text, translate):
        super().__init__(parent, title=translate("Update Available"), size=(600, 450))
        self.download_url = download_url
        self.latest_version = latest_version
        self.translate = translate
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        
        info_text = self.translate("A new version of TTMediaBot (v{version}) is available.\nWould you like to update now?").format(version=latest_version)
        self.lbl_info = wx.StaticText(self, label=info_text)
        sizer.Add(self.lbl_info, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 15)
        
        # Read-only multiline textbox for changelog
        self.txt_changelog = wx.TextCtrl(self, value="", style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        if changelog_text:
            self.txt_changelog.ChangeValue(changelog_text)
        else:
            self.txt_changelog.ChangeValue(self.translate("Release notes are not available."))
        sizer.Add(self.txt_changelog, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)
        
        self.progress = wx.Gauge(self, range=100, style=wx.GA_HORIZONTAL)
        self.progress.Hide()
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 15)
        
        self.btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_update = wx.Button(self, label=self.translate("Update"))
        self.btn_cancel = wx.Button(self, label=self.translate("Cancel"))
        
        self.btn_update.Bind(wx.EVT_BUTTON, self.on_update)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)
        
        self.btn_sizer.Add(self.btn_update, 0, wx.RIGHT, 10)
        self.btn_sizer.Add(self.btn_cancel, 0, wx.LEFT, 10)
        sizer.Add(self.btn_sizer, 0, wx.ALIGN_CENTER | wx.ALL, 15)
        
        self.SetSizer(sizer)
        self.CenterOnScreen()

        self.Bind(wx.EVT_CLOSE, self.on_cancel)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

    def on_char_hook(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE or (key == wx.WXK_F4 and event.AltDown()):
            self.on_cancel()
            return
        event.Skip()

    def on_cancel(self, event=None):
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()
        
    def on_update(self, event):
        self.btn_update.Disable()
        self.btn_cancel.Disable()
        self.lbl_info.SetLabel(self.translate("Downloading update... Please wait."))
        self.progress.Show()
        self.Layout()
        
        threading.Thread(target=self.download_update, daemon=True).start()

    def download_update(self):
        try:
            exe_path = os.path.join(temp_dir, f"Setup_TTMediaBot_v{self.latest_version}.exe")
            
            req = urllib.request.Request(self.download_url, headers={'User-Agent': 'TTMediaBot-AutoUpdater'})
            with urllib.request.urlopen(req) as response:
                total_size = int(response.info().get('Content-Length', 0))
                downloaded = 0
                chunk_size = 65536
                last_percent = -1
                with open(exe_path, 'wb') as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            percent = int((downloaded / total_size) * 100)
                            if percent != last_percent:
                                last_percent = percent
                                wx.CallAfter(self.progress.SetValue, percent)
            
            wx.CallAfter(self.on_download_finished, exe_path)
            
        except Exception as e:
            wx.CallAfter(self.show_error, str(e))

    def on_download_finished(self, exe_path):
        if not _is_valid_window(self):
            return
        self.progress.SetValue(100)
        self.lbl_info.SetLabel(self.translate("Installing update... Please wait."))
        self.Layout()
        self.Refresh()
        self.Update()
        wx.CallLater(1500, self.install_and_restart, exe_path)

    def show_error(self, err_msg):
        wx.MessageBox(self.translate("Failed to download update:") + f" {err_msg}", self.translate("Update Error"), wx.OK | wx.ICON_ERROR, self)
        if self.IsModal():
            self.EndModal(wx.ID_CANCEL)
        else:
            self.Destroy()

    def install_and_restart(self, exe_path):
        bat_path = os.path.join(temp_dir, "ttmediabot_update.bat")
        
        # Locate target executable to launch after installation
        if getattr(sys, 'frozen', False):
            target_exe = sys.executable
        else:
            candidates = [
                os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "TTMediaBot", "TTMediaBot.exe"),
                os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "TTMediaBot", "TTMediaBot.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "TTMediaBot", "TTMediaBot.exe"),
                os.path.abspath("dist\\TTMediaBot\\TTMediaBot.exe"),
                os.path.abspath("TTMediaBot.exe"),
            ]
            target_exe = next((c for c in candidates if os.path.exists(c)), "")

        bat_lines = [
            "@echo off",
            "timeout /t 1 /nobreak >nul",
            f'start /wait "" "{exe_path}" /SILENT /SUPPRESSMSGBOXES /FORCECLOSEAPPLICATIONS /SP-',
            f'if exist "{exe_path}" del /f /q "{exe_path}"',
        ]
        if target_exe:
            bat_lines.append(f'if exist "{target_exe}" (')
            bat_lines.append(f'    start "" "{target_exe}"')
            bat_lines.append(') else (')
            bat_lines.append('    if exist "%ProgramFiles%\\TTMediaBot\\TTMediaBot.exe" start "" "%ProgramFiles%\\TTMediaBot\\TTMediaBot.exe"')
            bat_lines.append('    if exist "%LOCALAPPDATA%\\Programs\\TTMediaBot\\TTMediaBot.exe" start "" "%LOCALAPPDATA%\\Programs\\TTMediaBot\\TTMediaBot.exe"')
            bat_lines.append(')')
        else:
            bat_lines.append('if exist "%ProgramFiles%\\TTMediaBot\\TTMediaBot.exe" start "" "%ProgramFiles%\\TTMediaBot\\TTMediaBot.exe"')
            bat_lines.append('if exist "%LOCALAPPDATA%\\Programs\\TTMediaBot\\TTMediaBot.exe" start "" "%LOCALAPPDATA%\\Programs\\TTMediaBot\\TTMediaBot.exe"')
        bat_lines.append('(goto) 2>nul & del "%~f0"')
        bat_lines.append("")

        with open(bat_path, "w", encoding="utf-8") as f:
            f.write("\n".join(bat_lines))
            
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(["cmd.exe", "/c", bat_path], creationflags=CREATE_NO_WINDOW)

        app = wx.GetApp()
        if app:
            top_window = app.GetTopWindow()
            if top_window and hasattr(top_window, 'Close'):
                top_window.Close(True)
        sys.exit(0)


def run_auto_updater(translate_func, parent=None, manual=False, callback_done=None):
    """
    Checks for updates and displays update dialog or status message.
    If manual=True, runs asynchronously in a background thread to keep the UI completely responsive.
    """
    if manual:
        def _worker():
            latest_version, download_url, changelog_text, error_msg = check_for_updates()

            def _on_ui():
                target_parent = parent if _is_valid_window(parent) else None
                if target_parent is None:
                    app = wx.GetApp()
                    if app:
                        top = app.GetTopWindow()
                        if _is_valid_window(top):
                            target_parent = top

                try:
                    if callback_done:
                        try:
                            callback_done()
                        except Exception:
                            pass

                    if latest_version and download_url:
                        dlg = UpdateDialog(target_parent, latest_version, download_url, changelog_text, translate_func)
                        try:
                            dlg.ShowModal()
                        finally:
                            dlg.Destroy()
                    elif error_msg:
                        wx.MessageBox(
                            translate_func("Could not check for updates. Please check your internet connection.\n{error}").format(error=error_msg),
                            translate_func("Update Check"),
                            wx.OK | wx.ICON_WARNING,
                            target_parent
                        )
                    else:
                        wx.MessageBox(
                            translate_func("You are using the latest version of TTMediaBot (v{version}).").format(version=app_version),
                            translate_func("Update Check"),
                            wx.OK | wx.ICON_INFORMATION,
                            target_parent
                        )
                finally:
                    if _is_valid_window(target_parent):
                        try:
                            target_parent.Raise()
                            target_parent.SetFocus()
                        except Exception:
                            pass

            wx.CallAfter(_on_ui)

        threading.Thread(target=_worker, daemon=True).start()
    else:
        latest_version, download_url, changelog_text, _ = check_for_updates()
        if latest_version and download_url:
            target_parent = parent if _is_valid_window(parent) else None
            dlg = UpdateDialog(target_parent, latest_version, download_url, changelog_text, translate_func)
            try:
                dlg.ShowModal()
            finally:
                dlg.Destroy()
